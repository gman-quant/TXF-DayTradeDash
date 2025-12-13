
"""
gale/dashboard/state.py

負責處理儀表板的「狀態與數據 (State & Data)」。

核心流程：
1. 解環 (Unroll): 將循環寫入的 RingBuffer 展平成線性的 NumPy Array。
2. 切片 (Slicing): 根據用戶的 Lookback (回溯 N 筆) 決定可視範圍。
3. 降頻 (Downsampling): 為了前端繪圖流暢度，對過多的數據點進行動態抽樣。
4. 指標聚合: 計算 Volume Profile 與即時 K 線狀態。
"""

import bisect
import numpy as np
from config.settings import TIMEFRAMES

VP_BIN_SIZE = 1 # Volume Profile 價格分箱大小 (點)

def get_last_value(history_dict: dict, key: str, default=0):
    """
    安全地從歷史數據字典中取得最新一筆值。
    """
    if key in history_dict and len(history_dict[key]) > 0:
        return history_dict[key][-1]
    return default

def process_market_data(indicator_manager, lookback_count, timeframe):
    """
    [核心資料管道] 處理原始數據供前端繪圖使用。
    
    Data Flow:
    Raw RingBuffer -> Linear Snapshot -> Slice Window -> Downsampling -> Plotly Arrays
    
    Args:
        indicator_manager: RingBuffer 管理物件 (Source of Truth)
        lookback_count: 回溯 Tick 數 (Slider 控制)
        timeframe: K 線週期 string (e.g. '1m', '5m')
        
    Returns:
        dict: 包含繪圖數據的字典 (None if no data)
    """
    
    # 1. 數據解環 (RingBuffer -> Linear Array)
    # 用戶看到的圖表是線性的，但後端存儲是循環的，這裡需要一次記憶體複製操作
    linear_timestamps = indicator_manager.get_linear_snapshot("timestamp")
    raw_len = len(linear_timestamps)
    
    if raw_len == 0:
        return None

    # 2. 決定顯示範圍 (Scope Calculation)
    tf_key = timeframe if timeframe in indicator_manager.candles else '10s'
    period_ms = TIMEFRAMES.get(tf_key, 10000)
    
    # 防呆：確保 lookback 合理
    lookback = int(lookback_count) if lookback_count else 50000
    if lookback > raw_len:
        lookback = raw_len
    
    start_idx = max(0, raw_len - lookback)
    start_ts = linear_timestamps[start_idx] 
    
    # 3. 智慧降頻 (Smart Downsampling)
    # 前端效能優化：即使有 50000 筆數據，為了流暢度我們只送出約 2000 個點
    # 這不會影響 K 線的準確度 (K 線是後端算好的)，只影響 Tick Level 的折線圖細節
    TARGET_POINTS = 2000
    step = 1
    if lookback > TARGET_POINTS: 
        step = lookback // TARGET_POINTS
    
    # 4. Tick 數據準備 (Vectorized)
    # 使用 NumPy 切片進行抽樣
    timestamps_slice = linear_timestamps[start_idx::step]
    # 時間轉換：int64 [ms] -> datetime64[ms] -> +8小時 (UTC+8 台灣時間)
    tick_x_axis = timestamps_slice.astype('datetime64[ms]') + np.timedelta64(8, 'h')
    
    # 5. K 線數據準備 (Candlesticks)
    candles = indicator_manager.candles[tf_key]
    current_candle = indicator_manager.current_candles[tf_key]
    
    # 搜尋繪圖起始點：找出第一個時間大於等於 start_ts 的 K 線索引
    # 使用 bisect (二分搜尋) 達到 O(log N) 效能
    temp_idx = bisect.bisect_left(candles['time'], start_ts)
    candle_start_idx = max(0, temp_idx - 1)
    
    plot_candles = {
        'time': candles['time'][candle_start_idx:],
        'open': candles['open'][candle_start_idx:],
        'high': candles['high'][candle_start_idx:],
        'low':  candles['low'][candle_start_idx:],
        'close': candles['close'][candle_start_idx:],
        'volume': candles['volume'][candle_start_idx:]
    }
    
    # 合併尚未結算的「即時 K 線」 (Current Candle)
    # 這樣圖表最右邊的那根 K 線才會跳動
    if current_candle and current_candle.get('time'):
        for k in plot_candles:
            plot_candles[k].append(current_candle[k])

    # K 線 X 軸計算 (平移至 K 線結束時間，符合視覺習慣)
    raw_candle_time = np.array(plot_candles['time'], dtype=np.int64)

    candle_x = (raw_candle_time + period_ms).astype('datetime64[ms]') + np.timedelta64(8, 'h')

    # 6. 技術指標數據解環
    view_history = {}
    for key in indicator_manager.history:
        # 同步取得對應的指標數據切片
        # Ensure we don't overwrite whale_nuke if it was already processed
        if key not in view_history:
            view_history[key] = indicator_manager.get_linear_snapshot(key)

    # [NEW] VWAP Bands Calculation (On-the-fly)
    if 'close' in view_history and 'volume' in view_history:
        import gale.alpha.engine as ne
        close_arr = view_history['close']
        vol_arr = view_history['volume']
        # [Session-Aware Fix] Pass timestamp for reset detection
        ts_arr = view_history['timestamp'] 
        
        # Calculate +2.0 SD Bands
        vwap, upper, lower = ne.calc_vwap_bands_linear(close_arr, vol_arr, ts_arr, 2.0)
        
        view_history['VWAP_Upper'] = upper
        view_history['VWAP_Lower'] = lower

    # 7. 計算預設縮放範圍 (Auto-Range)
    # 確保 View 預設顯示到最新的 K 線位置，並留一點右側空間
    if len(tick_x_axis) > 0:
        last_visible_ts = timestamps_slice[-1]
        current_candle_end_ts = (last_visible_ts // period_ms) * period_ms + period_ms
        x_max_ts = current_candle_end_ts + period_ms // 2
        
        x_min = tick_x_axis[0]
        x_max = np.datetime64(int(x_max_ts), 'ms') + np.timedelta64(8, 'h')
        default_range = [x_min, x_max]
    else:
        default_range = None
        
    # 8. 提取 Volume Profile 數據 (含分箱優化)
    vp_prices, vp_volumes, vp_buy, vp_sell = indicator_manager.vp_engine.get_distribution(bin_size=VP_BIN_SIZE)
    poc, vah, val = indicator_manager.vp_engine.calculate()

    return {
        'tick_x': tick_x_axis,
        'candle_x': candle_x,
        'candles': plot_candles,
        'start_idx': start_idx,
        'step': step,
        'history': view_history,
        'raw_len': raw_len,
        'default_range': default_range,
        'timeframe': tf_key,
        'vp_data': {
            'prices': vp_prices,
            'volumes': vp_volumes,
            'buy_volumes': vp_buy,
            'sell_volumes': vp_sell,
            'poc': poc,
            'vah': vah,
            'val': val
        }
    }
