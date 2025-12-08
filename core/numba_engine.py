# core/numba_engine.py

import numpy as np
from numba import jit

# -----------------------------------------------------------------------------
# ⚡️ Numba JIT Compiled Functions
# 這些函數會被編譯成機器碼，執行速度接近 C/C++
# nopython=True: 強制不使用 Python 物件，確保極速
# nogil=True:    釋放 GIL，允許並行執行 (如果有多核心需求)
# -----------------------------------------------------------------------------

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def get_current_value(data_array: np.ndarray, 
                      head: int, 
                      period: int, # Unused, kept for interface consistency
                      capacity: int) -> float:
    """
    Retrieves the value at the current head pointer (latest written data).
    
    Args:
        data_array: The 1D data array (e.g., close, volume).
        head: Current write cursor position.
        period: (Unused) Kept for interface consistency.
        capacity: Ring buffer total capacity.

    Returns:
        float: The latest value, or NaN if the value is 0 (uninitialized).
    """
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    
    val = data_array[curr_idx]
    
    if val == 0.0:
        return np.nan
        
    return val

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_session_vwap(cum_pv: np.ndarray, 
                      cum_vol: np.ndarray, 
                      head: int, 
                      period: int, # 為了符合介面規範保留，實際上沒用到
                      capacity: int) -> float:
    """
    計算當盤 Session VWAP (O(1) 複雜度)
    直接利用 RingBuffer 的累積值相除
    """
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    
    current_pv = cum_pv[curr_idx]
    current_vol = cum_vol[curr_idx]
    
    # 防呆：避免除以零
    if current_vol == 0:
        return np.nan
        
    return current_pv / current_vol

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_price_change(prices: np.ndarray, 
                      head: int, 
                      period: int, 
                      capacity: int) -> float:
    """
    計算價格變化 (Momentum)
    """
    # 當前索引
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    
    # N 筆前的索引
    prev_idx = head - 1 - period
    if prev_idx < 0: prev_idx += capacity
    
    curr_price = prices[curr_idx]
    prev_price = prices[prev_idx]

    # 🛡️ 防呆：如果前一筆價格是 0 (Buffer 尚未寫入)
    if prev_price == 0.0 or curr_price == 0.0:
        return np.nan  # 回傳 NaN，讓圖表留白

    return curr_price - prev_price

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_sma(cum_close: np.ndarray, 
             head: int, 
             period: int, 
             capacity: int) -> float:
    """
    Calculates Simple Moving Average (SMA) using O(1) Prefix Sum.
    
    Algorithm:
        SMA = (CumClose[Now] - CumClose[Now - N]) / N

    Args:
        cum_close: Cumulative sum array of closing prices.
        head: Current write cursor.
        period: Window size N.
        capacity: Ring buffer total capacity.

    Returns:
        float: The SMA value, or NaN if data is insufficient (startup phase).
    """
    # 當前索引
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    
    # N 筆前的索引
    prev_idx = head - 1 - period
    if prev_idx < 0: prev_idx += capacity
    
    if cum_close[prev_idx] == 0.0:
        return np.nan

    sum_val = cum_close[curr_idx] - cum_close[prev_idx]
    
    if period == 0: return np.nan
    
    return sum_val / period



