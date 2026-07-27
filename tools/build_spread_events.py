"""
跨月/次月價差「事件序列」產生器(spread/events)
==================================================================
只存**次月有成交的那些時刻** —— 因為 calendar_spread 與 r2_basis 都是階梯,
兩次成交之間沒有新資訊。次月一個完整交易日只成交約 700 筆,
對照 5s 固定網格的 13,680 格 → **小 17 倍**,而且無損。

⚠ `basis`(R1 − 現貨)**不存**:兩腿都密集(R1 每 0.38 秒成交),
  viewer 手上的 K 棒直接算得出來,沒有對齊問題(2026-07-27 實測:
  basis 每 5.66 秒就變 8.9 點,比 R1 的 7.0 還大 → 它本來就不該被階梯化)。

為什麼要「事件驅動」而不是在顯示 TF 上相減 —— 2026-07-23 10:00 的實例:
    逐筆真值 cs = 199~205
    1m bar  cs = 205  ✅       5m bar  cs = 39  ❌
  因為 5m 的兩腿各自是「該根最後一筆成交」,次月 10:00:58 成交完、近月卻是
  10:04:59 的收盤,差 4 分鐘而近月漲了 164 點。→ 誤差隨 TF 放大。

來源:kbars/5s(2020-02 起)。5s 是湖裡最細的 bar,兩腿時間差 ≤5 秒。
輸出:D:/txf-data/spread/events/{yyyy}/{date}_cs_events.parquet

用法:
    python -m tools.build_spread_events --date 2026-07-27
    python -m tools.build_spread_events --from 2020-02-25 --to 2026-07-24   # 回填
"""

import argparse
import glob
import os
import sys
from datetime import date as _date, datetime, timedelta
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import DATA_ROOT

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(DATA_ROOT)
OUT = ROOT / "spread" / "events"


def _load(sym: str, d: _date):
    p = ROOT / "kbars" / "5s" / sym / f"{d:%Y}" / f"{d}_{sym}_5s.parquet"
    if not p.exists():
        return None
    return (pl.read_parquet(p).select("ts", "date", "session", "close", "volume")
              .filter(pl.col("close") > 0).sort("ts"))


def build_day(d: _date):
    r1, r2, spot = _load("TXF", d), _load("TXFR2", d), _load("TSE", d)
    if r1 is None or r2 is None or r1.is_empty() or r2.is_empty():
        return None

    r1 = r1.select("ts", pl.col("close").alias("r1_px"))
    ev = r2.select("ts", "date", "session",
                   pl.col("close").alias("r2_px"), pl.col("volume").alias("r2_vol"))

    # 次月的每一根 5s(= 該 5 秒內有成交)配上「同時或稍早」的近月與現貨。
    # ⚠ 用 asof backward 而不是等值 join:次月成交的那 5 秒近月不一定有 bar
    #    (夜盤尤其),等值 join 會把那些事件整個丟掉。
    ev = ev.join_asof(r1, on="ts", strategy="backward")
    ev = ev.join_asof(r1.select("ts", pl.col("ts").alias("_r1_ts")),
                      on="ts", strategy="backward")
    if spot is not None and not spot.is_empty():
        ev = ev.join_asof(spot.select("ts", pl.col("close").alias("spot")),
                          on="ts", strategy="backward")
    else:
        ev = ev.with_columns(pl.lit(None, dtype=pl.Float64).alias("spot"))

    return ev.with_columns([
        (pl.col("r2_px") - pl.col("r1_px")).round(2).alias("calendar_spread"),
        (pl.col("r2_px") - pl.col("spot")).round(2).alias("r2_basis"),
        # 近月報價的陳舊度:夜盤可能拉長,是這一列可不可信的判準
        (pl.col("ts") - pl.col("_r1_ts")).dt.total_milliseconds().alias("r1_lag_ms"),
    ]).drop("_r1_ts").drop_nulls("calendar_spread")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--from", dest="d_from")
    ap.add_argument("--to", dest="d_to")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    if args.date:
        days = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    elif args.d_from and args.d_to:
        a = datetime.strptime(args.d_from, "%Y-%m-%d").date()
        b = datetime.strptime(args.d_to, "%Y-%m-%d").date()
        days = [a + timedelta(days=i) for i in range((b - a).days + 1)]
    else:
        sys.exit("需要 --date 或 --from/--to")

    n_ok = n_skip = n_rows = 0
    total_bytes = 0
    for d in days:
        out = OUT / f"{d:%Y}" / f"{d}_cs_events.parquet"
        if out.exists() and not args.overwrite:
            n_skip += 1
            continue
        ev = build_day(d)
        if ev is None or ev.is_empty():
            n_skip += 1
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        ev.write_parquet(out, compression="zstd")
        n_ok += 1
        n_rows += ev.height
        total_bytes += out.stat().st_size
        if len(days) == 1:
            lag = ev["r1_lag_ms"].drop_nulls()
            print(f"  {d}  {ev.height} 事件  {out.stat().st_size/1e3:.0f} KB")
            print(f"    cs 中位 {ev['calendar_spread'].median():.1f}  "
                  f"range [{ev['calendar_spread'].min():.0f}, {ev['calendar_spread'].max():.0f}]")
            print(f"    r1_lag_ms 中位 {lag.median():.0f}  p90 {lag.quantile(.9):.0f}  max {lag.max():.0f}")
            print(f"    sessions {dict(ev['session'].value_counts().iter_rows())}")

    if len(days) > 1:
        print(f"  產出 {n_ok} 天 / 跳過 {n_skip} 天(無資料或已存在)")
        print(f"  合計 {n_rows:,} 事件,{total_bytes/1e6:.1f} MB"
              f"(平均 {n_rows/max(n_ok,1):.0f} 事件/日、{total_bytes/1e3/max(n_ok,1):.0f} KB/日)")


if __name__ == "__main__":
    main()
