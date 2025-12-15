# TXF Gale Engine: LOB Microstructure Strategy Guide
> *From Order Flow to Market Intel*

本指南從微結構 (Microstructure) 視角，解析市場的**意圖 (Intent)** 與 **防守 (Defense)**。
本文件整合了訂單流 (Order Flow)、掛單簿 (Order Book) 與大戶籌碼 (Volume Profile) 的綜合分析邏輯。

---

## Chapter 1: 核心心法 (Philosophy)

微結構交易的本質在於觀察兩個力量的對決：**「做功 (Work)」** vs **「壓力 (Pressure)」**。
這是理解為何 OBI 需要正則化 (Normalized) 而 OFI 需要原始值 (Raw) 的物理基礎。

### 1. OFI (做功) - 矛
*   **物理意義**: 代表市場參與者**實際投入了多少真金白銀**來推動掛單簿。
*   **為什麼是 Raw Value (原始值)？**
    *   這是**絕對值 (Absolute Work)**。
    *   主力撤銷 500 口賣單的力道 (OFI +500)，遠大於散戶撤銷 1 口賣單 (OFI +1)。
    *   如果將其正則化，主力的大單會被降權，導致我們看不出**「大戶的腳印」**。
    *   保留原始口數，CumOFI 的斜率才能真實反映**資金流入/流出的速度**。

### 2. OBI (壓力) - 盾
*   **物理意義**: 代表多空雙方在戰場上的**兵力懸殊比例** (Relative Pressure)。
*   **為什麼要 Normalized (正則化)？**
    *   **消除「流動性潮汐」Bias**:
        *   **早盤 (高流動性)**: 買 1000 vs 賣 900 -> 淨差 100。
        *   **夜盤 (低流動性)**: 買 100 vs 賣 0 -> 淨差 100。
        *   如果看淨差，兩者強度似乎一樣。但實際上夜盤那是「絕對碾壓」。
    *   **正則化後**: 早盤 = 0.05 (勢均力敵)，夜盤 = 1.0 (絕對多頭)。這才符合真實的盤感。

---

## Chapter 2: 指標解剖 (Anatomy)

### A. 攻擊型指標 (Flow Indicators)
反映市場的「動態意圖」。

#### 1. CVD (Cumulative Volume Delta)
*   **定義**: `主動買成交量 - 主動賣成交量` 的累加值。
*   **意義**: **真金白銀的戰果**。代表買賣雙方實際「吃掉」了多少對手盤。最直觀的多空力道。

#### 2. CumOFI (Cumulative Order Flow Imbalance)
*   **定義**: `Sum(5檔委買量變化) - Sum(5檔委賣量變化)`。
*   **意義**: **全深度的攻擊 + 意圖**。
    *   我們的引擎計算了**所有五檔 (Top 5)** 的掛單變化，不僅僅是最佳一檔。
    *   **上升**: 有人掛買單 (想買) 或 撤銷賣單 (讓路)。
    *   **下降**: 有人掛賣單 (想賣) 或 撤銷買單 (抽腿)。
*   **特點**: 比 CVD 更敏銳，包含了「撤單讓路」的隱性訊號。

### B. 防守型指標 (State Indicators)
反映市場的「靜態厚度」。

#### 3. CumOBI (Cumulative Order Book Imbalance)
*   **定義**: `(委買量 - 委賣量) / (委買量 + 委賣量)` 的 tick-based 累加值。
*   **關鍵時機**: **Tick 發生當下的瞬間快照 (Snapshot on Tick)**。
    *   **同步性**: 我們只在「成交發生當下」去抓取掛單簿。這確保了我們分析的是「多空交戰瞬間」的戰場地形。
    *   **Zero-Order Hold**: 若無成交，延用上一筆狀態，避免無意義的雜訊。
*   **意義**: **防守深度與意圖**。
    *   **OBI 上升**: Tick 發生時，下方的支撐牆 (Bids) 比上方的壓力牆 (Asks) 厚。
    *   **OBI 下跌**: 上方賣壓更厚，或下方支撐空虛。

### C. 控盤型指標 (Lot Size)
將成交量依據單筆大小分類，觀察誰在主導市場。

