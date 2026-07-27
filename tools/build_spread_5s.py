"""
跨月價差 5s 基準序列 + 對照圖
==================================================================
從 md_raw 的 quote R1/R2 算出**可成交**的跨月價差,聚合成 5s 序列。
所有更高 TF 都從這條序列往上聚合 —— 「怎麼算」只在這裡定義一次。

可成交價(單式一檔與衍生一檔**擇優**):
    ask_eff = min(ask[0], derived_ask)     # derived 為 null = 無衍生報價
    bid_eff = max(bid[0], derived_bid)

空近多遠(賣 R1 / 買 R2):
    entry = ask_eff(R2) − bid_eff(R1)      你實際要付的
    exit  = bid_eff(R2) − ask_eff(R1)      你實際拿得到的
    width = entry − exit                   摩擦

對照組 shown = close(R2) − close(R1),就是現在圖上那條(兩邊都用最後成交價)。

⚠ 聚合用**時間加權**,不是事件平均 —— 報價是事件驅動、間隔不等長,
  直接平均會被開盤/快市那些毫秒級更新拉走。

用法:
    python -m tools.build_spread_5s --date 2026-07-27
    python -m tools.build_spread_5s --date 2026-07-27 --no-chart
"""

import argparse
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import DATA_ROOT

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(DATA_ROOT)


def load(date, role):
    p = ROOT / "md_raw" / "quote" / role / date[:4] / date[5:7] / f"{date}_quote_{role}.parquet"
    if not p.exists():
        sys.exit(f"❌ 找不到 {p}(先跑 export_md_raw)")
    return pl.read_parquet(p).select(
        "ts", "session", "bid_price", "ask_price",
        "first_derived_bid_price", "first_derived_ask_price", "close",
    ).with_columns(
        pl.col("bid_price").list.get(0, null_on_oob=True).alias("obid"),
        pl.col("ask_price").list.get(0, null_on_oob=True).alias("oask"),
    ).with_columns(
        # 擇優:單式與衍生取較好的一邊。null(無衍生報價)在 max/min 會被忽略。
        pl.max_horizontal("obid", "first_derived_bid_price").alias(f"bid_{role}"),
        pl.min_horizontal("oask", "first_derived_ask_price").alias(f"ask_{role}"),
        pl.col("close").alias(f"last_{role}"),
    ).select("ts", "session", f"bid_{role}", f"ask_{role}", f"last_{role}").sort("ts")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--no-chart", action="store_true")
    args = ap.parse_args()
    d = args.date

    r1, r2 = load(d, "R1"), load(d, "R2")
    print(f"quote R1 {r1.height:,} 則 | quote R2 {r2.height:,} 則")

    # 以 R2 的每次更新為評價時點(次月是稀缺的一方),向後取最近的 R1 報價。
    # ⚠ 這是 carry-forward,但對「報價」語意正確(掛單有效直到被取代);
    #    仍要記錄陳舊度,超標的時點不可信。
    j = (r2.join_asof(r1.drop("session"), on="ts", strategy="backward")
           .drop_nulls(["bid_R1", "ask_R1", "bid_R2", "ask_R2"]))
    print(f"對齊後 {j.height:,} 個評價時點")

    j = j.with_columns(
        (pl.col("ask_R2") - pl.col("bid_R1")).alias("entry"),
        (pl.col("bid_R2") - pl.col("ask_R1")).alias("exit"),
        (pl.col("last_R2") - pl.col("last_R1")).alias("shown"),
    ).with_columns(
        (pl.col("entry") - pl.col("exit")).alias("width"),
        # 每個報價「存活多久」= 時間加權的權重
        (pl.col("ts").shift(-1) - pl.col("ts")).dt.total_milliseconds()
            .fill_null(0).clip(0, 60_000).alias("dur"),
    )

    def tw(col):
        return ((pl.col(col) * pl.col("dur")).sum() / pl.col("dur").sum()).alias(col)

    g = (j.with_columns(pl.col("ts").dt.truncate("5s").alias("bucket"))
           .group_by("bucket", "session")
           .agg(tw("entry"), tw("exit"), tw("width"), tw("shown"),
                pl.col("entry").max().alias("entry_hi"),
                pl.col("entry").min().alias("entry_lo"),
                pl.col("exit").max().alias("exit_hi"),
                pl.col("exit").min().alias("exit_lo"),
                pl.len().alias("n_events"))
           .rename({"bucket": "ts"}).sort("ts"))
    print(f"5s 序列 {g.height:,} 根\n")

    out = ROOT / "spread" / "5s" / d[:4] / f"{d}_calendar_5s.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    g.write_parquet(out, compression="zstd")
    print(f"→ {out}  ({out.stat().st_size/1e3:.0f} KB)")

    for c, lab in (("entry", "進場(含 derived)"), ("exit", "平倉(含 derived)"),
                   ("width", "摩擦"), ("shown", "圖上顯示(last−last)")):
        s = g[c].drop_nulls()
        print(f"  {lab:<22} 中位 {s.median():>7.1f}  p10 {s.quantile(.1):>7.1f}  "
              f"p90 {s.quantile(.9):>7.1f}  幅度 {s.quantile(.9)-s.quantile(.1):>6.1f}")

    if args.no_chart:
        return

    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28],
                        vertical_spacing=0.05,
                        subplot_titles=("跨月價差:可成交帶 vs 圖上顯示", "摩擦(帶寬)"))
    x = g["ts"].to_list()
    # 可成交帶:上緣進場、下緣平倉,中間填色
    fig.add_trace(go.Scatter(x=x, y=g["exit"], name="平倉(拿得到)",
                             line=dict(color="#2E86C1", width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=g["entry"], name="進場(要付出)", fill="tonexty",
                             fillcolor="rgba(46,134,193,0.22)",
                             line=dict(color="#2E86C1", width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=g["shown"], name="圖上顯示(last−last)",
                             line=dict(color="#E74C3C", width=1, dash="dot")), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=g["width"], name="摩擦",
                             line=dict(color="#7D3C98", width=1)), row=2, col=1)
    fig.update_layout(template="plotly_white", height=760,
                      title=f"TXF 跨月價差 {d}(5s,時間加權)",
                      hovermode="x unified", legend=dict(orientation="h", y=1.06))
    fig.update_yaxes(title_text="點", row=1, col=1)
    fig.update_yaxes(title_text="點", row=2, col=1)
    html = ROOT / "spread" / f"{d}_calendar_spread.html"
    fig.write_html(html)
    print(f"\n圖 → {html}")


if __name__ == "__main__":
    main()
