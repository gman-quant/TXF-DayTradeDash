# 開發日誌 (2025-12-08 ~ 12-09) - 核心效能優化與架構重構

## 🌟 0. 基礎設施大換血 (Infrastructure Overhaul) - **最重要的改變**
這是本次重構的基石。我們徹底捨棄了舊版 `np.zeros` 的記憶體管理，轉向了工業級的 **Shared Memory (共享記憶體)** 架構。

*   **SharedRingBuffer 實作**:
    *   建立 `core/shared_memory.py`，封裝了 `multiprocessing.shared_memory`。
    *   **效益**: 實現了「寫入端 (Ingestion)」與「讀取端 (Strategy/Dash)」的物理隔離。
    *   **強韌性驗證**: 通過「雙盲測試 (Double-Blind Test)」，證明即使讀取端崩潰重啟，數據也能無縫接軌，達成 **Zero Data Loss (零掉單)**。

## 1. 核心運算優化 (Computational Optimization)
針對 Numba Engine 中的時間基礎指標 (Time-Based Indicators) 進行了演算法級別的優化，顯著降低 CPU 負載。

*   **Binary Search 導入**:
    *   在 `core/numba_engine.py` 中實作 `binary_search_boundary` 函數。
    *   利用 RingBuffer 時間戳記的有序性，將搜尋時間視窗邊界的複雜度由 $O(N)$ 降低至 $O(\log N)$。
*   **SMA & VWAP 優化**:
    *   配合 Prefix Sum (累積加總陣列)，在找到邊界後，運算複雜度降為 $O(1)$。
*   **Rolling Max/Min 優化**:
    *   移除迴圈內部的時間檢查，改為先計算精確的搜尋範圍 ($K$) 再進行極值掃描。

## 2. 資料吞吐優化 (Ingestion Throughput)
針對 Kafka 資料接收端與 Shared Memory 寫入端進行批次處理改造。

*   **Kafka Consumer 批次化**:
    *   修改 `ingestion/kafka_consumer.py`，將 `poll(1)` 替換為 `consume(batch_size=500)`。
*   **Shared Memory 向量化寫入 (Vectorized Write)**:
    *   在 `core/shared_memory.py` 實作 `write_batch` 方法。
    *   利用 NumPy Vectorization 一次性計算累積數據 (`cumsum`) 並寫入記憶體，大幅減少 Python Overhead。

## 3. 系統架構重構 (System Architecture)
將 Dashboard 從 Strategy Process 中剝離，實現「三位一體」的多進程架構。

*   **獨立 Dashboard Process**:
    *   建立 `core/dashboard_runner.py`，擁有獨立的 `IndicatorManager` 與 Shared Memory 連線。
*   **Supervisor 升級**:
    *   更新 `core/core_processor.py`，現在負責管理三個獨立進程：
        1.  **Ingestion Process**: 負責 IO (Kafka -> SHM)。
        2.  **Dashboard Process**: 負責 UI (SHM -> Web)。
        3.  **Strategy Process**: 負責核心運算 (SHM -> Strategy)。

## 4. 穩定性修復 (Stability Fixes)
*   **Resource Tracker Warning**:
    *   修正 Python 在 Daemon Thread 結束時誤報 Shared Memory 洩漏的問題。
    *   確保 `SharedRingBuffer.shutdown` 為 Idempotent (可重複呼叫) 並加入顯式資源清理。

---
**總結**: 
今天的重構將 TXF Gale Engine 從一個原型 (Prototype) 提升到了 **HFT 工業級 (Industrial Grade)** 的水準。
核心在於導入 **SharedRingBuffer**，它解鎖了多進程平行運算的能力，讓策略計算不再受制於 UI 繪圖或資料接收的延遲。
