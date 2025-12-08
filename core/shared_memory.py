
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
            
        self.head = new_head # Update head in Shared Memory (signal for readers)

    def write_batch(self, ticks: list):
        """
        批次寫入大量 Ticks (Vectorized Write)
        效能遠高於迴圈呼叫 write_tick。
        """
        n = len(ticks)
        if n == 0: return

        # 1. 準備數據 (Transformation)
        # 為了效能，我們這裡做 List Comprehension，雖然有 Python overhead，
        # 但比 n 次 Shared Memory access 快得多。
        
        # Extract basic fields
        ts_arr = np.array([t.timestamp_ms for t in ticks], dtype=np.int64)
        price_arr = np.array([t.close / 10000.0 for t in ticks], dtype=np.float64)
        vol_arr = np.array([t.volume for t in ticks], dtype=np.int32)
        type_arr = np.array([t.tick_type for t in ticks], dtype=np.int32)
        # Others if needed... but let's stick to core logic
        
        # 2. 計算累積數據 (Vectorized Cumulative)
        # 需要取得當前 Buffer 裡 "上一筆" 的累積值
        current_head = self.head
        prev_idx = current_head - 1
        if prev_idx < 0: prev_idx = self.capacity - 1
        
        # Base values from previous state
        last_cum_vol = self.cum_volume[prev_idx]
        last_cum_pv = self.cum_pv[prev_idx]
        last_cum_close = self.cum_close[prev_idx]
        
        last_cum_buy = self.cum_buy_vol[prev_idx]
        last_cum_sell = self.cum_sell_vol[prev_idx]
        
        # Vectorized CumSum for the batch
        batch_cum_vol = np.cumsum(vol_arr) + last_cum_vol
        batch_cum_pv  = np.cumsum(price_arr * vol_arr) + last_cum_pv
        batch_cum_close = np.cumsum(price_arr) + last_cum_close
        
        # Buy/Sell Volume Separation
        buy_mask = (type_arr == 1)
        sell_mask = (type_arr == 2)
        
        # numpy where to create 0/vol arrays
        buy_vols = np.where(buy_mask, vol_arr, 0)
        sell_vols = np.where(sell_mask, vol_arr, 0)
        
        batch_cum_buy = np.cumsum(buy_vols) + last_cum_buy
        batch_cum_sell = np.cumsum(sell_vols) + last_cum_sell
        
        # Stateful High/Low (Need to be careful)
        # Reset High/Low at 08:45 and 15:00... logic is complex in vectorized.
        # 為了簡化，我們暫時使用 Python loop for High/Low state reset 
        # 或者假設這批次內不會跨越開盤時間 (通常 batch 只包含幾百毫秒)
        # 我們沿用上一筆的 Session High/Low 當作基準，然後跟自己的 batch 比較
        
        # 簡易實作：先用上一筆 High/Low，然後用 accumulate max 更新
        last_high = self.session_high[prev_idx]
        last_low = self.session_low[prev_idx]
        
        # 修正：如果上一筆是 0 (剛啟動)，則用第一筆 price
        if last_high == 0: last_high = price_arr[0]
        if last_low == 0: last_low = price_arr[0]
        
        # Numpy accumulate (Running Max)
        batch_high = np.maximum.accumulate(price_arr)
        batch_high = np.maximum(batch_high, last_high)
        
        batch_low = np.minimum.accumulate(price_arr)
        batch_low = np.minimum(batch_low, last_low)
        
        # 3. 寫入 Shared Memory (Slicing)
        # 處理 Ring Buffer Wrapping
        # 我們要把 N 筆資料寫入從 current_head 開始的位置
        
        # Case 1: No Wrap
        if current_head + n <= self.capacity:
            end = current_head + n
            
            # Basic Arrays
            self.timestamp[current_head:end] = ts_arr
            self.close[current_head:end] = price_arr
            self.volume[current_head:end] = vol_arr
            self.tick_type[current_head:end] = type_arr
            
            # Cumulative Arrays
            self.cum_volume[current_head:end] = batch_cum_vol
            self.cum_pv[current_head:end] = batch_cum_pv
            self.cum_close[current_head:end] = batch_cum_close
            self.cum_buy_vol[current_head:end] = batch_cum_buy
            self.cum_sell_vol[current_head:end] = batch_cum_sell
            
            # State Arrays
            self.session_high[current_head:end] = batch_high
            self.session_low[current_head:end] = batch_low
            
            # Update Head
            self.head = end if end < self.capacity else 0
            if end == self.capacity: self.is_full = True
            
        else:
            # Case 2: Wrap Around
            # Split into two chunks
            # Chunk 1: Head -> Capacity
            first_len = self.capacity - current_head
            
            # Chunk 2: 0 -> Remainder
            remain_len = n - first_len
            
            # --- Write Chunk 1 ---
            self.timestamp[current_head:] = ts_arr[:first_len]
            self.close[current_head:] = price_arr[:first_len]
            self.volume[current_head:] = vol_arr[:first_len]
            self.tick_type[current_head:] = type_arr[:first_len]
            
            self.cum_volume[current_head:] = batch_cum_vol[:first_len]
            self.cum_pv[current_head:] = batch_cum_pv[:first_len]
            self.cum_close[current_head:] = batch_cum_close[:first_len]
            self.cum_buy_vol[current_head:] = batch_cum_buy[:first_len]
            self.cum_sell_vol[current_head:] = batch_cum_sell[:first_len]
            
            self.session_high[current_head:] = batch_high[:first_len]
            self.session_low[current_head:] = batch_low[:first_len]
            
            self.is_full = True # We hit the end
            
            # --- Write Chunk 2 ---
            self.timestamp[:remain_len] = ts_arr[first_len:]
            self.close[:remain_len] = price_arr[first_len:]
            self.volume[:remain_len] = vol_arr[first_len:]
            self.tick_type[:remain_len] = type_arr[first_len:]
            
            self.cum_volume[:remain_len] = batch_cum_vol[first_len:]
            self.cum_pv[:remain_len] = batch_cum_pv[first_len:]
            self.cum_close[:remain_len] = batch_cum_close[first_len:]
            self.cum_buy_vol[:remain_len] = batch_cum_buy[first_len:]
            self.cum_sell_vol[:remain_len] = batch_cum_sell[first_len:]
            
            self.session_high[:remain_len] = batch_high[first_len:]
            self.session_low[:remain_len] = batch_low[first_len:]

            # Update Head
            self.head = remain_len

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
        """關閉 SharedMemory 連線 (Idempotent: 可重複呼叫)"""
        # 1. 防止 Resource Tracker 誤報 (Reader/Writer 都要做)
        try:
            from multiprocessing import resource_tracker
            # Unregister first to silence tracker
            resource_tracker.unregister(self.shm._name, "shared_memory")
        except Exception:
            pass # 可能已經被 unregister 過，或 name 不存在

        # 2. 關閉 FD
        try:
            self.shm.close()
        except Exception:
            pass
        
        # 3. 如果是擁有者 (Writer)，負責銷毀實體檔案
        if self.is_owner:
            try:
                self.shm.unlink()
                self.logger.info(f"SharedMemory '{self.name}' unlinked.")
            except (FileNotFoundError, IndexError, ValueError):
                pass
            except Exception as e:
                self.logger.warning(f"Unlink warning: {e}")
