# 分析筆記：動態 Volume Profile 邏輯與實作

## 1. Volume Profile 類型定義 (Standard Definitions)

在業界標準圖表軟體 (如 TradingView, Sierra Chart) 中，Volume Profile 主要分為以下幾種邏輯：

### A. Session Volume Profile (SVP) - "Global Mode"
*   **定義**：計算 **當日開盤至今** (或是單一完整交易日) 的所有成交量分佈。
*   **特性**：隨著時間推移，總量只增不減。形狀雖會改變，但基礎是累積的。
*   **用途**：觀察當日整體的價值區間 (VA)，判斷今日是趨勢盤還是盤整盤。
*   **本專案現狀**：預設模式 (`vp_engine`) 即為此類。

### B. Visible Range Volume Profile (VRVP) - "Dynamic Mode"
*   **定義**：**僅計算圖表可視範圍內** (Visible Range) 的成交量。
*   **特性**：
    *   **高度動態**：當使用者縮放 (Zoom In/Out) 或拖曳 (Pan) 時間軸時，參與計算的 K 棒會改變，Profile 形狀會完全不同。
    *   **細節放大**：當放大觀察某波段 (例如 10:00~10:30 的急殺) 時，VRVP 能呈現該特定波段的籌碼堆疊，而不受其他時間干擾。
*   **用途**：微觀結構分析。判斷特定波段的套牢區與支撐區。

### C. Fixed Range Volume Profile (FRVP) - "Anchored Mode"
*   **定義**：使用者手動指定起始時間與結束時間 (例如：從起漲點畫到現在)。
*   **實作**：其實就是鎖定時間範圍的 VRVP。

---

## 2. 關於 "Time Decay" (時間衰減) 的迷思

### 討論
所有的標準 Volume Profile 算法 **都沒有** 內建各種數學上的 "Time Decay" (例如指數衰減)。
*   **原因**：成交量就是成交量。10 點鐘成交的 100 口，與 12 點鐘成交的 100 口，在「累積成交量」的定義上權重是相等的。
*   **替代方案**：交易者透過 **VRVP** 來達成類似效果。
    *   當你把圖表放大到最近 30 分鐘，你自然就過濾掉了 30 分鐘前的舊籌碼。
    *   這比數學上的衰減權重更直觀、更符合視覺交易邏輯。

---

## 3. 實作邏輯 (Implementation Logic)

為了在 `txf-gale-engine` 實現高效的 VRVP (Dynamic Mode)，我們採用以下架構：

### 流程圖
1.  **Frontend (Dash)**: 捕捉 `relayoutData` 事件 (Zoom/Pan)。
2.  **Server**: 提取 X 軸範圍 (`xaxis.range`)。
3.  **State**:
    *   **Data Unrolling**: 從 `RingBuffer` 拉出線性陣列。
    *   **Slicing**: 利用 `bisect` 快速找到時間切點 (`mask_start`, `mask_end`)。
    *   **Numpy Calculation**:
        ```python
        hist, edges = np.histogram(price_slice, bins=bins, weights=vol_slice)
        ```
    *   此運算極快 (Vectorized)，即使在 Python 層也能在毫秒級完成。
4.  **View**: 繪製新的 Bar Chart。

### 關鍵挑戰與解法
*   **挑戰 1：Plotly 的軸鎖定 (Axis Locking)**
    *   *現象*：切換模式後，VP 線條變成看不見的細線。
    *   *原因*：`uirevision` 導致 Plotly 沿用 Global 模式的大刻度 (e.g. Max=5000)，而 Dynamic 模式量小 (Max=200)。
    *   *解法*：後端強制計算 `vp_max` 並設定 `xaxis3.range`，強制重繪座標軸。

*   **挑戰 2：視覺更新延遲**
    *   *現象*：拖曳後過了幾秒才更新。
    *   *解法*：使用 `Trace Name` 策略 (e.g. `VP (Dynamic)`) 強制 Plotly 認為是新物件而立即重繪，並移除後端不必要的 Debug Log 減少 I/O 阻塞。

*   **挑戰 3：標籤可讀性**
    *   *解法*：使用 `Scatter(mode='text', cliponaxis=False)` 將 POC/VAH/VAL 數值標籤固定在圖表最右側，確保永遠可見。

---

## 4. 總結

Dynamic Volume Profile (VRVP) 是一個強大的分析工具，它讓交易者能深入每一個微小波段的籌碼結構。
透過 Python Numpy 的強大計算力與 Dash 的互動性，我們在不犧牲效能的前提下成功整合了此功能。
