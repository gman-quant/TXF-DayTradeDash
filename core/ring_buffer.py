# core/ring_buffer.py

import numpy as np
from typing import Tuple
from data_schemas.txf_data_pb2 import Tick

class TxfRingBuffer:
    """
    TXF 專用的 NumPy 環狀緩衝區。
    採用 'Structure of Arrays' (SoA) 佈局，將價格、量、時間分開存儲以優化 Numba 計算。
    """
    def __init__(self, capacity: int = 200000):
        self.capacity = capacity
        self.head = 0          # 指向「下一個寫入位置」的指標
        self.is_full = False   # 標記是否已經寫滿過一輪
        
        # ==========================================
        # 💾 基礎數據欄位
        # ==========================================
        self.timestamp = np.zeros(capacity, dtype=np.int64)
        self.close = np.zeros(capacity, dtype=np.float64)
        self.volume = np.zeros(capacity, dtype=np.int32)
        self.total_volume = np.zeros(capacity, dtype=np.int32)
        self.tick_type = np.zeros(capacity, dtype=np.int32)
        self.underlying_price = np.zeros(capacity, dtype=np.float64)

        # ==========================================
        # ⚡️ 狀態與累積欄位 (Stateful & Cumulative)
        # 這些欄位是為了 O(1) 指標計算而存在的
        # ==========================================
        
        # 1. 當盤高低點 (Stateful)
        self.session_high = np.zeros(capacity, dtype=np.float64)
        self.session_low  = np.zeros(capacity, dtype=np.float64)
        
        # 2. 累積和 (Cumulative Sums) - 用於 O(1) VWAP 和 SMA
        self.cum_volume = np.zeros(capacity, dtype=np.int64)   # 累積成交量 (分母)
        self.cum_pv     = np.zeros(capacity, dtype=np.float64) # 累積 PV (分子)
        self.cum_close  = np.zeros(capacity, dtype=np.float64) # 累積收盤價 (用於 SMA)

    def write_tick(self, tick: Tick):
        """
        將單筆 Tick 寫入緩衝區，並同步更新所有狀態欄位。
        """
        idx = self.head
        prev_idx = idx - 1
        if prev_idx < 0: prev_idx = self.capacity - 1
        
        # 1. 填入基礎數據
        self.timestamp[idx]    = tick.timestamp_ms
        self.volume[idx]       = tick.volume
        self.total_volume[idx] = tick.total_volume
        self.tick_type[idx]    = tick.tick_type
        
        # 價格正規化 (/10000.0)
        self.underlying_price[idx] = tick.underlying_price / 10000.0
        price = tick.close / 10000.0
        self.close[idx] = price

        # 2. 計算當盤最高/最低 (O(1))
        is_first_tick = (self.head == 0 and not self.is_full)
        
        if is_first_tick:
            # 第一筆：初始化
            self.session_high[idx] = price
            self.session_low[idx]  = price
            
            # 初始化累積值
            self.cum_volume[idx] = tick.volume
            self.cum_pv[idx]     = price * tick.volume
            self.cum_close[idx]  = price
        else:
            # 後續：遞迴更新
            prev_high = self.session_high[prev_idx]
            prev_low  = self.session_low[prev_idx]
            
            # 防呆：如果上一筆是 0，也把自己當起點
            if prev_high == 0: 
                self.session_high[idx] = price
                self.session_low[idx] = price
            else:
                self.session_high[idx] = max(prev_high, price)
                self.session_low[idx]  = min(prev_low, price)
            
            # 3. 計算累積和 (當前累積 = 上一筆累積 + 當前值)
            # 即使 total_volume 存在，我們還是手動累加 cum_volume 以確保分子分母同步
            self.cum_volume[idx] = self.cum_volume[prev_idx] + tick.volume
            self.cum_pv[idx]     = self.cum_pv[prev_idx]     + (price * tick.volume)
            self.cum_close[idx]  = self.cum_close[prev_idx]  + price

        # 4. 移動指標 (Wrap Around)
        self.head += 1
        if self.head >= self.capacity:
            self.head = 0
            self.is_full = True

    def get_snapshot(self):
        """
        回傳 Numba 計算所需的 Array 參照
        注意：這裡的回傳順序必須與 IndicatorManager.on_tick 的解包順序完全一致！
        """
        return (
            self.close,             # 0
            self.volume,            # 1
            self.tick_type,         # 2
            self.timestamp,         # 3
            self.underlying_price,  # 4
            self.cum_volume,        # 5 (新)
            self.cum_pv,            # 6 (新)
            self.cum_close,         # 7 (新)
            self.session_high,      # 8
            self.session_low,       # 9
            self.total_volume,      # 10
            self.head               # 11
        )

    def clear(self):
        """重置緩衝區 (例如開盤前)"""
        self.head = 0
        self.is_full = False
        self.timestamp.fill(0)
        self.close.fill(0)
        self.volume.fill(0)
        self.underlying_price.fill(0)
        self.session_high.fill(0)
        self.session_low.fill(0)
        self.cum_volume.fill(0)
        self.cum_pv.fill(0)
        self.cum_close.fill(0)