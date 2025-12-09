# Strategy: 逆勢震盪突破 (Chop Reversal)

## 核心邏輯 (Core Logic)
本策略專為 **夜盤** 或 **震盪盤 (Chop)** 設計。
核心思想是利用「市場微結構 (Microstructure)」中的極端現象來捕捉反轉點。

### 1. 進場訊號 (Entry Signal)
採用 **Fading the Move** (逆勢接刀) 邏輯：
*   **Buy Signal**:
    *   `Velocity > 25` (極速殺盤)
    *   `Imbalance < -0.6` (強烈賣壓，代表 Selling Climax)
    *   -> 判定為「賣過頭」，反手做多。
*   **Sell Signal**:
    *   `Velocity > 25` (極速拉抬)
    *   `Imbalance > 0.6` (強烈買氣，代表 Buying Climax)
    *   -> 判定為「買過頭」，反手放空。

### 2. 出場邏輯 (Exit Logic) - 保本戰法
採用 **"Breakeven Mode"**，旨在震盪中保護獲利。

*   **Hard Stop (初始停損)**: 20 點
    *   給予行情震盪空間，避免輕易被洗出場。
*   **Target Profit (停利目標)**: 40 點
    *   抓一段不錯的反彈波段。
*   **Breakeven Trigger (保本啟動)**: 獲利 > 20 點
    *   一旦行情順利跑出 20 點浮盈，立即啟動保護。
*   **Breakeven Cushion (保本墊)**: 10 點
    *   啟動後，停損移至 `Entry + 10`。
    *   確保最差情況下也能賺到便當錢 (10 點)，而不是只有 1 點。

---

## 參數設定 (Parameters)
```python
self.vel_threshold = 25
self.imb_threshold = 0.6

self.hard_stop = 20
self.target_profit = 40
self.breakeven_trigger = 20
self.breakeven_cushion = 10 # 賺便當錢 (保證獲利)
```
