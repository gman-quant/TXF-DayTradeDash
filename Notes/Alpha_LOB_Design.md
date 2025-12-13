# 📊 LOB Alpha Design: 從目標回推架構 (Working Backward)

為了避免「為了存而存」，我們先定義**頂級機構 (HFT/Quant)** 如何使用這份 LOB 資料，再來決定最輕量化的系統架構。

我們擁有的武器 (Schema)：
*   5 檔價格/掛單量 (`Price`, `Volume` L1-L5)
*   **掛單變化量 (`diff_bid/ask_vol`)** 🔥 (這是最關鍵的欄位，代表撤單/新增單的意圖)

## 1. 核心指標 (Priority 1)

### A. OBI (Order Book Imbalance)
*   **定義**：多空掛單的不平衡程度。
*   **公式**：$$ OBI = \frac{\sum V_{Bid} - \sum V_{Ask}}{\sum V_{Bid} + \sum V_{Ask}} $$
*   **用途**：
    *   **極短線預測 (Tick Level)**：當 OBI > 0.3，下一筆成交向上掃單的機率 > 65%。
    *   **濾網 (Filter)**：只在 OBI 支持方向時進場 (順勢)。
*   **效能需求**：極高頻。每秒可能更新 10~50 次。需在 `QuoteBuffer` 上直接計算。

### B. 流動性堆積 (Liquidity Walls / Resting Orders)
*   **定義**：偵測某個價位是否有異常大的掛單 (e.g. 500 口)。
*   **用途**：
    *   **阻力/支撐**：大單擋路，價格難以突破 (Resistance)。
    *   **引力效應 (Magnet Effect)**：若價格接近大單，反而會加速吸過去 (測試流動性)。
*   **視覺化**：需要在 Dashboard 右側繪製 **Depth Chart (深度圖)**。

### C. 假單撤單偵測 (Spoofing / Cancellation)
*   **利用 `diff_vol`**：
    *   如果 `Price` 上漲前，上方賣壓 (`Ask Vol`) 突然大量 `diff_vol < 0` (撤單)，代表早上的賣壓是假的 (Spoofing)。
    *   這是一個強力的**趨勢延續訊號**。

---

## 2. 系統架構影響 (Architecture Implications)

如果我們的目標是以上三者：

### ❌ 舊思維 (全部存下來)
*   把每一筆 LOB update 都畫在主圖上？
*   **結果**：Dashboard 會卡死。人眼跟不上每秒 50 次的更新。

### ✅ 新思維 (Event-Driven Sampling)
我們只需要在 **「關鍵時刻」** 取樣 LOB 數據：

1.  **On-Trade (成交觸發)**：
    *   每當有一筆成交 (`Tick`) 進來時，去抓當下的 `OBI` 和 `Depth`。
    *   這讓我們知道：**「這一筆買單是撞在牆上 (Thick Book)，還是切豆腐 (Thin Book)？」**
    *   這就是 **Dual Ring Buffer** 強大的地方：Tick 驅動，去 Query Quote。

2.  **On-Significantmap-Change (重大變化觸發)**：
    *   只有當 OBI 劇烈變化 (e.g. 從 +0.5 變成 -0.5) 或大單出現時，才發送事件給前端。
    *   這可以大幅降低傳輸量。

## 3. 結論：我們需要運算的指標

為了即時監控，建議優先實作這三個「合成指標」，而不是傳送原始數據：

1.  **`OBI_Stream`** (Time Series): 畫在副圖，看多空意願消長。
2.  **`Depth_Snapshot`** (Last State): 畫在右側，看當下牆在哪裡。
3.  **`Order_Flow_Intent`** (Event): 利用 `diff_vol` 計算出的「淨撤單量」，判斷虛假掛單。

這樣我們的 **Dual Ring Buffer** 設計依然完美適用，且 Dashboard 只需要從 Buffer 抽樣計算即可，負擔極低。
