# config/indicator_config.py

# 定義指標類型常數
TYPE_OVERLAY = 'overlay'       # 疊加在主圖 (如 SMA, VWAP)
TYPE_OSCILLATOR = 'oscillator' # 獨立副圖 (如 Momentum, RSI, Volume)
TYPE_VIRTUAL = 'virtual'       # 虛擬指標 (如 VWAP Bands)

# 預設不顯示的指標 ID 列表 (Default Hidden)
# 您可以在這裡定義需要的標準差倍數
VWAP_MULTIPLIERS = [1.0, 2.0, 2.5]

DEFAULT_OFF_LEGENDS = [
    f'VWAP_Band_{sd}' for sd in VWAP_MULTIPLIERS
] + [
    # 'SMA_3min', 
    # 'SMA_60', 
    # 'Max_250',
    # 'Min_250'
]

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
        'name': 'TAIEX',                # 顯示名稱
        'color': "#7F4A98",            # 棕色
        'style': 'solid',              # 實線 (區別於 50MA 的虛線)
        'legendrank': 110
    },
    # 當盤最高價
    {
        'id': 'Session_High',
        'func': 'get_current_value',
        'args': [0],                 
        'type': TYPE_OVERLAY,
        'inputs': ['session_high'],   
        'name': 'High',
        'color': "#004E00",         
        'style': 'solid',
        'legendgroup': 'Session_HL_Group',
        'legendrank': 120
    },
    # 當盤 VWAP
    {
        'id': 'VWAP',
        'func': 'calc_vwap',   
        'args': [0],                   
        'type': TYPE_OVERLAY,          
        'inputs': ['cum_pv', 'cum_volume'], 
        'color': "#008692",           
        'style': 'solid',
        'legendgroup': 'VWAP_Cost_Group',
        'legendrank': 130
    },

    # --- Fractal VWAP (Regime-Based) ---
    # Simplified: Only Level 1 (Upper/Lower Regime Means) for Support/Resistance
    # Outer boundaries are handled by StdDev Bands.

    {
        'id': 'Fractal_U',
        'type': TYPE_VIRTUAL,
        'color': '#FF9F43', # Pastel Orange
        'width': 2,
        'name': 'Bull Cost',
        'legendgroup': 'VWAP_Cost_Group',
        'legendrank': 140
    },
    {
        'id': 'Fractal_L',
        'type': TYPE_VIRTUAL,
        'color': '#FF9F43', # Pastel Orange
        'width': 2,
        'name': 'Bear Cost',
        'legendgroup': 'VWAP_Cost_Group',
        'legendrank': 141
    },

    # --- Global VWAP Bands (Auto-Generated) ---
    # Generated from VWAP_MULTIPLIERS
]

# Helper to determine style based on SD
def get_band_style(sd):
    if sd == VWAP_MULTIPLIERS[0]: return '#28B463', 1  # Green, Thin
    if sd == VWAP_MULTIPLIERS[1]: return '#F1C40F', 1  # Yellow, Thin
    if sd >= VWAP_MULTIPLIERS[2]: return '#E74C3C', 2  # Red, Thick
    return '#FFFFFF', 1

for sd in VWAP_MULTIPLIERS:
    color, width = get_band_style(sd)
    
    # 1. Global VWAP Bands
    INDICATORS_SETUP.append({
        'id': f'VWAP_Band_{sd}',
        'type': TYPE_VIRTUAL,
        'subtype': 'vwap_band',
        'sd': sd,
        'color': color,
        'width': width,
        'name': f'VWAP ±{sd}σ'
    })
    
    # 2. Bull Regime (Upper)
    INDICATORS_SETUP.append({
        'id': f'Bull_Band_{sd}',
        'type': TYPE_VIRTUAL,
        'color': color,
        'width': width,
        'style': 'dash',
        'name': f'Bull +{sd}σ',
        'legendgroup': 'Regime_Upper',
        'legendrank': 170 + int(sd)
    })

    # 3. Bear Regime (Lower)
    INDICATORS_SETUP.append({
        'id': f'Bear_Band_{sd}',
        'type': TYPE_VIRTUAL,
        'color': color,
        'width': width,
        'style': 'dash',
        'name': f'Bear -{sd}σ',
        'legendgroup': 'Regime_Lower',
        'legendrank': 180 + int(sd)
    })

