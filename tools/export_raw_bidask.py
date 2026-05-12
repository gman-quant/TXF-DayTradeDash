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

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("ExportBidAsk")

async def export_bidask(date_str, session, broker="192.168.1.50:9092", topic="txf-bidask"):
    """
    從 Kafka 匯出指定日期的原始五檔數據，存為 Parquet 檔案。
    """
    logger.info(f"🚀 Starting export for {date_str} ({session}) from {topic}")
    
    # 1. 計算時間範圍
    dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt_obj.strftime("%Y")
    month = dt_obj.strftime("%m")
    
    ticks_path = Path(DATA_ROOT) / "raw_ticks" / "TXF" / year / month / f"{date_str}_TXF_ticks.parquet"
    start_dt, end_dt = None, None
    suffix = "" if session == "both" else f"_{session}"
    
    if ticks_path.exists():
        logger.info(f"🔍 Found ticks file: {ticks_path}. Extracting exact time bounds...")
        try:
            lazy_df = pl.scan_parquet(ticks_path)
            ts_col = 'ts' if 'ts' in lazy_df.columns else 'timestamp'
            
            df_ticks = lazy_df.select([ts_col]).collect()
            
            if session == "day":
                df_ticks = df_ticks.filter((pl.col(ts_col).dt.hour() >= 8) & (pl.col(ts_col).dt.hour() < 14))
            elif session == "night":
                df_ticks = df_ticks.filter((pl.col(ts_col).dt.hour() >= 14) | (pl.col(ts_col).dt.hour() <= 5))
                
            if len(df_ticks) > 0:
                from datetime import timedelta
                min_dt = df_ticks[ts_col].min()
                max_dt = df_ticks[ts_col].max()
                
                # Expand buffer by 5 minutes
                start_dt = min_dt - timedelta(minutes=5)
                end_dt = max_dt + timedelta(minutes=5)
                logger.info(f"🎯 Dynamic bounds extracted from ticks: {start_dt} ~ {end_dt}")
            else:
                logger.warning(f"⚠️ No ticks found for session '{session}' in {ticks_path}.")
        except Exception as e:
            logger.error(f"Failed to extract bounds from ticks file: {e}")
            
    # Fallback to Calendar Logic if Ticks extraction failed or file missing
    if start_dt is None or end_dt is None:
        logger.info("⚠️ Falling back to Calendar Logic for time bounds...")
        try:
            start_dt, end_dt = get_history_range(date_str, None if session == "both" else session)
        except Exception as e:
            logger.error(f"Failed to get history range: {e}")
            return
            
    logger.info(f"📅 Final Time Range: {start_dt} ~ {end_dt}")

    # 2. 連線 Kafka
    group_id = f"gale_export_bidask_{datetime.now().strftime('%H%M%S')}"
    consumer = GaleKafkaConsumer(broker_url=broker, group_id=group_id, topics=[topic])
    
    data_list = []
    
    try:
        consumer.connect()
        logger.info("📡 Connected to Kafka. Consuming stream...")
        
        count = 0
        async for msg in consumer.consume_history(start_dt, end_dt):
            if not msg:
                continue
                
            try:
                # 解析 Protobuf
                quote = BidAsk()
                quote.ParseFromString(msg.value())
                
                # 轉為 Dictionary，保持 Array 格式
                # Protobuf 的 repeated 欄位直接 list() 即可轉換
                row = {
                    "timestamp_ms": quote.timestamp_ms,
                    "code": quote.code,
                    "bid_total_vol": quote.bid_total_vol,
                    "ask_total_vol": quote.ask_total_vol,
                    "bid_price": list(quote.bid_price),
                    "bid_volume": list(quote.bid_volume),
                    "diff_bid_vol": list(quote.diff_bid_vol),
                    "ask_price": list(quote.ask_price),
                    "ask_volume": list(quote.ask_volume),
                    "diff_ask_vol": list(quote.diff_ask_vol)
                }
                
                data_list.append(row)
                count += 1
                
                if count % 100000 == 0:
                    logger.info(f"⏳ Processed {count:,} messages...")
                    
            except Exception as e:
                logger.error(f"Parse error: {e}")
                
    except Exception as e:
        logger.error(f"Kafka error: {e}")
    finally:
        consumer.close()

    if not data_list:
        logger.warning("⚠️ No data found in the specified range.")
        return

    logger.info(f"✅ Finished reading {count:,} records. Creating Polars DataFrame...")
    
    # 3. 建立 DataFrame 並存成 Parquet
    df = pl.DataFrame(data_list)
    
    # 構建輸出路徑: D:\txf-data\raw_ticks\TXF\YYYY\MM\YYYY-MM-DD_TXF_bidask.parquet
    dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt_obj.strftime("%Y")
    month = dt_obj.strftime("%m")
    
    output_dir = Path(DATA_ROOT) / "raw_ticks" / "TXF" / year / month
    output_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{date_str}_TXF_bidask{suffix}.parquet"
    output_path = output_dir / filename
    
    logger.info(f"💾 Saving to {output_path} (Using zstd compression)")
    
    # 使用 zstd 壓縮，效果極佳且速度快
    df.write_parquet(output_path, compression="zstd")
    
    file_size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info(f"🎉 Export completed successfully! File size: {file_size_mb:.2f} MB")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export Raw BidAsk Data to Parquet")
    parser.add_argument("--date", type=str, required=True, help="Date in YYYY-MM-DD format")
    parser.add_argument("--session", type=str, choices=["day", "night", "both"], default="both", help="Session to export (default: both = full trading day)")
    parser.add_argument("--broker", type=str, default="192.168.1.50:9092", help="Kafka Broker")
    parser.add_argument("--topic", type=str, default="txf-bidask", help="Kafka Topic")
    
    args = parser.parse_args()
    
    try:
        asyncio.run(export_bidask(args.date, args.session, args.broker, args.topic))
    except KeyboardInterrupt:
        logger.info("❌ Export cancelled by user.")
