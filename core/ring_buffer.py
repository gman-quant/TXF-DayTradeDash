# core/ring_buffer.py

import numpy as np
from data_schemas.txf_data_pb2 import Tick

class TxfRingBuffer:
    """
    TXF 專用的 NumPy 環狀緩衝區 (Structure of Arrays)。
    
    核心特性：
    1. 預先分配記憶體 (Zero Allocation during runtime)。
    2. O(1) 寫入複雜度。
    3. 內建累積和 (Prefix Sum) 與狀態追蹤 (Stateful Tracking)。
    """
    def __init__(self, capacity: int = 200000):
        self.capacity = capacity
        self.head = 0          # 指向「下一個寫入位置」
        self.is_full = False   # 標記是否已寫滿一輪
        
        # ==========================================
        # 1. 基礎數據欄位 (Raw Data)
        # ==========================================
        self.timestamp        = np.zeros(capacity, dtype=np.int64)
        self.close            = np.zeros(capacity, dtype=np.float64)
        self.volume           = np.zeros(capacity, dtype=np.int32)
        self.total_volume     = np.zeros(capacity, dtype=np.int32)
        self.tick_type        = np.zeros(capacity, dtype=np.int32)
        self.underlying_price = np.zeros(capacity, dtype=np.float64)

        # ==========================================
        # 2. 狀態數據 (Stateful Data)
        # ==========================================
        self.session_high     = np.zeros(capacity, dtype=np.float64)
        self.session_low      = np.zeros(capacity, dtype=np.float64)
        
        # ==========================================
        # 3. 累積數據 (Cumulative Data for O(1) Calc)
        # ⚠️ 注意：成交量累積改用 int64 防止溢位
        # ==========================================
        self.cum_volume       = np.zeros(capacity, dtype=np.int64)   # 累積成交量 (VWAP分母)
        self.cum_pv           = np.zeros(capacity, dtype=np.float64) # 累積 PV (VWAP分子)
        self.cum_close        = np.zeros(capacity, dtype=np.float64) # 累積收盤價 (SMA)
        self.cum_buy_vol      = np.zeros(capacity, dtype=np.int64)   # 累積外盤量
        self.cum_sell_vol     = np.zeros(capacity, dtype=np.int64)   # 累積內盤量

    def write_tick(self, tick: Tick):
        """
        將單筆 Tick 寫入緩衝區，並同步更新所有狀態與累積欄位。
        Complexity: O(1)
        """
        idx = self.head
        
        # 計算前一筆索引 (用於讀取累積值)
        prev_idx = idx - 1
        if prev_idx < 0: prev_idx = self.capacity - 1
        
        # --- 1. 寫入基礎數據 ---
        self.timestamp[idx]    = tick.timestamp_ms
        self.volume[idx]       = tick.volume
        self.total_volume[idx] = tick.total_volume
        self.tick_type[idx]    = tick.tick_type
        
        # 價格正規化
        price = tick.close / 10000.0
        self.close[idx] = price
        self.underlying_price[idx] = tick.underlying_price / 10000.0

        # --- 2. 狀態與累積更新 ---
        
        # 判斷買賣方向 (1=Buy/外盤, 2=Sell/內盤)
        buy_vol  = tick.volume if tick.tick_type == 1 else 0
        sell_vol = tick.volume if tick.tick_type == 2 else 0
        
        # 判斷是否為冷啟動 (第一筆)
        is_first_tick = (self.head == 0 and not self.is_full)
        
        if is_first_tick:
            # 初始化狀態
            self.session_high[idx] = price
            self.session_low[idx]  = price
            
            # 初始化累積值
            self.cum_volume[idx]   = tick.volume
            self.cum_pv[idx]       = price * tick.volume
            self.cum_close[idx]    = price
            self.cum_buy_vol[idx]  = buy_vol
            self.cum_sell_vol[idx] = sell_vol
        else:
            # 遞迴更新狀態 (Stateful Update)
            prev_high = self.session_high[prev_idx]
            prev_low  = self.session_low[prev_idx]
            
            # 防呆：若上一筆是 0 (異常)，重置為當前價格
            if prev_high == 0: 
                self.session_high[idx] = price
                self.session_low[idx]  = price
            else:
                self.session_high[idx] = max(prev_high, price)
                self.session_low[idx]  = min(prev_low, price)
            
            # 遞迴更新累積值 (Cumulative Update)
            self.cum_volume[idx]   = self.cum_volume[prev_idx]   + tick.volume
            self.cum_pv[idx]       = self.cum_pv[prev_idx]       + (price * tick.volume)
            self.cum_close[idx]    = self.cum_close[prev_idx]    + price
            self.cum_buy_vol[idx]  = self.cum_buy_vol[prev_idx]  + buy_vol
            self.cum_sell_vol[idx] = self.cum_sell_vol[prev_idx] + sell_vol

        # --- 3. 移動指標 ---
        self.head += 1
        if self.head >= self.capacity:
            self.head = 0
            self.is_full = True

    def get_snapshot(self):
        """
        回傳 Numba 計算所需的 Array 參照 (View)。
        ⚠️ 順序必須與 IndicatorManager.on_tick 的解包順序嚴格一致！
        """
        return (
            self.close,             # 0
            self.volume,            # 1
            self.tick_type,         # 2
            self.timestamp,         # 3
            self.underlying_price,  # 4
            self.cum_volume,        # 5
            self.cum_pv,            # 6
            self.cum_close,         # 7
            self.session_high,      # 8
            self.session_low,       # 9
            self.total_volume,      # 10
            self.cum_buy_vol,       # 11
            self.cum_sell_vol,      # 12
            self.head               # 13
        )

    def clear(self):
        """
        強制清空緩衝區 (歸零所有數據)。
        """
        self.head = 0
        self.is_full = False
        
        # 使用 fill(0) 進行原地清零，效率極高
        self.timestamp.fill(0)
        self.close.fill(0)
        self.volume.fill(0)
        self.total_volume.fill(0)
        self.tick_type.fill(0)
        self.underlying_price.fill(0)
        
        self.session_high.fill(0)
        self.session_low.fill(0)
        
        self.cum_volume.fill(0)
        self.cum_pv.fill(0)
        self.cum_close.fill(0)
        self.cum_buy_vol.fill(0)
        self.cum_sell_vol.fill(0)