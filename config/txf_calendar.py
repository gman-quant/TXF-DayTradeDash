# config/txf_calendar.py

from datetime import datetime, timedelta, time as dt_time
from typing import Tuple, Literal

# ============================================================
# ⚙️ TXF 交易時間常數定義 (已包含邊界預留空間)
# ============================================================

# 日盤 (RTH) 預留時間
DAY_SESSION_START   = dt_time( 8, 30)      # 實際開盤 08:45
DAY_SESSION_END     = dt_time(13, 45, 5)   # 實際收盤 13:45 (+5s 預留)

# 夜盤 (ETH) 預留時間
NIGHT_SESSION_START = dt_time(14, 50)      # 實際開盤 15:00
NIGHT_SESSION_END   = dt_time( 5,  0, 5)   # 實際收盤 05:00 (+5s 預留)


# ------------------------------------------------------------
# 📦 Helper: 檢查是否為週末長休市 (週六 05:00 ~ 週一 08:30)
# ------------------------------------------------------------
def is_weekend_market_close(current_dt: datetime = None) -> bool:
    """檢查目前是否處於週末長休市時段。"""
    if current_dt is None:
        current_dt = datetime.now()

    current_time = current_dt.time()
    current_day  = current_dt.weekday() # 0=Mon, ..., 6=Sun
    
    # 週日全天休市
    if current_day == 6:
        return True
        
    # 週六 05:00:05 之後休市
    if current_day == 5 and current_time >= NIGHT_SESSION_END:
        return True
        
    # 週一 08:30 之前休市
    if current_day == 0 and current_time < DAY_SESSION_START:
        return True
        
    return False

# ------------------------------------------------------------
# 🎯 核心函數：取得當前交易盤別的起始時間 Offset
# ------------------------------------------------------------
def get_current_session_offset(
        current_dt: datetime = None
) -> Tuple[datetime, Literal['DAY', 'NIGHT', 'CLOSED']]:
    """
    判斷當前盤別 (日/夜/休市)，並返回該盤別的精確開盤時間 Offset。

    Returns: (開盤時間 Offset, 盤別名稱)
    """
    if current_dt is None:
        current_dt = datetime.now()

    # 1. 週末長休市檢查
    if is_weekend_market_close(current_dt):
        return current_dt, 'CLOSED'
    
    current_date = current_dt.date()
    current_time = current_dt.time()

    # 2. 判斷 夜盤時段 (當日 14:50 之後)
    if current_time >= NIGHT_SESSION_START:
        start_offset = datetime.combine(current_date, NIGHT_SESSION_START)
        return start_offset, 'NIGHT'
    
    # 3. 判斷 隔日夜盤時段 (00:00 到 05:00:05 之間)
    if current_time < DAY_SESSION_START:
        # 日期回溯一天，起始點為前一日的 NIGHT_SESSION_START
        previous_date = current_date - timedelta(days=1)
        start_offset = datetime.combine(previous_date, NIGHT_SESSION_START)
        return start_offset, 'NIGHT'
        
    # 4. 判斷 日盤時段 (08:30 到 13:45:05 之間)
    if DAY_SESSION_START <= current_time < NIGHT_SESSION_START:
        start_offset = datetime.combine(current_date, DAY_SESSION_START)
        return start_offset, 'DAY'
    
    # 5. 處於盤間休息時段
    return current_dt, 'CLOSED'