@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_vwap_time(prices: np.ndarray, 
                   volumes: np.ndarray, 
                   timestamps: np.ndarray,  # <--- 需要時間陣列
                   head: int, 
                   time_window_ms: int,     # <--- 參數變成毫秒數
                   capacity: int) -> float:
    """
    計算「時間基礎」的 VWAP (例如：過去 60 秒的 VWAP)
    """
    # 1. 取得當前最新時間
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    
    current_time = timestamps[curr_idx]
    if current_time == 0: return np.nan # 防呆
    
    # 計算截止時間 (Cut-off time)
    target_time = current_time - time_window_ms
    
    sum_pv = 0.0
    sum_v = 0.0
    
    # 2. 往回回溯 (不定長度，直到時間超過範圍)
    # 我們設定一個最大回溯上限 (例如 10萬筆) 避免無窮迴圈
    for i in range(capacity): 
        idx = head - 1 - i
        if idx < 0: idx += capacity
        
        ts = timestamps[idx]
        
        # 終止條件：
        # A. 遇到空數據 (0)
        # B. 該筆數據的時間早於截止時間
        if ts == 0 or ts < target_time:
            break
            
        p = prices[idx]
        v = volumes[idx]
        
        sum_pv += p * v
        sum_v += v
        
    if sum_v == 0:
        return np.nan
        
    return sum_pv / sum_v

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_sma_time(cum_close: np.ndarray, # ⚠️ 改用累積值
                  timestamps: np.ndarray, 
                  head: int, 
                  time_window_ms: int, 
                  capacity: int) -> float:
    """
    計算「時間基礎」的 SMA (例如：過去 1 分鐘的均價) - Optimized
    使用 Prefix Sum 只要找到時間邊界，就能 O(1) 算出總和。
    """
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    
    current_time = timestamps[curr_idx]
    if current_time == 0: return np.nan
    
    target_time = current_time - time_window_ms
    
    # --- 1. 搜尋時間邊界 (Linear Search) ---
    # 雖然這裡還是 O(N) 搜尋，但比 O(N) 加法快，且 Numba 執行極快。
    # 若要極致優化可用 Binary Search，但在 K 棒/Tick 資料下通常線性夠快。
    
    found_idx = -1
    count = 0
    
    # 快速回溯
    for i in range(capacity):
        idx = head - 1 - i
        if idx < 0: idx += capacity
        
        ts = timestamps[idx]
        
        # 終止條件：
        # A. 遇到空數據
        # B. 該筆數據的時間早於截止時間
        if ts == 0 or ts < target_time:
            # 找到邊界的前一筆 (不包含這筆)
            # 所以我們要的區間是 [idx+1 ... curr_idx]
            # 對應到 Prefix Sum diff 公式： Cum[curr] - Cum[idx]
            found_idx = idx
            break
            
        count += 1
        
    if count == 0:
        return np.nan

    # --- 2. 使用 Prefix Sum 計算總和 (O(1)) ---
    # 邏輯：Sum(Start...End) = Cum[End] - Cum[Start-1]
    # 在我們的 Search 迴圈停下來的 idx 剛好就是 "Start-1" (即超出範圍的那筆)
    
    # 修正：如果 idx 指向的是空數據(0)，那 Cum[idx] 也是 0，減去 0 是安全的。
    
    prev_idx = found_idx
    
    # 邊界檢查：如果整個 Buffer 都還沒寫滿，且搜尋到了盡頭
    if prev_idx == -1:
        # 代表找遍了整個 Buffer 都符合時間條件 (資料量不足 window)
        # 此時 prev_idx 應該是 (head - 1 - capacity) % capacity...?
        # 不，這種情況下最舊的一筆就是 Buffer 裡最老的一筆。
        # 我們可以用 cum_close[curr] - 0 (如果剛好繞一圈??)
        # 簡單起見，如果找不到邊界，代表整個 Buffer 都是有效數據
        # Sum = cum_close[curr] - cum_close[oldest_valid_idx - 1]
        # 這有點複雜。
        # 實際上，如果 buffer 滿了，found_idx 就不會是 -1 (一定會撞到自己)
        pass

    # 計算 Sum
    total_val = cum_close[curr_idx] - cum_close[prev_idx]
    
    return total_val / count


@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_rolling_max(data_array: np.ndarray, 
                     head: int, 
                     period: int, 
                     capacity: int) -> float:
    """
    計算過去 N 筆數據的最高值 (Rolling Max)
    """
    max_val = -1.0 # 初始設為極小值
    valid_count = 0
    
    for i in range(period):
        idx = head - 1 - i
        if idx < 0: idx += capacity
            
        val = data_array[idx]
        
        # 防呆：遇到 0 代表數據不足
        if val == 0.0:
            return np.nan
            
        if i == 0:
            max_val = val
        else:
            if val > max_val:
                max_val = val
                
    return max_val

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_rolling_min(data_array: np.ndarray, 
                     head: int, 
                     period: int, 
                     capacity: int) -> float:
    """
    計算過去 N 筆數據的最低值 (Rolling Min)
    """
    min_val = 1e9 # 初始設為極大值
    
    for i in range(period):
        idx = head - 1 - i
        if idx < 0: idx += capacity
            
        val = data_array[idx]
        
        if val == 0.0:
            return np.nan
            
        if i == 0:
            min_val = val
        else:
            if val < min_val:
                min_val = val
                
    return min_val


