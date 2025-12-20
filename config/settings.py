# config/settings.py

# 設定昨收價 (請根據每日實際狀況更新，或未來接 API 自動抓)
PREV_CLOSE_PRICE = 28220.0

# 定義支援的 K 線週期 (標籤: 毫秒數)
TIMEFRAMES = {
    '5s':    5000,
    '30s':  30000,
    '60s':  60000,
    '3m':  180000,
    '5m':  300000,
    '15m': 900000,
}

# 預設顯示週期
DEFAULT_TIMEFRAME = '5s'