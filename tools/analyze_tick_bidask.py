"""
分析 Tick 和 BidAsk 數據的對應關係

用途：
1. 統計某個 session 的 tick 和 bidask 資料筆數
2. 分析有多少 tick 對應到多筆 bidask data
3. 找出最大對應數量和發生時間
4. 評估對 LOB 狀態判定的影響
"""

import polars as pl
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# Fix ModuleNotFoundError for starting from tools/
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import DATA_ROOT

def analyze_tick_bidask_correspondence(date_str: str, session: str = 'day'):
    """
    分析指定日期和盤別的 tick-bidask 對應關係
    
    Args:
        date_str: 日期字串 (YYYY-MM-DD)
        session: 'day' 或 'night'
    """
    
    # 1. 構建檔案路徑
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt.strftime("%Y")
    month = dt.strftime("%m")
    
    # [Fix] Use centralized DATA_ROOT
    data_lake_root = f"{DATA_ROOT}/raw_ticks"
    
    tick_path = f"{data_lake_root}/TXF/{year}/{month}/{date_str}_TXF_ticks.parquet"
    bidask_path = f"{data_lake_root}/TXF/{year}/{month}/{date_str}_TXF_bidask.parquet"
    
    print(f"\n{'='*80}")
    print(f"分析目標: {date_str} {session.upper()} Session")
    print(f"{'='*80}\n")
    
    # 檢查檔案是否存在
    if not Path(tick_path).exists():
        print(f"❌ Tick 檔案不存在: {tick_path}")
        return
    if not Path(bidask_path).exists():
        print(f"❌ BidAsk 檔案不存在: {bidask_path}")
        return
        
    print(f"✅ Tick 檔案: {tick_path}")
    print(f"✅ BidAsk 檔案: {bidask_path}\n")
    
    # 2. 載入數據
    print("📂 載入數據中...")
    try:
        df_tick = pl.read_parquet(tick_path)
        df_bidask = pl.read_parquet(bidask_path)
    except Exception as e:
        print(f"❌ 載入失敗: {e}")
        return
    
    # 3. 檢查欄位並統一時間欄位名稱
    print(f"\n🔍 Tick Schema: {df_tick.columns}")
    print(f"🔍 BidAsk Schema: {df_bidask.columns}\n")
    
    # 統一時間欄位為 'timestamp'
    if 'ts' in df_tick.columns and 'timestamp' not in df_tick.columns:
        df_tick = df_tick.rename({'ts': 'timestamp'})
    if 'ts' in df_bidask.columns and 'timestamp' not in df_bidask.columns:
        df_bidask = df_bidask.rename({'ts': 'timestamp'})
    
    # 確保 timestamp 是 datetime 或可以轉換
    if df_tick['timestamp'].dtype == pl.Datetime:
        # 已經是 datetime，轉為毫秒整數
        df_tick = df_tick.with_columns(
            pl.col('timestamp').cast(pl.Int64).alias('ts_ms')
        )
    else:
        # 假設已經是毫秒整數
        df_tick = df_tick.with_columns(
            pl.col('timestamp').alias('ts_ms')
        )
    
    if df_bidask['timestamp'].dtype == pl.Datetime:
        df_bidask = df_bidask.with_columns(
            pl.col('timestamp').cast(pl.Int64).alias('ts_ms')
        )
    else:
        df_bidask = df_bidask.with_columns(
            pl.col('timestamp').alias('ts_ms')
        )
    
    # 4. Session 時間過濾
    # 日盤: 08:45 - 13:45 (台灣時間)
    # 夜盤: 15:00 - 次日 05:00
    
    # 將毫秒時間戳轉為 datetime 以便過濾
    df_tick = df_tick.with_columns(
        pl.from_epoch('ts_ms', time_unit='ms').alias('dt')
    )
    df_bidask = df_bidask.with_columns(
        pl.from_epoch('ts_ms', time_unit='ms').alias('dt')
    )
    
    if session == 'day':
        # 日盤時間過濾 (08:45-13:45 TW time, 但資料可能已經是 UTC-8)
        # 先嘗試直接用小時過濾
        df_tick = df_tick.filter(
            (pl.col('dt').dt.hour() >= 0) & (pl.col('dt').dt.hour() <= 5) |
            (pl.col('dt').dt.hour() >= 8) & (pl.col('dt').dt.hour() <= 13)
        )
        df_bidask = df_bidask.filter(
            (pl.col('dt').dt.hour() >= 0) & (pl.col('dt').dt.hour() <= 5) |
            (pl.col('dt').dt.hour() >= 8) & (pl.col('dt').dt.hour() <= 13)
        )
    
    # 5. 基本統計
    tick_count = len(df_tick)
    bidask_count = len(df_bidask)
    
    print(f"📊 基本統計:")
    print(f"  Tick 資料筆數: {tick_count:,}")
    print(f"  BidAsk 資料筆數: {bidask_count:,}")
    print(f"  比例: {bidask_count/tick_count:.2f}x\n")
    
    if tick_count == 0 or bidask_count == 0:
        print("❌ 沒有足夠的數據進行分析")
        return
    
    # 6. 分析對應關係
    print("🔬 分析 Tick-BidAsk 對應關係...")
    
    # 對每個 tick，計算有多少個 bidask 的時間戳 <= tick 時間戳且在下一個 tick 之前
    # 更簡單的方式：對每個唯一的 tick 時間戳，計算有多少個 bidask 在同一時間
    
    # 方法: 統計每個時間戳的出現次數
    tick_ts_counts = df_tick.group_by('ts_ms').agg([
        pl.count().alias('tick_count')
    ])
    
    bidask_ts_counts = df_bidask.group_by('ts_ms').agg([
        pl.count().alias('bidask_count')
    ])
    
    # Join: 找出在同一個時間戳上 tick 和 bidask 的對應
    correspondence = tick_ts_counts.join(
        bidask_ts_counts,
        on='ts_ms',
        how='left'
    ).with_columns([
        pl.col('bidask_count').fill_null(0)
    ])
    
    # 統計有多少 tick 時間戳對應到 N 筆 bidask
    print(f"\n📈 Tick 時間戳對應到 BidAsk 的分佈:")
    
    # 統計分佈
    distribution = correspondence.group_by('bidask_count').agg([
        pl.count().alias('tick_timestamps')
    ]).sort('bidask_count')
    
    print(distribution)
    
    # 找出對應超過 1 筆的情況
    multi_bidask = correspondence.filter(pl.col('bidask_count') > 1)
    multi_bidask_count = len(multi_bidask)
    
    print(f"\n🎯 關鍵發現:")
    print(f"  有 {multi_bidask_count:,} 個 tick 時間戳對應到超過 1 筆 bidask")
    print(f"  佔總 tick 時間戳的比例: {multi_bidask_count/len(correspondence)*100:.2f}%")
    
    if multi_bidask_count > 0:
        max_bidask = correspondence['bidask_count'].max()
        print(f"  最多對應到: {max_bidask} 筆 bidask")
        
        # 找出最大值發生的時間
        max_cases = correspondence.filter(
            pl.col('bidask_count') == max_bidask
        ).with_columns([
            pl.from_epoch('ts_ms', time_unit='ms').alias('datetime')
        ])
        
        print(f"\n⏰ 最大對應數發生時間:")
        print(max_cases.select(['datetime', 'ts_ms', 'tick_count', 'bidask_count']))
        
        # 顯示前 10 筆對應最多的情況
        print(f"\n🔝 對應最多 BidAsk 的前 10 個 Tick 時間戳:")
        top_10 = correspondence.sort('bidask_count', descending=True).head(10).with_columns([
            pl.from_epoch('ts_ms', time_unit='ms').alias('datetime')
        ])
        print(top_10.select(['datetime', 'ts_ms', 'tick_count', 'bidask_count']))
    
    # 7. 更精確的分析：使用 asof join 模擬實際對應關係
    print(f"\n🔍 精確分析: 使用 asof join 模擬時間對應...")
    
    # 對每個 tick，找出在這個 tick 發生時，有多少個 bidask 更新發生在：
    # 1. 這個 tick 的時間戳
    # 2. 或是在上一個 tick 到這個 tick 之間
    
    # 先排序
    df_tick_sorted = df_tick.sort('ts_ms')
    df_bidask_sorted = df_bidask.sort('ts_ms')
    
    # 計算每個 tick 到下一個 tick 之間有多少 bidask
    tick_with_next = df_tick_sorted.with_columns([
        pl.col('ts_ms').shift(-1).alias('next_tick_ts')
    ])
    
    # 對於每個 tick interval，計算有多少 bidask 落在這個區間
    results = []
    
    # 只分析前 1000 筆 tick 以加快速度（可調整）
    sample_size = min(1000, len(tick_with_next))
    print(f"  (分析前 {sample_size} 筆 tick 作為樣本)\n")
    
    for i in range(sample_size):
        tick_ts = tick_with_next['ts_ms'][i]
        next_tick_ts = tick_with_next['next_tick_ts'][i]
        
        if next_tick_ts is None:
            # 最後一筆 tick
            bidask_in_interval = df_bidask_sorted.filter(
                pl.col('ts_ms') == tick_ts
            )
        else:
            # 計算在 [tick_ts, next_tick_ts) 之間的 bidask
            bidask_in_interval = df_bidask_sorted.filter(
                (pl.col('ts_ms') >= tick_ts) & (pl.col('ts_ms') < next_tick_ts)
            )
        
        count = len(bidask_in_interval)
        if count > 1:
            results.append({
                'tick_index': i,
                'tick_ts': tick_ts,
                'next_tick_ts': next_tick_ts,
                'bidask_count': count
            })
    
    if results:
        df_results = pl.DataFrame(results).with_columns([
            pl.from_epoch('tick_ts', time_unit='ms').alias('tick_datetime')
        ])
        
        print(f"📊 Interval 分析結果:")
        print(f"  在前 {sample_size} 筆 tick 中，有 {len(df_results)} 個 tick interval 包含超過 1 筆 bidask")
        print(f"  比例: {len(df_results)/sample_size*100:.2f}%\n")
        
        if len(df_results) > 0:
            max_interval_bidask = df_results['bidask_count'].max()
            print(f"  單個 tick interval 最多包含: {max_interval_bidask} 筆 bidask\n")
            
            print(f"🔝 包含最多 BidAsk 的前 10 個 Tick Intervals:")
            top_intervals = df_results.sort('bidask_count', descending=True).head(10)
            print(top_intervals.select(['tick_index', 'tick_datetime', 'bidask_count']))
    else:
        print(f"✅ 在前 {sample_size} 筆 tick 中，每個 tick interval 都只有 0-1 筆 bidask")
    
    # 8. 結論與建議
    print(f"\n{'='*80}")
    print("📝 結論與建議")
    print(f"{'='*80}\n")
    
    print("❓ 問題: 多筆 bidask 對應到同一個 tick 是否會導致無法判定真正的訂單簿狀態?\n")
    
    if multi_bidask_count > 0:
        print("⚠️  確實存在多筆 bidask 在同一時間戳的情況。")
        print("\n可能的原因:")
        print("  1. Tick (成交) 和 Quote (報價) 是兩個獨立的數據流")
        print("  2. 多筆報價更新可能在成交之前、之間或之後發生")
        print("  3. 時間戳精度問題（毫秒級可能無法區分微秒級的順序）")
        
        print("\n對 LOB 狀態判定的影響:")
        print("  ✓ 如果使用 'timestamp-based matching'，可能會遺失中間狀態")
        print("  ✓ 建議使用 'sequence-based matching' 或保留所有 quote 更新")
        print("  ✓ LOB Engine 應該處理同一時間戳的多筆 quote 更新")
        
        print("\n建議:")
        print("  1. 在 LOB Engine 中按照接收順序處理所有 quote 更新")
        print("  2. 當 tick 到來時，使用最新的 LOB 狀態（所有 quote 都已處理）")
        print("  3. 考慮使用 sequence number 而非純粹依賴 timestamp")
    else:
        print("✅ 在分析的樣本中，沒有發現明顯的多對一對應問題")
        print("   當前的 timestamp-based matching 應該可以正常工作")
    
    print(f"\n{'='*80}\n")

if __name__ == "__main__":
    # 預設分析 2025-12-22 日盤
    date = "2025-12-22" if len(sys.argv) < 2 else sys.argv[1]
    session = "day" if len(sys.argv) < 3 else sys.argv[2]
    
    analyze_tick_bidask_correspondence(date, session)
