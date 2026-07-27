"""
txf-md-raw(Kafka,JSON)→ parquet 檔案庫
==================================================================
`export_raw_bidask.py` 的後繼者。差別:

  舊:txf-bidask(protobuf,只有 R1、只有五檔、**沒有衍生一檔**)
  新:txf-md-raw(JSON,R1+R2 × quote/tick,**含 first_derived_***)

為什麼需要:protobuf 那條路當初手選了欄位,漏掉衍生一檔(= 期交所組合簿的
唯一入口)整整八個月,而 bidask/quote **沒有歷史 API,補不回來**。
新的擷取層走 `to_dict()` 全收,這支只負責把它落地成可查詢的 parquet。

輸出:
    D:/txf-data/md_raw/{type}/{role}/{yyyy}/{mm}/{date}_{type}_{role}.parquet
    D:/txf-data/md_raw/_manifest/{date}.json          ← 完整性檢查的產物

用法:
    python -m tools.export_md_raw --date 2026-07-27
    python -m tools.export_md_raw --date 2026-07-27 --broker 192.168.1.50:9092

⚠ 三個踩過的坑,已在碼內處理:
  1. **時區**:舊的 `_TXF_bidask.parquet` 存 UTC、tick/kbar 存本地 → join 差 8 小時
     且不報錯。本檔一律寫**本地 naive**,欄位名叫 `ts` 不叫 `timestamp_ms`。
  2. **交易日 ≠ 日曆日**:交易日 D = 前一交易日晚上的夜盤 + D 的日盤。
     取資料的時窗因此是 [前一平日 14:00, D 14:00)。
  3. **衍生一檔的 0**:TAIFEX 規格「委託刪除時揭示價量 0」→ `price == 0` 是
     **無衍生報價**不是價格零,一律存 null。
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
from confluent_kafka import Consumer, TopicPartition

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import DATA_ROOT

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")      # cp950 會炸中文

TOPIC = "txf-md-raw"
OUT_ROOT = Path(DATA_ROOT) / "md_raw"

# producer 自己加的欄位(`_` 前綴,避免與 Shioaji 欄位相撞)
OWN = ["_type", "_role", "_seq", "_recv_ns"]

# 各型別預期的 Shioaji 欄位(= to_dict() 的 keys,2026-07-27 實測釘死)。
# 不在清單裡的 key **不會被丟掉**,會原樣進 `_extra`(見下方 build_frame)。
EXPECTED = {
    "quote": {
        "exchange", "code", "date", "time", "datetime", "underlying_price",
        "open", "avg_price", "close", "high", "low", "amount", "total_amount",
        "volume", "total_volume", "tick_type", "chg_type", "price_chg", "pct_chg",
        "bid_side_total_vol", "ask_side_total_vol", "bid_side_total_cnt", "ask_side_total_cnt",
        "bid_price", "bid_volume", "diff_bid_vol", "ask_price", "ask_volume", "diff_ask_vol",
        "first_derived_bid_price", "first_derived_ask_price",
        "first_derived_bid_vol", "first_derived_ask_vol", "simtrade",
    },
    "bidask": {
        "code", "date", "time", "datetime", "underlying_price",
        "bid_total_vol", "ask_total_vol",
        "bid_price", "bid_volume", "diff_bid_vol", "ask_price", "ask_volume", "diff_ask_vol",
        "first_derived_bid_price", "first_derived_ask_price",
        "first_derived_bid_vol", "first_derived_ask_vol", "simtrade",
    },
    "tick": {
        "code", "date", "time", "datetime", "underlying_price",
        "open", "close", "high", "low", "avg_price", "amount", "total_amount",
        "volume", "total_volume", "tick_type", "chg_type", "price_chg", "pct_chg",
        "bid_side_total_vol", "ask_side_total_vol", "simtrade",
    },
}

# 型別轉換規則
F_SCALAR = {"underlying_price", "open", "close", "high", "low", "avg_price",
            "amount", "total_amount", "price_chg", "pct_chg"}
F_DERIV = {"first_derived_bid_price", "first_derived_ask_price"}     # 0 → null
F_LIST = {"bid_price", "ask_price"}                                   # List[Float64]
I_LIST = {"bid_volume", "ask_volume", "diff_bid_vol", "diff_ask_vol"}  # List[Int32]
I_SCALAR = {"volume", "total_volume", "tick_type", "chg_type",
            "bid_side_total_vol", "ask_side_total_vol",
            "bid_side_total_cnt", "ask_side_total_cnt",
            "bid_total_vol", "ask_total_vol",
            "first_derived_bid_vol", "first_derived_ask_vol"}


# ------------------------------------------------------------------ 時窗
def prev_weekday(d):
    d -= timedelta(days=1)
    while d.weekday() >= 5:                 # 5=Sat 6=Sun
        d -= timedelta(days=1)
    return d


def window_for(trade_date):
    """交易日 D 的資料時窗 = [前一平日 14:00, D 14:00)。

    為什麼是 14:00:夜盤盤前 14:48 起、日盤收 13:45,14:00 落在兩者之間的空檔,
    是唯一不會把任何一盤切成兩半的分界點。
    ⚠ 只認平日,國定假日會誤判 —— 但假日 producer 不跑(crontab 一~五)且
      交易所無資料,實務上只會產生空檔案。
    """
    return (datetime.combine(prev_weekday(trade_date), datetime.min.time()).replace(hour=14),
            datetime.combine(trade_date, datetime.min.time()).replace(hour=14))


def session_of(ts):
    """⚠ 邊界要含收盤那一分鐘:日盤收 13:45、夜盤收 05:00,收盤集合競價的訊息
    時戳就是 13:45:00.xxx / 05:00:00.xxx,用 `< 1345` 會把它們誤判成 PreOpen。"""
    hm = ts.hour * 100 + ts.minute
    if 845 <= hm <= 1345:
        return "Day"
    if hm >= 1500 or hm <= 500:
        return "Night"
    return "PreOpen"                        # 08:30–08:44、14:50–14:59 試撮


# ------------------------------------------------------------------ 讀 Kafka
def read_window(broker, t0, t1):
    c = Consumer({"bootstrap.servers": broker, "group.id": "md-raw-exporter",
                  "enable.auto.commit": False,
                  "fetch.min.bytes": 1, "fetch.wait.max.ms": 10})
    tp = TopicPartition(TOPIC, 0)
    lo, hi = c.get_watermark_offsets(tp, timeout=15.0, cached=False)
    if hi <= lo:
        c.close()
        return []
    start = c.offsets_for_times(
        [TopicPartition(TOPIC, 0, int(t0.timestamp() * 1000))], timeout=15.0)[0].offset
    if start < 0:
        start = lo
    c.assign([TopicPartition(TOPIC, 0, start)])
    end_ms = int(t1.timestamp() * 1000)
    out, empty = [], 0
    while True:
        batch = c.consume(num_messages=5000, timeout=2.0)
        if not batch:
            empty += 1
            if empty >= 3:
                break
            continue
        empty = 0
        stop = False
        for m in batch:
            if m.error():
                continue
            if m.timestamp()[1] >= end_ms:
                stop = True
                break
            out.append((m.offset(), m.value()))
        if stop or (out and out[-1][0] >= hi - 1):
            break
    c.close()
    return out


# ------------------------------------------------------------------ 轉 DataFrame
def build_frame(recs, typ):
    """recs = [(offset, dict)]。已知欄位展開成型別化欄位,未知的原樣進 `_extra`。

    `_extra` 是這個設計最重要的一欄:Shioaji 哪天加欄位,它會掉進這裡而不是被
    丟掉。每日檢查只要看 `_extra` 非空的比例,非零就代表格式變了。
    """
    expected = EXPECTED[typ]
    rows = []
    for off, d in recs:
        ts = datetime.fromisoformat(d["datetime"])
        r = {"ts": ts, "session": session_of(ts), "kafka_offset": off,
             "code": d.get("code"), "role": d.get("_role"),
             "seq": d.get("_seq"), "recv_ns": d.get("_recv_ns")}
        extra = {}
        for k, v in d.items():
            if k in OWN or k in ("datetime", "date", "time"):
                continue                     # datetime 已轉成 ts;date/time 是它的分解,冗餘
            if k not in expected:
                extra[k] = v                 # ← 安全閥
                continue
            if k in F_DERIV:
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    fv = None
                r[k] = fv if fv else None    # 0 = 無衍生報價(TAIFEX 規格),存 null
            elif k in F_SCALAR:
                try:
                    r[k] = float(v)
                except (TypeError, ValueError):
                    r[k] = None
            elif k in F_LIST:
                r[k] = [None if x is None else float(x) for x in (v or [])]
            elif k in I_LIST:
                r[k] = [int(x) for x in (v or [])]
            elif k in I_SCALAR:
                r[k] = None if v is None else int(v)
            elif k == "simtrade":
                r[k] = bool(v)
            elif k == "exchange":
                continue                     # 永遠是 TAIFEX,無資訊量
            else:
                r[k] = v
        r["_extra"] = json.dumps(extra, ensure_ascii=False) if extra else None
        rows.append(r)

    schema = {"ts": pl.Datetime("ms"), "session": pl.Categorical, "kafka_offset": pl.Int64,
              "code": pl.Categorical, "role": pl.Categorical,
              "seq": pl.Int64, "recv_ns": pl.Int64}
    for k in sorted(expected - {"datetime", "date", "time", "exchange", "code"}):
        if k in F_DERIV or k in F_SCALAR:
            schema[k] = pl.Float64
        elif k in F_LIST:
            schema[k] = pl.List(pl.Float64)
        elif k in I_LIST:
            schema[k] = pl.List(pl.Int32)
        elif k in I_SCALAR:
            schema[k] = pl.Int64
        elif k == "simtrade":
            schema[k] = pl.Boolean
    schema["_extra"] = pl.String
    return pl.DataFrame(rows, schema=schema).sort("ts")


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser(description="txf-md-raw → parquet")
    ap.add_argument("--date", required=True, help="交易日 YYYY-MM-DD(不是日曆日)")
    ap.add_argument("--broker", default="192.168.1.50:9092")
    ap.add_argument("--dry-run", action="store_true", help="只檢查不寫檔")
    args = ap.parse_args()

    trade_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    t0, t1 = window_for(trade_date)
    print(f"交易日 {trade_date}  資料時窗 {t0} ~ {t1}")

    msgs = read_window(args.broker, t0, t1)
    print(f"讀入 {len(msgs):,} 則")
    if not msgs:
        print("⚠ 沒有資料 —— 假日?producer 沒跑?先確認再說,不要當正常。")
        sys.exit(1)

    groups, bad = {}, 0
    for off, raw in msgs:
        try:
            d = json.loads(raw)
            groups.setdefault((d["_type"], d["_role"]), []).append((off, d))
        except Exception:
            bad += 1
    if bad:
        print(f"⚠ {bad} 則無法解析為 JSON")

    # ⚠ `_seq` 是**跨所有流的全域計數器**(producer 只有一個 itertools.count),
    #   所以斷號必須在**合併後**依 offset 序算,不能在各 (type, role) 群組內算 ——
    #   群組內看到的「跳號」只是別的流拿走了中間那幾號,不是遺失。
    all_seq = []
    for off, raw in msgs:
        try:
            all_seq.append(json.loads(raw).get("_seq"))
        except Exception:
            pass
    all_seq = [s for s in all_seq if isinstance(s, int)]
    seq_gaps = seq_resets = 0
    for a, b in zip(all_seq, all_seq[1:]):
        if b == a + 1:
            continue
        if b < a:
            seq_resets += 1          # producer 重啟,計數器歸零 —— 正常(一天兩次)
        else:
            seq_gaps += b - a - 1    # 真正的遺失:_emit_raw 取號後 produce 失敗
    print(f"  全域 _seq:斷號 {seq_gaps}  歸零 {seq_resets}(= producer 重啟次數)")

    manifest, failed = {"trade_date": str(trade_date), "streams": {},
                        "bad_json": bad,
                        "seq_gaps": seq_gaps, "seq_resets": seq_resets}, []
    if seq_gaps:
        failed.append(f"全域 _seq 斷號 {seq_gaps} 個 = _emit_raw 失敗過,去查 journalctl 的 'Raw emit error'")
    for (typ, role), recs in sorted(groups.items()):
        if typ not in EXPECTED:
            failed.append(f"未知型別 {typ}(不在 EXPECTED,無法決定 schema)")
            continue

        keys = Counter()
        for _, d in recs:
            keys.update(k for k in d if k not in OWN)
        seen = {k for k in keys if k not in ("datetime", "date", "time")}
        missing = EXPECTED[typ] - seen - {"datetime", "date", "time"}
        unknown = seen - EXPECTED[typ]

        df = build_frame(recs, typ)
        info = {
            "n": df.height,
            "ts": [str(df["ts"].min()), str(df["ts"].max())],
            "offset": [recs[0][0], recs[-1][0]],
            "seq": [min(s for _, d in recs if isinstance(s := d.get("_seq"), int)),
                    max(s for _, d in recs if isinstance(s := d.get("_seq"), int))],
            "extra_nonempty": int(df["_extra"].is_not_null().sum()),
            "missing_keys": sorted(missing), "unknown_keys": sorted(unknown),
            "sessions": dict(Counter(df["session"].to_list())),
        }
        manifest["streams"][f"{typ}/{role}"] = info

        flag = ""
        if missing:
            failed.append(f"{typ}/{role} 缺少預期欄位 {sorted(missing)}")
            flag = "  ❌ 缺欄位"
        if unknown:
            flag += f"  ⚠ 新欄位 {sorted(unknown)}(已進 _extra,考慮升格)"
        print(f"  {typ}/{role:<3} {df.height:>8,} 則  {info['ts'][0][11:19]}~{info['ts'][1][11:19]}"
              f"  sessions={info['sessions']}{flag}")

        if not args.dry_run:
            out = (OUT_ROOT / typ / role / f"{trade_date:%Y}" / f"{trade_date:%m}"
                   / f"{trade_date}_{typ}_{role}.parquet")
            out.parent.mkdir(parents=True, exist_ok=True)
            df.write_parquet(out, compression="zstd")
            print(f"        → {out}  ({out.stat().st_size/1e6:.1f} MB)")

    if not args.dry_run:
        mp = OUT_ROOT / "_manifest" / f"{trade_date}.json"
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nmanifest → {mp}")

    # ⚠ 缺欄位要**中斷並回非零**,不是警告後繼續 ——
    #   這份資料補不回來,靜默寫出少欄位的檔比不寫還糟。
    if failed:
        print("\n❌ 斷言失敗:")
        for f in failed:
            print("   -", f)
        sys.exit(1)
    print("\n✅ 完成")


if __name__ == "__main__":
    main()
