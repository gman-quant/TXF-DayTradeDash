# 📉 LOB Data Analysis & Formula Spec

這份文件回答了兩個核心問題：
1.  **資料量有多大？** (硬體撐得住嗎？)
2.  **指標怎麼算？** (數學邏輯是什麼？)

---

## 1. 資料量估算 (Data Volume Estimation)

台指期 (TXF) 的報價更新頻率遠高於成交頻率。

### 數據推算
*   **Tick (成交筆數)**：一般的日子約 30,000 ~ 50,000 筆/天。
*   **Quote (掛單更新)**：通常是 Tick 的 **10~20 倍**。我們保守估計 **1,000,000 (一百萬) 筆/天**。

### 記憶體需求 (Shared Memory)
每一筆 BidAsk Quote 包含：
*   Timestamp: 8 bytes
*   Bid/Ask Price (5檔 x 2): 8 bytes * 10 = 80 bytes
*   Bid/Ask Volume (5檔 x 2): 4 bytes * 10 = 40 bytes
*   Diff Volume (5檔 x 2): 4 bytes * 10 = 40 bytes
*   Total fields: 4 bytes * 2 = 8 bytes
*   **單筆大小 (Row Size)**：約 **176 bytes**。

**單日總量**：
$$ 1,000,000 \text{ rows} \times 176 \text{ bytes} \approx 176 \text{ MB} $$

### 結論
**非常輕鬆！**
即使是 176 MB，對於現代 Server (通常 32GB/64GB RAM) 來說根本是九牛一毛。
就算開 5 天回放 (880 MB)，也完全在可控範圍內。
**Dual Ring Buffer 架構絕對可行。**

---

## 2. 指標計算邏輯 (Calculation Logic)

### A. OBI (Order Book Imbalance) - 趨勢預測
我們採用 **加權平均 (Weighted)** 算法，越靠近市價的掛單權重越重。

$$ \text{OBI} = \frac{\sum_{i=1}^5 (V_{Bid,i} \cdot W_i) - \sum_{i=1}^5 (V_{Ask,i} \cdot W_i)}{\sum_{i=1}^5 (V_{Bid,i} \cdot W_i) + \sum_{i=1}^5 (V_{Ask,i} \cdot W_i)} $$

*   $W_i$: 權重。建議 $W_1=5, W_2=4, ... W_5=1$ (Level 1 最重要)。
*   **數值解讀**：
    *   `+1.0`：極度看多 (只有買單，沒賣單)。
    *   `0.0`：多空平衡。
    *   `-1.0`：極度看空。

### B. 液態牆 (Liquidity Walls) - 支撐壓力
簡單的閾值過濾 (Thresholding)。

$$ \text{IsWall}(P) = \begin{cases} \text{True,} & \text{if } V(P) > \text{Threshold} \times \text{AvgVol} \\ \text{False,} & \text{otherwise} \end{cases} $$

*   **實作**：我們會計算過去 1 分鐘的「平均掛單量」(例如平均每檔 20 口)。
*   如果有某一檔突然出現 **500 口** (25倍平均量)，那就是一道牆。
*   **策略意義**：價格碰到牆通常會反彈 (Reversal)，或者如果穿過牆 (Breakout) 則會大噴出。

### C. 虛假掛單 (Spoofing / Intent) - 誘多誘空
利用 `diff_bid_vol` 與 `diff_ask_vol`。

**情境：假買單 (Spoofing the Bid)**
1.  主力在 Best Bid 下方掛大量買單 (`diff_bid_vol > 0`)，製造「支撐很強」的假象。
2.  散戶看到支撐，進場做多，價格被推高。
3.  主力在高點出貨。
4.  主力瞬間撤掉下方買單 (`diff_bid_vol < 0` 且 **沒有成交**)。

**偵測邏輯**：
$$ \text{NetCancel} = \sum (\text{diff\_vol} < 0) $$
如果某個瞬間 `NetCancel` 異常大，且價格沒有變動，代表有人在大舉撤單，這是強烈的反轉訊號。

---

## 3. 系統實作優先序

為了效能，我們不會即時計算這麼多東西給前端。我們只算 **OBI**。
其他的 (Wall, Spoofing) 留給 Python Backend 的策略模組計算，有訊號才送 Alert。

這樣安排您覺得如何？
