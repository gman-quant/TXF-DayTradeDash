import sys
import os
import asyncio
import logging
from datetime import datetime
import polars as pl
import argparse
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gale.feed.kafka_client import GaleKafkaConsumer
from data_schemas.txf_data_pb2 import BidAsk
from config.txf_calendar import get_history_range
from config.settings import DATA_ROOT

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("VerifyBidAsk")

async def verify_bidask(date_str, session, limit=100, broker="192.168.1.50:9092", topic="txf-bidask"):
    """
    驗證匯出的 Parquet 檔案與 Kafka 原始資料是否 100% 一致。
    """
    if limit > 0:
        print(f"🔍 驗證目標: {date_str} ({session}) - 抽取 {limit} 筆")
    else:
        print(f"🔍 驗證目標: {date_str} ({session}) - 執行【全量比對】(Full Validation)")
    print("=" * 60)
    
    # 1. 解析 Parquet 路徑
    dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt_obj.strftime("%Y")
    month = dt_obj.strftime("%m")
    
    suffix = ""
    if session != "both":
        suffix = "_night" if session == "night" else ""
    filename = f"{date_str}_TXF_bidask{suffix}.parquet"
    parquet_path = Path(DATA_ROOT) / "raw_ticks" / "TXF" / year / month / filename
    
    if not parquet_path.exists():
        # 如果找不到帶 suffix 的，試試看沒帶的 (看 export_raw_bidask 怎麼存)
        alt_path = Path(DATA_ROOT) / "raw_ticks" / "TXF" / year / month / f"{date_str}_TXF_bidask.parquet"
        if alt_path.exists():
            parquet_path = alt_path
        else:
            print(f"❌ 找不到對應的 Parquet 檔案: {parquet_path}")
            return
            
    print(f"✅ 找到 Parquet 檔案: {parquet_path}")
    
    # 2. 載入 Parquet 並抽出前 limit 筆 (或是中間的筆數) 作為基準
    try:
        df = pl.read_parquet(parquet_path)
        if len(df) == 0:
            print("❌ Parquet 檔案是空的")
            return
            
        if limit > 0:
            # 為了避免開盤的稀疏資料，從中間開始取樣
            start_idx = min(1000, max(0, len(df) - limit))
            df_sample = df.slice(start_idx, limit)
        else:
            # 全量比對
            df_sample = df
            
        sample_timestamps = set(df_sample['timestamp_ms'].to_list())
        
        print(f"✅ 成功從 Parquet 載入資料。共 {len(df_sample):,} 筆等待比對。")
        
    except Exception as e:
        print(f"❌ 無法讀取 Parquet: {e}")
        return

    # 3. 準備從 Kafka 抓資料比對
    try:
        if session == "both":
            start_dt, end_dt = get_history_range(date_str, None)
        else:
            start_dt, end_dt = get_history_range(date_str, session)
    except Exception as e:
        print(f"❌ 無法取得時間範圍: {e}")
        return

    group_id = f"gale_verify_bidask_{datetime.now().strftime('%H%M%S')}"
    consumer = GaleKafkaConsumer(broker_url=broker, group_id=group_id, topics=[topic])
    
    kafka_records = {}
    
    try:
        consumer.connect()
        print("📡 正在從 Kafka 下載對應區段的原始訊息...")
        
        # 這裡我們可以用 timestamp 去對，但 Kafka 的 history range 是看 offset 的
        # 所以直接讀取，然後過濾出在 sample_timestamps 裡面的資料
        # 轉為 set 以大幅加速 in 的尋找速度
        min_ts = min(sample_timestamps)
        max_ts = max(sample_timestamps)
        target_count = len(sample_timestamps)
        
        # 將 min_ts 轉回 datetime 供 seek 使用，稍微往前抓一點點以防萬一
        seek_dt = datetime.fromtimestamp((min_ts - 5000) / 1000.0) 
        # 但如果是跨日/盤別，直接用 start_dt seek 比較安全
        
        count = 0
        match_count = 0
        
        # 由於 consume_history 已經綁定 start/end offset，我們直接遍歷並找目標
        async for msg in consumer.consume_history(start_dt, end_dt):
            if not msg: continue
            
            # 必須解碼 Protobuf 才能拿到精準的業務時間戳
            quote = BidAsk()
            quote.ParseFromString(msg.value())
            
            payload_ts = quote.timestamp_ms
            
            if payload_ts in sample_timestamps:
                # 命中取樣目標
                if payload_ts not in kafka_records:
                    kafka_records[payload_ts] = []
                
                kafka_records[payload_ts].append(quote)
                match_count += 1
                
                # 如果是全量比對，或是命中數量已達標，就提早結束
                if limit > 0 and match_count >= target_count * 2:
                    break
            
            count += 1
            if count % 100000 == 0:
                print(f"  ... 已讀取 {count:,} 筆 Kafka 訊息 (目前命中 {match_count:,} 筆)")
                
            if limit > 0 and payload_ts > max_ts + 60000:
                # 只有在取樣模式下，才啟動提早結束
                print(f"  ... 搜尋超過最大目標時間，提早結束。")
                break
                
    except Exception as e:
        print(f"❌ Kafka 讀取錯誤: {e}")
    finally:
        consumer.close()

    # 4. 進行 1 對 1 比對
    print("\n" + "=" * 60)
    print("🔬 開始嚴格比對 (Strict Validation)")
    print("=" * 60)
    
    errors = 0
    passed = 0
    
    for row in df_sample.iter_rows(named=True):
        ts = row['timestamp_ms']
        
        if ts not in kafka_records:
            print(f"❌ 找不到 Kafka 紀錄: Timestamp {ts}")
            errors += 1
            continue
            
        # 找尋符合的 Kafka 紀錄 (處理同一毫秒多筆的情況)
        matched = False
        for k_quote in kafka_records[ts]:
            # 比對長度與內容
            p_bid = list(row['bid_price'])
            k_bid = list(k_quote.bid_price)
            p_bvol = list(row['bid_volume'])
            k_bvol = list(k_quote.bid_volume)
            p_ask = list(row['ask_price'])
            k_ask = list(k_quote.ask_price)
            p_avol = list(row['ask_volume'])
            k_avol = list(k_quote.ask_volume)
            
            if p_bid == k_bid and p_bvol == k_bvol and p_ask == k_ask and p_avol == k_avol:
                # 完全命中
                passed += 1
                matched = True
                break
                
        if not matched:
            print(f"❌ 資料內容不符: Timestamp {ts}")
            print(f"  Parquet Bid: {list(row['bid_price'])} / {list(row['bid_volume'])}")
            print(f"  Parquet Ask: {list(row['ask_price'])} / {list(row['ask_volume'])}")
            print(f"  Kafka    :   {[ {'bid_p': list(q.bid_price), 'ask_p': list(q.ask_price)} for q in kafka_records[ts] ]}")
            errors += 1

    print("\n" + "=" * 60)
    if errors == 0 and passed == len(df_sample):
        print(f"🟢 [PASS] Data integrity verified: Kafka == Parquet")
        print(f"🟢 成功比對 {passed:,} 筆，陣列結構與數值 100% 無損！")
    else:
        print(f"🔴 [FAIL] 驗證失敗。通過: {passed}, 錯誤: {errors}")
    print("=" * 60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify BidAsk Data Integrity")
    parser.add_argument("--date", type=str, required=True, help="Date in YYYY-MM-DD format")
    parser.add_argument("--session", type=str, choices=["day", "night", "both"], default="both", help="Session to verify (default: both)")
    parser.add_argument("--limit", type=int, default=100, help="Number of samples to verify. Set to 0 for FULL validation.")
    
    args = parser.parse_args()
    
    try:
        asyncio.run(verify_bidask(args.date, args.session, args.limit))
    except KeyboardInterrupt:
        pass
