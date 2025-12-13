import math
import logging
from collections import defaultdict
from typing import Tuple, Dict

# Config
MAX_BUCKET_HISTORY = 50000     # Keep last 50k buckets (large buffer for high throughput sync)

class LOBEngine:
    """
    機構級 LOB (Level Order Book) 核心運算引擎 (Ingestion Version)。
    
    Architecture for Ingestion Integration:
    1. **Burst Aggregation (微秒級聚合)**:
       同一毫秒內的所有 Quote 變化 (diff) 會被加總 (OFI)，
       狀態 (OBI) 取該毫秒最後一筆。
       
    2. **Passive Query (被動查詢)**:
       不負責等待或鎖定。由 IngestServer 的 Sequencer 決定何時查詢。
    """
    
    def __init__(self):
        self.logger = logging.getLogger("LOBEngine")
        
        # --- State ---
        self.max_seen_ts = 0  # 目前看到的最新 Quote 時間 (Watermark)
        
        # Buckets: timestamp_ms -> { 'ofi': float, 'obi': float, 'count': int }
        # 用於暫存每一毫秒的聚合結果
        self.buckets: Dict[int, dict] = defaultdict(lambda: {'ofi': 0.0, 'obi': 0.0, 'count': 0})
        
        # Latest Snapshot (用於找不到 Bucket 時的回退 / 或計算 OBI)
        self.last_bid_vol_sum = 0
        self.last_ask_vol_sum = 0
        
    def update(self, quote):
        """
        接收 Kafka BidAsk (Quote) 訊息並更新內部狀態。
        """
        ts = quote.timestamp_ms
        
        # 計算此筆 Quote 的 OBI (State)
        # OBI = (Bid - Ask) / (Bid + Ask)
        bid_vol_sum = sum(quote.bid_volume)
        ask_vol_sum = sum(quote.ask_volume)
        total = bid_vol_sum + ask_vol_sum
        
        current_obi = 0.0
        if total > 0:
            current_obi = (bid_vol_sum - ask_vol_sum) / total
            
        # 計算此筆 Quote 的 OFI Flow (diff)
        # Flow = sum(diff_bid) - sum(diff_ask)
        diff_bid_sum = sum(quote.diff_bid_vol)
        diff_ask_sum = sum(quote.diff_ask_vol)
        
        flow_delta = diff_bid_sum - diff_ask_sum

        # 1. Update Watermark
        if ts > self.max_seen_ts:
            self.max_seen_ts = ts
        
        # 2. Aggregation into Bucket
        bucket = self.buckets[ts]
        bucket['ofi'] += flow_delta      # Flow 是累加的
        bucket['obi'] = current_obi      # State 是覆寫的 (取最後一筆)
        bucket['count'] += 1
        
        # Update cache
        self.last_bid_vol_sum = bid_vol_sum
        self.last_ask_vol_sum = ask_vol_sum
        
        # 3. Opportunistic Cleanup (每 1000 筆做一次，或檢查 size)
        if len(self.buckets) > MAX_BUCKET_HISTORY:
            # 清除比當前 ts 舊太多的 bucket
            threshold = ts - (MAX_BUCKET_HISTORY // 2)
            keys_to_remove = [k for k in self.buckets if k < threshold]
            for k in keys_to_remove:
                del self.buckets[k]

    def get_metrics(self, tick_ts: int) -> Tuple[float, float, float]:
        """
        取得指定 Tick 時間點的 LOB 指標。
        
        Returns:
            (obi, ofi, lag_ms)
        """
        # Check Lag (Tick vs Latest Quote)
        lag = self.max_seen_ts - tick_ts
        
        if tick_ts in self.buckets:
            # 完美命中 (Hit)
            bucket = self.buckets[tick_ts]
            return bucket['obi'], bucket['ofi'], float(lag)
        else:
            # 沒命中 (Miss) -> Tick 發生在安靜的毫秒
            # OBI 應該沿用前一筆 (Holding the state)
            # OFI 應該是 0 (没有 Flow 發生)
            
            last_total = self.last_bid_vol_sum + self.last_ask_vol_sum
            last_obi = 0.0
            if last_total > 0:
                last_obi = (self.last_bid_vol_sum - self.last_ask_vol_sum) / last_total
                
            return last_obi, 0.0, float(lag)
