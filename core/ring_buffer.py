# core/ring_buffer.py

import numpy as np
from data_schemas.txf_data_pb2 import Tick
from core.shared_memory_utils import (
    SharedBufferWrapper, 
    calculate_layout, 
    init_shared_memory
)

class TxfRingBuffer:
    """
    Fixed-size Circular Buffer for High-Frequency Tick Data.
    Uses Structure of Arrays (SoA) layout for CPU cache efficiency and Numba compatibility.

    Memory Layout (SoA):
    --------------------
    Timestamp: [t0, t1, t2, ..., tN]
    Close:     [p0, p1, p2, ..., pN]
    Volume:    [v0, v1, v2, ..., vN]
               ^
               |
             Head (Write Pointer) -->

    Key Features:
    1. **Zero Allocation**: All arrays pre-allocated. No malloc during runtime.
    2. **O(1) Write**: Constant time insertion with state updates.
    3. **Prefix Sums**: Auto-maintains cumulative sums for O(1) SMA/VWAP.
    """

    def __init__(self, capacity: int = 200000, shm_name: str = 'txf_ring_buffer', create_shm: bool = True):
        self.capacity = capacity
        
        # Calculate size needed
        total_size, offsets = calculate_layout()
        
        # Initialize Shared Memory
        self.shm = init_shared_memory(shm_name, create=create_shm, size=total_size)
        
        # Wrap with Numpy Arrays
        self.wrapper = SharedBufferWrapper(self.shm, offsets)
        
        # Bind arrays to self for compatibility with existing code
        self.timestamp        = self.wrapper.views['timestamp']
        self.close            = self.wrapper.views['close']
        self.volume           = self.wrapper.views['volume']
        self.total_volume     = self.wrapper.views['total_volume']
        self.tick_type        = self.wrapper.views['tick_type']
        self.underlying_price = self.wrapper.views['underlying_price']
        
        self.session_high     = self.wrapper.views['session_high']
        self.session_low      = self.wrapper.views['session_low']
        
        self.cum_volume       = self.wrapper.views['cum_volume']
        self.cum_pv           = self.wrapper.views['cum_pv']
        self.cum_close        = self.wrapper.views['cum_close']
        self.cum_buy_vol      = self.wrapper.views['cum_buy_vol']
        self.cum_sell_vol     = self.wrapper.views['cum_sell_vol']

        # Sync Header State
        if create_shm:
            # Writer Mode: Initialize Header
            self.head = 0
            self.is_full = False
            self.wrapper.set_header(0, False)
        else:
            # Reader Mode: Read Header
            self.head, self.is_full = self.wrapper.get_header()

    def write_tick(self, tick: Tick):
        """
        Writes a single Tick into the buffer and updates all stateful/cumulative columns.
        
        Complexity: O(1)
        
        Args:
            tick: The Protobuf Tick message containing market data.
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
        
        # 價格正規化 (Adaptive Scaling)
        # Handle both Scaled Int (x10000) and Raw Int inputs
        if tick.close > 500000: # Heuristic: > 50萬 represents scaled price (e.g. 20000 * 10000 = 2億)
            price = tick.close / 10000.0
        else:
            price = float(tick.close)
            
        self.close[idx] = price
        self.underlying_price[idx] = tick.underlying_price / 10000.0 # Keep this unless proven otherwise? Or apply same logic?
        # Let's apply same logic to underlying
        if tick.underlying_price > 500000:
             self.underlying_price[idx] = tick.underlying_price / 10000.0
        else:
             self.underlying_price[idx] = float(tick.underlying_price)

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
            
        # Update Header in Shared Memory (Atomic-ish)
        self.wrapper.set_header(self.head, self.is_full)

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
        
    def refresh_state(self):
        """
        [Reader Mode Only]
        從 Shared Memory 標頭讀取最新的 head 和 is_full 狀態。
        在 Reader Process 每次計算前呼叫此函數。
        """
        self.head, self.is_full = self.wrapper.get_header()

    def clear(self):
        """
        強制清空緩衝區 (歸零所有數據)。
        """
        self.head = 0
        self.is_full = False
        self.wrapper.set_header(0, False)
        
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