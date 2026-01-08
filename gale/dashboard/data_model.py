
"""
gale.dashboard.data_model.py

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
from config.indicator_config import INDICATORS_SETUP, VWAP_MULTIPLIERS

VP_BIN_SIZE = 1 # Volume Profile 價格分箱大小 (點)

def get_last_value(history_dict: dict, key: str, default=0):
    """
    安全地從歷史數據字典中取得最新一筆值。
    """
    if key in history_dict and len(history_dict[key]) > 0:
        return history_dict[key][-1]
    return default


def _safe_calculate_vwap(view_history, pv_key, vol_key, default_arr):
    """
    Helper for safe division to calculate VWAP segment.
    """
    if pv_key in view_history and vol_key in view_history:
        pv = view_history[pv_key]
        vol = view_history[vol_key]
        res = np.zeros_like(pv)
        valid = vol > 0
        
        # If valid, calculate specific VWAP
        np.divide(pv, vol, out=res, where=valid)
        
        # If invalid (no volume in this sub-regime yet), use Default (Parent VWAP)
        res[~valid] = default_arr[~valid]
        return res
    return default_arr # Should not happen if keys exist

def _calculate_regime_bands(view_history, vwap_arr, pv_sq_key, vol_key, suffix_name, multipliers):
    """
    Helper to calculate and set Regime StdDev Bands.
    """
    if pv_sq_key in view_history and vol_key in view_history:
        pv_sq = view_history[pv_sq_key]
        vol = view_history[vol_key]
        
        # Variance = E[X^2] - (E[X])^2
        mean_sq = np.zeros_like(pv_sq)
        valid = vol > 0
        np.divide(pv_sq, vol, out=mean_sq, where=valid)
        
        variance = mean_sq - (vwap_arr * vwap_arr)
        variance[variance < 0] = 0.0
        std_dev = np.sqrt(variance)
        
        # Output Bands
        for mult in multipliers:
            # Upper Regime -> Add Bands (Resistance)
            if 'Bull' in suffix_name:
                    view_history[f'{suffix_name}_Band_{mult}'] = vwap_arr + (std_dev * mult)
            # Lower Regime -> Subtract Bands (Support)
            else:
                    view_history[f'{suffix_name}_Band_{mult}'] = vwap_arr - (std_dev * mult)

def process_market_data(indicator_manager, lookback_count, timeframe):
    """
    [核心資料管道] 處理原始數據供前端繪圖使用。
    [Optimized] 使用 Smart Slicing (Vectorized View) 避免全量複製。
    
    Data Flow:
    Calc Window -> Smart Slice (O(1)) -> Downsampling -> Plotly Arrays
    """
    
    # 1. 計算視窗範圍 (Scope Calculation, O(1))
    lookback = int(lookback_count) if lookback_count else 50000
    
    # 向 Manager 請求對應的 Buffer Indices
    window_indices = indicator_manager.get_view_window(lookback)
    start_idx, end_idx, is_wrapped = window_indices
    
    # 2. 智慧讀取 (Smart View Fetching, O(1))
    # 只複製視窗內的數據，而非整個 200k history
    timestamp_view = indicator_manager.get_linear_snapshot("timestamp", window=window_indices)
    raw_len = len(timestamp_view)
    
    if raw_len == 0:
        return None

    # 3. 智慧降頻 (Smart Downsampling)
    # 基於 View 的長度進行降頻
    start_ts = timestamp_view[0]
    
    TARGET_POINTS = 2000
    step = 1
    if raw_len > TARGET_POINTS:
        step = raw_len // TARGET_POINTS
    
    # 4. Tick 數據準備 (Vectorized)
    timestamps_slice = timestamp_view[::step]
    
    # 時間轉換：int64 [ms] -> datetime64[ms] -> +8小時 (UTC+8 台灣時間)
    tick_x_axis = timestamps_slice.astype('datetime64[ms]') + np.timedelta64(8, 'h')
    
    # 5. K 線數據準備 (Candlesticks) - 保持不變 (K線本身就是聚合過的)
    tf_key = timeframe if timeframe in indicator_manager.candles else '10s'
    period_ms = TIMEFRAMES.get(tf_key, 10000)
    
    candles = indicator_manager.candles[tf_key]
    current_candle = indicator_manager.current_candles[tf_key]
    
    # 搜尋繪圖起始點
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
    
    if current_candle and current_candle.get('time'):
        for k in plot_candles:
            plot_candles[k].append(current_candle[k])

    raw_candle_time = np.array(plot_candles['time'], dtype=np.int64)
    candle_x = (raw_candle_time + period_ms).astype('datetime64[ms]') + np.timedelta64(8, 'h')

    # 6. 技術指標數據解環 (Smart Slicing)
    view_history = {}
    
    # 修正：必須遍歷所有 Keys，否則像是 RSI, Energy 等動態指標會漏掉
    for key in indicator_manager.history:
        # 使用 Smart Slicing 讀取
        view_history[key] = indicator_manager.get_linear_snapshot(key, window=window_indices)
              
    # [Refactor] 集中累積邏輯 (Data Prep Layer)
    # 將 OBI/OFI 的原始流量 (Flow) 轉換為累積狀態 (Cumulative State)。
    # 注意：因為只有 Slice，我們需要加上 "Slice 之前" 的累積值嗎？
    # 答案：需要。但在這個 V1 優化中，可以先假設用戶只看 Delta，或者只做這段區間的 cumsum (Local Relative).
    # 若要全域正確，需要 manager 提供 window_start 之前的 cumsum 值。
    # 為了效能與簡化，目前先做 Window 內的 CumSum (視覺上會歸零重算，可能會有斷層)。
    # [Correction]: OBI/OFI 在 Manager 已經是累積值？
    # 檢查 adapter: batch_cum_vol = np.cumsum(vol) + last_cum_vol.
    # 是的！ SharedMemory 內的數據已經是 Global Cumulative 了！
    # 所以我們不需要在這裡做 np.cumsum。 View 拿到的就是累積好的值。
    # 等等， lines 118-122 原本有 cumsum?
    # 原本代碼: view_history['obi'] = np.cumsum(view_history['obi'])
    # 這代表 SHM 存的是流量 (Flow)，前端做累積。
    # 如果 SHM 存的是 Flow，那我們切片後做 cumsum，起點會歸零。這在圖表上會呈現 "從左邊開始累積"。
    # 這對於 "區間觀察" 是合理的 (Relative OBI)。
    
    if 'obi' in view_history:
        view_history['obi'] = np.cumsum(view_history['obi'])
        
    if 'ofi' in view_history:
        view_history['ofi'] = np.cumsum(view_history['ofi'])

    # [NEW] VWAP Bands Calculation (Session-Based)
    # 現在改為直接使用 SHM 中已經重置好的累積值 (cum_pv, cum_volume, cum_pv_sq)
    # 不再依賴 Viewport，數值與 Session 絕對綁定。
    if 'cum_pv' in view_history and 'cum_volume' in view_history:
        cum_pv = view_history['cum_pv']
        cum_vol = view_history['cum_volume']
        # Handle cum_pv_sq if present (for StdDev)
        cum_pv_sq = view_history.get('cum_pv_sq', None)

        # Vectorized Division (O(1))
        # Handle division by zero
        valid_vol = cum_vol > 0
        
        vwap = np.full_like(cum_pv, np.nan)
        np.divide(cum_pv, cum_vol, out=vwap, where=valid_vol)
        
        # Calculate Bands
        # StdDev = sqrt( E[X^2] - (E[X])^2 ) = sqrt( (cum_pv_sq / cum_vol) - vwap^2 )
        std_dev = np.zeros_like(cum_pv)
        
        if cum_pv_sq is not None:
            mean_sq = np.zeros_like(cum_pv)
            np.divide(cum_pv_sq, cum_vol, out=mean_sq, where=valid_vol)
            
            variance = mean_sq - (vwap * vwap)
            # Clip negative variance due to floating point consistency
            variance[variance < 0] = 0.0
            std_dev = np.sqrt(variance)
        
        # Dynamic VWAP Bands Calculation (from Config)
        for band in INDICATORS_SETUP:
            if band.get('subtype') == 'vwap_band':
                sd = band['sd']
                # Keys: VWAP_Upper_2.0, VWAP_Lower_2.0
                view_history[f'VWAP_Upper_{sd}'] = vwap + (std_dev * sd)
                view_history[f'VWAP_Lower_{sd}'] = vwap - (std_dev * sd)

        
        # [Fractal VWAP Calculation]
        # Retrieve Cumulative Arrays from View History
        # Note: These keys must match what is in IndicatorManager.history
        
        # [Fractal VWAP Calculation]
        # Retrieve Cumulative Arrays from View History
        # Note: These keys must match what is in IndicatorManager.history

        # Level 1
        vwap_up = _safe_calculate_vwap(view_history, 'cum_up_pv', 'cum_up_vol', vwap)
        vwap_down = _safe_calculate_vwap(view_history, 'cum_dn_pv', 'cum_dn_vol', vwap)
        
        view_history['Fractal_U'] = vwap_up
        view_history['Fractal_L'] = vwap_down
        
        # Calculate Regime Bands (Bull/Bear)
        _calculate_regime_bands(view_history, vwap_up, 'cum_up_pv_sq', 'cum_up_vol', 'Bull', VWAP_MULTIPLIERS)
        _calculate_regime_bands(view_history, vwap_down, 'cum_dn_pv_sq', 'cum_dn_vol', 'Bear', VWAP_MULTIPLIERS)


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
        
    # 8. 提取 Volume Profile 數據 (含分箱優化)
    # VP 是全域的，不需要 slice
    vp_prices, vp_volumes, vp_buy, vp_sell = indicator_manager.vp_engine.get_distribution(bin_size=VP_BIN_SIZE)
    poc, vah, val = indicator_manager.vp_engine.calculate()

    return {
        'tick_x': tick_x_axis,
        'candle_x': candle_x,
        'candles': plot_candles,
        'start_idx': 0, # Since we sliced, start is relative 0
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

def get_session_static_data(indicator_manager):
    """
    [New] 取得盤中固定不變的靜態數據 (Session Open, Prev Close)。
    修正 Lookback 改變導致 Open 價格浮動的 Bug。
    """
    try:
        rb = indicator_manager.ring_buffer
        
        # 1. Prev Close (From Header)
        # PrevClose is stored in header offset 16
        prev_close = rb.prev_close
        
        # 2. Session Open (True First Tick)
        # 邏輯：永遠取 RingBuffer 中最早的一筆數據當作開盤價
        # Case A: Buffer Wrapped (Full) -> Head 指向的是「被覆寫的最老數據」的下一個，即「當前最老數據」
        # Case B: Buffer Not Full -> Index 0 就是第一筆
        
        # Note: indicator_manager.get_linear_snapshot 是複製一份 View
        # 但我們只需要讀一個數字，直接用 RingBuffer 原生屬性讀取即可 (Zero-copy)
        
        open_price = 0.0
        # Fix: SharedRingBuffer does not have .count, use manager.count or derive from head/is_full
        current_count = rb.capacity if rb.is_full else rb.head
        
        if current_count > 0:
            if rb.is_full:
                # 若已滿，head 指向最舊的資料
                open_price = rb.close[rb.head]
            else:
                # 若未滿，0 是起點
                open_price = rb.close[0]
                
        return {
            'prev_close': prev_close,
            'open': open_price
        }
            
    except Exception as e:
        print(f"Error fetching static data: {e}")
        return {'prev_close': 0, 'open': 0}
