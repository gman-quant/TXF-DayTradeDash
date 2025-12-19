# TXF Gale Engine: System Technical Reference

本文件旨在提供系統各項指標的客觀技術定義、功能說明、使用場景及優劣勢分析，作為量化分析與策略開發的基礎文檔。

---

## 1. 微結構指標 (Microstructure Metrics)

### 1.1 Cumulative Order Flow Imbalance (Cum OFI)
*   **定義**：計算訂單流的不平衡淨額。公式：$\sum (e_n \cdot q_n)$，其中 $e_n$ 為買賣方向，$q_n$ 為掛單變化量 (包含市價單成交與撤單)。本系統採用 **Level 1-5 Deep OFI** 算法。
*   **功能**：量化市場的「積極買賣壓」與「撤單意圖」。
*   **用途**：
    *   判斷短期價格趨勢的動能方向。
    *   偵測價格與資金流的背離 (Divergence)。
*   **SWOT 分析**：
    *   **優勢 (Pros)**：比單純的成交量 (Volume) 更早反應市場意圖，具備領先性。
    *   **劣勢 (Cons)**：在極低流動性或主力刻意刷單 (Wash Trade) 時可能出現雜訊。

### 1.2 Cumulative Order Book Imbalance (Cum OBI)
*   **定義**：計算訂單簿的靜態不平衡比率。公式：$\sum_{t} \frac{BidQty_t - AskQty_t}{BidQty_t + AskQty_t}$。數值範圍為 -1 (全賣壓) 至 +1 (全買撐)。
*   **功能**：量化特定時間點的「掛單深度」與「流動性供給」。
*   **用途**：
    *   識別支撐 (Support) 與壓力 (Resistance) 的厚度。
    *   偵測流動性突然抽離 (Liquidity Withdrawal / Vacuum) 的事件。
*   **SWOT 分析**：
    *   **優勢 (Pros)**：能直接看見非成交的潛在掛單 (Visible Liquidity)。
    *   **劣勢 (Cons)**：易受 Spoofing (虛假掛單) 影響，需配合價格行為確認。

### 1.3 LOB Lag (Data Latency)
*   **定義**：系統最新報價時間戳 (`max_seen_ts`) 與當前處理成交時間戳 (`tick_ts`) 的差值。
*   **功能**：監控數據源的時效性與系統處理效能。
*   **用途**：
    *   正值 (>0)：確保策略決策基於最新或未來的資訊。
    *   負值 (<0)：警示數據過期，策略應暫停。
*   **SWOT 分析**：
    *   **優勢 (Pros)**：防止在數據延遲時做出錯誤決策的「保險絲」。
    *   **劣勢 (Cons)**：僅反映系統狀態，非交易濾網。

---

## 2. 成交量與分布 (Volume & Profile)

### 2.1 Volume Profile (VP)
*   **定義**：將成交量依照「價格」而非時間進行堆疊的直方圖。
*   **功能**：顯示市場在不同價格水平的交易興趣與價值認同。
*   **關鍵組件**：
    *   **POC (Point of Control)**：最大成交量價格 (公平價值)。
    *   **VA (Value Area)**：70% 成交量分佈區間 (震盪區)。
*   **用途**：識別強支撐/壓力位 (High Volume Node) 與價格加速區 (Low Volume Node)。
*   **SWOT 分析**：
    *   **優勢 (Pros)**：提供價格的「空間」情境，過濾掉無意義的盤整。
    *   **劣勢 (Cons)**：對歷史長度敏感，不同時間窗口 (Window) 會產生不同形狀。

### 2.2 VWAP Bands (Volume Weighted Average Price)
*   **定義**：成交量加權平均價格及其標準差通道 (StdDev Bands)。
*   **功能**：提供機構法人的平均持有成本與統計學上的極端價格邊界。
*   **用途**：
    *   **回歸策略 (Mean Reversion)**：價格觸及外軌 (2.0 SD) 時反向操作。
    *   **趨勢確認**：價格持續在中軸之上為多頭結構。
*   **SWOT 分析**：
    *   **優勢 (Pros)**：機構演算法最常用的基準指標，具備自我實現性。
    *   **劣勢 (Cons)**：在強烈單邊趨勢中，價格可能長期沿著外軌鈍化。

---

## 3. 籌碼與動能 (Flow & Momentum)

### 3.1 Cumulative Volume Delta (CVD)
*   **定義**：主動買入成交量減去主動賣出成交量的累計值。
*   **功能**：反映市場實際成交的淨買賣力道 (Aggressive Volume)。
*   **用途**：確認價格趨勢是否有實質成交量支撐 (Volume Confirmation)。
*   **SWOT 分析**：
    *   **優勢 (Pros)**：無法造假，每一筆都是真金白銀的成交。
    *   **劣勢 (Cons)**：屬滯後指標 (Lagging)，通常同時或晚於價格反應。

### 3.2 Trade Sizing (Large/Mega Lots)
*   **定義**：依據單筆成交口數過濾交易。
    *   **Small**: < 5口 (散戶)
    *   **Mega**: >= 15口 (主力/法人)
*   **功能**：拆解不同市場參與者的行為意圖。
*   **用途**：跟隨大單方向 (Smart Money)，利用散戶反向指標。
*   **SWOT 分析**：
    *   **優勢 (Pros)**：有效過濾雜訊，直接觀察主力動向。
    *   **劣勢 (Cons)**：主力可能使用拆單 (Slicing) 演算法隱藏大單蹤跡。