*   **🔴/🟤 Small Lot (小單 < 5口)**: **散戶 (Retail)**。反向指標。追高殺低的代表。
*   **🔵 Large Lot (大單 >= 5口)**: **主力 (Smart Money)**。趨勢指標。行情的發動者。
*   **🟢/💠 Mega Lot (特大單 >= 15口)**: **法人/造市商 (Insto/MM)**。關鍵指標。轉折確認訊號。

---

## Chapter 3: 市場動態 (Market Dynamics)

我們將市場情境分為四大類：趨勢、吸籌、出貨、虛漲/虛跌。

### A. 趨勢確認 (Trend Confirmation)
當所有指標同向時，趨勢最健康，勝率最高。
*   **強勢多頭 (Strong Bull)**: 價格 ↗️, CVD ↗️, OBI ↗️ (買盤攻，賣壓退)。
*   **強勢空頭 (Strong Bear)**: 價格 ↘️, CVD ↘️, OBI ↘️ (賣盤殺，買盤縮)。

### B. 吸收與反轉 (Absorption & Reversal)
當價格創新低/新高，但指標卻沒有跟隨 (背離)，代表「動能被牆擋住了」。

#### 1. 頂部出貨 (Bearish Absorption) - 爆量滯漲
*   **現象**: 價格 ↗️ 創新高。
*   **OFI / CVD**: **↗️ 創新高 (CVD Up)**。
    *   *關鍵*: 這裡的 CVD 是往上的！代表多頭真的在買 (主動買 > 主動賣)。
*   **OBI**: **↘️ 背離下跌 (Divergence)**。
    *   *解讀*: 雖然買盤一直吃 (Market Buy)，但賣方不斷補單 (Reloading Asks)，導致賣壓越吃越厚。或者下方根本沒人掛買單。
*   **結構**: **矛 (CVD) 攻不過 盾 (OBI)**。
*   **策略**: **力竭 (Exhaustion)** 訊號。多單出場，等待 CVD 轉弱時做空。

#### 2. 底部吸籌 (Bullish Absorption) - 跌深有撐
*   **現象**: 價格 ↘️ 創新低 (看似還在跌)。
*   **OFI / CVD**: ↘️ 創新低 (賣盤還在殺)。
*   **OBI**: **↗️ 更高的低點 (Higher Low) / 持平** (關鍵！)。
    *   **為什麼是 HL 不是 HH？**
    *   因為我們在抓「底部」。在空頭趨勢中，價格會不斷創「更低的低點 (LL)」。
    *   如果有一次價格創了 LL，但 OBI 卻拒絕創 LL (也就是 HL)，這代表**該次下跌的賣壓被吸收了**。這是最早期的反轉訊號。
*   **策略**: **等待價格止穩後做多**。
*   **經典案例**: **12/12 夜盤 (The Shakeout & Accumulation)**。
    1.  **早期吸籌**: 價格緩跌，但 OBI 一路爬升。
    2.  **清洗/甩轎**: OBI 轉弱下跌 (此時應離場)。
    3.  **再吸籌**: 暴跌後價格續創新低，但 OBI **重新站上高點**並背離。這才是 Re-Entry 點。

### C. 流動性真空 (Liquidity Vacuum)
最危險也最暴利的訊號。價格與**所有指標**都背離。

#### 1. 虛漲 (Vacuum Rise) - 無量乾漲
*   **現象**: 價格 ↗️ 緩步或垂直創高 (Drifting Up)。
*   **OFI / CVD**: **↘️ 往下或持平** (沒有主動買盤)。
    *   *關鍵*: 這裡的 CVD 是往下的！與 Bearish Absorption 完全不同。
*   **OBI**: **↘️ 急劇下墜**。
    *   **微結構原理**: 為什麼上方賣單撤了 OBI 還會跌？
    *   因為**下方買單撤得更快**！或者根本沒人掛買單。
    *   這代表市場進入「真空狀態」。上方沒壓力，下方沒支撐。價格像氣球一樣飄上去。
*   **策略**: **誘多陷阱 (Trap)**。極度危險，切勿追高。一但賣單回來就是崩盤 (如 12/10, 12/11 夜盤)。

#### 2. 虛跌 (Vacuum Drop)
*   **現象**: 價格 ↘️ 緩步創低。
*   **OFI / CVD**: **↗️ 往上** (主動盤其實在買)。
*   **OBI**: **↗️ 往上** (下方支撐很強)。
*   **策略**: **準備強勢反彈做多**。

