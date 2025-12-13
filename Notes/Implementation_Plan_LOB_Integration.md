# 實作計畫 - 機構級趨勢轉折 LOB 整合 (Institutional Trend Reversal LOB Integration)

本計畫的目標是將 **委託簿 (Order Book / LOB)** 數據整合進 Gale Engine，以實作「趨勢轉折策略」。這涉及創建一個新的 LOB 處理引擎，並計算 **OBI (訂單不平衡)**、**OFI (訂單流意圖)** 與 **Spoofing (假單)** 等機構級指標。

## ⚠️ 用戶審查事項 (User Review Required)

> [!IMPORTANT]
> **架構決策**: 我們將採用 **「Tick 驅動取樣 (Tick-Driven Sampling)」** 的方式。
> `LOBEngine` 會在背景非同步接收 `txf-bidask` 數據並更新內部狀態。
> 但 **指標數值** (OBI, OFI) 只有在 `Tick` 發生時 (`on_tick`) 才會被取樣並存入 **主要 RingBuffer**。
> 這確保了數據與現有的 OHLCV 架構和 Dashboard 完美對齊。

## 建議變更 (Proposed Changes)

### 1. 新增邏輯組件: `LOBEngine`
#### [NEW] [gale/alpha/lob.py](file:///Users/gtai/Projects/txf-gale-engine/gale/alpha/lob.py)
- 建立 `LOBEngine` 類別。
- **狀態 (State)**:
    - 最新 Bid/Ask 價格與量 (5檔) + **委託總量 (Total Vol)**。
    - 最新 `diff` 值與 `diff` 歷史。
    - **最後更新時間 (Last Update Timestamp)**: 用於同步檢核。
- **方法 (Methods)**:
    - `update(quote)`: 
        - 更新內部 `current_state` (OBI)。
        - 寫入 **Time Buckets**: `buckets[ts].ofi += diff`, `buckets[ts].obi = current_state`。
        - 更新 `max_seen_ts = quote.timestamp`。
    - `get_metrics(target_tick_time)`: 
        - **Watermark Guard (水位線機制)**: 
            - 檢查 `max_seen_ts`。
            - 迴圈等待 (Spin Wait)，直到 `max_seen_ts > target_tick_time` (代表該毫秒已結束，數據已收齊)。
            - 設定超時 (e.g. 5ms)，若超時則強制讀取當前值。
        - **Fetch**: 從 `buckets[target_tick_time]` 讀取精確的聚合值。
        - **Cleanup**: 清除 `target_tick_time` 以前的舊 buckets 以釋放記憶體。
    - `detect_spoofing()`: 檢查假單。

### 2. 整合: `IndicatorManager`
#### [MODIFY] [gale/alpha/handler.py](file:///Users/gtai/Projects/txf-gale-engine/gale/alpha/handler.py)
- 匯入並實例化 `LOBEngine`。
- 新增 RingBuffer 欄位: `obi` (L1-5), `total_obi` (全市場), `ofi`。
- 新增 `on_quote(quote)` 方法:
    - 呼叫 `self.lob_engine.update(quote)`。
- 修改 `on_tick`:
    - 呼叫 `self.lob_engine.get_metrics(tick.timestamp)`。
    - 寫入 `self.history`。若 Lag 過大，該筆設為 `np.nan` 或沿用上一筆。

### 3. 整合: Dashboard Runner
#### [MODIFY] [bin/start_dashboard.py](file:///Users/gtai/Projects/txf-gale-engine/bin/start_dashboard.py)
- **新增 LOB 執行緒**:
    - 初始化 `GaleKafkaConsumer` 訂閱 `txf-bidask`。
    - 關鍵參數: `max.poll.interval.ms` 調小，確保高頻更新。
    - 在獨立執行緒中跑迴圈接收報價 (Quotes)。
    - 將報價餵給 `self.manager.on_quote(quote)`。
- **同步機制**:
    - 確保 `LOBEngine` 使用 `threading.Lock` (類似 MicrostructureEngine)。
    - 這允許安全的並發存取：寫入者 (Kafka Thread) vs 讀取者 (Tick Sync Thread)。

### 4. Server 整合
> [!NOTE]
> 目前不需要修改 `gale/feed/server.py`，因為我們選擇直接在 Dashboard 端消費 LOB 數據，以降低基礎建設的複雜度 (不需要改動 Shared Memory 結構)。

## 驗證計畫 (Verification Plan)

### 自動化測試 (Automated Tests)
- **單元測試 `LOBEngine`**:
    - 餵入已知 `diff` 的合成 `BidAsk` 數據。
    - 驗證 `OBI` 與 `OFI` 計算結果是否正確。
    - 驗證 Spoofing 偵測邏輯 (例如：價格上漲時 Ask 增加且 Bid 撤單)。

### 手動驗證 (Manual Verification)
- **重播測試 (Replay Test)**:
    - 修改 `tools/inspect_bidask.py` 使用新的 Engine 進行測試。
    - 或使用 `Session: Replay` 模式運行完整 Server。
    - 觀察 Log 輸出，驗證價格轉折時是否出現 `OBI` 訊號。
