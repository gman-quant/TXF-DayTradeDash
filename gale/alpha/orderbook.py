import math
import logging
import heapq  # [Optimization] Priority Queue for O(1) access
from collections import defaultdict
from typing import Tuple, Dict

# Config
# 2^16 = 65536ms ~= 65.5 seconds history
# 這是 HFT 常見的優化技巧: Power of 2 Size 允許使用 Bitwise AND (&) 代替 Mod (%) 運算
BUFFER_SIZE = 65536
BUFFER_MASK = 65535

class LOBEngine:
    """
    機構級 LOB (Level Order Book) 核心運算引擎 (High-Performance Hybrid Sequencer).
    
    [v2.3 Optimization] Min-Heap Edition (Replay Lag Fix)
    
    改良重點：
    1. **Heap-Based Flow Control**: replace `set` with `min_heap`.
       - Problem: `set` iteration is O(N) where N is *all* buffered future quotes. 
         During replay/lag, N grows large (e.g. 5000), and we act on M ticks (e.g. 30000). 
         Total Ops = 150,000,000. System hangs.
       - Solution: `min_heap` allows O(1) check to see if we have valid data <= tick_ts.
         Consumption is O(K log N) where K is valid items.
       - Result: "Death Spiral" eliminated. Replay speed limited only by CPU/IO, not algorithmic complexity.
    
    2. **Hybrid OFI Tracking**: 
       - `heap` 儲存 (ts, idx) Tuple。
       - `buffer` 儲存數值。
       
    核心邏輯架構 (Core Architecture):
    -------------------------------
    1. **環狀陣列 (Circular Buffer)**:
       存儲 OBI/OFI 數值與 Tag。
    
    2. **OFI Flow Control (Heap-Based)**:
       Update: `heapq.heappush(heap, (ts, idx))`.
       Get: `while heap and heap[0][0] <= tick_ts`: pop & consume.
       
    3. **Deduplication**:
       使用 Set `active_push_signatures` (ts, idx) 避免同一毫秒重複 Push 到 Heap，
       雖然 Heap 重複 Pop 沒壞處 (Buffer 會被清空)，但減少 Heap Size 有助於效能。
    """
    
    def __init__(self):
        self.logger = logging.getLogger("LOBEngine")
        
        # --- 狀態變數 (State) ---
        self.max_seen_ts = 0  # 目前系統看過的最新 Quote 時間 (Watermark)
        self.last_read_ts = 0 # 上次讀取 Metrics 的時間點
        
        # [Optimization] Hybrid Buffers
        # Value Buffers
        self.ofi_buffer = [0.0] * BUFFER_SIZE
        self.obi_sum_buffer = [0.0] * BUFFER_SIZE
        self.obi_count_buffer = [0] * BUFFER_SIZE
        
        # Timestamp Tags (Init with -1)
        self.ofi_ts_buffer = [-1] * BUFFER_SIZE
        self.obi_ts_buffer = [-1] * BUFFER_SIZE
        
        # ACTIVE PRIORITY QUEUE (For Time-Ordered Access)
        # Elements: (timestamp, index)
        self.pending_ofi_heap = []
        
        # [Optimization] To avoid duplicate heap pushes for the same slot update
        # (Though duplicate pops are safe, keeping heap small is better)
        self.active_push_set = set()
        
        # 3. 去重狀態 (Deduplication State)
        self.last_quote_signature = None
        
        # 4. 快照緩存 (Latest Snapshot)
        self.last_bid_vol_sum = 0
        self.last_ask_vol_sum = 0
        self.last_avg_obi = 0.0 
        
    def update(self, quote):
        """
        接收 Kafka BidAsk (Quote) 訊息並更新內部狀態 (O(log N) Heap Push).
        """
        
        # --- 1. 嚴格去重 (Strict Deduplication) ---
        current_signature = (
            quote.timestamp_ms, 
            sum(quote.bid_volume), 
            sum(quote.ask_volume),
            sum(quote.bid_price), 
            sum(quote.ask_price)
        )
        
        if current_signature == self.last_quote_signature:
            return
            
        self.last_quote_signature = current_signature
        
        # --- 2. 核心指標計算 ---
        ts = quote.timestamp_ms
        idx = ts & BUFFER_MASK # 極速 Bitwise Indexing
        
        # A. 計算總量與 OBI
        bid_vol_sum = sum(quote.bid_volume)
        ask_vol_sum = sum(quote.ask_volume)
        total = bid_vol_sum + ask_vol_sum
        
        current_obi = 0.0
        if total > 0:
            current_obi = (bid_vol_sum - ask_vol_sum) / total
            
        # B. 計算 OFI Flow
        # Flow = sum(diff_bid) - sum(diff_ask)
        diff_bid_sum = sum(quote.diff_bid_vol)
        diff_ask_sum = sum(quote.diff_ask_vol)
        flow_delta = diff_bid_sum - diff_ask_sum

        # --- 3. 更新狀態 (Hybrid Write) ---
        
        if ts > self.max_seen_ts:
            self.max_seen_ts = ts
        
        # [OFI] Check Tag Mismatch (New Millisecond or Wrap Around)
        if self.ofi_ts_buffer[idx] != ts:
            self.ofi_buffer[idx] = 0.0 # Reset
            self.ofi_ts_buffer[idx] = ts # Update Tag
        
        self.ofi_buffer[idx] += flow_delta
        
        # [Heap Optimization] Push only if not already tracked for this generic slot-time
        # 注意: 這裡的 Key 是 (ts, idx)。因為 Buffer 覆蓋機制，我們只關心 "該時間點的該 Slot 有資料"。
        if (ts, idx) not in self.active_push_set:
            heapq.heappush(self.pending_ofi_heap, (ts, idx))
            self.active_push_set.add((ts, idx))
        
        # [OBI] Check Tag Mismatch
        if self.obi_ts_buffer[idx] != ts:
            self.obi_sum_buffer[idx] = 0.0
            self.obi_count_buffer[idx] = 0
            self.obi_ts_buffer[idx] = ts
            
        self.obi_sum_buffer[idx] += current_obi
        self.obi_count_buffer[idx] += 1
        
        # 更新最後快照
        self.last_bid_vol_sum = bid_vol_sum
        self.last_ask_vol_sum = ask_vol_sum
        
    def get_metrics(self, tick_ts: int) -> Tuple[float, float, float]:
        """
        取得指定 Tick 時間點的 LOB 指標 (Heap Access).
        """
        
        lag = self.max_seen_ts - tick_ts
        
        # --- OFI 計算：Heap 提取 (Ordered Consumption) ---
        # 優勢: 
        # 1. 快速停止: 若 heap[0].ts > tick_ts，立即停止 loop (O(1))。
        # 2. 自動排序: 總是先處理最早的資料，符合時間序。
        
        accumulated_ofi = 0.0
        
        while self.pending_ofi_heap:
            # Peek minimal timestamp
            min_ts, idx = self.pending_ofi_heap[0]
            
            if min_ts > tick_ts:
                # 遇到未來資料 -> 停止計算 (這些資料屬於更晚的 Tick)
                break
            
            # Pop valid item
            heapq.heappop(self.pending_ofi_heap)
            self.active_push_set.remove((min_ts, idx))
            
            # Check consistency (Wrap around protection)
            if self.ofi_ts_buffer[idx] == min_ts:
                # 加總並清空
                accumulated_ofi += self.ofi_buffer[idx]
                self.ofi_buffer[idx] = 0.0 
                # self.ofi_ts_buffer[idx] = -1 # Keep tag to allow duplicate check if needed, but value is gone.
            
        self.last_read_ts = tick_ts
            
        # --- OBI 計算：時間戳平均 ---
        idx = tick_ts & BUFFER_MASK
        avg_obi = self.last_avg_obi
        
        # [Crucial Check] 確認 Slot 屬於現在這個 Tick 的時間
        if self.obi_ts_buffer[idx] == tick_ts:
            count = self.obi_count_buffer[idx]
            if count > 0:
                avg_obi = self.obi_sum_buffer[idx] / count
                self.last_avg_obi = avg_obi
                
        return avg_obi, accumulated_ofi, float(lag)
