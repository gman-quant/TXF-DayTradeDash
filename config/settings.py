# config/settings.py

import os

# Kafka broker(單一真相)。**預設 localhost** —— 這是給 clone 下來的人的正確預設,
# 而不是某台特定機器的位址。自己的環境用環境變數 `GALE_KAFKA_BROKER` 覆寫,
# 或在每個指令上帶 `--broker <ip>:9092`。
#
# 2026-08-12 從一個寫死的私網位址改過來(本 repo 要開 public)。
# 改預設值本身是**會弄壞生產鏈的**動作:workspace 的 `daily_sync.py` 第 4 步
# (`batch_export_bidask`)與第 8 步(`export_md_raw`)**原本都不傳 `--broker`**,
# 靠的就是那個寫死的預設 —— 而那兩步的資料**補不回來**(五檔無歷史 API、
# md_raw 的 Kafka 只留 30 天)。所以同一次改動裡把那兩步改成**顯式傳入**,
# `TXF_Live_Monitor.bat` 也改成先設環境變數。
# 教訓:能被「預設值剛好對」掩蓋的相依,遲早會在改預設值那天靜靜爆掉。
KAFKA_BROKER = os.environ.get("GALE_KAFKA_BROKER", "localhost:9092")

# 設定資料根目錄
DATA_ROOT = "D:/txf-data"

# 批次匯出 HTML 快照的存放根目錄(可被 batch_export --out-dir 覆寫)。
# 快照是「可重生的衍生快取」(source 為 DATA_ROOT 的 parquet),故獨立於 repo、放 D 槽。
SNAPSHOT_ROOT = "D:/txf-snapshot"

# [Central Config] 共享記憶體預設容量 (筆數)
SHM_CAPACITY = 400000

# 設定昨收價 (請根據每日實際狀況更新，或未來接 API 自動抓)
PREV_CLOSE_PRICE = 31669.0

# 定義支援的 K 線週期 (標籤: 毫秒數)
TIMEFRAMES = {
    '5s':    5000,
   '.5m':   30000,
    '1m':   60000,
    '3m':  180000,
    '5m':  300000,
   '10m':  600000,
   '15m':  900000,
   '30m': 1800000,
   '60m': 3600000,
}

# 預設顯示週期
DEFAULT_TIMEFRAME = '1m'

# -------------------------------------------------------
# 交易時段實際開盤/收盤時間 (用於 K 線 Bucket 對齊)
# 以距本地 (UTC+8) 午夜的「總秒數」表示，方便直接換算。
# 若 TXF 更改開盤時間，只需修改此處。
# -------------------------------------------------------
DAY_OPEN_SECOND   =  8 * 3600 + 45 * 60   # 08:45 = 31500 秒
NIGHT_OPEN_SECOND = 15 * 3600             # 15:00 = 54000 秒
NIGHT_END_SECOND  =  5 * 3600             # 05:00 = 18000 秒