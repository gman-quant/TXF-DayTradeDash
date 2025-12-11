# 分析筆記：動態 Volume Profile 策略邏輯與微結構分析
> **Target Audience**: Quantitative Researchers, HFT Strategists, Institutional Traders
> **Context**: High-Frequency Market Microstructure Analysis for TXF (Taiwan Futures)

---

## 1. 核心定義 (Core Definitions)

在量化交易與機構視角中，Volume Profile 不僅僅是「支撐壓力的畫線工具」，它是 **「拍賣市場理論 (Auction Market Theory, AMT)」** 的數據化體現。我們不再視價格為單一變數，而是視為 **價格 (Price) + 時間 (Time) + 成交量 (Volume)** 的三維分佈矩陣。

### A. Volume Profile 類型
1.  **SVP (Session Volume Profile)**: 全域視角，用於定義當日的主戰場 (Fair Value) 與價值區間。
2.  **VRVP (Visible Range Volume Profile)**: 微觀視角，隨著時間軸縮放動態重算。這是 HFT 最關注的層級，用於捕捉短線的 Order Flow Imbalance。
3.  **FRVP (Fixed Range Volume Profile)**: 事件視角，針對特定事件 (e.g. 數據公佈、起漲點) 進行錨定分析。

### B. 關鍵統計量 (Key Statistics)
我們採用標準常態分佈 (Normal Distribution) 的概念來定義市場價值：
*   **POC (Point of Control)**: 機率密度函數 (PDF) 的 **Mode (眾數)**。代表市場共識最強、成交效率最高 (High Liquidity) 的價格。
*   **VA (Value Area)**: 涵蓋 **70%** 總成交量的區間 (約等於 1 個標準差)。
    *   **VAH (Value Area High)**: 價值區上緣 (壓力/超買界線)。
    *   **VAL (Value Area Low)**: 價值區下緣 (支撐/超賣界線)。
*   **HVN / LVN (High/Low Volume Nodes)**:
    *   **HVN**: 高流動性區域，價格傾向在此停留 (Magnet Effect)。
    *   **LVN**: 低流動性區域，由市價單 (Market Orders) 快速掃過，價格傾向快速通過或強烈拒絕 (Rejection)。

---

## 2. Delta Profile 與微結構解讀 (Microstructure Analysis)

單純的總量 (Total Volume) 只能告訴我們「哪裡有交易」，而 **Delta (Buy - Sell)** 才能告訴我們 **「誰在主導」**。

### A. Delta Profile 可視化邏輯
我們採用 **"Stacked Imbalance Model"**：
*   **Bar Length**: Total Volume (流動性深度)。
*   **Red Segment**: Aggressive Sell Volume (主動賣盤)。
*   **Green Segment**: Aggressive Buy Volume (主動買盤)。

### B. 高階異常訊號：吸收 (Absorption) 與受困 (Trapped)
這是法人與主力最常使用的操盤手法，也是 HFT 策略的 Alpha 來源。

#### 1. 買盤受困 (Trapped Buyers) / 被動賣出吸收 (Passive Sell Absorption)
*   **現象**：價格創高 (High) 或在阻力位，Delta Profile 顯示 **極端買超 (Aggressive Buy >>> Sell)**，但 **價格不漲反跌**。
*   **微結構機制**：
    *   散戶/動能交易者看到價格上漲，瘋狂敲進 **市價買單 (Market Buy)**。
    *   主力/造市商在該價位掛出巨量的 **限價賣單 (Iceberg Limit Sell)**。
    *   所有的主動買盤都被這道「看不見的牆」吃掉 (Absorbed)。
*   **交易訊號**：這是頂部確立的強烈訊號。一旦買盤力竭，價格將崩跌 (Long Liquidation)。

#### 2. 賣盤被吸收 (Absorption) / 賣盤受困 (Trapped Sellers)
*   **現象**：價格破底 (Low) 或在支撐位，Delta Profile 顯示 **極端賣超 (Aggressive Sell >>> Buy)**，但 **價格跌不下去**。
*   **微結構機制**：
    *   恐慌性停損或追空者瘋狂敲進 **市價賣單 (Market Sell)**。
    *   主力在下方掛出巨量的 **限價買單 (Limit Buy)** 接手。
