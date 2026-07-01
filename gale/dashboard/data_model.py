
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
from config.indicator_config import INDICATORS_SETUP, VWAP_MULTIPLIERS, BAND_WARMUP_VOL

VP_BIN_SIZE = 1 # Volume Profile 價格分箱大小 (點)

# 盤間斷層門檻:相鄰 tick 時間差 > 此值 → 視為換盤/資料斷層,在該處切斷色帶與 U/L-Cost 線,
# 避免 Plotly fill='tonexty' 跨越空白時段直接連線/填色(parquet 夜盤+日盤同框時的三角形 artifact)。
# 30 分鐘:安全高於盤中最長靜默、低於最短盤間休息(日→夜 75 分、夜→日 225 分)。
GAP_BREAK_MS = 30 * 60 * 1000

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

def _calculate_regime_bands(view_history, vwap_center, side_mean, pv_sq_key, vol_key, suffix_name, multipliers):
    """[改] 以 session VWAP 為中心 + 半邊σ(σ 繞 VWAP) 的 regime 色塊 = cB 分區。
    原本錨定在 U/L-Cost 且 σ 繞 U/L-Cost；現改為錨定 session VWAP、σ 繞 VWAP，
    使色塊位置直接等於 cB(價在 Bull_Band_2.0 ⟺ cB=+2)。
      半邊變異數 = E[p^2]_side - 2*VWAP*E[p]_side + VWAP^2
    """
    if pv_sq_key in view_history and vol_key in view_history:
        pv_sq = view_history[pv_sq_key]
        vol = view_history[vol_key]

        mean_sq = np.zeros_like(pv_sq)
        valid = vol > 0
        np.divide(pv_sq, vol, out=mean_sq, where=valid)

        # 繞 session VWAP 的半邊變異數(side_mean = 該邊的 E[p] = U/L-Cost)
        variance = mean_sq - 2.0 * vwap_center * side_mean + (vwap_center * vwap_center)
        variance[variance < 0] = 0.0
        std_dev = np.sqrt(variance)

        # Output Bands(錨定在 session VWAP)
        for mult in multipliers:
            if 'Bull' in suffix_name:
                view_history[f'{suffix_name}_Band_{mult}'] = vwap_center + (std_dev * mult)
            else:
                view_history[f'{suffix_name}_Band_{mult}'] = vwap_center - (std_dev * mult)

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
        'volume': candles['volume'][candle_start_idx:],
        'small_lot': candles.get('small_lot', [])[candle_start_idx:],
        'large_lot': candles.get('large_lot', [])[candle_start_idx:],
        'mega_lot': candles.get('mega_lot', [])[candle_start_idx:]
    }
    
    if current_candle and current_candle.get('time'):
        for k in plot_candles:
            if k in current_candle:
                plot_candles[k].append(current_candle[k])

    raw_candle_time = np.array(plot_candles['time'], dtype=np.int64)
    candle_x = (raw_candle_time + period_ms).astype('datetime64[ms]') + np.timedelta64(8, 'h')

    # 6. 技術指標數據解環 (Smart Slicing)
    view_history = {}
    
    # 修正：必須遍歷所有 Keys，否則像是 RSI, Energy 等動態指標會漏掉
    for key in indicator_manager.history:
        # 使用 Smart Slicing 讀取
        view_history[key] = indicator_manager.get_linear_snapshot(key, window=window_indices)
        
    # [NEW] Time-Based Rolling Window Lots (Tick-aligned continuous delta)
    # 取代原本的 K-Bar 階梯狀，提供每個刻度都往回算 period_ms 的精確累計值。
    tf_keys = {'Small_Lot_TF': 'cum_small_net', 'Large_Lot_TF': 'cum_large_net', 'Mega_Lot_TF': 'cum_mega_net'}
    has_tf_lots = any([ind['id'] in tf_keys for ind in INDICATORS_SETUP])
    
    if has_tf_lots and raw_len > 0:
        raw_timestamps = view_history['timestamp']
        # We need to compute values for the downsampled slice `timestamps_slice` (length = TARGET_POINTS)
        # 1. 為了確保能回溯，找出每个 slice 時間點往前回推 period_ms 的 timestamp
        # period_ms 來自當前選擇的 timeframe
        target_timestamps = timestamps_slice - period_ms
        
        # 2. 透過 searchsorted 找到視窗起始索引 (O(N log M))
        # left 插值代表找到第一個 >= target_time 的元素 (也就是剛進 window 的那筆)
        window_start_indices = np.searchsorted(raw_timestamps, target_timestamps, side='left')
        
        # 建立 downsampled indices
        curr_indices = np.arange(0, raw_len, step)[:len(timestamps_slice)]
        
        for tgt_key, cum_key in tf_keys.items():
            if cum_key in view_history:
                cum_arr = view_history[cum_key]
                # Delta = Cum[Current] - Cum[Start]
                # Notice: Start_idx points to the first element INSIDE the window.
                # So we subtract the prefix sum at Start_idx - 1 (or Start_idx if it's 0)
                
                # 計算與原歷史紀錄長度等長的 Delta 陣列 (未 Downsample，供後續模組對齊)
                target_full = raw_timestamps - period_ms
                start_idx_full = np.searchsorted(raw_timestamps, target_full, side='left')
                sub_indices_full = np.maximum(0, start_idx_full - 1)
                
                view_history[tgt_key] = cum_arr - cum_arr[sub_indices_full]
              
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
    
    # [Session-Aware Cumsum]
    # 當 Viewport 跨越盤別 (例如: 昨晚夜盤 -> 今早日盤) 時，COBI/COFI 應該重新歸零。
    # 偵測方式：兩筆 Tick 之間隔超過 1 小時 (3600000 ms) 視為新盤。
    if 'obi' in view_history or 'ofi' in view_history:
        ts = view_history.get('timestamp', np.array([]))
        if len(ts) > 0:
            RESET_THRESHOLD_MS = 3600000
            reset_mask_full = np.concatenate(([False], np.diff(ts) > RESET_THRESHOLD_MS))
            reset_indices = np.where(reset_mask_full)[0]
            
            def grouped_cumsum(arr):
                c_arr = np.cumsum(arr)
                if len(reset_indices) == 0:
                    return c_arr
                baselines = np.zeros_like(c_arr)
                for idx in reset_indices:
                    baselines[idx:] = c_arr[idx - 1]
                return c_arr - baselines
            
            if 'obi' in view_history:
                view_history['obi'] = grouped_cumsum(view_history['obi'])
                
            if 'ofi' in view_history:
                view_history['ofi'] = grouped_cumsum(view_history['ofi'])

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

        
        # 各邊平均價 E[p](= U-Cost / L-Cost):既當半邊σ 一階項,也輸出成線(只線、不影響色塊)
        vwap_up = _safe_calculate_vwap(view_history, 'cum_up_pv', 'cum_up_vol', vwap)
        vwap_down = _safe_calculate_vwap(view_history, 'cum_dn_pv', 'cum_dn_vol', vwap)
        view_history['Fractal_U'] = vwap_up
        view_history['Fractal_L'] = vwap_down

        # Calculate Regime Bands (Bull/Bear)
        # [改] regime 色塊改以 session VWAP 為中心 + 半邊σ(繞 VWAP) → 色塊位置 = cB 分區
        # (vwap_up / vwap_down 即各邊的 E[p] = U-Cost / L-Cost，當作半邊變異數的一階項)
        _calculate_regime_bands(view_history, vwap, vwap_up, 'cum_up_pv_sq', 'cum_up_vol', 'Bull', VWAP_MULTIPLIERS)
        _calculate_regime_bands(view_history, vwap, vwap_down, 'cum_dn_pv_sq', 'cum_dn_vol', 'Bear', VWAP_MULTIPLIERS)

        # 開盤暖身 guard:該邊累積量未達門檻前 σ 不穩(色塊爆寬/跳)→ 該邊色塊設 NaN 不畫。
        # 用 session-anchored 累積量判定(已重置),與 viewport 無關 → 只在真開盤觸發。
        if 'cum_up_vol' in view_history:
            warm_up = view_history['cum_up_vol'] < BAND_WARMUP_VOL
            for _m in VWAP_MULTIPLIERS:
                _k = f'Bull_Band_{_m}'
                if _k in view_history:
                    view_history[_k] = np.where(warm_up, np.nan, view_history[_k])
        if 'cum_dn_vol' in view_history:
            warm_dn = view_history['cum_dn_vol'] < BAND_WARMUP_VOL
            for _m in VWAP_MULTIPLIERS:
                _k = f'Bear_Band_{_m}'
                if _k in view_history:
                    view_history[_k] = np.where(warm_dn, np.nan, view_history[_k])

        # (跨盤填塞不在這裡以 NaN 切單一 trace——那對 full 模式的中央填塞無效。改由繪製端「每盤拆成
        #  獨立 fill trace」根治;所需的盤切換斷點索引見下方 session_breaks。)


    # 6.5 盤切換斷點(降頻後的 rendered 索引):相鄰時間差 > GAP_BREAK_MS = 換盤/資料斷層。
    # 供繪製端把每盤色帶/線拆成各自獨立 trace(fill 不跨盤→中央不填塞)+ 畫換盤分隔線。
    # 用 timestamps_slice(=已 [::step] 降頻的 x),與 rendered 序列 history[key][0::step] 同長對齊。
    session_breaks = []
    if len(timestamps_slice) > 1:
        session_breaks = (np.where(np.diff(np.asarray(timestamps_slice, dtype=np.int64)) > GAP_BREAK_MS)[0] + 1).tolist()

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
        'session_breaks': session_breaks,   # 降頻後 rendered 索引:換盤/斷層處(拆 trace + 分隔線用)
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
        # Prior Close is stored in header offset 16
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
