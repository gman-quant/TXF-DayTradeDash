# 學習筆記：系統架構與設計模式 (System Architecture & Design Patterns)

本專案 (`txf-gale-engine`) 是一個高頻交易 (HFT) 風格的事件驅動系統。
為了兼顧極致效能 (Low Latency) 與開發彈性 (Maintainability)，我們運用了多種進階技術與設計模式。

---

## 1. 核心觀念 (Core Concepts)

### Class vs. Function (狀態封裝 vs. 邏輯執行)
*   **Class (類別)**：用於封裝 **狀態 (State)** 與 **行為 (Behavior)**。
    *   *例子*：`OrderManager` 需要記住當前的部位、未平倉訂單。這些 "記憶" 就是 State。
    *   *優點*：高內聚 (High Cohesion)，狀態不外洩。
*   **Function (函式)**：用於 **純邏輯運算 (Pure Logic)**。
    *   *例子*：`calculate_sma(prices, period)`。給定輸入，永遠得到相同輸出，不依賴外部狀態。
    *   *優點*：易於測試 (Testable)、無副作用 (Side-effect free)。
*   **本專案應用**：
    *   核心邏輯 (`Logic`) 傾向使用 Functional Style。
    *   組件 (`Component`) 與 策略 (`Strategy`) 使用 OOP 來管理生命週期與狀態。

### Multiprocessing vs. Threading (並行運算模型)
Python 的 GIL (Global Interpreter Lock) 限制了同一時間只能有一個 Thread 執行 Bytecode。
*   **Threading (多執行緒)**：適用於 **I/O Bound** 任務 (等待網路、讀寫檔案)。
    *   *例子*：`WebSocketClient` (等待報價推送)、`APIClient` (下單)。
*   **Multiprocessing (多進程)**：適用於 **CPU Bound** 任務 (複雜計算)。
    *   *例子*：`DashboardProcess` (獨立進程，避免繪圖計算卡住交易)、`IngestionProcess`。
*   **本專案應用**：
    *   主程式 (`Main`) 啟動多個 `Process` (Strategy, Dashboard, Ingestion) 以利用多核 CPU。
    *   進程間通訊 (IPC) 使用 `SharedMemory` (極速) 與 `Pipe` (控制信號)。

---

## 2. 硬核架構 (Hardcore Architecture)

### Shared Memory & Zero-Copy (共享記憶體)
*   **問題**：Process A 傳送 100萬筆 Tick 給 Process B，若用 Queue/Pickle 序列化，延遲極高。
*   **解法**：`multiprocessing.shared_memory`。
    *   開闢一塊 RAM (e.g. `/dev/shm`)，Process A 直接寫入，Process B 直接讀取。
    *   **Zero-Copy**：資料不需要在兩個進程間複製，指針指向同一塊記憶體即可。
*   **本專案應用**：`TxfRingBuffer` 底層使用 Shared Memory，讓 Dashboard 能從 Strategy 毫秒級同步數據。

### Ring Buffer (環狀緩衝區)
*   **特點**：固定大小的陣列，寫滿後回到開頭覆蓋舊資料。
*   **優點**：
    1.  **O(1) 寫入**：不需要動態配置記憶體 (No Allocation)。
    2.  **Cache Friendly**：記憶體連續，CPU 快取命中率高。
    3.  **GC Free**：不會產生垃圾回收 (Garbage Collection) 停頓。
*   **本專案應用**：儲存 Ticks, Candles。

### Lock-Free Programming (無鎖編程)
*   **策略**：Single Writer, Multiple Readers (一寫多讀)。
*   **實作**：Ingestion 負責寫入 Ring Buffer；Strategy 與 Dashboard 只負責讀取。
    *   因為只有一個人在寫，所以不需要 Mutex Lock (互斥鎖)，大大降低 Context Switch 開銷。

---

## 3. 設計模式 (Design Patterns)

### Facade Pattern (外觀模式)
*   **目的**：為複雜的子系統提供一個簡單的單一介面。
*   **本專案應用**：
    *   `gale.dashboard.logic`：Dashboard 的前端只需要呼叫 `logic.process_market_data`，不需要知道底層是怎麼去 RingBuffer 抓資料、解環、切片、計算的。`logic` 隱藏了所有複雜度。

### Strategy Pattern (策略模式)
*   **目的**：定義一系列演算法，將它們封裝起來，並使它們可以互相替換。
*   **本專案應用**：
    *   `BaseStrategy` 定義了標準介面 (`on_tick`, `on_order_update`)。
    *   `RsiStrategy`, `ChopStrategy` 是具體實作。
    *   `Engine` 只需要持有 `BaseStrategy`，不需要知道具體跑哪支策略，隨時可抽換。

### Dependency Injection (依賴注入)
*   **目的**：將組件的依賴關係 (Dependency) 從內部創建移到外部注入。
*   **本專案應用**：
    *   `Strategy(engine_api)`：策略不自己創建 API Client，而是由外部傳入 `engine_api`。這讓測試時可以輕鬆傳入 `MockAPI`。

---

## 4. 數據與演算法 (Data & Algorithms)

### Vectorization (向量化運算)
*   **概念**：使用 SIMD 指令集 (Single Instruction, Multiple Data) 一次處理整個 Arrays。
*   **工具**：`numpy`, `pandas`。
*   **優點**：比 Python `for-loop` 快 100~1000 倍。
*   **本專案應用**：指標計算 (SMA, RSI, Volume Profile) 全部依賴 `numpy`。

### OLAP Database (DuckDB)
*   **概念**：Columnar Storage (行式存儲)，專為分析 (Analytics) 設計。
*   **應用**：處理歷史 tick data (億級數據)。DuckDB 可以在秒級完成數千萬筆數據的聚合。

---

## 5. 前端與視覺化 (Frontend & Visualization)

### Reactive Programming (響應式編程)
*   **概念**：數據變動 -> 自動觸發更新 (Push-based)。
*   **實作**：`Dash Callback`。
    *   Input (`Interval`, `Zoom`) 改變 -> 觸發 Function -> Output (`Figure`) 更新。

### MVC Architecture (Model-View-Controller)
*   **Model**: `state.py` (數據處理、商業邏輯)。
*   **View**: `chart.py` (繪圖邏輯、視覺呈現)。
*   **Controller**: `logic.py` / `server.py` (接收使用者輸入，調度 Model 更新 View)。

---

## 6. 工程最佳實踐 (Best Practices)

### Defensive Programming (防禦式編程)
*   **概念**：預設系統會出錯，預先處理邊界情況 (Edge Cases)。
*   **應用**：
    *   `try...finally` 確保資料庫連線關閉。
    *   Ring Buffer 讀取時檢查 `count > 0`。
    *   Volume Profile 遇到空切片 (`empty slice`) 時的回退保護。

### Lazy Import (延遲載入)
*   **概念**：只在需要時才 import 重量級套件 (`pandas`, `plotly`)。
*   **優點**：大幅加快系統啟動速度 (`start_engine.py`)，特別是對於不需要繪圖的 Strategy Process。

### Type Hinting (型別提示)
*   **概念**：Python 是動態語言，但加上 Type Hint (`x: int`, `-> float`) 可增加可讀性並利用 IDE 檢查錯誤。
