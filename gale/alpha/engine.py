# core/numba_engine.py

import numpy as np
from numba import jit

# -----------------------------------------------------------------------------
# ⚡️ Numba JIT Compiled Functions
# 這些函數會被編譯成機器碼，執行速度接近 C/C++
# nopython=True: 強制不使用 Python 物件，確保極速
# nogil=True:    釋放 GIL，允許並行執行 (如果有多核心需求)
# -----------------------------------------------------------------------------

@jit(nopython=True, cache=True, fastmath=True)
def get_current_value(data_array: np.ndarray, 
                      head: int, 
                      period: int, # 這個參數沒用到，只是為了符合介面規範
                      capacity: int) -> float:
    """
    直接讀取當前指標值 (用於已在 RingBuffer 預計算好的數據)
    """
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    
    val = data_array[curr_idx]
    
    if val == 0.0:
        return np.nan
        
    return val

@jit(nopython=True, cache=True, fastmath=True)
def calc_vwap(cum_pv: np.ndarray, 
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

@jit(nopython=True, cache=True, fastmath=True)
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

@jit(nopython=True, cache=True, fastmath=True)
def calc_sma(cum_close: np.ndarray, 
             head: int, 
             period: int, 
             capacity: int) -> float:
    """
    計算簡單移動平均 (SMA) - O(1) Optimized
    利用累積收盤價 (Prefix Sum) 計算，取代迴圈。
    """
    # 當前索引
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    
    # N 筆前的索引
    prev_idx = head - 1 - period
    if prev_idx < 0: prev_idx += capacity
    
    # 公式: (累積到現在 - 累積到N筆前) / N
    
    # 🩹 優化修正: 檢查 prev_idx 是否指到尚未寫入的區域 (Init Zero)
    # 假設 cum_close[prev_idx] 為 0，代表我們回溯到了尚未有資料的緩衝區
    if cum_close[prev_idx] == 0.0:
        return np.nan

    sum_val = cum_close[curr_idx] - cum_close[prev_idx]
    
    # ⚠️ 邏輯備註：
    # RingBuffer 的 cum_close 在 wrap around 時會接續累加 (Stateful Update)，
    # 所以直接相減是安全的，除非 float64 overflow (機率極低)。
    
    if period == 0: return np.nan
    
    return sum_val / period





@jit(nopython=True, cache=True, fastmath=True)
def calc_vwap_time(cum_pv: np.ndarray, 
                   cum_vol: np.ndarray, 
                   timestamps: np.ndarray,
                   head: int, 
                   time_window_ms: int,
                   capacity: int) -> float:
    """
    計算「時間基礎」的 VWAP (O(log N) Optimized)
    Use: Sum(PV) / Sum(Volume)
    """
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    
    current_time = timestamps[curr_idx]
    if current_time == 0: return np.nan
    
    target_time = current_time - time_window_ms
    
    # 1. Binary Search 找邊界
    found_k = binary_search_boundary(timestamps, head, target_time, capacity)
    
    if found_k == -1: found_k = capacity
    if found_k == 0: return np.nan
    
    # head - 1 - found_k : the index just outside the window
    boundary_idx = head - 1 - found_k
    if boundary_idx < 0: boundary_idx += capacity
    
    # 2. 用 Prefix Sum O(1) 計算區間總合
    # Window PV = CumPV[curr] - CumPV[boundary]
    # Window Vol = CumVol[curr] - CumVol[boundary]
    
    sum_pv = cum_pv[curr_idx] - cum_pv[boundary_idx]
    sum_v  = cum_vol[curr_idx] - cum_vol[boundary_idx]
    
    if sum_v == 0:
        return np.nan
        
    return sum_pv / sum_v

@jit(nopython=True, cache=True, fastmath=True)
def binary_search_boundary(timestamps: np.ndarray, 
                           head: int, 
                           target_time: float, 
                           capacity: int) -> int:
    """
    [演算法核心] 在 RingBuffer 中使用二分搜尋法尋找時間邊界。
    
    目標：找到最小的 k，使得 timestamps[head - 1 - k] < target_time。
    (即找到第一個「超出時間視窗」的舊資料點)
    
    挑戰：
    1. RingBuffer 是環狀的，物理索引不連續。
    2. 時間戳記是「邏輯遞減」的 (最新在 head-1)，但二分搜通常用於遞增陣列。
    
    解決方案：
    - 對「邏輯索引 k」進行二分搜 (範圍 0 到 capacity)。
    - 轉換邏輯索引 k -> 物理索引 idx，讀取時間戳。
    - 因為資料是遞減的 (越來越舊)，若 T[k] >= Target，代表還在視窗內，需要往更舊找 (K 變大)。
    
    Returns:
        int: found_k (視窗內的資料筆數)
    """
    low = 0
    high = capacity - 1 
    
    ans = -1
    
    while low <= high:
        mid = (low + high) // 2
        
        # 轉換: 邏輯索引 mid -> 物理索引 idx
        idx = head - 1 - mid
        if idx < 0: idx += capacity
        
        ts = timestamps[idx]
        
        # 檢查有效性 (0 代表未初始化的空位，視為「非常舊」)
        if ts == 0:
            # 視為小於 Target (Outside)，嘗試往更近找 (Smaller K)
            ans = mid
            high = mid - 1
            continue
            
        if ts < target_time:
            # 找到視窗外的點了 (Outside)
            # 嘗試縮小範圍，看有沒有更小的 k 也符合 (希望能精確切在邊界)
            ans = mid
            high = mid - 1
        else:
            # 還在視窗內 (Inside, ts >= target)
            # 需要往更深處找 (Larger K)
            low = mid + 1
            
    return ans

@jit(nopython=True, cache=True, fastmath=True)
def calc_sma_time(cum_close: np.ndarray, 
                  timestamps: np.ndarray, 
                  head: int, 
                  time_window_ms: int, 
                  capacity: int) -> float:
    """
    計算「時間基礎」的 SMA (O(log N) Optimized)
    """
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    
    current_time = timestamps[curr_idx]
    if current_time == 0: return np.nan
    
    target_time = current_time - time_window_ms
    
    # 1. Binary Search 找邊界 (Logical Index)
    # found_k 是第一個 "小於 target_time" 的位置 (即剛好出局的那筆)
    found_k = binary_search_boundary(timestamps, head, target_time, capacity)
    
    if found_k == -1:
        # 沒找到小於 target 的 -> 代表全部都在 window 內 (或是 buffer 全空?)
        # 這種情況我們應該拿整個 buffer 計算嗎？
        # 如果 buffer 滿了，最後一筆還是 >= target，那說明 window 超大，超過 buffer 容量。
        # 我們就用整個 buffer
        found_k = capacity 
    
    if found_k == 0:
        # 第一筆就出局了 (Window 太小，涵蓋不到任何過去資料)
        return np.nan

    # 2. 計算區間
    # Window 區間是 Logical [0 ... found_k - 1]
    # 也就是 Physical [curr_idx ... boundary_prev]
    # boundary_idx (Physical) = head - 1 - found_k
    
    boundary_idx = head - 1 - found_k
    if boundary_idx < 0: boundary_idx += capacity
    
    # Prefix Sum Diff: Cum[End] - Cum[Start-1]
    # End = curr_idx
    # Start = ... (從 binary search 回推)
    # 其實：Cum[curr] - Cum[boundary]
    # boundary 就是那個 "剛好出局" 的點。
    # 減去它累積的值，剩下的就是 [curr ... boundary+1] 的總和
    
    total_val = cum_close[curr_idx] - cum_close[boundary_idx]
    
    # 數量就是 found_k (0到found_k-1 共K筆)
    count = found_k
    
    return total_val / count


@jit(nopython=True, cache=True, fastmath=True)
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

@jit(nopython=True, cache=True, fastmath=True)
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

@jit(nopython=True, cache=True, fastmath=True)
def calc_rolling_max_time(data_array: np.ndarray, 
                          time_array: np.ndarray, 
                          head: int, 
                          period_ms: int, 
                          capacity: int) -> float:
    """
    計算過去 period_ms 毫秒內的最高值 (O(N) Scans, but O(log N) for boundary)
    """
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
        
    current_time = time_array[curr_idx]
    if current_time == 0: return np.nan
    
    target_time = current_time - period_ms
    
    # 1. Binary Search 找邊界 (Count K)
    # found_k is the number of elements INSIDE the window
    found_k = binary_search_boundary(time_array, head, target_time, capacity)
    
    if found_k == -1: found_k = capacity
    if found_k == 0: return np.nan
    
    search_count = found_k
    max_val = -1e9 # Init small
    
    # 2. 只需迴圈 value，不用再 check timestamp
    # 這裡還是 O(K) 線性掃描，若要 O(1) 需用 Monotonic Queue，但在 Stateless 設計下做不到
    for i in range(search_count):
        idx = head - 1 - i
        if idx < 0: idx += capacity
        
        val = data_array[idx]
        if val == 0.0: continue # Skip empty
        
        if val > max_val:
            max_val = val
            
    if max_val == -1e9:
        return np.nan
        
    return max_val

@jit(nopython=True, cache=True, fastmath=True)
def calc_rolling_min_time(data_array: np.ndarray, 
                          time_array: np.ndarray, 
                          head: int, 
                          period_ms: int, 
                          capacity: int) -> float:
    """
    計算過去 period_ms 毫秒內的最低值 (O(N) Scans, but O(log N) for boundary)
    """
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
        
    current_time = time_array[curr_idx]
    if current_time == 0: return np.nan
    
    target_time = current_time - period_ms
    
    found_k = binary_search_boundary(time_array, head, target_time, capacity)
    
    if found_k == -1: found_k = capacity
    if found_k == 0: return np.nan
    
    search_count = found_k
    min_val = 1e9 # Init large
    
    for i in range(search_count):
        idx = head - 1 - i
        if idx < 0: idx += capacity
        
        val = data_array[idx]
        if val == 0.0: continue
        
        if val < min_val:
            min_val = val
            
    if min_val == 1e9:
        return np.nan
        
    return min_val

@jit(nopython=True, cache=True, fastmath=True)
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

@jit(nopython=True, cache=True, fastmath=True)
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

@jit(nopython=True, cache=True, fastmath=True)
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

# =============================================================================
# 🧩 Dashboard Helper Functions (On-the-fly Calculation)
# 這些函數專為 Dashboard "解環後" 的線性陣列設計，不涉及 RingBuffer 回溯。
# =============================================================================

@jit(nopython=True, cache=True, fastmath=True)
def calc_vwap_bands_linear(close_arr: np.ndarray, 
                           vol_arr: np.ndarray, 
                           multiplier: float) -> tuple:
    """
    計算 VWAP 及上下通道 (VWAP Bands) - Vectorized Optimized
    
    Returns:
        (vwap_arr, upper_arr, lower_arr)
    """
    # 1. 基礎向量運算
    # 避免 Numba warning，轉換型別 (雖然通常不用)
    pv = close_arr * vol_arr
    pv_sq = close_arr * close_arr * vol_arr
    
    # 2. 累積總和 (Prefix Sum) - O(N)
    cum_vol = np.cumsum(vol_arr)
    cum_pv = np.cumsum(pv)
    cum_pv_sq = np.cumsum(pv_sq)
    
    # 3. 處理除以零的情況 (初期無成交量)
    # 為了計算方便，將 0 的 CumVol 暫時換成 1 (避免 DivZero error)
    valid_mask = (cum_vol > 0)
    safe_cum_vol = cum_vol.copy()
    
    # 計算 VWAP (E[X])
    vwap_arr = cum_pv / safe_cum_vol
    
    # 計算 Variance (E[X^2] - (E[X])^2)
    mean_sq_arr = cum_pv_sq / safe_cum_vol
    variance_arr = mean_sq_arr - (vwap_arr * vwap_arr)
    
    # 數值穩定性修正 (Variance >= 0)
    variance_arr = np.maximum(variance_arr, 0.0)
    
    sd_arr = np.sqrt(variance_arr)
    
    # 計算 Bands
    upper_arr = vwap_arr + (multiplier * sd_arr)
    lower_arr = vwap_arr - (multiplier * sd_arr)
    
    # 4. 修正無效區間 (初期 Volume=0)
    # Plotly 會自動忽略 NaN，無需特別回填特定值
    
    return vwap_arr, upper_arr, lower_arr