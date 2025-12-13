# 📉 機構級趨勢轉折邏輯與路線圖 (Institutional Trend Reversal Logic & Roadmap)

> **Core Philosophy**: 真正的轉折不僅僅是指標上的「超賣 (Oversold)」。它是 **統計極端值 (Statistical Extremes)**、**流動性耗盡 (Liquidity Exhaustion)** 與 **結構性失衡 (Structural Imbalance)** 三者交會的結果。

---

## 1. 核心邏輯：頂級機構如何識別轉折 (How Institutions Spot Reversals)

### A. 微結構與訂單流 (Microstructure & Order Flow) - 毫秒級戰場
這些訊號代表了市場在毫秒級別的物理機制與真實供需。

1.  **Order Book Imbalance (OBI / LOB 失衡)**
    *   **邏輯**：「流動性牆 (Liquidity Wall)」。當價格急跌時，如果 LOB (Limit Order Book) 的買方 (Bid side) 突然顯著變厚 (掛單量大增)，這物理上阻止了價格進一步下跌。
    *   **訊號**：市場賣單 (Market Sells) 被限價買單 (Limit Buys) 大量吸收 (Absorption)。

2.  **Iceberg Detection (冰山單偵測 / 隱藏流動性)**
    *   **邏輯**：價格停滯在某個 Tick 跳動，儘管有激進的市價單狂敲。Best Ask 表面上只掛了很少的量，但成交量卻不斷累計放大。
    *   **訊號**：「吸收 (Absorption)」。某個大戶正在隱密地重新掛單 (Reloading)。一旦攻擊者的子彈耗盡，價格就會反轉。

3.  **CVD Divergence (累計成交量背離)**
    *   **邏輯**：「努力 vs 結果 (Effort vs Result)」。價格創出新低，但 CVD (Cumulative Volume Delta) 卻呈現較高低點 (Higher Low)。
    *   **訊號**：賣方還在用力賣 (Effort)，但已經推不動價格了 (No Result)，這意味著賣壓衰竭或被完全吸收。

### B. 統計套利 (Statistical Arbitrage) - 數學回歸
量化基金 (Quant Funds) 依賴統計異常值的均值回歸 (Mean Reversion)。

1.  **Basis Reversion (基差回歸)**
    *   **邏輯**：期貨與現貨 (如台指期 vs 加權指數) 的價差 (Basis) 不可能無限擴大。
    *   **訊號**：Z-Score > 3。套利者 (Arbitrageurs) 會進場鎖定價差，強制將價格拉回。

2.  **VWAP Bands (成交量加權平均價乖離)**
    *   **邏輯**：VWAP 是機構法人的「公允價格 (Fair Price)」。
    *   **訊號**：當價格觸及 VWAP +/- 2.0 或 3.0 個標準差 (SD) 時，代表價格處於「流動性真空 (Liquidity Vacuum)」，這種移動通常是不穩定的，極易發生報復性反彈 (Snap back)。

### C. 拍賣市場理論 (Auction Market Theory) - 市場心理

1.  **Failed Auction (拍賣失敗 / Look Above and Fail)**
    *   **邏輯**：價格突破關鍵價位 (例如昨日高點) 但無法吸引新的買盤 (量能萎縮)。
    *   **訊號**：價格被迅速拒絕 (Rejection) 並跌回原本的價值區間。目標價通常會直接殺到區間的另一端。

2.  **Buying/Selling Tails (長尾效應)**
    *   **邏輯**：TPO 或 Volume Profile 圖上出現單一且細長的長下影線。
    *   **訊號**：機構法人在該價位進行了強烈的防守或拒絕 (Rejection)。

---

## 2. 現有能力：如何使用 Gale Engine 實戰

我們的 **TXF Gale Engine (v1.1)** 已經具備實踐上述策略的強大工具。

### ✅ 可用工具 (Available Tools)

| 功能 (Feature) | 引擎組件 (Component) | 狀態 |
| :--- | :--- | :--- |
| **CVD / Delta** | `MicrostructureEngine` | **Ready**. 計算每筆 Tick 的買賣失衡。 |
| **VWAP Bands** | `AlphaEngine` | **Ready**. 支援 Session-aware 的 VWAP +/- 2.0 SD。 |
| **Volume Profile** | `VolumeProfileEngine` | **Ready**. 即時計算 POC/VAH/VAL 價值區。 |
| **Velocity** | `MicrostructureEngine` | **Ready**. 監控交易速率 (Vol/Sec) 以偵測恐慌。 |

### 🛠 實戰策略指南 (Strategy Construction)

