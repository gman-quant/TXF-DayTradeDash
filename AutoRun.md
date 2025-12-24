# macOS 自動執行 TXF Gale Dashboard Supervisor SOP

**服務識別碼**：`com.garrett.txf.gale_dashboard_supervisor`
**開發環境**：macOS Monterey (12.7.6) 適用 (亦相容於 Ventura / Sonoma / Sequoia)
**核心邏輯**：週一至週五 24H 無縫守護，盤後緩衝重置，週六早盤後自動收工。

---

## ▍ 行為規格與時段定義

| 情境 | 時段 / 時間點 | 行為描述 | 狀態 |
| --- | --- | --- | --- |
| **週一至週五全天** | 08:43 ~ 隔日 08:43 | **24H 無縫守護**：斷線自動重啟，午休與清晨維持畫面。 | ✅ Keep Alive |
| **日盤換盤重置** | **每天 08:44** | **緩衝重置**：殺掉昨晚夜盤進程，開啟今日日盤。 | 🔄 Hard Reset |
| **夜盤換盤重置** | **每天 14:59** | **緩衝重置**：殺掉今日日盤進程，開啟今日夜盤。 | 🔄 Hard Reset |
| **週六結算** | **週六 08:44** | **週末收工**：夜盤結束後緩衝至此時，執行最後清理。 | 💤 Cleanup |
| **週日期間** | 週日全天 | 管家持續守護，確保無異常進程運行。 | ❌ Disabled |

---

## STEP 1｜準備工作

```bash
# 1. 建立 LaunchAgents 資料夾（若已存在可跳過）
mkdir -p ~/Library/LaunchAgents

# 2. 清理舊的日誌檔（避免權限衝突）
sudo rm -f /tmp/txf_gale_dashboard_supervisor.*
rm -f /tmp/txf_gale_last_restart
```

---

## STEP 2｜建立 LaunchAgent 檔案

執行以下指令進入編輯器：

```bash
nano ~/Library/LaunchAgents/com.garrett.txf.gale_dashboard_supervisor.plist
```

---

## STEP 3｜貼上終極版配置內容

複製下方完整 XML 內容並貼上：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.garrett.txf.gale_dashboard_supervisor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>-lc</string>
        <string>
            <![CDATA[
            # ============================================================
            # 1. 環境設定 (Environment)
            # ============================================================
            PROJECT_DIR="/Users/gtai/Projects/txf-gale-engine"
            PYTHON_EXEC="$PROJECT_DIR/.venv/bin/python"
            PROCESS_PATTERN="[b]in.run_supervisor"
            LAST_RESTART_FILE="/tmp/txf_gale_last_restart"

            # ============================================================
            # 2. 時間參數 (Scheduling)
            # ============================================================
            T_AM_START="0843"   # 日盤守護起
            T_AM_END="1458"     # 日盤守護止
            T_PM_START="1458"   # 夜盤守護起
            T_PM_END="0843"     # 夜盤守護止 (跨日)

            T_RESET_AM="0844"   # 日盤強制重置點
            T_RESET_PM="1459"   # 夜盤強制重置點

            # ============================================================
            # 3. 監控主程式
            # ============================================================
            while true; do
                WEEKDAY=$(date +%u)
                HM=$(date +%H%M)
                NOW="[$(date '+%Y-%m-%d %H:%M:%S')]"

                # --- A. 時段判定 (Decision Matrix) ---
                IN_WINDOW=0
                if [[ "$WEEKDAY" -le 5 ]]; then
                    # 平日 (週一至週五)
                    [[ "$HM" -ge "$T_AM_START" && "$HM" -le "$T_AM_END" ]] && IN_WINDOW=1
                    [[ "$HM" -ge "$T_PM_START" || "$HM" -le "$T_PM_END" ]] && IN_WINDOW=1
                elif [[ "$WEEKDAY" -eq 6 ]]; then
                    # 週六 (守護週五夜盤至收盤)
                    [[ "$HM" -le "$T_PM_END" ]] && IN_WINDOW=1
                fi

                # --- B. 重置判定 (Reset Logic) ---
                IS_RESET_TIME=0
                if [[ "$HM" == "$T_RESET_AM" || "$HM" == "$T_RESET_PM" ]]; then
                    # 只有在尚未紀錄過此分鐘時才觸發
                    [[ $(cat "$LAST_RESTART_FILE" 2>/dev/null) != "$HM" ]] && IS_RESET_TIME=1
                fi

                # --- C. 動作執行 (Action) ---
                if [[ "$IN_WINDOW" -eq 1 ]]; then
                    # 檢查進程：缺失中 或 觸發定時重置
                    if ! pgrep -f "$PROCESS_PATTERN" > /dev/null || [[ "$IS_RESET_TIME" -eq 1 ]]; then
                        
                        echo "$NOW [RESTART] 偵測到需要重啟，執行全域清理..."
                
                        # 1. 殺掉主程式 (Supervisor)
                        pkill -9 -f "$PROCESS_PATTERN" || true
                    
                        # 2. 擊殺所有屬於這個專案的 Python 進程 (使用專案關鍵字 'gale')
                        # 這會把 IngestServer, Dashboard 等子進程一網打盡
                        pkill -9 -f "gale" || true
                    
                        # 3. 擊殺霸佔 8050 埠號的進程 (最保險，確保 Dashboard 一定死掉)
                        lsof -ti:8050 | xargs kill -9 2>/dev/null || true
                
                        sleep 2
                        
                        if cd "$PROJECT_DIR" 2>/dev/null; then
                            "$PYTHON_EXEC" -u -m bin.run_supervisor &
                            [[ "$IS_RESET_TIME" -eq 1 ]] && echo "$HM" > "$LAST_RESTART_FILE"
                        else
                            echo "$NOW [ERROR] 無法切換至專案路徑: $PROJECT_DIR"
                        fi
                    fi
                else
                    # --- D. 清理動作 (Cleanup) ---
                    if pgrep -f "$PROCESS_PATTERN" > /dev/null; then
                        echo "$NOW [OFF] 交易時段結束 ($HM)，執行清理關閉"
                        pkill -15 -f "$PROCESS_PATTERN" || true
                    fi
                    
                    # 在非時段內確保標記檔被移除 (避免與週一開盤衝突)
                    [[ -f "$LAST_RESTART_FILE" ]] && rm -f "$LAST_RESTART_FILE"
                fi

                sleep 20
            done
            ]]>
        </string>
    </array>

    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/txf_gale_dashboard_supervisor.out.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/txf_gale_dashboard_supervisor.err.log</string>
