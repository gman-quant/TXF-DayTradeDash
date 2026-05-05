
import numpy as np
from numba import njit
import threading

# =============================================================================
# ⚡️ Numba Logic (High Performance)
# =============================================================================

@njit(nogil=True, cache=True)
def _update_histogram(histogram, price_int, volume):
    """
    [JIT] 高速更新直方圖
    
    Args:
        histogram: NumPy Array (int64)
        price_int: 價格整數 (直接對應 Array Index)
        volume: 成交量
    """
    if 0 <= price_int < len(histogram):
        histogram[price_int] += volume

@njit(nogil=True, cache=True)
def _calc_distribution(histogram, total_volume, value_area_pct=0.70):
    """
    [JIT] 計算 POC, VAH, VAL
    
    演算法：
    1. POC (Point of Control): 直方圖中成交量最大的點。
    2. Value Area (VA): 從 POC 開始向外擴散，直到累積成交量達到總量的 70%。
       - 使用雙指針 (Dual Pointer) 貪婪算法。
       - 每次比較左邊 (價格-1) 與右邊 (價格+1) 的成交量，優先選擇成交量較大的一邊納入 VA。
         (這符合拍賣市場理論：市場傾向於在流動性較好的區域交易)
    
    Returns: (poc_price_int, vah_price_int, val_price_int)
    """
    # 1. 尋找 POC (眾數)
    poc_idx = np.argmax(histogram)
    
    # 2. 計算價值區 (Value Area)
    target_vol = total_volume * value_area_pct
    current_vol = histogram[poc_idx]
    
    left = poc_idx
    right = poc_idx
    max_idx = len(histogram) - 1
    
    while current_vol < target_vol:
        # 邊界檢查
        can_go_left = (left > 0)
        can_go_right = (right < max_idx)
        
        if not can_go_left and not can_go_right:
            break
            
        # 貪婪擴散：比較左右兩邊的成交量
        vol_left = histogram[left - 1] if can_go_left else -1
        vol_right = histogram[right + 1] if can_go_right else -1
        
        # 選擇成交量較大的一邊擴展
        if vol_left >= vol_right:
            left -= 1
            current_vol += vol_left
        else:
            right += 1
            current_vol += vol_right
            
    return poc_idx, right, left # right=Highest Price (VAH), left=Lowest Price (VAL)

# =============================================================================
# 📦 Python Wrapper (State Management)
# =============================================================================

class VolumeProfileEngine:
    """
    Volume Profile 運算引擎
    
    職責：
    1. 狀態管理: 維護 Buy/Sell/Total 三種直方圖。
    2. 並發安全: 使用 `threading.Lock` 確保 Dashboard (讀) 與 Strategy (寫) 安全互斥。
    3. 數據聚合: 提供 `get_distribution` 進行稀疏矩陣壓縮與 Binning (合併價格檔位)。
    """
    def __init__(self, size=60000):
        # Index = Price (Points). e.g. Index 15000 means price 15000.
        # 台指期價格約 20000 點，40000 點足夠涵蓋且不浪費太多記憶體 (int64 * 40000 * 3 ~ 1MB)
        self.histogram = np.zeros(size, dtype=np.int64)
        self.buy_histogram = np.zeros(size, dtype=np.int64)
        self.sell_histogram = np.zeros(size, dtype=np.int64)
        
        self.total_volume = 0
        self.lock = threading.Lock()
        
        # 快取最後一次計算的關鍵價位 (避免 Dashboard 重複計算)
        self.cached_levels = (0, 0, 0) # POC, VAH, VAL
    
    def update(self, price, volume, tick_type=0):
        """
        [Thread-Safe] 更新成交量分佈
        
        Args:
            price: 成交價
            volume: 成交量
            tick_type: 1=Buy (主動買), 2=Sell (主動賣), 0=Unknown
        """
        p_int = int(price)
        with self.lock:
            # 更新總量
            _update_histogram(self.histogram, p_int, volume)
            
            # 更新買賣分量 (Delta Profile)
            if tick_type == 1:
                _update_histogram(self.buy_histogram, p_int, volume)
            elif tick_type == 2:
                _update_histogram(self.sell_histogram, p_int, volume)
            
            self.total_volume += volume
            
    def calculate(self):
        """
        [Thread-Safe] 計算 POC/VAH/VAL
        通常由 Dashboard 定期呼叫，或在 Strategy 需要 level 時呼叫。
        """
        with self.lock:
            if self.total_volume == 0:
                return (0, 0, 0)
            
            # 呼叫 JIT 函數計算
            poc, vah, val = _calc_distribution(self.histogram, self.total_volume)
            self.cached_levels = (poc, vah, val)
            return self.cached_levels
            
    def get_distribution(self, bin_size=1):
        """
        [Thread-Safe] 獲取繪圖用數據 (View Copy)
        
        特點：
        1. **稀疏壓縮 (Sparse)**: 過濾掉 Volume=0 的價格檔位，大幅減少傳輸數據量。
        2. **分箱 (Binning)**: 支援將相鄰價格合併 (e.g. 每 10 點一格)，平滑 Noise。
            
        Returns:
            (prices, total_vols, buy_vols, sell_vols)
        """
        with self.lock:
            # 1. 稀疏化：只取有成交量的 Index
            non_zeros = np.nonzero(self.histogram)[0]
            if len(non_zeros) == 0:
                return np.array([]), np.array([]), np.array([]), np.array([])
            
            prices = non_zeros
            volumes = self.histogram[non_zeros]
            buy_vols = self.buy_histogram[non_zeros]
            sell_vols = self.sell_histogram[non_zeros]
            
            # --- Binning Logic (分箱聚合) ---
            if bin_size > 1:
                # 1. 計算新的 Bin Index (整除)
                binned_prices = prices // bin_size * bin_size
                
                # 2. 重新聚合 (Sum volumes by bin)
                # np.unique + return_inverse 是處理稀疏數據聚合的高效方法
                unique_bins, inverse_indices = np.unique(binned_prices, return_inverse=True)
                
                binned_volumes = np.zeros_like(unique_bins, dtype=np.int64)
                binned_buy = np.zeros_like(unique_bins, dtype=np.int64)
                binned_sell = np.zeros_like(unique_bins, dtype=np.int64)
                
                # 高速累加：將同一 Bin 的量加總
                np.add.at(binned_volumes, inverse_indices, volumes)
                np.add.at(binned_buy, inverse_indices, buy_vols)
                np.add.at(binned_sell, inverse_indices, sell_vols)
                
                return unique_bins.copy(), binned_volumes.copy(), binned_buy.copy(), binned_sell.copy()
            
            # 若無 Binning，直接回傳 Copy (避免外部修改影響內部)
            return prices.copy(), volumes.copy(), buy_vols.copy(), sell_vols.copy()