#### 策略 1: "力竭背離" (The Exhausted Divergence) - 極短線 (Scalping)
*   **概念**：當微結構顯示「賣壓耗盡」時，接住掉下來的刀子。
*   **觸發條件**: 
    1.  價格 < `VWAP - 2.0 SD` (統計極端)。
    2.  價格創 Session 新低。
    3.  `MicrostructureEngine.imbalance` > -0.2 (賣壓減弱) 或 CVD 斜率轉正。
*   **執行**: 在 Best Bid 掛限價單承接。

#### 策略 2: "價值區防守" (Value Area Defense) - 波段 (Swing)
*   **概念**：在上升趨勢中，回檔到「公允價格」邊界時買進。
*   **觸發條件**:
    1.  價格回測 `VolumeProfileEngine.VAL` (價值區下緣)。
    2.  成交量顯著萎縮 (Dry up / 惜售)。
*   **執行**: 當價格重新勾回價值區內時進場 (Ticket back inside)。

#### 策略 3: "基差收斂" (Basis Arb Snap) - 均值回歸
*   **概念**：簡單的價差套利。
*   **觸發條件**:
    1.  `Tick.close` 與 `Tick.underlying_price` 的價差 > X 點。
    2.  `MicrostructureEngine.velocity` 飆升 (恐慌性殺盤或拉抬)。
*   **執行**: 反向操作 (Fade the move)。

---

## 3. 未來路線圖：最後的拼圖 (The Final Piece: LOB Integration)

現在我們有了 `txf.BidAsk` 數據 (包含 `diff_bid_vol` 與 `diff_ask_vol`)，我們可以實作真正的機構級轉折策略。

### 🚀 Phase 1: 基礎 LOB 指標 (Fundamental LOB Metrics)

| 指標 | 邏輯 (Logic) | 轉折訊號 (Reversal Signal) |
| :--- | :--- | :--- |
| **OBI (Order Book Imbalance)** | `(BidVol - AskVol) / (BidVol + AskVol)` | **背離 (Divergence)**: 價格創新低，但 OBI 底部墊高 (買單掛入接刀)。 |
| **Liquidity Wall (流動性牆)** | 偵測 `Bid/Ask Volume` 中的異常大單 (Outliers)。 | **拒絕 (Rejection)**: 價格觸碰大單價位後迅速反彈 (Ping off the wall)。 |
| **OFI (Order Flow Imbalance)** | $\sum(Bid_{Add} - Bid_{Remove}) - \sum(Ask_{Add} - Ask_{Remove})$ | **淨流量反轉**: 價格還在跌，但 OFI 已經翻正 (Limit Order 積極佈局)。 |

### 🚀 Phase 2: 進階轉折策略 (Advanced Reversal Strategies)

這些策略利用 `diff` 數據來偵測「意圖 (Intent)」。

#### 策略 4: "假突破反轉" (The Spoofing Reversal)
*   **情境**: 價格試圖突破區間上緣。
*   **微結構特徵**:
    1.  價格上漲時，上方 Ask 突然出現大量掛單 (阻力增強)。
    2.  同時，下方 Bid 的支撐單突然 **撤銷 (`diff_bid_vol` < 0)** (虛假支撐消失)。
*   **訊號**: 價格突破失敗 (Look Above and Fail) + Bid 撤單 = **強烈放空訊號**。

#### 策略 5: "吸籌背離" (Absorption Divergence)
*   **情境**: 下跌趨勢末端。
*   **微結構特徵**:
    1.  市價賣單 (CVD 下降) 持續湧入。
    2.  但 `Best Bid` 價格不跌，且 `diff_bid_vol` 持續為正 (Reloading/Iceberg)。
*   **訊號**: CVD 創新低 + Price Higher Low + Bid Reloading = **強烈做多訊號**。

#### 策略 6: "流動性真空回補" (Vacuum Snap-back)
*   **情境**: 發生瞬間崩盤 (Flash Crash) 或急拉。
*   **微結構特徵**:
    1.  某一方的 Book 瞬間被打穿 (Thin Liquidity)。
    2.  隨後快速在遠端出現厚實的掛單 (Providing Liquidity)。
*   **訊號**: V型反轉確認，回補流動性真空區。

---

> **Implementation Priority**:
> 1.  **OBI Stream**: 計算即時多空掛單比。
> 2.  **Depth Map**: 視覺化流動性牆。
> 3.  **Spoofing Detector**: 監控 `diff` 異常撤單。

> **Summary**: 目前我們在 **成交分析 (Trade Analysis)** 上已經很強 (What happened)，但在 **流動性分析 (Liquidity Analysis)** 上還較弱 (What is pending)。Gale Engine 的下一個重大進化就是 **LOB (Order Book) 整合**。
