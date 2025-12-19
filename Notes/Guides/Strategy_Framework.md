# TXF Gale Engine: Strategy Framework

本文件基於《System Technical Reference》定義的底層指標，構建三類核心交易策略架構。

---

## 1. 動能與趨勢策略 (Momentum Strategies)
**核心邏輯**：利用微結構指標的「領先性」來確認價格突破的有效性，避免假突破。

### 1.1 OFI Trend Confirmation (趨勢確認)
*   **邏輯**：價格上漲必須伴隨「進攻意圖 (OFI)」的增強。
*   **條件**：
    1.  Price 突破關鍵價位 (如前高)。
    2.  `Cum OFI` 斜率為正且持續創新高。
    3.  `CVD` (Delta) 顯示 Mega Lot (大戶) 為淨買入。
*   **失效場景**：價格創新高但 OFI 轉折向下 (Divergence)，代表買氣力竭，應立即平倉或反手。

### 1.2 Vacuum Breakout (真空突破)
*   **邏輯**：利用「阻力撤除」造成的流動性真空，捕捉瞬間噴出。
*   **條件**：
    1.  `Cum OBI` 突然大幅負向跳水 (Spoofing/撤賣單)。
    2.  隨後價格快速穿越該價格帶 (Low Volume Node)。
*   **優勢**：速度極快，通常在幾秒內完成獲利。
*   **風險**：若為誘多 (Suppression)，價格會迅速回落，需嚴格設損。

---

## 2. 均值回歸策略 (Mean Reversion Strategies)
**核心邏輯**：利用統計學邊界與成交量分佈，尋找價格「過度延伸」後的修正機會。

### 2.1 VWAP Band Fade (通道逆勢)
*   **邏輯**：價格極少長期停留在 2.0 標準差之外。
*   **條件**：
    1.  Price 觸及或突破 `VWAP Upper Band (2.0 SD)`。
    2.  `Cum OFI` 出現背離 (價格新高但 OFI 沒新高/轉弱)。
    3.  `Volume Profile` 顯示該處為低量區 (無實質籌碼支撐)。
*   **操作**：做空，目標價位設為 VWAP 中軸 (回歸均值)。

### 2.2 Volume Profile Rotation (價值區震盪)
*   **邏輯**：在盤整日，價格傾向於留在 Value Area (VA) 內震盪。
*   **條件**：
    1.  Price 接近 `VAH` (價值區高點) 或 `VAL` (價值區低點)。
    2.  `Cum OBI` 顯示該處有厚實掛單 (Wall) 防守。
*   **操作**：在邊界處逆勢操作 (高賣低買)，目標為 `POC` (最大成交量價位)。

---

## 3. 流動性提供策略 (Liquidity Provision / Market Making)
**核心邏輯**：模仿造市商行為，賺取 Bid-Ask Spread 或捕捉極短線價差。

### 3.1 Passive Front-Running (掛單搶先)
*   **邏輯**：當偵測到單邊掛單極厚 (High OBI Imbalance) 時，跟隨該方向掛單。
*   **條件**：
    1.  `Cum OBI` 強烈偏正 (Bid Side Heavy)。
    2.  在 Best Bid + 1 tick 處掛單 (預期有支撐)。
*   **優勢**：勝率高，因為有厚實的 Order Book 保護。
*   **風險**：若發生 "Pulling Support" (支撐撤單)，需毫秒級撤單速度 (此策略建議僅供 HFT 自動化使用)。

### 3.2 Absorption Scalp (吸收剝頭皮)
*   **邏輯**：尋找「市價單打不穿掛單」的時刻。
*   **條件**：
    1.  Price 正在測試支撐位。
    2.  `Cum OFI` 顯示大量賣出 (負值擴大)。
    3.  但 Price **拒絕下跌** (Held)。
*   **操作**：做多。這代表有隱形買盤 (Iceberg) 正在吸收賣壓。

---

## 策略矩陣 (Strategy Matrix)

| 市場狀態 | 推薦策略 | 關鍵指標 |
| :--- | :--- | :--- |
| **強趨勢 (Trend)** | OFI Confirmation, Vacuum Breakout | Cum OFI, CVD |
| **盤整 (Range)** | VWAP Fade, VP Rotation | VWAP, Volume Profile |
| **轉折 (Reversal)** | Absorption Scalp | Divergence (Price vs OFI) |
