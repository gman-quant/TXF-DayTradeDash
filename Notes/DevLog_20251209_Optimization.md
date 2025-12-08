# 開發日誌 (2025-12-08 ~ 12-09) - 核心效能優化與架構重構

## 1. 核心運算優化 (Computational Optimization)
針對 Numba Engine 中的時間基礎指標 (Time-Based Indicators) 進行了演算法級別的優化，顯著降低 CPU 負載。

*   **Binary Search 導入**:
    *   在 `core/numba_engine.py` 中實作 `binary_search_boundary` 函數。
    *   利用 RingBuffer 時間戳記的有序性，將搜尋時間視窗邊界的複雜度由 $O(N)$ 降低至 $O(\log N)$。
*   **SMA & VWAP 優化**:
    *   配合 Prefix Sum (累積加總陣列)，在找到邊界後，運算複雜度降為 $O(1)$。
    *   影響函數：`calc_sma_time`, `calc_vwap_time`。
*   **Rolling Max/Min 優化**:
    *   移除迴圈內部的時間檢查 (`timestamp check`)，改為先計算精確的搜尋範圍 ($K$) 再進行極值掃描。
    *   影響函數：`calc_rolling_max_time`, `calc_rolling_min_time`。

## 2. 資料吞吐優化 (Ingestion Throughput)
針對 Kafka 資料接收端與 Shared Memory 寫入端進行批次處理改造，大幅減少 IO Overhead 與 Python Context Switch。

*   **Kafka Consumer 批次化**:
    *   修改 `ingestion/kafka_consumer.py`，將 `poll(1)` 替換為 `consume(batch_size=500)`。
    *   大幅減少 `await loop.run_in_executor` 的呼叫頻率。
*   **Shared Memory 向量化寫入**:
    *   在 `core/shared_memory.py` 實作 `write_batch` 方法。
    *   利用 NumPy Vectorization (Vectorized Operations) 一次性計算累積數據 (`cumsum`) 並寫入記憶體。
    *   自動處理 Ring Buffer 的繞圈 (Wrap-around) 切割邏輯，實現 Zero-looping overhead。

## 3. 系統架構重構 (System Architecture)
將 Dashboard 從 Strategy Process 中剝離，實現「三位一體」的多進程架構，確保穩定性與資源隔離。

*   **獨立 Dashboard Process**:
    *   建立 `core/dashboard_runner.py`，複製 Reader 邏輯但擁有獨立的 `IndicatorManager`。
    *   從 `core/strategy_server.py` 移除所有 Dashboard 相關程式碼與執行緒。
*   **Supervisor 升級**:
    *   更新 `core/core_processor.py`，現在負責管理三個獨立進程：
        1.  **Ingestion Process**: 負責 IO (Kafka -> SHM)。
        2.  **Dashboard Process**: 負責 UI (SHM -> Web)。
        3.  **Strategy Process**: 負責核心運算 (SHM -> Strategy)。

## 4. 穩定性修復 (Stability Fixes)
*   **Resource Tracker Warning**:
    *   修正 Python `multiprocessing.resource_tracker` 在 Daemon Thread 結束時誤報 Shared Memory 洩漏的問題。
    *   改進 `SharedRingBuffer.shutdown` 為 Idempotent (可重複呼叫)。
    *   在 `DashboardRunner` 主執行緒加入顯式資源清理 (Explicit Cleanup)。

---
**總結**: 系統現在具備 HFT 等級的資料吞吐能力與運算效率，且 UI 與策略邏輯完全隔離，互不影響。
