# core/numba_engine.py

import numpy as np
from numba import jit

# -----------------------------------------------------------------------------
# ⚡️ Numba JIT Compiled Functions
# 這些函數會被編譯成機器碼，執行速度接近 C/C++
# nopython=True: 強制不使用 Python 物件，確保極速
# nogil=True:    釋放 GIL，允許並行執行 (如果有多核心需求)
# -----------------------------------------------------------------------------

@jit(nopython=True, cache=True)
def calc_vwap(prices: np.ndarray, 
              volumes: np.ndarray, 
              head: int, 
              period: int, 
              capacity: int) -> float:
    """
    計算過去 N 筆 Tick 的 VWAP (Volume Weighted Average Price)。
    
    演算法：
    1. 從 head 指標往回回溯 period 筆數據。
    2. 處理環狀緩衝區的邊界 (Wrap-around)。
    3. 計算 Sum(P*V) / Sum(V)。
    """
    
    # 如果數據量不足 (剛啟動時)，直接回傳當前價格或 0
    # 這裡簡單處理：假設還沒滿一圈且 head < period，只算現有的
    # 嚴謹的 RingBuffer 會有 is_full 標記，這裡為了效能簡化邏輯
    
    sum_pv = 0.0
    sum_v = 0.0
    
    # 從最新的一筆數據開始 (head - 1)
    # 往回推 period 筆
    for i in range(period):
        # 關鍵：處理環狀索引
        # 當 (head - 1 - i) 變負數時，加上 capacity 繞回陣列尾部
        idx = (head - 1 - i)
        if idx < 0:
            idx += capacity
            
        p = prices[idx]
        v = volumes[idx]
        
        sum_pv += p * v
        sum_v += v
        
    if sum_v == 0:
        return 0.0
        
    return sum_pv / sum_v

@jit(nopython=True, cache=True)
def calc_price_change(prices: np.ndarray, 
                      head: int, 
                      period: int, 
                      capacity: int) -> float:
    """
    計算價格變化 (Momentum)
    Current Price - Price N ticks ago
    """
    # 當前索引
    curr_idx = head - 1
    if curr_idx < 0: curr_idx += capacity
    
    # N 筆前的索引
    prev_idx = head - 1 - period
    if prev_idx < 0: prev_idx += capacity

    prev_price = prices[prev_idx]

    # 🛡️ 防呆機制：如果回溯到的價格是 0 (代表 Buffer 該處尚未有數據)
    # 就直接回傳 0.0 (表示沒有動能)，避免圖表爆掉
    if prev_price == 0.0:
        return 0.0
    
    return prices[curr_idx] - prices[prev_idx]