# tools/inspect_bidask.py

import sys
import os
import asyncio
import logging
from datetime import datetime

# 將專案根目錄加入路徑，以便匯入模組
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config.txf_calendar import get_history_range
from ingestion.kafka_consumer import GaleKafkaConsumer
from data_schemas.txf_data_pb2 import BidAsk # 引入 Quote 的 Protobuf

# --- 設定 ---
DATE = '2025-12-01'      # 您想檢查的日期
SESSION = 'day'          # 日盤 (資料量通常最大)
BROKER = '192.168.1.50:9092'
TOPIC = 'txf-bidask'     # 您的報價 Topic
GROUP = 'inspector_v1'   # 使用獨立的 Group ID 避免干擾主程式

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("Inspector")

async def run_inspection():
    # 1. 計算時間範圍 (使用既有的 Calendar 模組)
    start_dt, end_dt = get_history_range(DATE, SESSION)
    logger.info(f"🔍 Inspecting {TOPIC} for range: {start_dt} ~ {end_dt}")

    # 2. 初始化 Consumer
    consumer = GaleKafkaConsumer(
        broker_url=BROKER,
        group_id=GROUP,
        topics=[TOPIC]
    )
    consumer.connect()

    # 3. 執行歷史區間消費
    # 這裡直接使用我們寫好的 consume_history，它會自動定位 Start/End Offset
    data_generator = consumer.consume_history(start_dt, end_dt)

    count = 0
    sample_limit = 5 # 只印出前 5 筆詳細內容
    
    print("-" * 60)
    print(f"{'Time':<12} | {'Bid1':<8} | {'Ask1':<8} | {'DiffBid':<8} | {'DiffAsk':<8}")
    print("-" * 60)

    start_time = datetime.now()

    try:
        async for msg in data_generator:
            if not msg: continue

            # 解析 Protobuf
            quote = BidAsk()
            try:
                quote.ParseFromString(msg.value())
            except Exception as e:
                logger.error(f"Parse error: {e}")
                continue

            count += 1

            # 抽樣印出前幾筆，確認 Diff 欄位是否有值
            if count <= sample_limit:
                # 取得時間 (假設 timestamp_ms 存在)
                ts_str = datetime.fromtimestamp(quote.timestamp_ms / 1000).strftime('%H:%M:%S.%f')[:-3]
                
                # 取得 Level 1 價格
                bid1 = quote.bid_price[0] / 10000.0 if len(quote.bid_price) > 0 else 0
                ask1 = quote.ask_price[0] / 10000.0 if len(quote.ask_price) > 0 else 0
                
                # 取得 Diff (關鍵檢查點！)
                # 根據 Schema，diff 是 repeated int32
                diff_bid = quote.diff_bid_vol[0] if len(quote.diff_bid_vol) > 0 else 0
                diff_ask = quote.diff_ask_vol[0] if len(quote.diff_ask_vol) > 0 else 0
                
                print(f"{ts_str:<12} | {bid1:<8.0f} | {ask1:<8.0f} | {diff_bid:<+8} | {diff_ask:<+8}")

            # 每 5萬筆回報進度
            if count % 50000 == 0:
                print(f"🚀 Processed {count} quotes...")

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        consumer.close()
        
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    print("=" * 60)
    print(f"📊 Inspection Result for {DATE} ({SESSION})")
    print("=" * 60)
    print(f"Total Quotes  : {count:,}")
    print(f"Time Elapsed  : {duration:.2f} sec")
    if duration > 0:
        print(f"Speed         : {count / duration:,.0f} msgs/sec")
    print("-" * 60)
    
    # 評估記憶體 (RingBuffer 需要的大小)
    # 假設我們要把 Tick 和 Quote 混在一起，這裡算出的 count 加上 Tick 的 count 就是總需求
    print(f"💡 Insight: If you use a unified RingBuffer,")
    print(f"   you need capacity >= {count} + (Tick Count)")

if __name__ == "__main__":
    try:
        asyncio.run(run_inspection())
    except KeyboardInterrupt:
        pass