### D. 特殊形態 (Special Patterns)

#### 軋空停損盤 (The Stop Run / Short Squeeze)
*   **場景**: 常見於突破關鍵壓力位時 (如 12/10 04:00 AM)。
*   **OFI 形狀**: **「Spike 噴出 -> Plateau 高原 -> Drop 下墜」**
*   **階段解析**:
    1.  **Spike (噴出)**: 觸發大量 Buy Stops (市價買單停損)。OFI 瞬間暴衝。這是**被動買盤**。
    2.  **Plateau (高原)**: 停損結束後，沒有新的主力買盤，OFI 停在高檔不動。價格靠慣性懸浮。
    3.  **Drop (下墜)**: 力竭時，當沖客獲利了結，OFI 快速回落。
*   **策略**: **Plateau 階段是誘多陷阱**。等待 Drop 階段做空。

---

## Chapter 4: 實戰手冊 (The Playbook)

### 籌碼角色矩陣 (Lot Size Matrix)
搭配 CVD 與 OBI，分辨是誰在主導行情：

| 情境 | 價格 | CVD | Lot Size (籌碼) | 解讀 | 訊號 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **散戶接盤** | ↗️ High | ↗️ | **Small Lot (Buy)** <br> Mega Lot (Sell/None) | 大戶倒貨給散戶，CVD 上升是散戶堆出來的。 | **Strong Short** (假突破) |
| **主力硬攻** | ↗️ High | ↗️ | **Mega Lot (Buy)** <br> Small Lot (None) | 大戶在吃貨，正面對決 OBI 賣壓牆。 | **Wait** (看誰贏) |
| **主力棄守** | ↘️ Low | ↘️ | **Mega Lot (Sell)** | 大戶帶頭殺。 | **Follow** (順勢空) |
| **散戶恐慌** | ↘️ Low | ↘️ | **Small Lot (Sell)** <br> Mega Lot (Buy) | 散戶停損，大戶在底下接刀 (Bottom Fishing)。 | **Long** (反彈將至) |

### 訊號速查表 (Cheat Sheet)

| 情境 | 價格 | 主動流 (OFI/CVD) | 掛單簿 (OBI) | 訊號含義 | 操作建議 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **真突破** | ↗️ | ↗️ | ↗️ | 動能強、地基穩 | Buy |
| **真跌破** | ↘️ | ↘️ | ↘️ | 殺盤強、支撐破 | Sell |
| **頂部出貨** | ↗️ | **↗️ (CVD Up)** | **↘️ (OFI/OBI Down)** | 買盤撞牆 (Absorption) | Wait -> Sell |
| **虛漲 (誘多)**| ↗️ | **↘️ (CVD Down)** | **↘️ (All Down)** | 無量乾漲、真空 | **Strong Sell** |
| **虛跌 (誘空)**| ↘️ | **↗️ (CVD Up)** | **↗️ (All Up)** | 無量乾跌、真空 | **Strong Buy** |

---

## Appendix: 學術依據與技術細節

### A. 學術理論 (References)
本系統的指標定義基於微結構領域的權威研究：
1.  **Cont, Kukanov, Stoikov (2014)**: *The Price Impact of Order Book Events*. (定義了 OFI)
2.  **Xu et al. (Deep Order Flow)**: 提出了 Multi-Level OFI 的概念，證明考量深層掛單能防止 Spoofing 誤導。

### B. 進階微結構 (Advanced Microstructure)
1.  **為什麼是 5 檔 (Full Depth)?**
    *   **防假單**: 將 L1~L5 視為整體 (Aggregate Liquidity)，大幅降低被 L1 假動作欺騙的機率。
    *   **真實厚度**: OBI 數值高代表這面牆有 5 層厚，而不僅僅是第一線防守。

2.  **斜率分析 (Slope Analysis)**
    *   **急跌 (Sharp Drop)**: 代表恐慌性撤單 (Panic Pulling)。若發生在價格上漲時，是強烈反轉訊號。
    *   **LOB Lag**: 若 OBI 呈現一直線死魚狀，可能是數據源延遲，應暫停交易。
