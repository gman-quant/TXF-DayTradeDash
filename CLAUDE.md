# txf-gale-engine(repo: TXF-DayTradeDash)— AI Agent 指南

低延遲 tick 管線 + Dash 即時「戰情室」看板(TXF 當沖監控):supervisor 開兩個子行程 —— 攝取(Kafka live/history 或 parquet 回放)寫入共享記憶體 ring buffer;Dash UI 讀 buffer 算指標(VWAP 帶、CVD/COFI/COBI、volume profile,Numba 加速)、2 秒刷新。另有 headless 批次工具輸出 HTML 快照與 BidAsk parquet —— **這兩個匯出是每日 sync 管線的一部分,本 repo 仍在生產鏈上**。

## 環境與執行

- 一律 `.\.venv\Scripts\python.exe`(Python 3.12)+ `PYTHONUTF8=1`(bin/*.py 沒自我 reconfigure)。cwd 必須是 repo 根(.bat 會硬檢查)。
- 離線驗證用 parquet 回放(安全):`.venv/Scripts/python.exe -m bin.run_supervisor --source parquet --date 2025-12-08 --speed 0`(dashboard 在 **8051**;live 模式在 **8050**)。
- Live 模式(`-m bin.run_supervisor` 不帶 --source)連 Kafka 192.168.1.50 —— **agent 別隨便啟動**(吃 live feed、開伺服器)。
- **沒有測試套件**(tests/ 刻意刪除);驗證 = py_compile + 已知日期的 --speed 0 回放。
- HTML 快照:`tools/batch_export_html.py --source parquet --session full` → `D:\txf-snapshot\`;BidAsk:`tools/batch_export_bidask.py`(需 Kafka)。

## 載重事實

- **SHM 配對契約**:ring buffer 名 = `gale_shm_{topic}_{run_id}`;獨立起 `bin.run_dashboard` 必須帶與攝取端相同的 `--run-id`/`--capacity`,否則永遠卡在 "Waiting for Shared Buffer"(x_gale.txt 裡的示範就少了 --run-id,是壞的)。
- **--session 語意各工具不同**:run_supervisor 只收 `day|night`;ingest/replay 收 `day|night|full`;batch_export_html 收 `day|night|both|full`(README 參數表已於 2026-07-04 修正為按工具分列;有疑義以各腳本 argparse 為準)。
- **週五夜盤要用週六的日期存取**(夜盤歸「下一日曆日」的 date+session 定址;與 data-lake 的「夜盤歸前一交易日」不同套,別搞混:這裡是 `config/txf_calendar.py` 的 get_history_range 語意)。
- supervisor **刻意無視 SIGTERM**(AutoRun 管家遺產)——停它用 Ctrl+C 或在 repo 根建 `.restart_signal` 檔;殘留的 .restart_signal 會讓下次啟動立即自殺。
- 圖表刻意用 SVG Scatter **不用 WebGL**(rangebreaks 不相容、線會整條消失)——別「優化」成 Scattergl。
- 指標開關/參數在 `config/indicator_config.py`,色彩在 `config/ui_theme.py` —— 走 config 不寫死。
- Live Kafka 模式**沒有 TSE 現貨 feed**:TAIEX 疊圖與真 basis 只在 parquet 回放存在。
- prev-close 從 `kbars/1d` 經 DuckDB 讀,失敗回 0.0(不崩但基準線全錯)——看到 0 基準先查 1d 檔。
- `.gitignore` 有 `x*` 規則:x 開頭的新檔會被靜默忽略(唯一例外:`!x_pic/` 反向排除;x_gale.txt 是本地小抄);*.html/*.parquet 產物也進不了 git,是設計。
- `AutoRun.md` = 已退役的 macOS launchd 文件(**純歷史**;現行 Windows 上是手動 .bat + workspace 每日 sync)。它唯一的現役價值:解釋 SIGTERM-ignore 的由來。
- 多日回放 SHM 容量 = 400000 × 檔數,長區間會吃大記憶體;Windows SHM unlink 非原子,連續重啟偶發建立失敗(碼內有 retry)。
