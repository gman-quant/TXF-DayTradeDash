# 🚀 TXF Gale Quant Engine (v1.0)

**TXF Gale Quant Engine** 是一個專為台灣指數期貨 (TXF) 設計的超低延遲量化數據管線與實時監控系統。

本版本 (V1.0) 專注於 **Tick 成交數據** 的極速處理與視覺化，採用 **RingBuffer + Numba** 架構，實現了 $O(1)$ 複雜度的實時指標運算，並透過 Dash 提供毫秒級的戰情室監控。

---

## 🌟 核心功能 (Key Features)

### ⚡️ 極限效能 (Performance)
* **RingBuffer 架構**：使用預先分配記憶體的 NumPy 陣列，實現零動態分配 (Zero-allocation) 的數據寫入。
* **Numba JIT 加速**：指標運算邏輯編譯為機器碼，計算速度接近 C/C++。
* **O(1) 演算法**：利用累積和 (Prefix Sum) 技術，無論計算 5 分鐘還是 5 小時的 VWAP，耗時皆相同且極低。
* **Smart Downsampling**：前端繪圖採用智慧降頻與二分搜尋 (Bisect)，即使回溯 5 萬筆數據，CPU 佔用率仍低於 5%。

### 📊 專業視覺化 (Professional Visualization)
* **暗黑戰情室 UI**：針對長時間看盤設計的低對比度深色主題。
* **多週期 K 線切換**：支援 5s, 1m, 5m, 15m 等多種週期的 K 線即時聚合與切換。
* **雙向同步縮放**：主圖 (價格) 與副圖 (動能) 的 X 軸縮放與平移完美同步。
* **即時戰情看板**：即時顯示當盤高低、波幅、開盤漲跌、VWAP 乖離率及基差。

### 🔄 雙模運作 (Dual Mode)
* **Live Mode**：連接 Kafka 進行實時串流監控。
* **History Mode**：指定日期進行歷史數據全速回放 (Backtest Replay)，用於策略驗證與除錯。

---

## 🏗️ 系統架構 (Architecture)

```mermaid
graph LR
    Kafka["Kafka / History Loader"] --> Consumer["Ingestion Layer"]
    Consumer --> Core["Core Processor"]
    
    subgraph Core Processor
        RingBuffer["RingBuffer (O1 Storage)"]
        Numba["Numba Engine (JIT Calc)"]
        Manager["Indicator Manager"]
    end
    
    Core --> RingBuffer
    RingBuffer --> Numba
    Numba --> Manager
    
    Manager --> Dash["Analysis Layer (Dash Server)"]
    Dash --> Browser["Web UI"]
````

-----

## 🛠️ 安裝與設定 (Setup)

### 1\. 環境需求

  * Python 3.10+
  * Kafka Server

### 2\. 安裝依賴

```bash
pip install -r requirements.txt
```

*(核心依賴: `confluent-kafka`, `numpy`, `numba`, `dash`, `plotly`, `pandas`, `uvloop`)*

### 3\. 配置設定

修改 `config/settings.py` 與 `config/indicator_config.py` 以調整參數：

  * **PREV\_CLOSE\_PRICE**: 設定昨日收盤價 (計算漲跌幅用)。
  * **INDICATORS\_SETUP**: 定義要計算的指標參數 (如 SMA 週期)。

-----

## 🚀 如何執行 (Usage)

### 1\. 實時監控模式 (Live Mode)

預設連接 Kafka 並開始接收即時 Tick。

```bash
python -m core.core_processor
```

### 2\. 歷史回測模式 (History Mode)

指定日期，全速回放當天數據以驗證邏輯。

```bash
python -m core.core_processor --mode history --date 2025-12-02  --session day
```

*加上 `--session night` 可回測夜盤。*

-----

## 📂 專案結構 (Project Structure)

```text
txf-gale-engine/
├── analysis/           # 視覺化與前端邏輯
│   ├── dashboard_server.py  # Dash 伺服器入口
│   ├── dash_layout.py       # HTML 佈局與樣式
│   └── dash_logic.py        # 繪圖數據處理核心
├── config/             # 系統配置
│   ├── indicator_config.py  # 指標定義
│   ├── settings.py          # 全域參數
│   ├── txf_calendar.py      # 交易日曆邏輯
│   └── ui_theme.py          # UI 顏色主題
├── core/               # 運算核心
│   ├── core_processor.py    # 主程式入口 (Controller)
│   ├── ring_buffer.py       # 高效能數據儲存
│   ├── numba_engine.py      # JIT 計算函數庫
│   └── indicator_manager.py # 指標管理與調度
├── data_schemas/       # Protobuf 定義
├── ingestion/          # 數據攝入層 (Kafka)
└── tools/              # 實用工具 (Data Profiler)
```

-----

## ⚠️ Disclaimer

本系統僅供量化研究與技術分析使用，不構成任何投資建議。高頻交易涉及高風險，請謹慎使用。

-----