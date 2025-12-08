# config/indicator_config.py

# 定義指標類型常數
TYPE_OVERLAY = 'overlay'       # 疊加在主圖 (如 SMA, VWAP)
TYPE_OSCILLATOR = 'oscillator' # 獨立副圖 (如 Momentum, RSI, Volume)

"""
INDICATOR SCHEMA DEFINITION
---------------------------
Each indicator entry in `INDICATORS_SETUP` is a dictionary:

- id (str): Unique identifier for the indicator. Used as key in history buffers.
- func (str): Name of the function in `core/numba_engine.py` to execute.
- args (List): Fixed arguments passed to the function (e.g., [period, threshold]).
- type (str): `TYPE_OVERLAY` (Main Chart) or `TYPE_OSCILLATOR` (Sub Chart) or 'hidden'.
- inputs (List[str]): List of data arrays from RingBuffer to pass as arguments.
    - Options: ['close', 'volume', 'type', 'timestamp', 'cum_close', etc...]
- color (str): Hex color code (e.g., "#FF0000") or 'dynamic'.
- style (str): 'solid', 'dash', 'bar', etc.
- yaxis (str, optional): 'y' (Left Axis) or 'y2' (Right Axis, for oscillators).
"""

# 指標配置清單
# 系統會自動讀取此清單，並去 core/numba_engine.py 找對應的函數執行
INDICATORS_SETUP = [
    # ==========================================
    # 📈 Main Chart Indicators (Overlay)
    # ------------------------------------------
    # Price, VWAP, Bands, SMA, etc.
    # ==========================================
    
    # 1. Underlying Price (Base)
    {
        'id': 'Underlying_Price',
        'func': 'get_current_value',    # 對應新的 Numba 函數
        'args': [0],                    # 參數給 0 即可 (不需要 period)
        'type': TYPE_OVERLAY,           # 畫在主圖
        'inputs': ['underlying_price'], # ⚠️ 需要這個累積陣列
        'color': "#7F4A98",             # 棕色
        'style': 'solid'                # 實線 (區別於 50MA 的虛線)
    },
    # 當盤最高價
    {
        'id': 'Session_High',
        'func': 'get_current_value',
        'args': [0],                 
        'type': TYPE_OVERLAY,
        'inputs': ['session_high'],   
        'color': "#004E00",         
        'style': 'solid'              
    },
    # 當盤 VWAP
    {
        'id': 'Session_VWAP',
        'func': 'calc_session_vwap',   
        'args': [0],                   
        'type': TYPE_OVERLAY,          
        'inputs': ['cum_pv', 'cum_volume'], 
        'color': "#008692",           
        'style': 'solid'               
    },
    # 當盤最低價
    {
        'id': 'Session_Low',
        'func': 'get_current_value',
        'args': [0],
        'type': TYPE_OVERLAY,
        'inputs': ['session_low'],
        'color': "#780000",         
        'style': 'solid'
    },
    
    # 3. Volatility Indicators (Bollinger / STD)
    {
        'id': 'SMA_20',
        'func': 'calc_sma',
        'args': [20],
        'type': TYPE_OVERLAY,
        'inputs': ['cum_close'],
        'color': "#FFA500", # Orange
        'style': 'solid'
    },
    {
        'id': 'Boll_Upper',
        'func': 'calc_bollinger_band',
        'args': [20, 2.0, 1], # Period=20, Std=2.0, Direction=1 (Upper)
        'type': TYPE_OVERLAY,
        'inputs': ['close', 'cum_close'],
        'color': '#00FF00', # Green
        'style': 'dash'
    },
    {
        'id': 'Boll_Lower',
        'func': 'calc_bollinger_band',
        'args': [20, 2.0, -1], # Period=20, Std=2.0, Direction=-1 (Lower)
        'type': TYPE_OVERLAY,
        'inputs': ['close', 'cum_close'],
        'color': '#FF0000', # Red
        'style': 'dash'
    },
    {
        'id': 'STD_20',
        'func': 'calc_std_dev',
        'args': [20],
        'type': 'hidden', 
        'inputs': ['close', 'cum_close'],
        'color': '#FFFFFF',
        'style': 'solid'
    },
    # Bollinger Upper = SMA + 2*STD
    # 由於我們沒有直接的 "Combine" 函數，這裡我們暫時只畫出 SMA
    # 若要畫通道，需要在 numba_engine 加 calc_bollinger_upper
    # 其實最簡單的作法是：Dashboard 直接訂閱 STD 和 SMA，自己在前端畫 Area
    # 或者我們在 Numba 寫 calc_bollinger_upper
    # 這裡先註冊 Z-Score (Mean Reversion)
    
    # 4. Z-Score (Mean Reversion Signal)
    {
        'id': 'Z_Score_60',
        'func': 'calc_zscore',
        'args': [60],
        'type': TYPE_OSCILLATOR, # 加到副圖觀察
        'inputs': ['close', 'cum_close'],
        'color': '#BBBBBB', # 灰色
        'style': 'solid',
        'yaxis': 'y2' # 用右軸，因為數值在 -3 ~ +3 之間
    },
    # ==========================================
    # 👻 Hidden Indicators (Computation Only)
    # ------------------------------------------
    # Metrics needed for dashboard text but not plotted.
    # ==========================================

    # 總成交量
    {
        'id': 'Total_Vol',
        'func': 'get_current_value',
        'args': [0],
        'type': 'hidden',            # 標記為 hidden (稍後 Dashboard 會過濾掉不畫)
        'inputs': ['total_volume'],  # 對應 RingBuffer 的累積量
        'color': '#FFFFFF',
        'style': 'solid'
    },
    # --- 技術指標 ---
    {
        'id': 'Max_250',
        'func': 'calc_rolling_max',
        'args': [250],               # 過去 250 筆
        'type': TYPE_OVERLAY,        # 疊加在主圖
        'inputs': ['close'],         # 只需要收盤價
        'color': '#00FF00',          # 綠色 (壓力線)
        'style': 'dash'
    },
    {
        'id': 'Min_250',
        'func': 'calc_rolling_min',
        'args': [250],     
        'type': TYPE_OVERLAY,
        'inputs': ['close'],
        'color': '#FF0000',          # 紅色 (支撐線)
        'style': 'dash'
    },
    # --- 移動平均線 (SMA) ---
    {
        'id': 'SMA_60',        
        'func': 'calc_sma',           
        'args': [60],                 
        'type': TYPE_OVERLAY,        
        'inputs': ['cum_close'], 
        'color': "#FFF000",           
        'style': 'solid'               
    },
    {
        'id': 'SMA_3min',
        'func': 'calc_sma_time',
        'args': [3 * 60000], # 3 分鐘 (毫秒)
        'type': TYPE_OVERLAY,
        'inputs': ['cum_close', 'timestamp'],
        'color': "#E0930F",
        'style': 'solid'
    },
    
    # ==========================================
    # 📉 Sub Chart Indicators (Oscillator)
    # ------------------------------------------
    # Volume, CVD, Delta, Momentum
    # ==========================================

    # 1. Session CVD (Accumulated Volume Delta)


    # 1. 當盤 CVD (數值大 -> 用右軸 y2)
    {
        'id': 'CVD',
        'func': 'calc_session_cvd',
        'args': [0],
        'type': TYPE_OSCILLATOR,
        'inputs': ['cum_buy_vol', 'cum_sell_vol'],
        'color': "#FFF000",
        'style': 'solid',
        'yaxis': 'y2'       # 🆕 新增：指定使用右側 Y 軸
    },
    
    # # 2. 短線 Delta (數值小 -> 用預設左軸)
    # {
    #     'id': 'RCVD_180',
    #     'func': 'calc_period_delta',
    #     'args': [180],
    #     'type': TYPE_OSCILLATOR,
    #     'inputs': ['cum_buy_vol', 'cum_sell_vol'],
    #     'color': 'dynamic',
    #     'style': 'bar'
    #     # 沒寫 yaxis 預設就是 y1 (左軸)
    # },
    
    # 1. 🟢 螞蟻搬象 (Retail Flow)
    # 監控 1~4 口的散戶動向。
    # 這是市場的「背景噪音」或「反向指標」。
    {
        'id': 'Retail_Flow',
        'func': 'calc_small_lot_net',
        'type': TYPE_OSCILLATOR,
        'color': '#00FF00',  # 綠色
        'inputs': ['volume', 'type'], 
        'args': [250, 5],    # 統計 < 5 的單
        'yaxis': 'y',
        'style': 'bar'
    },

    # 2. 🟡 主力部隊 (Smart Money)
    # 監控 >= 5 口的單。
    # 日盤：這是中實戶與程式單。
    # 夜盤：這就是主力了！
    {
        'id': 'Smart_Money', # 改個名字，這是一般大戶
        'func': 'calc_large_lot_net',
        'type': TYPE_OSCILLATOR,
        'color': '#FFFF00',  # 黃色
        'inputs': ['volume', 'type'], 
        'args': [250, 5],    # 統計 >= 5 的單
        'yaxis': 'y',
        'style': 'bar'
    },

    # 3. 🔴 巨鱷核彈 (Whale Nuke) - 專門抓那筆 299 口的
    # 監控 >= 15 口的超大單。
    # 這種單出現時，通常是日盤的「趨勢發動點」或「停損引爆」。
    # 夜盤可能幾天都看不到一根，但一出來就是送分題。
    {
        'id': 'Whale_Nuke',
        'func': 'calc_large_lot_net',
        'type': TYPE_OSCILLATOR,
        'color': '#FF0000',  # 紅色 (極度顯眼)
        'inputs': ['volume', 'type'], 
        'args': [250, 15],   # 門檻拉高到 15
        'yaxis': 'y',
        'style': 'bar'
    }

    

]