# config/indicator_config.py

# 定義指標類型常數
TYPE_OVERLAY = 'overlay'       # 疊加在主圖 (如 SMA, VWAP)
TYPE_OSCILLATOR = 'oscillator' # 獨立副圖 (如 Momentum, RSI, Volume)

# 指標配置清單
# 系統會自動讀取此清單，並去 core/numba_engine.py 找對應的函數執行
INDICATORS_SETUP = [
    # --- 價格主圖指標 (Main Chart) ---
    # 當盤 VWAP (Session VWAP)
    {
        'id': 'Underlying_Price',
        'func': 'get_current_value',   # 對應新的 Numba 函數
        'args': [0],                   # 參數給 0 即可 (不需要 period)
        'type': TYPE_OVERLAY,          # 畫在主圖
        'inputs': ['underlying_price'], # ⚠️ 需要這兩個累積陣列
        'color': "#008692",            # 棕色
        'style': 'solid'               # 實線 (區別於 50MA 的虛線)
    },
    {
        'id': 'Session_VWAP',
        'func': 'calc_session_vwap',   
        'args': [0],                   
        'type': TYPE_OVERLAY,          
        'inputs': ['cum_pv', 'cum_volume'], 
        'color': "#008692",           
        'style': 'solid'               
    },
    # 當盤最高價
    {
        'id': 'Session_High',
        'func': 'get_current_value',   # 直接讀取
        'args': [0],                   # 參數給 0 即可 (沒用到)
        'type': TYPE_OVERLAY,
        'inputs': ['session_high'],    # 指定輸入源
        'color': "#004E00",            # 綠色
        'style': 'solid'                 # 點線
    },

    # 當盤最低價
    {
        'id': 'Session_Low',
        'func': 'get_current_value',
        'args': [0],
        'type': TYPE_OVERLAY,
        'inputs': ['session_low'],
        'color': "#780000",            # 紅色
        'style': 'solid'
    },

    {
        'id': 'Total_Vol',
        'func': 'get_current_value',
        'args': [0],
        'type': 'hidden',          # 標記為 hidden (稍後 Dashboard 會過濾掉不畫)
        'inputs': ['total_volume'],  # 對應 RingBuffer 的累積量
        'color': '#FFFFFF',
        'style': 'solid'
    },

    {
        'id': 'SMA_180',        
        'func': 'calc_sma',           
        'args': [180],                 
        'type': TYPE_OVERLAY,        
        'inputs': ['close'], 
        'color': "#FFF000",           
        'style': 'solid'               
    },
    {
        'id': 'SMA_3min',
        'func': 'calc_sma_time',
        'args': [3 * 60000], # 3 分鐘 (毫秒)
        'type': TYPE_OVERLAY,
        'inputs': ['close', 'timestamp'],
        'color': "#E0930F",
        'style': 'solid'
    },
    # 1. 過去 60 筆的最高價 (High Band)
    {
        'id': 'Max_180',
        'func': 'calc_rolling_max',
        'args': [180],                  # 過去 60 筆
        'type': TYPE_OVERLAY,          # 疊加在主圖
        'inputs': ['close'],           # 只需要收盤價
        'color': '#00FF00',            # 綠色 (壓力線)
        'style': 'dot'
    },

    # 2. 過去 60 筆的最低價 (Low Band)
    {
        'id': 'Min_180',
        'func': 'calc_rolling_min',
        'args': [180],                  # 過去 60 筆
        'type': TYPE_OVERLAY,
        'inputs': ['close'],
        'color': '#FF0000',            # 紅色 (支撐線)
        'style': 'dot'
    },
    # {
    #     'id': 'Max_5m',
    #     'func': 'calc_rolling_max_time',
    #     'args': [10 * 60000],                  # 5 分鐘 = 300,000 毫秒
    #     'type': TYPE_OVERLAY,
    #     'inputs': ['close', 'timestamp'],   # 🆕 需要 close 和 timestamp 兩個陣列
    #     'color': '#8000FF',                 # 紫色 (與 Tick-Based 區分)
    #     'style': 'dash'
    # },
    # {
    #     'id': 'Min_5m',
    #     'func': 'calc_rolling_min_time',
    #     'args': [10 * 60000],                  # 5 分鐘 = 300,000 毫秒
    #     'type': TYPE_OVERLAY,
    #     'inputs': ['close', 'timestamp'],   # 🆕 需要 close 和 timestamp 兩個陣列
    #     'color': '#FF8000',                 # 橘色 (與 Tick-Based 區分)
    #     'style': 'dash'
    # },

    # --- 副圖指標 (Sub Chart) ---
    {
        'id': 'Mom_180ticks',
        'func': 'calc_price_change',
        'args': [180],
        'type': TYPE_OSCILLATOR,
        'inputs': ['close'],
        'color': 'dynamic', # 特殊標記：代表紅綠變色
        'style': 'bar'
    }
]