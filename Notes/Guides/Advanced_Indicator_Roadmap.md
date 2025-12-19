# Advanced Leading Indicators Roadmap

在 OBI/OFI 之後，下一階段的微結構指標專注於 **「更精細的價格發現」** 與 **「隱藏流動性偵測」**。

---

## 1. Micro-Price (微觀價格)
> **"The True Fair Price"**

*   **痛點**: 傳統的 $Mid Price = (Bid+Ask)/2$ 反應太慢。如果 Bid 有 100 口，Ask 只有 1 口，理論上價格**已經**偏向 Ask 了，但 Mid Price 還在中間。
*   **定義**: 將 OBI 融入價格計算。
    $$ P_{micro} = \frac{Price_{Ask} \cdot Vol_{Bid} + Price_{Bid} \cdot Vol_{Ask}}{Vol_{Bid} + Vol_{Ask}} $$
*   **信號 (Leading)**:
    *   **先行性**: Micro-Price 會比 Last Price **早 0.5~2 秒** 發生移動。
    *   **策略**: 當 $P_{micro}$ 顯著偏離 $P_{last}$ 時，代表即將發生 Tick 變動，可搶先掛單。

## 2. Iceberg Detection (冰山偵測)
> **"Seeing the Invisible"**

*   **痛點**: 很多大戶為了不驚動市場，會用隱藏單 (Hidden Order)。你在 Heatmap 上看不到牆，實際上卻怎麼買都買不穿。
*   **定義**: 比較「成交量」與「可見掛單量」。
    *   若 `Trade_Volume > Best_Ask_Volume` 且 `Price` **沒有** 往上跳一檔。
    *   **結論**: 這裡有一座冰山 (Iceberg Sell)。
*   **信號**:
    *   **Absorption**: 偵測到冰山被連續吃掉但價格不破 -> 反轉訊號。
    *   **Defense**: 價格撞到冰山彈回 -> 確認壓力位。

## 3. Sweep Aggression (穿價/掃單)
> **"Panic Detector"**

*   **痛點**: 同樣是 10 口買單，「分 10 次買」跟「一次掃掉 3 檔價格」的意義完全不同。後者代表**「不計代價的急迫 (Urgency)」**。
*   **定義**: 偵測單筆成交 (Tick) 是否造成了 Best Bid/Ask 的價位移動 (Change of Level)。
*   **信號**:
    *   **Sweep Run**: 連續出現 Sweep Ticks -> 強趨勢噴出 (Momentum)。
    *   **Exhaustion**: Sweep 掃上去但馬上被打回來 -> 假突破。

---

## 實作優先級建議

1.  **Micro-Price** (⭐⭐⭐): CP 值最高。計算簡單 (公式代入即可)，但能讓您的價格線變得極度靈敏，濾掉雜訊。
2.  **Sweep** (⭐⭐): 中等。需要比對 Tick 與 Snapshot 的價位變化。
3.  **Iceberg** (⭐): 較難。需要精準快照 (Snapshot) 配合，容易有誤判，建議最後做。