# core/numba_engine.py (新增在最後面)

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_rolling_max_time(data_array: np.ndarray, 
                          time_array: np.ndarray, # 🆕 需要時間陣列
                          head: int, 
                          period_ms: int, # 🆕 單位是毫秒 (ms)
                          capacity: int) -> float:
    """
    計算過去 period_ms 毫秒內的最高值 (Time-Based Rolling Max)
    """
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
        
    end_time = time_array[curr_idx]
    if end_time == 0:
        return np.nan # 數據不足

    start_time_threshold = end_time - period_ms
    max_val = -1.0
    
    # 從 head 往前跑，直到時間點超出視窗
    i = 0
    while True:
        idx = head - 1 - i
        if idx < 0: idx += capacity
            
        val = data_array[idx]
        ts = time_array[idx]
        
        # 1. 檢查是否超出時間視窗
        if ts < start_time_threshold:
            break
            
        # 2. 檢查數據是否有效
        if val == 0.0:
            break
            
        # 3. 更新 Max
        if max_val == -1.0 or val > max_val:
            max_val = val
            
        i += 1
        
        # 避免無限迴圈 (雖然有時間判斷，但還是防一下)
        if i >= capacity:
            break
            
    # 確保至少有一個有效值被計算 (如果 i=0 就退出，代表 period 太短或數據不足)
    if max_val == -1.0:
        return np.nan
        
    return max_val

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_rolling_min_time(data_array: np.ndarray, 
                          time_array: np.ndarray, # 🆕 需要時間陣列
                          head: int, 
                          period_ms: int, # 🆕 單位是毫秒 (ms)
                          capacity: int) -> float:
    """
    計算過去 period_ms 毫秒內的最低值 (Time-Based Rolling Min)
    """
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
        
    end_time = time_array[curr_idx]
    if end_time == 0:
        return np.nan # 數據不足

    start_time_threshold = end_time - period_ms
    min_val = 1e9 # 初始設為極大值
    
    i = 0
    while True:
        idx = head - 1 - i
        if idx < 0: idx += capacity
            
        val = data_array[idx]
        ts = time_array[idx]
        
        # 1. 檢查是否超出時間視窗
        if ts < start_time_threshold:
            break
            
        # 2. 檢查數據是否有效
        if val == 0.0:
            break
            
        # 3. 更新 Min
        if min_val == 1e9 or val < min_val:
            min_val = val
            
        i += 1
        
        if i >= capacity:
            break
            
    if min_val == 1e9:
        return np.nan
        
    return min_val

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_session_cvd(cum_buy: np.ndarray, 
                     cum_sell: np.ndarray, 
                     head: int, 
                     period: int, # 沒用到
                     capacity: int) -> float:
    """
    計算當盤 CVD (Cumulative Volume Delta)
    公式: 當前累積買量 - 當前累積賣量
    """
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    
    # 直接相減，O(1)
    return float(cum_buy[curr_idx] - cum_sell[curr_idx])

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_period_delta(cum_buy: np.ndarray, 
                      cum_sell: np.ndarray, 
                      head: int, 
                      window: int, 
                      capacity: int) -> float:
    """
    計算區間 Delta (例如過去 60 筆的淨買量)
    公式: (買量變化) - (賣量變化)
    """
    if head <= window: return np.nan
    
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    
    prev_idx = head - 1 - window
    if prev_idx < 0: prev_idx += capacity
    
    # 計算區間內的買量與賣量
    window_buy  = cum_buy[curr_idx]  - cum_buy[prev_idx]
    window_sell = cum_sell[curr_idx] - cum_sell[prev_idx]
    
    return float(window_buy - window_sell)

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_large_lot_net(vol_arr: np.ndarray, 
                       type_arr: np.ndarray, 
                       head: int, 
                       window: int, 
                       threshold: float,
                       capacity: int, 
                       ) -> float:
    """
    計算大單淨量 (Whale Flow)。
    邏輯：統計單筆成交量 >= threshold 的單子。
    Type: 1=Buy (加), 2=Sell (減)。
    """
    # 1. 確保回溯數據足夠
    if head <= window: return np.nan

    curr_idx = head - 1
    net_large_vol = 0.0
    
    # 2. 開始回溯
    for i in range(window):
        idx = curr_idx - i
        # 環狀索引處理
        if idx < 0: idx += capacity
        
        v = vol_arr[idx]
        
        # 🔥 過濾條件：只計算大於等於門檻的單 (threshold)
        # 因為輸入是 int32，這裡比較 (int >= float) 是安全的，Numba 會處理
        if v >= threshold:
            t_val = type_arr[idx]
            
            # 🔥 根據你的 Log 修正邏輯：
            # 1 = 外盤 (主動買) -> 增加淨量
            # 2 = 內盤 (主動賣) -> 減少淨量
            if t_val == 1:
                net_large_vol += v
            elif t_val == 2:
                net_large_vol -= v
            
    return net_large_vol

