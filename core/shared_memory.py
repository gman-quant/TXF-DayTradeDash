
import numpy as np
import logging
from multiprocessing.shared_memory import SharedMemory
from data_schemas.txf_data_pb2 import Tick
from core.ring_buffer import TxfRingBuffer

class SharedRingBuffer:
    """
    基於 multiprocessing.shared_memory 的多進程 RingBuffer。
    
    特點：
    1. 記憶體佈局與 TxfRingBuffer 完全兼容，但數據存於 Shared Memory。
    2. Writer (Ingestion Process) 負責 create=True，Reader (Strategy Process) 負責 create=False。
    3. 使用 Head 指標進行無鎖同步 (Single Writer, Multiple Reader)。
    
    Memory Layout:
    [Header (128 bytes)]
      - int64 head (offset 0)
      - int64 is_full (offset 8)
      - ... reserved ...
    [Body (Arrays)]
      - timestamp (int64 * capacity)
      - close (float64 * capacity)
      - ...
    """
    
    HEADER_SIZE = 128
    
    def __init__(self, name: str, capacity: int = 200000, create: bool = False):
        self.name = name
        self.capacity = capacity
        self.logger = logging.getLogger(f"SharedBuffer-{name}")
        
        # 定義欄位與型別 (順序必須固定！)
        # (Name, Dtype, ItemSize)
        self.schema = [
            ('timestamp',        np.int64,   8),
            ('close',            np.float64, 8),
            ('volume',           np.int32,   4),
            ('total_volume',     np.int32,   4),
            ('tick_type',        np.int32,   4),
            ('underlying_price', np.float64, 8),
            ('session_high',     np.float64, 8),
            ('session_low',      np.float64, 8),
            ('cum_volume',       np.int64,   8),
            ('cum_pv',           np.float64, 8),
            ('cum_close',        np.float64, 8),
            ('cum_buy_vol',      np.int64,   8),
            ('cum_sell_vol',     np.int64,   8),
        ]
        
        # 計算總大小
        self.total_body_size = sum(item[2] * capacity for item in self.schema)
        self.total_size = self.HEADER_SIZE + self.total_body_size
        
        # 連接 Shared Memory
        if create:
            try:
                # 嘗試先 unlink (如果上次沒清乾淨)
                try:
                    existing = SharedMemory(name=name)
                    existing.close()
                    existing.unlink()
                    self.logger.info(f"Existing shared memory '{name}' unlinked.")
                except FileNotFoundError:
                    pass
                
                self.shm = SharedMemory(name=name, create=True, size=self.total_size)
                self.logger.info(f"Created SharedMemory '{name}' size={self.total_size} bytes")
                
                # 初始化 Header (歸零)
                self.shm.buf[:self.HEADER_SIZE] = b'\x00' * self.HEADER_SIZE
                
            except Exception as e:
                self.logger.error(f"Failed to create shared memory: {e}")
                raise
        else:
            try:
                self.shm = SharedMemory(name=name, create=False)
                self.logger.info(f"Attached to SharedMemory '{name}'")
            except FileNotFoundError:
                raise RuntimeError(f"Shared memory '{name}' not found. Start Ingestion Server first.")

        # 映射 Numpy Arrays
        current_offset = self.HEADER_SIZE
        
        for field_name, dtype, item_size in self.schema:
            byte_size = item_size * capacity
            
            # 使用 numpy.ndarray 建構視圖 (Zero-copy)
            array_view = np.ndarray(
                (capacity,), 
                dtype=dtype, 
                buffer=self.shm.buf, 
                offset=current_offset
            )
            
            # 將視圖綁定到 instance 屬性 (ex: self.timestamp)
            setattr(self, field_name, array_view)
            
            current_offset += byte_size

        # 標記是否為擁有者 (負責 unlink)
        self.is_owner = create
        
        # 初始化 Head View (Int64 Array of size 1)
        self.head_view = np.ndarray((1,), dtype=np.int64, buffer=self.shm.buf, offset=0)
        self.full_view = np.ndarray((1,), dtype=np.int64, buffer=self.shm.buf, offset=8) # 用 int64 存 bool 方便

    @property
    def head(self):
        return self.head_view[0]

    @head.setter
    def head(self, value):
        self.head_view[0] = value

    @property
    def is_full(self):
        return bool(self.full_view[0])

    @is_full.setter
    def is_full(self, value):
        self.full_view[0] = 1 if value else 0

    def write_tick(self, tick: Tick):
        """
        邏輯與 TxfRingBuffer.write_tick 完全相同。
        直接複製以確保行為一致性。
        """
        idx = self.head  # 讀取 Shared Memory 中的 head
        
        # 計算前一筆索引
        prev_idx = idx - 1
        if prev_idx < 0: prev_idx = self.capacity - 1
        
        # --- 1. 寫入基礎數據 ---
        self.timestamp[idx]    = tick.timestamp_ms
        self.volume[idx]       = tick.volume
        self.total_volume[idx] = tick.total_volume
        self.tick_type[idx]    = tick.tick_type
        
        price = tick.close / 10000.0
        self.close[idx] = price
        self.underlying_price[idx] = tick.underlying_price / 10000.0

        # --- 2. 狀態與累積更新 ---
        buy_vol  = tick.volume if tick.tick_type == 1 else 0
        sell_vol = tick.volume if tick.tick_type == 2 else 0
        
        is_first_tick = (self.head == 0 and not self.is_full)
        
        if is_first_tick:
            self.session_high[idx] = price
            self.session_low[idx]  = price
            
            self.cum_volume[idx]   = tick.volume
            self.cum_pv[idx]       = price * tick.volume
            self.cum_close[idx]    = price
            self.cum_buy_vol[idx]  = buy_vol
            self.cum_sell_vol[idx] = sell_vol
        else:
            prev_high = self.session_high[prev_idx]
            prev_low  = self.session_low[prev_idx]
            
            if prev_high == 0: 
                self.session_high[idx] = price
                self.session_low[idx]  = price
            else:
                self.session_high[idx] = max(prev_high, price)
                self.session_low[idx]  = min(prev_low, price)
            
            self.cum_volume[idx]   = self.cum_volume[prev_idx]   + tick.volume
            self.cum_pv[idx]       = self.cum_pv[prev_idx]       + (price * tick.volume)
            self.cum_close[idx]    = self.cum_close[prev_idx]    + price
            self.cum_buy_vol[idx]  = self.cum_buy_vol[prev_idx]  + buy_vol
            self.cum_sell_vol[idx] = self.cum_sell_vol[prev_idx] + sell_vol

        # --- 3. 移動指標 ---
        new_head = self.head + 1
        if new_head >= self.capacity:
            new_head = 0
            self.is_full = True # 寫入 Shared Memory
            
        self.head = new_head # 更新 Shared Memory，這是 Reader 看到的訊號

    def get_snapshot(self):
        """
        回傳與 TxfRingBuffer 一致的 Snapshot Tuple。
        """
        # 注意：這裡回傳的是 self.close (它是指向 SHM 的 View)
        # Numba 直接讀取這個 View 就能讀到 SHM 數據
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
            self.head               # 13 (注意：這裡是回傳 int value 還是 view? TxfRingBuffer 回傳 int value)
        )

    def shutdown(self):
        """關閉 SharedMemory 連線"""
        # 1. 防止 Resource Tracker 誤報 (Reader/Writer 都要做)
        # Python 的 multiprocessing.resource_tracker 會試圖在進程結束時 unlink 所有它知道的 SHM
        # 我們手動管理生命週期，所以要叫它閉嘴
        try:
            from multiprocessing import resource_tracker
            # 必須使用 _name 因為 shm.name 可能因版本不同有差異，但通常是一樣的
            resource_tracker.unregister(self.shm._name, "shared_memory")
        except Exception:
            pass

        # 2. 關閉 FD
        self.shm.close()
        
        # 3. 如果是擁有者 (Writer)，負責銷毀實體檔案
        if self.is_owner:
            try:
                self.shm.unlink()
                self.logger.info(f"SharedMemory '{self.name}' unlinked.")
            except FileNotFoundError:
                pass
