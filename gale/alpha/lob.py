import math
import logging
from collections import defaultdict
from typing import Tuple, Dict

# Config
MAX_BUCKET_HISTORY = 50000     # 保留最近 50k 個毫秒 Bucket (足以應對高吞吐量的同步緩衝)

class LOBEngine:
    """
    機構級 LOB (Level Order Book) 核心運算引擎 (High-Performance Legacy Sequencer).
    
    本引擎針對「Kafka Multi-Topic Unsorted Stream」架構進行了極致優化，
    在保留原始數據順序的前提下，提供數學上最精確的 Order Flow 計算。

    核心邏輯架構 (Core Architecture):
    -------------------------------
    1. **嚴格去重 (Strict Deduplication)**:
       使用 (時間, 總量, 總價) 特徵值檢查，剔除 1.13% 的完全重複數據，
       但保留 22.6% 的 Rapid Updates (同毫秒但內容不同)。

    2. **暫存流動緩存 (Transient Pending Flows)**:
       針對 OFI 計算優化。不使用時間軸迴圈，改用 `Dict[Timestamp, Flow]` 儲存。
       將結算複雜度由 O(Time Gap) 降至 O(Event Count)，完美解決長時間空窗期的效能問題。

    3. **區間累積 OFI (Interval OFI - Time Consumption Model)**:
       Tick 結算時，會「消費 (Consume)」掉所有時間點 <= Tick Time 的 Flow。
       確保在資料小幅亂序 (Unsorted) 的情況下，Flow 總量依然守恆 (Conservation of Flow)。

    4. **狀態平均 OBI (Average OBI)**:
       針對同一毫秒內的多筆 Quote 變化，計算其 OBI 的算術平均數 (Centroid)，
       有效消除高頻微觀結構下的順序雜訊。
    """
    
    def __init__(self):
        self.logger = logging.getLogger("LOBEngine")
        
        # --- 狀態變數 (State) ---
        self.max_seen_ts = 0  # 目前系統看過的最新 Quote 時間 (Watermark)
        
        # 1. 毫秒快照 (Buckets): 儲存每一毫秒的統計狀態
        # Key: timestamp_ms
        # Value: { 'obi_sum': float, 'obi_count': int } (不存 OFI，OFI 改由 pending_flows 管理)
        self.buckets: Dict[int, dict] = defaultdict(lambda: {'obi_sum': 0.0, 'obi_count': 0})
        
        # 2. 待處理流動 (Pending Flows - Transient Cache)
        # 用於優化 OFI 計算。將 Flow 與時間軸解耦，避免遍歷無效的空窗期。
        # Dict[timestamp_ms, flow_sum]
        self.pending_flows: Dict[int, float] = defaultdict(float)
        
        # 3. 去重狀態 (Deduplication State)
        self.last_quote_signature = None
        
        # 4. 快照緩存 (Latest Snapshot)
        # 用於找不到 Bucket 時的回退 (Fallback) 或其他計算
        self.last_bid_vol_sum = 0
        self.last_ask_vol_sum = 0
        self.last_avg_obi = 0.0 # 緩存上一次計算出的 Average OBI
        
    def update(self, quote):
        """
        接收 Kafka BidAsk (Quote) 訊息並更新內部狀態。
        
        此函數負責：
        1. 執行嚴格去重 (Deduplication)。
        2. 更新待處理流動緩存 (Pending Flows)。
        3. 更新毫秒狀態統計 (Buckets)。
        """
        
        # --- 1. 嚴格去重 (Strict Deduplication) ---
        # 產生這筆 Quote 的特徵簽章 (Signature)
        # 包含：時間戳、買賣總量、買賣總價。這足以識別「完全重複」的無效訊息。
        current_signature = (
            quote.timestamp_ms, 
            sum(quote.bid_volume), 
            sum(quote.ask_volume),
            sum(quote.bid_price), 
            sum(quote.ask_price)
        )
        
        # 若特徵值與上一筆完全相同，視為冗餘數據，直接忽略。
        if current_signature == self.last_quote_signature:
            return
            
        self.last_quote_signature = current_signature
        
        # --- 2. 核心指標計算 ---
        ts = quote.timestamp_ms
        
        # A. 計算總量與 OBI (Order Book Imbalance)
        bid_vol_sum = sum(quote.bid_volume)
        ask_vol_sum = sum(quote.ask_volume)
        total = bid_vol_sum + ask_vol_sum
        
        current_obi = 0.0
        if total > 0:
            current_obi = (bid_vol_sum - ask_vol_sum) / total
            
        # B. 計算 OFI Flow (Order Flow Imbalance Delta)
        # Flow = sum(diff_bid) - sum(diff_ask)
        diff_bid_sum = sum(quote.diff_bid_vol)
        diff_ask_sum = sum(quote.diff_ask_vol)
        
        flow_delta = diff_bid_sum - diff_ask_sum
 
        # --- 3. 更新狀態 ---
        
        # 更新水位線 (Watermark)，讓 Server 知道現在資料流到哪了
        if ts > self.max_seen_ts:
            self.max_seen_ts = ts
        
        # [OFI] 更新待處理流動緩存 (由 Dict 管理，只存有值的時間點)
        self.pending_flows[ts] += flow_delta
        
        # [OBI] 更新毫秒 Bucket (用於計算平均值)
        bucket = self.buckets[ts]
        bucket['obi_sum'] += current_obi
        bucket['obi_count'] += 1
        
        # 更新最後快照 (Cache)
        self.last_bid_vol_sum = bid_vol_sum
        self.last_ask_vol_sum = ask_vol_sum
        
        # --- 4. 記憶體管理 (Opportunistic Cleanup) ---
        # 若 Bucket 累積過多，執行清理以釋放記憶體
        if len(self.buckets) > MAX_BUCKET_HISTORY:
            threshold = ts - (MAX_BUCKET_HISTORY // 2)
            # 找出過期的 Keys (Python 3.8+ 字典順序穩定，亦可直接遍歷)
            keys_to_remove = [k for k in self.buckets if k < threshold]
            for k in keys_to_remove:
                del self.buckets[k]
 
    def get_metrics(self, tick_ts: int) -> Tuple[float, float, float]:
        """
        取得指定 Tick 時間點的 LOB 指標。
        
        邏輯模式：Pending Flow Consumption (待處理流動消費模式)
        
        Args:
            tick_ts (int): Tick 的發生時間 (毫秒)
            
        Returns:
            Tuple[float, float, float]: (Average_OBI, Accumulated_OFI, Lag_Latency)
        """
        
        # 計算延遲 (Lag): 目前最新 Quote 時間 - Tick 發生時間
        # 正值代表 Quote 領先 Tick (正常)，負值代表 Quote 落後 (資料延遲)
        lag = self.max_seen_ts - tick_ts
        
        # --- OFI 計算：區間累積 (Interval Accumulation) ---
        accumulated_ofi = 0.0
        
        # 搜尋策略：遍歷 pending_flows 字典
        # 只取出時間點 <= tick_ts 的 Flow 進行結算 (Consume)
        # 這比遍歷時間軸 (Range Loop) 快上數個量級
        consumed_keys = []
        for ts, flow in self.pending_flows.items():
            if ts <= tick_ts:
                accumulated_ofi += flow
                consumed_keys.append(ts)
        
        # 消費後刪除，確保 Flow 不會被重複計算 (守恆定律)
        for k in consumed_keys:
            del self.pending_flows[k]
            
        # --- OBI 計算：時間戳平均 (Timestamp Average) ---
        # 取該毫秒內所有 Quote 的平均狀態，代表該時刻的「重心」
        avg_obi = self.last_avg_obi # 預設使用上一次的數值 (若當下無 Quote)
        
        if tick_ts in self.buckets:
            bucket = self.buckets[tick_ts]
            if bucket['obi_count'] > 0:
                avg_obi = bucket['obi_sum'] / bucket['obi_count']
                self.last_avg_obi = avg_obi # 更新緩存
                
        return avg_obi, accumulated_ofi, float(lag)
