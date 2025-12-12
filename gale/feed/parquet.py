
import time
import sys
import argparse
import logging
import numpy as np
import datetime

# 引入核心組件
from data_schemas.txf_data_pb2 import Tick
from gale.infra.memory import SharedRingBuffer

# 設定 Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("ReplayFeed")

try:
    import polars as pl
except ImportError:
    logger.error("❌ Polars not installed. Please run: pip install polars")
    sys.exit(1)

def run_replay(parquet_file, topic, speed_factor=1.0, underlying_file=None):
    """
    Parquet 回放器主邏輯 (Polars High Performance Version)
    Args:
        parquet_file: Parquet 檔案路徑
        topic: 用於建立 Shared Memory 名稱 (gale_shm_{topic})
        speed_factor: 回放速度 (1.0 = 原速, 0 = 極速)
        underlying_file: 加權指數 Parquet 檔 (Optional)
    """
    
    # 1. 載入數據 (TXF)
    logger.info(f"📂 Loading parquet file (Polars): {parquet_file}")
    try:
        # Lazy Loading if file is huge, but eager is fine for daily data (<1GB)
        df = pl.read_parquet(parquet_file)
        
        # [Schema Adaptation]
        # 1. Rename columns map
        rename_map = {}
        if 'ts' in df.columns: rename_map['ts'] = 'timestamp'
        if 'close' in df.columns: rename_map['close'] = 'price'
        
        if rename_map:
            df = df.rename(rename_map)
            
        # 2. Timestamp Conversion (Datetime -> Int ms)
        # Polars Datetime handling
        # [Timezone Fix]
        # Data Lake 的 Parquet 通常是 Local Time (TW, UTC+8)
        # 系統核心 (Engine) 預期的是 UTC Timestamp
        # 儀表板 (Dashboard) 會再 +8 小時顯示
        # 所以這裡必須把 Local Time 轉回 UTC (-8小時)
        
        if df['timestamp'].dtype in (pl.Datetime, pl.Date):
            # 1. 轉為毫秒
            df = df.with_columns(
                (pl.col("timestamp").cast(pl.Int64) / 1_000_000).cast(pl.Int64).alias("timestamp")
            )
            # 2. 扣掉 8 小時 (28800000 ms)
            df = df.with_columns(
                (pl.col("timestamp") - 28800000).alias("timestamp")
            )
            
        # Select required columns
        # volume / tick_type existence
        required_cols = ['timestamp', 'price', 'volume']
        if 'tick_type' not in df.columns:
            df = df.with_columns(pl.lit(0).alias('tick_type')) # default 0 (unknown)
            
        # [Fix] Synthesize 'total_volume' if missing
        if 'total_volume' not in df.columns:
            logger.info("ℹ️ 'total_volume' missing. Synthesizing from cumulative sum of 'volume'.")
            df = df.with_columns(pl.col("volume").cum_sum().alias("total_volume"))
            
        # Sort
        df = df.sort("timestamp")
            
        logger.info(f"✅ Loaded {len(df)} ticks. Range: {df['timestamp'][0]} ~ {df['timestamp'][-1]}")
        
    except Exception as e:
        logger.error(f"Failed to load parquet: {e}")
        return

    # 1.5 載入加權指數 (TSE) - Optional Merge
    if underlying_file:
        logger.info(f"📉 Loading Underlying (TSE): {underlying_file}")
        try:
            df_tse = pl.read_parquet(underlying_file)
            
            # 適配 TSE 格式
            tse_rename = {}
            if 'ts' in df_tse.columns: tse_rename['ts'] = 'timestamp'
            if 'close' in df_tse.columns: tse_rename['close'] = 'underlying_price'
            if tse_rename:
                df_tse = df_tse.rename(tse_rename)
            
            # 轉換時間
            if df_tse['timestamp'].dtype in (pl.Datetime, pl.Date):
                df_tse = df_tse.with_columns(
                    (pl.col("timestamp").cast(pl.Int64) / 1_000_000).cast(pl.Int64).alias("timestamp")
                )
                # [Timezone Fix] Minus 8 hours for TSE as well
                df_tse = df_tse.with_columns(
                    (pl.col("timestamp") - 28800000).alias("timestamp")
                )
            
            # Select & Sort
            df_tse = df_tse.select(['timestamp', 'underlying_price']).sort('timestamp')
            
            logger.info("🔗 Merging TSE data using join_asof (backward)...")
            
            # Polars join_asof
            df = df.join_asof(
                df_tse, 
                on='timestamp', 
                strategy='backward'
            )
            
            # Fill Nulls (Forward Fill then Backward Fill)
            # Polars fill_null strategy
            df = df.with_columns(
                pl.col("underlying_price").fill_null(strategy="forward").fill_null(strategy="backward").fill_null(0)
            )
            
            avg_underlying = df['underlying_price'].mean()
            logger.info(f"✅ Merged Underlying Price (Avg: {avg_underlying:.2f})")
            
        except Exception as e:
            logger.warning(f"Failed to merge underlying data: {e}")

    # 2. 初始化 Shared Memory
    shm_name = f"gale_shm_{topic}"
    try:
        ring_buffer = SharedRingBuffer(name=shm_name, capacity=200000, create=True)
        # Try to find prev_close from polars df if exists
        # if 'prev_close' in df.columns: ...
        logger.info(f"✅ Shared Buffer Created: {shm_name}")
    except Exception as e:
        logger.error(f"Failed to init Shared Buffer: {e}")
        return

    # 3. 回放迴圈 (Polars Iteration)
    logger.info(f"🚀 Starting Replay (Speed: {speed_factor}x)...")
    
    batch_buffer = []
    BATCH_SIZE = 1000 # Increase batch size for efficiency
    
    start_wall_time = time.time()
    
    # Pre-fetch columns as underlying numpy arrays/lists for faster iteration
    # Iterating Polars rows directly is slower than numpy
    ts_arr = df['timestamp'].to_numpy() # already int64 ms
    price_arr = df['price'].to_numpy()
    vol_arr = df['volume'].to_numpy()
    type_arr = df['tick_type'].to_numpy()
    
    underlying_arr = None
    if 'underlying_price' in df.columns:
        underlying_arr = df['underlying_price'].to_numpy()

    total_vol_arr = None
    if 'total_volume' in df.columns:
        total_vol_arr = df['total_volume'].to_numpy()
        
    start_data_time = ts_arr[0] / 1000.0
    total = len(df)
    
    count = 0
    
    # Fast Loop using zipped numpy arrays
    # 這是 Python 效能優化的關鍵，避免每一次 iter_rows 都產生 dict
    
    # 準備迭代器
    iter_src = zip(ts_arr, price_arr, vol_arr, type_arr)
    
    for idx, (ts, price, vol, tick_type) in enumerate(iter_src):
        t = Tick()
        t.timestamp_ms = ts # int64
        t.close = int(price * 10000) # float -> int
        t.volume = int(vol) # int
        t.tick_type = int(tick_type)
        
        if underlying_arr is not None:
            t.underlying_price = int(underlying_arr[idx] * 10000)
            
        if total_vol_arr is not None:
             t.total_volume = int(total_vol_arr[idx])
        
        batch_buffer.append(t)
        
        if len(batch_buffer) >= BATCH_SIZE:
             # Speed Control
            if speed_factor > 0:
                current_data_time = ts / 1000.0
                elapsed_data = current_data_time - start_data_time
                target_wall_time = start_wall_time + (elapsed_data / speed_factor)
                
                now = time.time()
                if now < target_wall_time:
                    sleep_sec = target_wall_time - now
                    if sleep_sec > 0.001:
                        time.sleep(sleep_sec)

            ring_buffer.write_batch(batch_buffer)
            count += len(batch_buffer)
            batch_buffer.clear()
            
            if count % 20000 == 0:
                logger.info(f"Replayed {count}/{total} ticks ({(count/total)*100:.1f}%)")
                
    if batch_buffer:
        ring_buffer.write_batch(batch_buffer)
        
    logger.info("🏁 Replay Completed.")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        ring_buffer.shutdown()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('file', help="Parquet file path")
    parser.add_argument('--topic', default='txf-tick', help="Topic name for SHM")
    parser.add_argument('--speed', type=float, default=1.0, help="Replay speed (1.0=Realtime, 0=Max)")
    parser.add_argument('--underlying', help="Path to Underlying (TSE) parquet file", default=None)
    
    args = parser.parse_args()
    run_replay(args.file, args.topic, args.speed, args.underlying)