# Continue with remaining indicators
INDICATORS_SETUP += [

    # 當盤最低價
    {
        'id': 'Session_Low',
        'func': 'get_current_value',
        'args': [0],
        'type': TYPE_OVERLAY,
        'inputs': ['session_low'],
        'name': 'Low',
        'color': "#780000",         
        'style': 'solid',
        'legendgroup': 'Session_HL_Group',
        'legendrank': 120
    },
    # 總成交量 (Hidden 指標，只為了 Dashboard 顯示數值用)
    {
        'id': 'Total_Vol',
        'func': 'get_current_value',
        'args': [0],
        'type': 'hidden',          # 標記為 hidden (稍後 Dashboard 會過濾掉不畫)
        'inputs': ['total_volume'],  # 對應 RingBuffer 的累積量
        'color': '#FFFFFF',
        'style': 'solid'
    },
    # --- 技術指標 ---
    # {
    #     'id': 'Max_250',
    #     'func': 'calc_rolling_max',
    #     'args': [250],                  # 過去 60 筆
    #     'type': TYPE_OVERLAY,          # 疊加在主圖
    #     'inputs': ['close'],           # 只需要收盤價
    #     'color': '#00FF00',            # 綠色 (壓力線)
    #     'style': 'dash'
    # },
    # {
    #     'id': 'Min_250',
    #     'func': 'calc_rolling_min',
    #     'args': [250],                  # 過去 60 筆
    #     'type': TYPE_OVERLAY,
    #     'inputs': ['close'],
    #     'color': '#FF0000',            # 紅色 (支撐線)
    #     'style': 'dash'
    # },
    # --- 移動平均線 (SMA) ---
    # {
    #     'id': 'SMA_60',        
    #     'func': 'calc_sma',           
    #     'args': [60],                 
    #     'type': TYPE_OVERLAY,        
    #     'inputs': ['cum_close'], 
    #     'color': "#FFF000",           
    #     'style': 'solid'               
    # },
    # {
    #     'id': 'SMA_3min',
    #     'func': 'calc_sma_time',
    #     'args': [3 * 60000], # 3 分鐘 (毫秒)
    #     'type': TYPE_OVERLAY,
    #     'inputs': ['cum_close', 'timestamp'],
    #     'color': "#E0930F",
    #     'style': 'solid'
    # },
    
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
    # {
    #     'id': 'Mom_180ticks',
    #     'func': 'calc_price_change',
    #     'args': [180],
    #     'type': TYPE_OSCILLATOR,
    #     'inputs': ['close'],
    #     'color': 'dynamic', # 特殊標記：代表紅綠變色
    #     'style': 'bar'
    # },

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
    
    # 1. 🟢 小單淨量 (Small Lot Net)
    # 監控 1~4 口的零散交易動向。
    # [Upgraded] 使用 effective_volume 過濾拆單。
    {
        'id': 'Small_Lot',
        'func': 'calc_small_lot_net',
        'type': TYPE_OSCILLATOR,
        'name': 'Small Lot',
        'color': '#00FF00',  # 綠色
        'inputs': ['effective_volume', 'type'], 
        'args': [250, 5],    # < 5
        'yaxis': 'y',
        'style': 'bar'
    },

    # 2. 🟡 大單 (Large Lot)
    # 監控 >= 5 口的所有大單 (包含特大單)。
    # [Inclusive] 標準法人單，顯示為黃色系。
    {
        'id': 'Large_Lot', 
        'func': 'calc_large_lot_net', # Inclusive
        'type': TYPE_OSCILLATOR,
        'name': 'Large Lot',
        'color': '#FFD700',  # 金黃色
        'inputs': ['effective_volume', 'type'], 
        'args': [250, 5],    # >= 5
        'yaxis': 'y',
        'style': 'bar'
    },

    # 3. 🔴 特大單 (Mega Lot)
    # 監控 >= 15 口的極端大單 (Large Lot 的子集)。
    # [Highlight] 顯示為紅色系，疊加在黃色之上，代表極高強度。
    {
        'id': 'Mega_Lot',
        'func': 'calc_large_lot_net',
        'type': TYPE_OSCILLATOR,
        'name': 'Mega Lot',
        'color': '#FF0000',  # 紅色
        'inputs': ['effective_volume', 'type'], 
        'args': [250, 15],   # >= 15
        'yaxis': 'y',
        'style': 'bar'
    },



    



    # 4. 🌊 Order Flow (OFI)
    # Order Flow Imbalance: Net Aggressor Volume
    # 4. 🌊 Order Flow (OFI)
    # Order Flow Imbalance (Cumulative from Shared Memory)
    {
        'id': 'CumOFI',
        'func': 'get_current_value', # Fetch pre-calculated
        'type': TYPE_OSCILLATOR,
        'name': 'COFI',
        'color': '#FFD700',  # Gold
        'inputs': ['ofi'],   # Key in history
        'args': [0],
        'yaxis': 'y2',       
        'style': 'line',
        'legendrank': 240
    },

    # 5. 📚 Order Book (OBI)
    # Order Book Imbalance (Cumulative from Shared Memory)
    {
        'id': 'CumOBI',
        'func': 'get_current_value', # Fetch pre-calculated
        'type': TYPE_OSCILLATOR,
        'name': 'COBI',
        'color': '#00FFFF',  # Cyan
        'inputs': ['obi'],   # Key in history
        'args': [0],
        'yaxis': 'y2',
        'style': 'line',
        'legendrank': 250
    },

]