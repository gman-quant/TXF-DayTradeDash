import bisect
import numpy as np
from config.settings import TIMEFRAMES
from core.numba_engine import get_profile_stats

def process_market_data(indicator_manager, lookback_count, timeframe):
    """
    處理原始數據：執行解環 (Unroll)、切片 (Slicing)、降頻 (Downsampling) 與向量化運算。
    
    Args:
        indicator_manager: RingBuffer 管理物件
        lookback_count: 回溯 Tick 數 (Slider)
        timeframe: K 線週期
    Returns:
        dict: 包含繪圖數據的字典 (None if no data)
    """
    
    # 1. 數據解環 (RingBuffer -> Linear Array)
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
    # 前端效能優化：限制最大繪圖點數
    TARGET_POINTS = 2000
    step = 1
    if lookback > TARGET_POINTS: 
        step = lookback // TARGET_POINTS
    
    # 4. Tick 數據準備 (Vectorized)
    timestamps_slice = linear_timestamps[start_idx::step]
    # int64 -> datetime64[ms] -> +8hr (UTC+8)
    tick_x_axis = timestamps_slice.astype('datetime64[ms]') + np.timedelta64(8, 'h')
    
    # 5. K 線數據準備 (Candlesticks)
    candles = indicator_manager.candles[tf_key]
    current_candle = indicator_manager.current_candles[tf_key]
    
    # 二分搜尋定位
    temp_idx = bisect.bisect_left(candles['time'], start_ts)
    candle_start_idx = max(0, temp_idx - 1)
    
    plot_candles = {
        'time': candles['time'][candle_start_idx:],
        'open': candles['open'][candle_start_idx:],
        'high': candles['high'][candle_start_idx:],
        'low':  candles['low'][candle_start_idx:],
        'close': candles['close'][candle_start_idx:]
    }
    
    # 合併即時 K 線
    if current_candle and current_candle.get('time'):
        for k in plot_candles:
            plot_candles[k].append(current_candle[k])

    # K 線 X 軸計算 (平移至 K 線結束時間)
    raw_candle_time = np.array(plot_candles['time'], dtype=np.int64)
    candle_x = (raw_candle_time + period_ms).astype('datetime64[ms]') + np.timedelta64(8, 'h')

    # 6. 指標數據解環
    view_history = {}
    for key in indicator_manager.history:
        view_history[key] = indicator_manager.get_linear_snapshot(key)

    # 7. 計算預設縮放範圍 (Auto-Range)
    if len(tick_x_axis) > 0:
        last_visible_ts = timestamps_slice[-1]
        current_candle_end_ts = (last_visible_ts // period_ms) * period_ms + period_ms
        x_max_ts = current_candle_end_ts + period_ms // 2
        
        x_min = tick_x_axis[0]
        x_max = np.datetime64(int(x_max_ts), 'ms') + np.timedelta64(8, 'h')
        default_range = [x_min, x_max]
    else:
        default_range = None

    # 8. 🆕 Volume Profile Snapshot
    # Only need non-zero part to save bandwidth
    raw_profile = indicator_manager.session_profile
    # Find active range to slice
    active_indices = np.where(raw_profile > 0)[0]
    # Filter out noise (Price 0~1000)
    active_indices = active_indices[active_indices > 1000]
    
    vp_data = None
    vp_stats = None
    
    if len(active_indices) > 0:
        min_p = active_indices[0]
        max_p = active_indices[-1]
        
        # Sliced arrays for plotting
        vp_prices = np.arange(min_p, max_p + 1)
        vp_volumes = raw_profile[min_p : max_p + 1]
        
        # Calc Stats (POC, VA)
        poc_idx, vah_idx, val_idx, total_vol = get_profile_stats(raw_profile)
        
        vp_data = {
            'price': vp_prices,
            'volume': vp_volumes
        }
        vp_stats = {
            'poc': int(poc_idx),
            'vah': int(vah_idx),
            'val': int(val_idx),
            'total_vol': int(total_vol)
        }

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
        'vp_data': vp_data,   
        'vp_stats': vp_stats  
    }

def get_last_value(history_dict: dict, key: str, default=0):
    """
    安全地從歷史數據字典中取得最新一筆值。
    """
    if key in history_dict and len(history_dict[key]) > 0:
        return history_dict[key][-1]
    return default
