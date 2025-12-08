# BidAsk Order Flow Indicators (OFI, OBI, Microstructure)

這份資料結構非常完整且強大，特別是 `txf.BidAsk` 中包含了 **`diff_..._vol` (掛單變化量)**，這是一個「殺手級」的欄位。通常這需要客戶端自己算（Current - Previous），但您的源頭已經算好給您了，這意味著我們可以極速偵測 **「掛單意圖」**。

基於這份 Schema，我們將指標分為三個層次來討論：**基礎壓力 (Static Pressure)**、**動態流向 (Dynamic Flow)** 以及 **微結構事件 (Micro-Events)**。

---

### 1. 核心指標：訂單流失衡 (OFI - Order Flow Imbalance)

這是目前高頻交易中最主流的「短期價格預測」指標。

* **數據來源：** `BidAsk` (最佳一檔價格與量) + `diff_..._vol`
* **邏輯：** 觀察最佳買賣價 (Best Bid/Ask) 的變化與掛單量的增減。
    * 如果 Best Bid **價格上移** $\rightarrow$ 強烈買進訊號 ($e_n = 1$)。
    * 如果 Best Bid **價格不變** 但 **掛單量增加** $\rightarrow$ 支撐增強 ($e_n = 1$)。
    * 如果 Best Bid **價格下移** $\rightarrow$ 支撐潰散 ($e_n = -1$)。
* **策略價值：** OFI 對於未來 1~10 個 Tick 的價格變動有極高的相關性（Correlation）。當 OFI 顯著偏向一方時，通常價格隨後就會跟上。

### 2. 深度指標：掛單簿失衡 (OBI - Order Book Imbalance)

這是衡量「靜態壓力」的指標，利用了您有的 **5 檔數據**。

* **數據來源：** `bid/ask_volume` (List[5])
* **邏輯：** 計算 5 檔總掛買量與總掛賣量的比例。
    $$OBI = \frac{\sum Q_{Bid} - \sum Q_{Ask}}{\sum Q_{Bid} + \sum Q_{Ask}}$$
* **進階版 (WOBI)：** **加權 (Weighted)** 掛單簿失衡。
    * Level 1 的掛單比 Level 5 更重要。給予 Level 1 權重 5，Level 5 權重 1。
* **策略價值：**
    * **OBI > 0 (正值大)：** 下方支撐厚，上方壓力輕 $\rightarrow$ 易漲難跌。
    * **OBI < 0 (負值大)：** 上方賣壓重 $\rightarrow$ 易跌難漲。

### 3. 意圖指標：撤單與虛掛 (Cancellation & Spoofing)

利用您的 **`diff_..._vol`** 欄位，我們可以偵測市場的「欺騙」行為。

* **數據來源：** `diff_..._vol` (BidAsk) + `volume` (Tick)
* **邏輯：**
    * **撤單 (Cancellation)：** 如果 `diff` 是 **負數**，且同時刻 **沒有 Tick 成交** (或成交量遠小於 diff)，代表這筆單是 **被抽掉的 (Cancelled)**，而非被吃掉的。
    * **虛掛 (Spoofing)：** 如果在 Level 2~5 出現大量新增掛單 (Positive Diff)，但當價格接近時，這些掛單突然大量撤銷 (Negative Diff)。
* **策略價值：**
    * **假支撐：** 看到下方有大單，但價格一跌下來，大單就撤 $\rightarrow$ **追空訊號**。
    * **誘多/誘空偵測。**

### 4. 互動指標：吸收 (Absorption)

這是判斷趨勢反轉的關鍵，結合了 Tick (主動) 和 BidAsk (被動)。

* **數據來源：** `Tick.volume` (主動單) vs `diff_..._vol` (被動單變化)
* **情境：** 價格來到壓力位。
    * **現象：** 外盤成交 (Aggressive Buy) 爆量，照理說價格要漲。
    * **結果：** 價格不漲，且 Ask 端的 `diff` 顯示掛單量 **持續補入 (Replenishment/Iceberg)**。
* **策略價值：** 這代表 **「冰山單 (Iceberg Order)」** 正在吸收買盤。主力在該價位無限供應籌碼。這是一個極高勝率的 **反轉做空** 訊號。

### 5. 衝擊指標：掃單 (Sweeping)

* **數據來源：** `Tick.volume` vs `BidAsk.ask_volume[0]` (Level 1 量)
* **邏輯：** 如果單筆 `Tick.volume` > `Best Ask Volume`。
* **含義：** 這筆買單大到 **吃光了第一檔**，並且滑價到第二檔甚至第三檔。
* **策略價值：** 這代表極強的攻擊意圖。如果連續出現 Sweeping，就是 **動能策略 (Momentum)** 進場的最佳時機。

---

### 💡 總結：我們可以發展的策略矩陣

有了這兩個 Topic 的資料，我們可以建立一個立體監控系統：

| 指標類型 | 名稱 | 數據源 | 作用 |
| :--- | :--- | :--- | :--- |
| **趨勢 (Trend)** | **CVD / VWAP** | Tick | 告訴我們現在誰在主導大方向。 |
| **動能 (Momentum)** | **Delta / Sweeping** | Tick + BidAsk | 告訴我們現在的攻擊力道有多強。 |
| **壓力 (Pressure)** | **OFI / OBI** | BidAsk | 告訴我們哪邊的阻力比較小 (路徑最小抵抗)。 |
| **陷阱 (Trap)** | **Absorption / Cancellation** | Tick + BidAsk (Diff) | 告訴我們哪邊是假突破或假支撐。 |

**建議下一步：**
我們可以先從 **OFI (Order Flow Imbalance)** 開始。
因為它只需要比較前後兩筆 Quote 的 Level 1 狀態，計算簡單 ($O(1)$)，但對短線價格的預測能力最強。

您覺得從 **Level 1 OFI** 開始切入合適嗎？