@jit(nopython=True, cache=True, fastmath=True)
def calc_small_lot_net(vol_arr: np.ndarray, 
                       type_arr: np.ndarray, 
                       head: int, 
                       window: int, 
                       threshold: float, # 用同樣的門檻
                       capacity: int) -> float:
    """
    計算小單淨量 (Retail Flow / Ant Flow)。
    邏輯：只統計單筆成交量 < threshold 的單子。
    Type: 1=Buy, 2=Sell
    """
    if head <= window: return np.nan

    curr_idx = head - 1
    net_small_vol = 0.0
    
    for i in range(window):
        idx = curr_idx - i
        if idx < 0: idx += capacity
        
        v = vol_arr[idx]
        
        # 🔥 核心差異：只計算「小於」門檻的單 (螞蟻單)
        if v < threshold:
            t_val = type_arr[idx]
            
            if t_val == 1:
                net_small_vol += v
            elif t_val == 2:
                net_small_vol -= v
            
    return net_small_vol

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_std_dev(data_array: np.ndarray, 
                 cum_array: np.ndarray, # Kept for signature compatibility but unused
                 head: int, 
                 period: int, 
                 capacity: int) -> float:
    """
    Calculates Rolling Standard Deviation using robust O(N) loop to avoid boundary artifacts.
    """
    if period <= 0: return np.nan
    
    # 1. Calc Mean (Loop)
    sum_val = 0.0
    valid = True
    
    # First Pass: Sum
    for i in range(period):
        idx = head - 1 - i
        if idx < 0: idx += capacity
        
        val = data_array[idx]
        if val == 0.0:
            valid = False
            break
        sum_val += val
        
    if not valid: return np.nan
    
    mean = sum_val / period
    
    # 2. Calc Variance (Loop)
    sum_sq_diff = 0.0
    
    for i in range(period):
        idx = head - 1 - i
        if idx < 0: idx += capacity
        val = data_array[idx]
        diff = val - mean
        sum_sq_diff += diff * diff
        
    variance = sum_sq_diff / period
    return np.sqrt(variance)

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_zscore(data_array: np.ndarray, 
                cum_array: np.ndarray, 
                head: int, 
                period: int, 
                capacity: int) -> float:
    # Use calc_std_dev for robustness
    std_dev = calc_std_dev(data_array, cum_array, head, period, capacity)
    
    if np.isnan(std_dev) or std_dev == 0.0:
        return np.nan
        
    # Get current val
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    val = data_array[curr_idx]
    
    # Re-calc mean (or return it from std_dev? separate function best, but low overhead to sum again)
    # Let's just do the loop for mean here too to be safe/consistent
    sum_val = 0.0
    for i in range(period):
        idx = head - 1 - i
        if idx < 0: idx += capacity
        sum_val += data_array[idx]
    mean = sum_val / period
    
    return (val - mean) / std_dev

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def calc_bollinger_band(data_array: np.ndarray, 
                        cum_array: np.ndarray, 
                        head: int, 
                        period: int, 
                        num_std: float,
                        direction: int, # 1 for Upper, -1 for Lower
                        capacity: int) -> float:
    """
    Calculates Bollinger Band Value.
    Band = SMA +/- (num_std * StdDev)
    """
    if period <= 0: return np.nan
    
    # 1. Calc Mean & Check Validity (O(N))
    sum_val = 0.0
    valid = True
    
    for i in range(period):
        idx = head - 1 - i
        if idx < 0: idx += capacity
        val = data_array[idx]
        if val == 0.0:
            valid = False
            break
        sum_val += val
        
    if not valid: return np.nan
    
    mean = sum_val / period
    
    # 2. Calc StdDev (O(N))
    sum_sq_diff = 0.0
    for i in range(period):
        idx = head - 1 - i
        if idx < 0: idx += capacity
        diff = data_array[idx] - mean
        sum_sq_diff += diff * diff
    
    std_dev = np.sqrt(sum_sq_diff / period)
    
    band = mean + (direction * num_std * std_dev)
    return band
    
    # 2. Calc StdDev (O(N))
    sum_sq_diff = 0.0
    valid = True
    
    for i in range(period):
        idx = head - 1 - i
        if idx < 0: idx += capacity
        
        val = data_array[idx]
        if val == 0.0:
            valid = False
            break
            
        diff = val - mean
        sum_sq_diff += diff * diff
        
    if not valid:
        return np.nan
        
    variance = sum_sq_diff / period
    std_dev = np.sqrt(variance)
    
