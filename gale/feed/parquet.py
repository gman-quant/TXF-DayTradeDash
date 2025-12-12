
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
            
        # Add session_id for session-aware calculations
        # Assuming timestamp is in ms (UTC)
        df = df.with_columns(
            (pl.from_epoch("timestamp", time_unit="ms").dt.date().cast(pl.Utf8)).alias("session_id")
        )

        # [Fix] Synthesize 'total_volume' if missing
        if 'total_volume' not in df.columns:
            logger.info("ℹ️ 'total_volume' missing. Synthesizing from cumulative sum of 'volume'.")
            # 3. 分組計算累計量 (Volume Reset per Session)
            df = df.with_columns(
                pl.col("volume").cum_sum().alias("total_volume")
            )
            
        # [Session-Aware Advanced Indicators] 
        # 為了修正 "跨盤時指標沒歸零" 的問題，我們在這裡手動計算所有狀態指標
        # 並稍後直接覆寫 Shared Memory，繞過 write_batch 的單純累加邏輯
        
        # A. Session High/Low
        df = df.with_columns([
            pl.col("price").cum_max().over("session_id").alias("session_high"),
            pl.col("price").cum_min().over("session_id").alias("session_low")
        ])
        
        # B. VWAP Components (cum_pv, cum_volume)
        df = df.with_columns([
            (pl.col("price") * pl.col("volume")).cum_sum().over("session_id").alias("cum_pv"),
            pl.col("volume").cum_sum().over("session_id").alias("cum_volume")
        ])
        
        # C. CVD Components (cum_buy_vol, cum_sell_vol)
        df = df.with_columns([
            pl.when(pl.col("tick_type") == 1).then(pl.col("volume")).otherwise(0).cum_sum().over("session_id").alias("cum_buy_vol"),
            pl.when(pl.col("tick_type") == 2).then(pl.col("volume")).otherwise(0).cum_sum().over("session_id").alias("cum_sell_vol")
        ])
        
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
    logger.info("✨ Using Enhanced Session-Aware Logic for indicators.")
    
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
        
    # [Pre-fetched Advanced Indicators]
    # 我們將這些已經算好的 Session-Aware 數據直接寫入 SHM，覆蓋 write_batch 的預設值
    arr_session_high = df['session_high'].to_numpy()
    arr_session_low  = df['session_low'].to_numpy()
    arr_cum_volume   = df['cum_volume'].to_numpy() # Note: logic differs from total_volume? total_volume is cumsum of volume. Yes usually same but total_volume behaves as "Day Volume".
    arr_cum_pv       = df['cum_pv'].to_numpy()
    arr_cum_buy      = df['cum_buy_vol'].to_numpy()
    arr_cum_sell     = df['cum_sell_vol'].to_numpy()
    
    start_data_time = ts_arr[0] / 1000.0
    total = len(df)
    
    count = 0
    
    # Fast Loop using zipped numpy arrays
    # 這是 Python 效能優化的關鍵，避免每一次 iter_rows 都產生 dict
    
    # 準備迭代器
    iter_src = zip(ts_arr, price_arr, vol_arr, type_arr)
    
    for idx, (ts, price, vol, tick_type) in enumerate(iter_src):
        # 1. 構建 Protobuf (Basic Fields)
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
        
        # 2. 批次寫入與覆寫
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

            # A. 呼叫標準寫入 (寫入 Basic Data)
            # 這裡要注意：write_batch 會自動更新 head，我們需要記錄寫入前的 head
            start_head = ring_buffer.head
            ring_buffer.write_batch(batch_buffer)
            
            # B. [Critical] 覆寫 Session-Aware Indicators
            # 因為 write_batch 不懂 Session Reset，我們手動覆蓋
            end_idx = idx + 1 # 當前 batch 結束的 global index (exclusive)
            start_idx = end_idx - len(batch_buffer) # global index
            
            # 準備要寫入的 Numpy 片段
            chunk_high = arr_session_high[start_idx:end_idx]
            chunk_low  = arr_session_low[start_idx:end_idx]
            chunk_cv   = arr_cum_volume[start_idx:end_idx]
            chunk_cpv  = arr_cum_pv[start_idx:end_idx]
            chunk_cb   = arr_cum_buy[start_idx:end_idx]
            chunk_cs   = arr_cum_sell[start_idx:end_idx]
            
            # 執行 RingBuffer 覆寫 (Handle Wrapping)
            capacity = ring_buffer.capacity
            write_len = len(batch_buffer)
            
            if start_head + write_len <= capacity:
                # No Wrap
                ring_buffer.session_high[start_head : start_head+write_len] = chunk_high
                ring_buffer.session_low[start_head : start_head+write_len]  = chunk_low
                ring_buffer.cum_volume[start_head : start_head+write_len]   = chunk_cv
                ring_buffer.cum_pv[start_head : start_head+write_len]       = chunk_cpv
                ring_buffer.cum_buy_vol[start_head : start_head+write_len]  = chunk_cb
                ring_buffer.cum_sell_vol[start_head : start_head+write_len] = chunk_cs
            else:
                # Wrap
                first_len = capacity - start_head
                remain_len = write_len - first_len
                
                # Part 1
                ring_buffer.session_high[start_head:] = chunk_high[:first_len]
                ring_buffer.session_low[start_head:]  = chunk_low[:first_len]
                ring_buffer.cum_volume[start_head:]   = chunk_cv[:first_len]
                ring_buffer.cum_pv[start_head:]       = chunk_cpv[:first_len]
                ring_buffer.cum_buy_vol[start_head:]  = chunk_cb[:first_len]
                ring_buffer.cum_sell_vol[start_head:] = chunk_cs[:first_len]
                
                # Part 2
                ring_buffer.session_high[:remain_len] = chunk_high[first_len:]
                ring_buffer.session_low[:remain_len]  = chunk_low[first_len:]
                ring_buffer.cum_volume[:remain_len]   = chunk_cv[first_len:]
                ring_buffer.cum_pv[:remain_len]       = chunk_cpv[first_len:]
                ring_buffer.cum_buy_vol[:remain_len]  = chunk_cb[first_len:]
                ring_buffer.cum_sell_vol[:remain_len] = chunk_cs[first_len:]

            count += len(batch_buffer)
            batch_buffer.clear()
            
            if count % 20000 == 0:
                logger.info(f"Replayed {count}/{total} ticks ({(count/total)*100:.1f}%)")
                
    # Flush remaining
    if batch_buffer:
        start_head = ring_buffer.head
        ring_buffer.write_batch(batch_buffer)
        # B. Flush overwrite (Duplicate logic, simplified)
        # ... (For strictness we should duplicate the logic, but usually last batch is small importance. 
        # Actually logic is generic enough, let's just accept it might be minorly inaccurate for last <1000 ticks or copy logic.
        # I will copy logic for correctness)
        end_idx = total
        start_idx = total - len(batch_buffer)
        chunk_high = arr_session_high[start_idx:end_idx]
        chunk_low  = arr_session_low[start_idx:end_idx]
        chunk_cv   = arr_cum_volume[start_idx:end_idx]
        chunk_cpv  = arr_cum_pv[start_idx:end_idx]
        chunk_cb   = arr_cum_buy[start_idx:end_idx]
        chunk_cs   = arr_cum_sell[start_idx:end_idx]
        
        capacity = ring_buffer.capacity
        write_len = len(batch_buffer)
        if start_head + write_len <= capacity:
            ring_buffer.session_high[start_head : start_head+write_len] = chunk_high
            ring_buffer.session_low[start_head : start_head+write_len]  = chunk_low
            ring_buffer.cum_volume[start_head : start_head+write_len]   = chunk_cv
            ring_buffer.cum_pv[start_head : start_head+write_len]       = chunk_cpv
            ring_buffer.cum_buy_vol[start_head : start_head+write_len]  = chunk_cb
            ring_buffer.cum_sell_vol[start_head : start_head+write_len] = chunk_cs
        else:
            first_len = capacity - start_head
            ring_buffer.session_high[start_head:] = chunk_high[:first_len]
            ring_buffer.session_low[start_head:]  = chunk_low[:first_len]
            ring_buffer.cum_volume[start_head:]   = chunk_cv[:first_len]
            ring_buffer.cum_pv[start_head:]       = chunk_cpv[:first_len]
            ring_buffer.cum_buy_vol[start_head:]  = chunk_cb[:first_len]
            ring_buffer.cum_sell_vol[start_head:] = chunk_cs[:first_len]
            ring_buffer.session_high[:write_len-first_len] = chunk_high[first_len:]
            ring_buffer.session_low[:write_len-first_len]  = chunk_low[first_len:]
            ring_buffer.cum_volume[:write_len-first_len]   = chunk_cv[first_len:]
            ring_buffer.cum_pv[:write_len-first_len]       = chunk_cpv[first_len:]
            ring_buffer.cum_buy_vol[:write_len-first_len]  = chunk_cb[first_len:]
            ring_buffer.cum_sell_vol[:write_len-first_len] = chunk_cs[first_len:]
        
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
