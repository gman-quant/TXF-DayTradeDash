# macOS 自動執行 TXF Gale Dashboard Supervisor SOP

**服務識別碼**：`com.garrett.txf.gale_dashboard_supervisor`
**開發環境**：macOS (Sonoma/Ventura 適用)
**核心邏輯**：週一至週五交易時段自動守護 (Keep Alive)，盤前強制重置，非交易時段自動收工。

---

## ▍ 行為規格與時段定義

| 情境 | 時段 / 時間點 | 行為描述 | 狀態 |
| --- | --- | --- | --- |
| **日盤交易時段** | 08:30 - 13:45 | 斷線自動重啟 (每 20 秒檢查一次) | ✅ Keep Alive |
| **夜盤交易時段** | 14:50 - 05:00 | 跨午夜守護，確保夜盤不中斷 | ✅ Keep Alive |
| **盤前強制重置** | **08:30 & 14:50** | **必殺舊行程並開啟新實例**，確保環境乾淨 | 🔄 Hard Reset |
| **非交易時段** | 其餘時間 | 自動關閉進程，清空重啟標記檔 | 💤 Idle |
| **週末期間** | 週六 / 週日 | 全天不執行，維持系統安靜 | ❌ Disabled |

---

## STEP 1｜準備工作

```bash
# 1. 建立 LaunchAgents 資料夾（若已存在可跳過）
mkdir -p ~/Library/LaunchAgents

# 2. 清理舊的日誌檔（避免權限衝突）
sudo rm -f /tmp/txf_gale_dashboard_supervisor.*

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
            # ==========================================
            # 1. 基礎路徑設定
            # ==========================================
            PROJECT_DIR="/Users/gtai/Projects/txf-gale-engine"
            PROCESS_PATTERN="[b]in.run_supervisor"
            LAST_RESTART_FILE="/tmp/txf_gale_last_restart"

            # ==========================================
            # 2. 時間參數設定 (請依照 HHMM 格式修改)
            # ==========================================
            T_AM_START="0843"   # 日盤守護開始
            T_AM_END="1458"     # 日盤守護結束
            
            T_PM_START="1458"   # 夜盤守護開始
            T_PM_END="0843"     # 夜盤守護結束 (跨日)

            # 強制重置點 (建議設在開盤後 1 分鐘作為緩衝)
            T_RESET_AM="0844"   
            T_RESET_PM="1459"   

            # ==========================================
            # 3. 核心監控迴圈
            # ==========================================
            while true; do
                WEEKDAY=$(date +%u)
                HM=$(date +%H%M)

                # 判斷是否為週一至週六 (1-6)
                if [[ "$WEEKDAY" -ge 1 && "$WEEKDAY" -le 6 ]]; then
                    
                    # --- A. 判定是否在交易時段 (IN_WINDOW) ---
                    IN_WINDOW=0
                    if [[ "$WEEKDAY" -le 5 ]]; then
                        # 週一至週五：24小時無縫接軌判斷
                        if [[ "$HM" -ge "$T_AM_START" && "$HM" -le "$T_AM_END" ]]; then
                            IN_WINDOW=1
                        elif [[ "$HM" -ge "$T_PM_START" || "$HM" -le "$T_PM_END" ]]; then
                            IN_WINDOW=1
                        fi
                    elif [[ "$WEEKDAY" -eq 6 ]]; then
                        # 週六：僅守護至夜盤結束時間 (08:43)
                        # 08:43 之前還在 Window 內，08:44 會跳到 else 執行清理
                        if [[ "$HM" -le "$T_PM_END" ]]; then
                            IN_WINDOW=1
                        fi
                    fi

                    # --- B. 判定是否為重置時間 (IS_RESET_TIME) ---
                    IS_RESET_TIME=0
                    if [[ "$HM" == "$T_RESET_AM" || "$HM" == "$T_RESET_PM" ]]; then
                        # 檢查標記檔內容，確保該分鐘內只執行一次重置
                        if [[ ! -f "$LAST_RESTART_FILE" || $(cat "$LAST_RESTART_FILE" 2>/dev/null) != "$HM" ]]; then
                            IS_RESET_TIME=1
                        fi
                    fi

                    if [[ "$IN_WINDOW" -eq 1 ]]; then
                        # 核心判斷：程式沒在跑 OR 觸發重置時間
                        if ! pgrep -f "$PROCESS_PATTERN" > /dev/null || [[ "$IS_RESET_TIME" -eq 1 ]]; then
                            
                            if [[ "$IS_RESET_TIME" -eq 1 ]]; then
                                echo "[$(date)] >>> 【計畫重置】緩衝時間已到 ($HM)，執行先殺後開 <<<"
                            else
                                echo "[$(date)] >>> 【守護啟動】偵測進程缺失，正在拉起程序... <<<"
                            fi

                            # 啟動前強制清理
                            pkill -15 -f "$PROCESS_PATTERN" || true
                            sleep 2

                            if cd "$PROJECT_DIR"; then
                                source .venv/bin/activate
                                python -m bin.run_supervisor &
                                
                                # 若為重置點，寫入標記檔
                                if [[ "$IS_RESET_TIME" -eq 1 ]]; then
                                    echo "$HM" > "$LAST_RESTART_FILE"
                                fi
                            fi
                        fi
                    else
                        # --- 非交易時段清理 ---
                        if pgrep -f "$PROCESS_PATTERN" > /dev/null; then
                            echo "[$(date)] 交易時段結束 ($HM)，執行關閉。"
                            pkill -15 -f "$PROCESS_PATTERN" || true
                        fi
                        
                        # 非時段內確保標記檔被移除，供下次開盤重置使用
                        # 範圍設在夜盤結束後到日盤開始前
                        if [[ "$HM" -gt "$T_PM_END" && "$HM" -lt "$T_AM_START" ]]; then
                            [[ -f "$LAST_RESTART_FILE" ]] && rm -f "$LAST_RESTART_FILE"
                        fi
                    fi
                else
                    # --- 週末清理 ---
                    [[ -f "$LAST_RESTART_FILE" ]] && rm -f "$LAST_RESTART_FILE"
                    pgrep -f "$PROCESS_PATTERN" > /dev/null && pkill -15 -f "$PROCESS_PATTERN" || true
                fi

                # 每 20 秒檢查一次
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
| **檢查 Python 進程** | `ps aux |

---

## ▍ 故障排除 (Troubleshooting)

1. **Status 顯示 `- 78`**：通常是 plist 權限問題，請重新執行 `chmod 644`。
2. **Status 顯示 `- 1`**：代表 Zsh 腳本報錯，請 `cat /tmp/txf_gale_dashboard_supervisor.err.log` 檢查錯誤訊息。
3. **無法手動 pkill**：因為管家太勤勞了（20秒檢查一次），如要手動停止測試，請先 `bootout` 管家服務。

---

這份文件現在已經是完全體，涵蓋了從環境建立到日後維運的所有場景。