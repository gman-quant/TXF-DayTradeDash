# core/ring_buffer.py

import numpy as np
from typing import Tuple

# 引入 Protobuf 定義以便 Type Hinting (非必要，但有助 IDE 提示)
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
        # 💾 預先分配記憶體 (Pre-allocation)
        # ==========================================
        # 1. 時間戳 (Unix Int64)
        self.timestamp = np.zeros(capacity, dtype=np.int64)
        
        # 2. 價格 (Float64)
        # 雖然 Protobuf 傳來的是 Scaled Int，但為了計算均線等指標，
        # 我們在寫入時直接轉為 Float64，省去後續計算重複轉換的開銷。
        self.close = np.zeros(capacity, dtype=np.float64)
        
        # 3. 成交量 (Int32)
        self.volume = np.zeros(capacity, dtype=np.int32)
        
        # 4. 總量 (Int32) - 用於檢查封包是否有遺漏
        self.total_volume = np.zeros(capacity, dtype=np.int32)
        
        # 5. 買賣盤別 (Int32)
        self.tick_type = np.zeros(capacity, dtype=np.int32)

        # 6. 標的物價格 (Float64)
        self.underlying_price = np.zeros(capacity, dtype=np.float64)

    def write_tick(self, tick: Tick):
        """
        將單筆 Tick 寫入緩衝區 (O(1) 複雜度)
        """
        idx = self.head
        
        # 1. 填入數據 (直接操作 NumPy 記憶體)
        self.timestamp[idx]    = tick.timestamp_ms
        self.volume[idx]       = tick.volume
        self.total_volume[idx] = tick.total_volume
        self.tick_type[idx]    = tick.tick_type
        
        # 關鍵優化：在此處處理 Scaled Integer (/10000.0)
        # 讓後續 Numba 計算直接面對乾淨的 Float
        self.close[idx]            = tick.close / 10000.0
        self.underlying_price[idx] = tick.underlying_price / 10000.0

        # 2. 移動指標 (Wrap Around)
        self.head += 1
        if self.head >= self.capacity:
            self.head = 0
            self.is_full = True

    def get_snapshot(self):
        """
        回傳 Numba 計算所需的 Array 參照
        順序邏輯：價格(最常用) -> 量/方向(策略核心) -> 時間/輔助 -> 指標(Head)
        """
        return (
            self.close,             # 1. 價格 (Price)：最常被用到
            self.volume,            # 2. 成交量 (Volume)：次常用
            self.tick_type,         # 3. 內外盤 (Type)：計算 OFI/CVD 必備
            self.timestamp,         # 4. 時間 (Time)：時間窗口計算用
            self.underlying_price,  # 5. 標的 (Aux)：計算價差用
            self.head               # 6. 指標 (Cursor)：告訴 Numba 目前寫到哪
        )

    def clear(self):
        """重置緩衝區 (例如開盤前)"""
        self.head = 0
        self.is_full = False
        # 選擇性：將數據歸零 (視需求而定，追求極速可不歸零，只需重置 head)
        self.timestamp.fill(0)
        self.close.fill(0)
        self.volume.fill(0)
        self.underlying_price.fill(0)