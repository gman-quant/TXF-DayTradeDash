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
    # sum_val = P[now] + P[now-1] + ... + P[now-period+1]
    # PrefixSum[now] = Sum(0...now)
    # PrefixSum[prev] = Sum(0...now-period)
    # RangeSum = PrefixSum[now] - PrefixSum[prev]
    
    # 注意：這裡假設 cum_close 是持續累加且正確維護的
    # 如果 head < period (剛啟動資料不足)，理論上 ring buffer 會 wrap around 讀到舊資料 (或是0)
    # 對於嚴謹的實作，我們可以用 cum_volume 輔助檢查，但這裡求快直接算
    
    # 🩹 優化修正: 檢查 prev_idx 是否指到尚未寫入的區域 (Init Zero)
    # 假設 cum_close[prev_idx] 為 0，代表我們回溯到了尚未有資料的緩衝區
    # 此時計算出來的 SMA 會是 (Sum / N) 但 Sum 其實只有 partial sum，數值會錯誤(偏小)。
    # 故回傳 NaN 讓指標暫時無效，直到資料足夠。
    if cum_close[prev_idx] == 0.0:
        return np.nan

    sum_val = cum_close[curr_idx] - cum_close[prev_idx]
    
    # 修正極端情況：如果 cross boundary 導致數值跳變 (通常在 RingBuffer 不會，因為是持續累加)
    # 可是 TxfRingBuffer 的 cum_close 是這一輪的累積，還是永續累積？
    # 查看 ring_buffer.py: self.cum_close[idx] = self.cum_close[prev_idx] + price
    # 它是一個持續累加值。
    # 潛在問題：如果累加太久 float64 會失去精度，但在日內交易(Day Trading)幾萬筆內通常沒問題。
    # 另一個問題：RingBuffer 是環狀的，當 head wrap around 回到 0 時，
    # 舊的 cum_close 會被覆寫。
    
    # ⚠️ 修正邏輯：
    # RingBuffer 的 cum_close 在 wrap around 時會怎樣？
    # 它是 "Stateful Update": next = prev + curr
    # 所以即使繞回 index 0, 它的值還是接續 index MAX 的值繼續加上去。
    # 所以直接相減是安全的，除非... overflow (但 float64 很大)。
    
    # 唯一例外：剛啟動時 (head < period)，prev_idx 會指到 array 尾端
    # 而 array 尾端可能是 0 (還沒寫到)。
    # 這時減出來會是 sum_val = cum_close[curr] - 0 = cum_close[curr] (從開頭到現在的總合)
    # 這其實就是 SMA (只是分母應該是 head 而不是 period)。
    # 為了保持簡單與一致性，我們接受這個短暫的暖機誤差，或者由上層控制 head > period 才呼叫。
    
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
    使用二分搜尋法在 RingBuffer 中尋找時間邊界 (Find first index where ts < target_time)。
    
    Mapping Strategy:
    Logical Index 0 = head - 1 (Latest)
    Logical Index k = head - 1 - k (History)
    
    Timestamps in Logical Index sequence are DESCENDING: [T_now, T_now-1, ...]
    But Binary Search usually works on ASCENDING arrays.
    
    So we search on Logical Index k [0, capacity].
    Wait, Timestamps array is physically circular but logically Sorted (Monotonic).
    
    Logical View: 
    Idx 0:   10:00:05 (Latest)
    Idx 1:   10:00:04
    ...
    Idx N:   09:00:00
    
    We want smallest `k` such that timestamp[logical k] < target_time.
    Since array is DESCENDING, first element < target matches "binary search right side".
    
    Let's use classic binary search on Logical Index k.
    Low = 0, High = capacity - 1 (or valid count)
    
    If ts[mid] >= target_time:
        # We need to go deeper into history (larger k) because array is descending.
        # But wait, if TS >= Target, it means this point is STILL INSIDE the window.
        # We want to find OUTSIDE the window.
        # So we want larger k.
        Low = mid + 1
    else:
        # TS < Target. This point is outside.
        # Could be the boundary, or something even earlier (smaller k is closer to boundary).
        # We want the *first* one that is outside.
        High = mid - 1
        Ans = mid
        
    """
    low = 0
    # Determine search depth: limited by actual valid data count if we had it, 
    # but lacking that, we assume full capacity or stop at 0.
    # Better to just search full capacity. Sentinel 0s will be < target_time (since target ~ current time).
    high = capacity - 1 
    
    ans = -1
    
    while low <= high:
        mid = (low + high) // 2
        
        # Convert Logical mid to Physical idx
        idx = head - 1 - mid
        if idx < 0: idx += capacity
        
        ts = timestamps[idx]
        
        # Check validity (0 is considered extremely old, so < target_time)
        if ts == 0:
            # 0 < target. It is "outside". Try smaller k to find the first outside.
            ans = mid
            high = mid - 1
            continue
            
        if ts < target_time:
            # Found a point outside window.
            # Try to see if there is a smaller k that is also outside (closer to boundary)
            ans = mid
            high = mid - 1
        else:
            # ts >= target_time. Still inside window.
            # Need to look further back (larger k).
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