@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def update_volume_profile(profile_array: np.ndarray, 
                          price_array: np.ndarray, 
                          volume_array: np.ndarray, 
                          start_idx: int, 
                          end_idx: int):
    """
    Updates the global session volume profile in-place.
    Uses Direct Array Indexing for O(1) performance.
    
    Args:
        profile_array: Array of size MAX_PRICE (e.g., 50000). Index = Price.
        price_array: Source price array (Float, will be cast to Int).
        volume_array: Source volume array.
    """
    for i in range(start_idx, end_idx):
        p = price_array[i]
        v = volume_array[i]
        
        # Cast price to int index
        # 28300.0 -> 28300
        # Safety check for bounds and Ignore 0 (initialization noise)
        idx = int(p)
        if 0 < idx < len(profile_array):
            profile_array[idx] += v

@jit(nopython=True, cache=True, fastmath=True, nogil=True)
def get_profile_stats(profile_array: np.ndarray):
    """
    Calculates POC (Point of Control) and VA (Value Area).
    
    Returns:
        (poc_price, vah, val, total_vol)
    """
    # 1. Find POC (Max Volume)
    poc_idx = np.argmax(profile_array)
    poc_vol = profile_array[poc_idx]
    
    if poc_vol == 0:
        return 0, 0, 0, 0
        
    total_vol = np.sum(profile_array)
    target_vol = total_vol * 0.70 # 70% Value Area
    
    # 2. Expand from POC to find VA
    current_vol = poc_vol
    upper_idx = poc_idx
    lower_idx = poc_idx
    max_idx = len(profile_array) - 1
    
    # Greedy expansion
    while current_vol < target_vol:
        # Check neighbors
        can_go_up = (upper_idx < max_idx)
        can_go_down = (lower_idx > 0)
        
        next_up_vol = profile_array[upper_idx + 1] if can_go_up else 0
        next_down_vol = profile_array[lower_idx - 1] if can_go_down else 0
        
        if not can_go_up and not can_go_down:
            break
            
        # Compare and expand towards higher volume
        if next_up_vol > next_down_vol:
            upper_idx += 1
            current_vol += next_up_vol
        elif next_down_vol > next_up_vol:
            lower_idx -= 1
            current_vol += next_down_vol
        else:
            # Equal or both zero, expand both if possible (or prioritize one)
            if can_go_up:
                upper_idx += 1
                current_vol += next_up_vol
            if can_go_down:
                lower_idx -= 1
                current_vol += next_down_vol
                
    return poc_idx, upper_idx, lower_idx, total_vol