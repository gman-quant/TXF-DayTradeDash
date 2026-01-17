import numpy as np
import logging
from multiprocessing.shared_memory import SharedMemory
from data_schemas.txf_data_pb2 import Tick


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
            ("timestamp", np.int64, 8),
            ("close", np.float64, 8),
            ("volume", np.int32, 4),
            ("total_volume", np.int32, 4),
            ("tick_type", np.int32, 4),
            ("underlying_price", np.float64, 8),
            ("session_high", np.float64, 8),
            ("session_low", np.float64, 8),
            ("cum_volume", np.int64, 8),
            ("cum_pv", np.float64, 8),
            ("cum_close", np.float64, 8),
            ("cum_buy_vol", np.int64, 8),
            ("cum_sell_vol", np.int64, 8),
            # [LOB Integration]
            ("obi", np.float64, 8),
            ("ofi", np.float64, 8),
            ("lob_lag", np.float64, 8),
            # [VWAP Optimization]
            ("cum_pv_sq", np.float64, 8),
        ]

        # 計算總大小
        self.total_body_size = sum(item[2] * capacity for item in self.schema)
        self.total_size = self.HEADER_SIZE + self.total_body_size

        # 連接 Shared Memory
        if create:
            try:
                # [Fix] Robust Re-creation Loop
                # On Windows, unlink() is not atomic/instant. We might need to wait a bit.
                import time

                for i in range(5):
                    try:
                        # 嘗試先 unlink (如果上次沒清乾淨)
                        try:
                            existing = SharedMemory(name=name)
                            existing.close()
                            existing.unlink()
                            self.logger.info(
                                f"Existing shared memory '{name}' unlinked."
                            )
                            time.sleep(0.1)  # Yield to OS
                        except FileNotFoundError:
                            pass

                        self.shm = SharedMemory(
                            name=name, create=True, size=self.total_size
                        )
                        self.logger.info(
                            f"Created SharedMemory '{name}' size={self.total_size} bytes"
                        )
                        break
                    except FileExistsError:
                        if i == 4:
                            raise
                        self.logger.warning(
                            f"Shared memory '{name}' still exists. Retrying ({i + 1}/5)..."
                        )
                        time.sleep(0.5)

                # 初始化 Header (歸零)
                self.shm.buf[: self.HEADER_SIZE] = b"\x00" * self.HEADER_SIZE

            except Exception as e:
                self.logger.error(f"Failed to create shared memory: {e}")
                raise
        else:
            try:
                self.shm = SharedMemory(name=name, create=False)
                self.logger.info(f"Attached to SharedMemory '{name}'")
            except FileNotFoundError:
                raise RuntimeError(
                    f"Shared memory '{name}' not found. Start Ingestion Server first."
                )

        # 映射 Numpy Arrays
        current_offset = self.HEADER_SIZE

        for field_name, dtype, item_size in self.schema:
            byte_size = item_size * capacity

            # 使用 numpy.ndarray 建構視圖 (Zero-copy)
            array_view = np.ndarray(
                (capacity,), dtype=dtype, buffer=self.shm.buf, offset=current_offset
            )

            # 將視圖綁定到 instance 屬性 (ex: self.timestamp)
            setattr(self, field_name, array_view)

            current_offset += byte_size

        # 標記是否為擁有者 (負責 unlink)
        self.is_owner = create

        # 初始化 Head View (Int64 Array of size 1)
        self.head_view = np.ndarray((1,), dtype=np.int64, buffer=self.shm.buf, offset=0)
        self.full_view = np.ndarray(
            (1,), dtype=np.int64, buffer=self.shm.buf, offset=8
        )  # 用 int64 存 bool 方便
        self.prev_close_view = np.ndarray(
            (1,), dtype=np.float64, buffer=self.shm.buf, offset=16
        )  # Offset 16: Prev Close

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

    @property
    def prev_close(self):
        """Reference Price (Yesterday's Close) stored in Header Offset 16"""
        # Create a temporary view or cache the view in __init__?
        # Better to cache the view in __init__ for performance, but this is accessed infrequent (dashboard update).
        # Let's add view in __init__
        return self.prev_close_view[0]

    @prev_close.setter
    def prev_close(self, value):
        self.prev_close_view[0] = value

    def write_tick(self, tick: Tick):
        """
        邏輯與 TxfRingBuffer.write_tick 完全相同。
        直接複製以確保行為一致性。
        """
        idx = self.head  # 讀取 Shared Memory 中的 head

        # 計算前一筆索引
        prev_idx = idx - 1
        if prev_idx < 0:
            prev_idx = self.capacity - 1

        # --- 1. 寫入基礎數據 ---
        self.timestamp[idx] = tick.timestamp_ms
        self.volume[idx] = tick.volume
        self.total_volume[idx] = tick.total_volume
        self.tick_type[idx] = tick.tick_type

        price = tick.close / 10000.0
        self.close[idx] = price
        self.underlying_price[idx] = tick.underlying_price / 10000.0

        # --- 2. 狀態與累積更新 (Session Reset Logic) ---
        buy_vol = tick.volume if tick.tick_type == 1 else 0
        sell_vol = tick.volume if tick.tick_type == 2 else 0
        pv = price * tick.volume
        pv_sq = price * price * tick.volume

        # 重置檢測
        # Day Session Start: ~08:45 (Gap > 1hr from prev)
        # Night Session Start: ~15:00 (Gap > 1hr from prev)
        RESET_THRESHOLD_MS = 3600000  # 1 hour

        is_first_tick = self.head == 0 and not self.is_full
        should_reset = is_first_tick

        if not is_first_tick:
            prev_ts = self.timestamp[prev_idx]
            # [Fix] 如果 prev_ts 是 0 (buffer剛初始化)，也視為 reset
            if prev_ts == 0:
                should_reset = True
            else:
                dt = tick.timestamp_ms - prev_ts
                if dt > RESET_THRESHOLD_MS:
                    should_reset = True

        if should_reset:
            # [Reset State]
            self.session_high[idx] = price
            self.session_low[idx] = price

            self.cum_volume[idx] = tick.volume
            self.cum_pv[idx] = pv
            self.cum_pv_sq[idx] = pv_sq
            self.cum_buy_vol[idx] = buy_vol
            self.cum_sell_vol[idx] = sell_vol

            # cum_close 不重置，為了 SMA 連續性
            # 若是第一筆，則為 price；若重置但非第一筆，延續上一筆
            if is_first_tick:
                self.cum_close[idx] = price
            else:
                self.cum_close[idx] = self.cum_close[prev_idx] + price

        else:
            # [Accumulate]
            prev_high = self.session_high[prev_idx]
            prev_low = self.session_low[prev_idx]

            self.session_high[idx] = max(prev_high, price)
            self.session_low[idx] = min(prev_low, price)

            self.cum_volume[idx] = self.cum_volume[prev_idx] + tick.volume
            self.cum_pv[idx] = self.cum_pv[prev_idx] + pv
            self.cum_pv_sq[idx] = self.cum_pv_sq[prev_idx] + pv_sq
            self.cum_close[idx] = self.cum_close[prev_idx] + price
            self.cum_buy_vol[idx] = self.cum_buy_vol[prev_idx] + buy_vol
            self.cum_sell_vol[idx] = self.cum_sell_vol[prev_idx] + sell_vol

        # --- 3. 移動指標 ---
        new_head = self.head + 1
        if new_head >= self.capacity:
            new_head = 0
            self.is_full = True  # 寫入 Shared Memory

        self.head = new_head  # Update head in Shared Memory (signal for readers)

    def write_batch(self, ticks: list, lob_data: list = None):
        """
        批次寫入大量 Ticks (Vectorized Write)
        Args:
            ticks: List of Tick objects
            lob_data: Optional list of (obi, ofi, lob_lag) tuples, matching ticks length.
        """
        n = len(ticks)
        if n == 0:
            return

        # 1. 準備數據 (Transformation)
        # 為了效能，我們這裡做 List Comprehension，雖然有 Python overhead，
        # 但比 n 次 Shared Memory access 快得多。

        # Extract basic fields
        ts_arr = np.array([t.timestamp_ms for t in ticks], dtype=np.int64)
        price_arr = np.array([t.close / 10000.0 for t in ticks], dtype=np.float64)
        vol_arr = np.array([t.volume for t in ticks], dtype=np.int32)
        type_arr = np.array([t.tick_type for t in ticks], dtype=np.int32)

        # [Fix] Add missing fields
        total_vol_arr = np.array([t.total_volume for t in ticks], dtype=np.int64)
        underlying_arr = np.array(
            [t.underlying_price / 10000.0 for t in ticks], dtype=np.float64
        )

        # [LOB Integration] Extract OBI/OFI/Lag
        if lob_data and len(lob_data) == n:
            # zip(*lob_data) unzip list of tuples -> (tuple of obis, tuple of ofis, ...)
            # This is efficient.
            lob_tuple = list(zip(*lob_data))
            obi_arr = np.array(lob_tuple[0], dtype=np.float64)
            ofi_arr = np.array(lob_tuple[1], dtype=np.float64)
            lag_arr = np.array(lob_tuple[2], dtype=np.float64)
        else:
            # Default zeros
            obi_arr = np.zeros(n, dtype=np.float64)
            ofi_arr = np.zeros(n, dtype=np.float64)
            lag_arr = np.zeros(n, dtype=np.float64)

        # 2. 計算累積數據 (Vectorized Cumulative with Session Reset)
        # 需要取得當前 Buffer 裡 "上一筆" 的累積值
        current_head = self.head
        prev_idx = current_head - 1
        if prev_idx < 0:
            prev_idx = self.capacity - 1

        # Base values from previous state
        last_cum_vol = self.cum_volume[prev_idx]
        last_cum_pv = self.cum_pv[prev_idx]
        last_cum_pv_sq = self.cum_pv_sq[prev_idx]  # New
        last_cum_close = self.cum_close[prev_idx]

        last_cum_buy = self.cum_buy_vol[prev_idx]
        last_cum_sell = self.cum_sell_vol[prev_idx]

        # [Session Reset Handling for Batch]
        # 這比單點複雜，因為一個 batch 可能橫跨 session。
        # 策略：
        # 1. 找出 Batch 內部的 Session Breakpoints (Delta Time > Threshold)
        # 2. 針對每個區段做 cumsum，並在斷點處歸零。

        RESET_THRESHOLD_MS = 3600000

        # A. 計算時間差
        # 第一筆跟上一筆比
        dt_first = ts_arr[0] - self.timestamp[prev_idx]

        # 內部相鄰互比: ts[i] - ts[i-1]
        # np.diff 回傳長度 n-1
        dt_internal = np.diff(ts_arr)

        # 組合完整的 dt 陣列
        all_dts = np.concatenate(([dt_first], dt_internal))

        # 找出斷點 (Boolean Mask)
        reset_mask = all_dts > RESET_THRESHOLD_MS

        # 如果前一筆是空的 (first start)，第一筆也要當作 reset
        if current_head == 0 and not self.is_full:
            reset_mask[0] = True

        # B. 執行分段累積 (Vectorized Grouped Cumsum)
        # 技巧：使用 "Reset Group ID"
        # 每次遇到 reset=True，Group ID + 1。
        # 然後對每個 Group ID 各自做 cumsum。

        group_ids = np.cumsum(reset_mask)

        # 計算原始增量 (Flow)
        pv_flow = price_arr * vol_arr
        pv_sq_flow = price_arr * price_arr * vol_arr

        # 預分配結果陣列
        batch_cum_vol = np.zeros(n, dtype=np.int64)
        batch_cum_pv = np.zeros(n, dtype=np.float64)
        batch_cum_pv_sq = np.zeros(n, dtype=np.float64)
        batch_cum_buy = np.zeros(n, dtype=np.int64)
        batch_cum_sell = np.zeros(n, dtype=np.int64)

        buy_mask = type_arr == 1
        sell_mask = type_arr == 2
        buy_flow = np.where(buy_mask, vol_arr, 0)
        sell_flow = np.where(sell_mask, vol_arr, 0)

        # 取得唯一的 group id (通常只有 1~2 個，因為換盤不頻繁)
        unique_groups = np.unique(group_ids)

        for gid in unique_groups:
            # 遮罩選取該群組
            mask = group_ids == gid

            # 局部累積 (Local Cumsum)
            # 這些是 "從該 Group 起點" 開始累積的值
            g_cum_vol = np.cumsum(vol_arr[mask])
            g_cum_pv = np.cumsum(pv_flow[mask])
            g_cum_pv_sq = np.cumsum(pv_sq_flow[mask])
            g_cum_buy = np.cumsum(buy_flow[mask])
            g_cum_sell = np.cumsum(sell_flow[mask])

            # 決定「基準值 (Base)」
            # 如果這個 Group 是由 Reset 觸發的 (Reset Mask 為 True 的第一筆所屬 Group)，Base = 0
            # 如果這個 Group 是延續上一筆 (通常是第一個 Group 且 reset_mask[0] False)，Base = last_cum

            is_continuation = (gid == group_ids[0]) and (not reset_mask[0])

            if is_continuation:
                batch_cum_vol[mask] = g_cum_vol + last_cum_vol
                batch_cum_pv[mask] = g_cum_pv + last_cum_pv
                batch_cum_pv_sq[mask] = g_cum_pv_sq + last_cum_pv_sq
                batch_cum_buy[mask] = g_cum_buy + last_cum_buy
                batch_cum_sell[mask] = g_cum_sell + last_cum_sell
            else:
                # Reset Base = 0
                batch_cum_vol[mask] = g_cum_vol
                batch_cum_pv[mask] = g_cum_pv
                batch_cum_pv_sq[mask] = g_cum_pv_sq
                batch_cum_buy[mask] = g_cum_buy
                batch_cum_sell[mask] = g_cum_sell

        # cum_close 保持不重置 (為了 SMA 連續性)
        batch_cum_close = np.cumsum(price_arr) + last_cum_close

        # Stateful High/Low (Simplified for Batch)
        last_high = self.session_high[prev_idx]
        last_low = self.session_low[prev_idx]

        # 修正：如果上一筆是 0 (剛啟動)，則用第一筆 price
        if last_high == 0:
            last_high = price_arr[0]
        if last_low == 0:
            last_low = price_arr[0]

        # 當遇到 Session Reset 時，High/Low 也應該重置
        batch_high = np.zeros(n, dtype=np.float64)
        batch_low = np.zeros(n, dtype=np.float64)

        for gid in unique_groups:
            mask = group_ids == gid
            prices_g = price_arr[mask]

            is_continuation = (gid == group_ids[0]) and (not reset_mask[0])

            g_high = np.maximum.accumulate(prices_g)
            g_low = np.minimum.accumulate(prices_g)

            if is_continuation:
                batch_high[mask] = np.maximum(g_high, last_high)
                batch_low[mask] = np.minimum(g_low, last_low)
            else:
                batch_high[mask] = g_high
                batch_low[mask] = g_low

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

            # [Fix] Write missing fields
            self.total_volume[current_head:end] = total_vol_arr
            self.underlying_price[current_head:end] = underlying_arr

            # [LOB Integration] Write new fields
            self.obi[current_head:end] = obi_arr
            self.ofi[current_head:end] = ofi_arr
            self.lob_lag[current_head:end] = lag_arr

            # Cumulative Arrays
            self.cum_volume[current_head:end] = batch_cum_vol
            self.cum_pv[current_head:end] = batch_cum_pv
            self.cum_pv_sq[current_head:end] = batch_cum_pv_sq  # New
            self.cum_close[current_head:end] = batch_cum_close
            self.cum_buy_vol[current_head:end] = batch_cum_buy
            self.cum_sell_vol[current_head:end] = batch_cum_sell

            # State Arrays
            self.session_high[current_head:end] = batch_high
            self.session_low[current_head:end] = batch_low

            # Update Head
            self.head = end if end < self.capacity else 0
            if end == self.capacity:
                self.is_full = True

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

            # [Fix] Write missing fields (Chunk 1)
            self.total_volume[current_head:] = total_vol_arr[:first_len]
            self.underlying_price[current_head:] = underlying_arr[:first_len]

            # [LOB Integration]
            self.obi[current_head:] = obi_arr[:first_len]
            self.ofi[current_head:] = ofi_arr[:first_len]
            self.lob_lag[current_head:] = lag_arr[:first_len]

            self.cum_volume[current_head:] = batch_cum_vol[:first_len]
            self.cum_pv[current_head:] = batch_cum_pv[:first_len]
            self.cum_pv_sq[current_head:] = batch_cum_pv_sq[:first_len]  # New
            self.cum_close[current_head:] = batch_cum_close[:first_len]
            self.cum_buy_vol[current_head:] = batch_cum_buy[:first_len]
            self.cum_sell_vol[current_head:] = batch_cum_sell[:first_len]

            self.session_high[current_head:] = batch_high[:first_len]
            self.session_low[current_head:] = batch_low[:first_len]

            self.is_full = True  # We hit the end

            # --- Write Chunk 2 ---
            self.timestamp[:remain_len] = ts_arr[first_len:]
            self.close[:remain_len] = price_arr[first_len:]
            self.volume[:remain_len] = vol_arr[first_len:]
            self.tick_type[:remain_len] = type_arr[first_len:]

            # [Fix] Write missing fields (Chunk 2)
            self.total_volume[:remain_len] = total_vol_arr[first_len:]
            self.underlying_price[:remain_len] = underlying_arr[first_len:]

            # [LOB Integration]
            self.obi[:remain_len] = obi_arr[first_len:]
            self.ofi[:remain_len] = ofi_arr[first_len:]
            self.lob_lag[:remain_len] = lag_arr[first_len:]

            self.cum_volume[:remain_len] = batch_cum_vol[first_len:]
            self.cum_pv[:remain_len] = batch_cum_pv[first_len:]
            self.cum_pv_sq[:remain_len] = batch_cum_pv_sq[first_len:]  # New
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
            self.close,  # 0
            self.volume,  # 1
            self.tick_type,  # 2
            self.timestamp,  # 3
            self.underlying_price,  # 4
            self.cum_volume,  # 5
            self.cum_pv,  # 6
            self.cum_close,  # 7
            self.session_high,  # 8
            self.session_low,  # 9
            self.total_volume,  # 10
            self.cum_buy_vol,  # 11
            self.cum_sell_vol,  # 12
            self.obi,  # 13 [New]
            self.ofi,  # 14 [New]
            self.lob_lag,  # 15 [New]
            self.cum_pv_sq,  # 16 [New VWAP]
            self.head,  # 17
        )

    def shutdown(self):
        """關閉 SharedMemory 連線 (Idempotent: 可重複呼叫)"""
        # 1. 如果是 Reader (Create=False)，需要手動 Unregister
        if not self.is_owner:
            try:
                from multiprocessing import resource_tracker

                name = self.shm._name
                try:
                    resource_tracker.unregister(name, "shared_memory")
                except KeyError:
                    pass
            except Exception:
                pass

        # 2. 關閉 FD
        try:
            self.shm.close()
        except Exception:
            pass

        # 3. 如果是 Owner (Writer)，呼叫 unlink (它內部會自動 unregister，不要手動呼叫避免 KeyError)
        if self.is_owner:
            try:
                self.shm.unlink()
                self.logger.info(f"SharedMemory '{self.name}' unlinked.")
            except (FileNotFoundError, IndexError, ValueError):
                pass
            except Exception as e:
                # 只有非 KeyError 的才報錯 (KeyError 表示已經被清過了)
                self.logger.warning(f"Unlink warning: {e}")
