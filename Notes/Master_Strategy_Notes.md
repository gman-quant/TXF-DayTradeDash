# TXF Gale Engine: Technical Strategy Reference
> **Objective Analysis of Microstructure Signals & System Limitations**

此文件旨在以客觀、批判性的角度，定義系統儀表板所提供的資訊優勢與其內在缺陷。本系統並非預測未來的工具，而是針對當下市場狀態 (Market State) 的機率評估系統。

---

## Ⅰ. 儀表板資訊架構 (Dashboard Information Architecture)

### Pane 1: 價格與價值偏離 (Price Context)
*   **數據**: 5秒 K線 (5s OHLC) + VWAP Bands (Stdev +/- 2.0).
*   **功能**: 這裡顯示的是「結果」而非「原因」。
*   **統計特性**: 價格在 95% 的時間內會位於 VWAP +/- 2.0 SD 之間。
*   **危險**: 在極端趨勢日 (Trend Day)，價格可能沿著 +2.0 SD 連續運行數小時，此時「回歸策略」失效。

### Pane 2: 成交意圖與積極度 (Aggression)
*   **數據**: Lot Size分類 (Red/Cyan/Blue) + CVD (Yellow Line).
*   **功能**: 顯示市場參與者的積極程度。
    *   **Mega Lot (Blue, >=15)**: 代表有能力的資金 (Smart Money) 或激進的停損盤。
    *   **CVD Slope**: 代表市價單 (Market Order) 的淨流向。
*   **缺陷**: 
    1.  **滯後性**: 必須等待成交發生才能確認。
    2.  **噪音**: 散戶的連續小單有時會誤導 CVD 方向。

### Pane 3: 潛在流動性 (Liquidity Inventory)
*   **數據**: CumOBI (Cyan Area) + CumOFI (Gold Area).
*   **功能**: 顯示 Order Book (LOB) 中的掛單佈局。
*   **理論**: 掛單堆積的方向往往產生「吸力 (Magnet)」或「支撐 (Wall)」。
*   **缺陷 (重要)**: **掛單可隨時撤銷 (Spoofing)**。歷史回測顯示 OBI 與未來價格的相關性顯著，但在實時交易中存在高度不確定性。

---

## Ⅱ. 策略假設與驗證 (Strategic Hypotheses)

### 假設 A：均值回歸 (Mean Reversion)
*   **邏輯**: 當價格觸及統計極端 (VWAP Bands) 且動能衰竭時，價格應回歸均值。
*   **訊號結構**:
    1.  Price @ VWAP Band (極端位置)。
    2.  **Absorption**: Pane 2 顯示大量市價單 (CVD) 攻擊但價格停滯。
    3.  **OBI Support**: Pane 3 顯示反向掛單增加 (接刀)。
*   **失效模式 (Failure Mode)**:
    *   **強趨勢 (Strong Trend)**: CVD 呈現垂直攻擊，完全無視吸收。此時逆勢操作期望值為負。

### 假設 B：動能跟隨 (Momentum Scapling)
*   **邏輯**: 當大單 (Mega Lot) 連續出現時，短線上價格具有慣性。
*   **訊號結構**:
    1.  Pane 2 出現連續藍色柱狀體。
    2.  CVD 斜率陡峭。
*   **失效模式 (Failure Mode)**:
    *   **假突破 (False Breakout)**: 大單進場後流動性瞬間枯竭，導致價格快速回落。
    *   **滑價風險**: 追逐動能通常意味著市價進場，需承受較高的滑價成本。

---

## Ⅲ. 系統性風險評估 (Systemic Risk Assessment)

### 1. 虛假訊號風險 (Spoofing Risk)
Pane 3 的 CumOBI 極易受操縱。主力可掛出大量虛假買單誘使系統發出「偏多」訊號，隨後撤單。
*   **對策**: **不信任單一指標**。若 OBI 上升但 Pane 2 (CVD) 未見實質買盤，應視為誘多陷阱。

### 2. 時序因果風險 (Causality Risk)
Kafka 數據傳輸存在毫秒級延遲。在極端快市中，我們看到的 LOB 狀態可能是「成交後」的殘影。
*   **對策**: 避免在该级別 (Tick-level) 進行全倉博弈。承認數據的不完美。

---

## Ⅳ. 資金控管與執行 (Execution & Capital Management)

基於上述風險，38 萬本金的 **2026 年執行計畫** 應建立在「容錯」而非「預測」之上。

### 核心原則：以微台 (Micro) 換取生存空間
由於系統訊號存在 30%~40% 的雜訊 (False Positives)，單次重押 (如 1 口大台) 將面臨極高的破產風險 (Ruin Risk)。

### 建議配置
*   **試單 (Testing)**: 使用 2~4 口微台。這是為了測試 Pane 2 的動能是否真實。
*   **加碼 (Pyramiding)**: 僅在脫離成本區且訊號持續 (Blue Bars 持續) 時，才加碼至小台規模。
*   **停損 (Stop Loss)**: 
    *   **時間停損**: 進場 15 秒價格未發動，即視為假設錯誤，市價平倉。
    *   **價格停損**: 跌破關鍵支撐 (Pane 3 的大量掛單區) 即離場。

### 結論
此系統提供的是 **「統計學上的微弱優勢 (Statistical Edge)」**，而非必勝的預測。獲利的關鍵在於嚴格執行「失敗時的小賠 (微台停損)」與「成功時的加碼 (波段持有)」。