*   **交易訊號**：這是底部確立的訊號。當賣壓耗盡 (Seller Exhaustion)，價格將報復性反彈 (Short Covering)。

> **HFT Insight**: 當您看到顏色 (Delta) 與價格走勢 (Price Action) 背離時，**相信顏色 (Order Flow)**。因為市價單 (顏色) 代表意圖，而價格只是結果。意圖受阻，結果必將反轉。

---

## 3. 拍賣市場理論之圖形判讀 (Profile Shapes)

透過當日 Volume Profile 的形狀，我們可以識別當日的 **Market Regime (市場體制)**。

### 1. P型分佈 (P-Shape) —— 空頭回補 (Short Covering)
*   **特徵**：頭重腳輕。上半部寬廣 (High Volume)，下半部細長 (Low Volume/Single Prints)。
*   **成因**：開盤後價格迅速拉升 (Short Covering Rally)，隨後在高檔區間進行價值交換與換手。
*   **機構視角**：這通常發生在**上升趨勢的延續**或**空頭不死心的回補**。下方的細長區域是 LVN，代表多方強勢防守，價格不應輕易回到該處。

### 2. b型分佈 (b-Shape) —— 多頭停損 (Long Liquidation)
*   **特徵**：頭輕腳重。上半部細長，下半部寬廣。
*   **成因**：開盤後遭受恐慌性賣壓 (Panic Selling)，價格快速尋找底部，直到在低檔區找到買盤承接並開始盤整。
*   **機構視角**：這是標準的**弱勢盤**。主力在低檔吸籌，但尚未發動攻擊。任何反彈至 b 字上緣 (頸線) 通常會遭遇「舊多頭」的解套賣壓。

### 3. D型分佈 (D-Shape) —— 平衡市 (Balanced Market)
*   **特徵**：標準鐘形曲線 (Normal Distribution)。中間胖，兩頭瘦。
*   **成因**：買賣雙方勢均力敵，市場對於「公允價格 (Fair Price)」有高度共識。
*   **機構視角**：**區間震盪策略 (Mean Reversion)** 的最佳戰場。
    *   策略：Fade Extremes。價格觸及 VAH 做空，觸及 VAL 做多，目標價設為 POC。切忌追價。

### 4. B型分佈 (B-Shape / Double Distribution) —— 趨勢市 (Trend Day)
*   **特徵**：兩個獨立的鐘形，中間透過一條細長的 LVN 連接。
*   **成因**：市場對價值產生了「重新定價 (Repricing)」。早盤在一個區間，隨後發生突破，價格迅速遷移至新的區間並重新建立平衡。
*   **機構視角**：這是利潤最豐厚的**趨勢日**。中間的 LVN 是單向通道，一旦突破不應回頭。策略應由 Mean Reversion 轉為 Momentum (動能跟隨)。

---

## 4. 系統實作邏輯 (System Implementation)

為了支撐上述的高頻分析，`txf-gale-engine` 採用了極致效能的架構設計：

### A. 數據與運算 (Vectorized Backend)
*   **Numpy & Numba Engine**: 不使用迴圈，全向量化運算。Delta Profile 的買賣分流 (Split) 採用 `O(1)` 的直接索引更新 (Index Mapping)，確保 Zero-Overhead。
*   **Stateful Accumulation**: 不同於傳統指標的 Rolling Window，VP 採用 **Spatial Accumulation (空間累積)**，將價格軸 (Spatial) 視為 Hash Map 的 Key。

### B. 動態渲染 (Smart Rendering)
*   **Overlay Mode Trick**: 為了解決 Plotly `stack` 模式與其他指標的衝突，我們開發了 **"Layering Rendering"** 技術。
    *   **Layer 1 (Bottom)**: 繪製 Total Volume (Green/Buy Color)。
    *   **Layer 2 (Top)**: 繪製 Sell Volume (Red)，直接覆蓋於 Layer 1 之上。
    *   **Result**: 視覺上呈現完美的 `[Sell | Buy]` 堆疊效果，且保持 `hovermode='closest'` 下的精準數據互動 (透過 `customdata` 綁定真實數值)。

---

> **Summary**: 本系統已不僅是看盤軟體，而是具備 **HFT 等級微結構分析能力** 的量化工作站。請善用 Delta Profile 的「吸收」訊號與 Profile Shape 的「架構」解讀，以獲得法人級的市場洞察。