</dict>
</plist>
```

---

## STEP 4｜存檔並載入服務

按 `Ctrl + O` 再按 `Enter` 存檔，按 `Ctrl + X` 離開。隨後執行：

```bash
# 1. 修正權限（啟動成功的關鍵）
chmod 644 ~/Library/LaunchAgents/com.garrett.txf.gale_dashboard_supervisor.plist

# 2. 徹底卸載舊任務 (不論是否存在)
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.garrett.txf.gale_dashboard_supervisor.plist 2>/dev/null || true

# 3. 正式載入並啟用
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.garrett.txf.gale_dashboard_supervisor.plist
launchctl enable gui/$(id -u)/com.garrett.txf.gale_dashboard_supervisor

```

---

## STEP 5｜驗證與監控

### 檢查狀態

```bash
# 應看到第一個欄位有 PID 數字
launchctl list | grep txf.gale

```

### 觀察日誌

```bash
# 查看程式啟動紀錄與 Keep Alive 狀況
tail -f /tmp/txf_gale_dashboard_supervisor.out.log

# 查看有無報錯
tail -f /tmp/txf_gale_dashboard_supervisor.err.log

```

---

## STEP 6｜維運管理常用指令表

| 需求 | 指令 |
| --- | --- |
| **立即強制重啟管家** | `launchctl kickstart -kp gui/$(id -u)/com.garrett.txf.gale_dashboard_supervisor` |
| **手動停止服務 (不再自動重啟)** | `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.garrett.txf.gale_dashboard_supervisor.plist` |
| **修改 plist 後重新生效** | 依序執行 `Step 4` 的第 2 與第 3 條指令 |
| **檢查目前運行中的 Python** | `ps aux | grep [b]in.run_supervisor` |

---

## ▍ 維護錦囊 (Cheat Sheet)

### 1. 想要「徹底暫停」所有監控與程式

因為設定了 `KeepAlive`，直接用 `pkill` 殺掉 Python 程式，它會在 20 秒內復活。若要進行程式更新或維護，請務必先停用管家：

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.garrett.txf.gale_dashboard_supervisor.plist

```

### 2. 懷疑為什麼沒有重啟？

* **看管家日誌**：`tail -n 50 /tmp/txf_gale_dashboard_supervisor.out.log`
（若有執行重置，會顯示 `>>> 【計畫重置】... <<<`）
* **看 Python 報錯**：`tail -n 50 /tmp/txf_gale_dashboard_supervisor.err.log`
（檢查是否因為 API Key 過期或網路中斷導致 Python 啟動即崩潰）

### 3. 如何修改「換盤重置」時間？

若交易所調整開盤時間，直接編輯 `.plist` 修改 `T_RESET_AM` 變數（例如改為 `0831`），存檔後執行 **Step 6 的重載指令** 即可。

---

## ▍ 故障排除 (Troubleshooting)

1. **Status 顯示 `- 78`**：通常是 plist 權限問題，請重新執行 `chmod 644`。
2. **Status 顯示 `- 1`**：代表 Zsh 腳本語法報錯，請檢查 XML 內容。
3. **無法手動 pkill**：因為管家太勤勞了（20秒檢查一次），請先執行 `bootout`。