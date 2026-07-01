# config/indicator_config.py

# 定義指標類型常數
TYPE_OVERLAY = 'overlay'       # 疊加在主圖 (如 SMA, VWAP)
TYPE_OSCILLATOR = 'oscillator' # 獨立副圖 (如 Momentum, RSI, Volume)
TYPE_VIRTUAL = 'virtual'       # 虛擬指標 (如 VWAP Bands)

from config.ui_theme import UI_COLOR

# 預設不顯示的指標 ID 列表 (Default Hidden)
VWAP_MULTIPLIERS = [1.0, 2.0, 3.0]  # 門檻=1/2(2.5 為舊 U/L-Cost 遺留,cB 空間無行為意義已砍)
BAND_WARMUP_VOL = 100  # 開盤暖身:該邊累積量 < 此值前 σ 不穩 → 不畫該邊色塊(防開盤爆寬)

DEFAULT_OFF_LEGENDS = [
    'VP',          # Volume Profile (預設隱藏)
    # σ 色塊三環(1-2/2-2.5/2.5+)預設全顯示,對齊 1/2/2.5 色階
]

# 指標配置清單
# 系統會自動讀取此清單，並去 core/numba_engine.py 找對應的函數執行
INDICATORS_SETUP = [
    # --- 價格主圖指標 (Main Chart) ---
    # spot price
    {
        'id': 'Underlying_Price',
        'func': 'get_current_value',   # 對應新的 Numba 函數
        'args': [0],                   # 參數給 0 即可 (不需要 period)
        'type': TYPE_OVERLAY,          # 畫在主圖
        'inputs': ['underlying_price'], # ⚠️ 需要這兩個累積陣列
        'name': 'TAIEX',                # 顯示名稱
        'color': UI_COLOR['SPOT_PRICE'], # 棕紫色
        'style': 'solid',              # 實線 (區別於 50MA 的虛線)
        'legendrank': 110
    },
    # Session Low
    {
        'id': 'Session_Low',
        'func': 'get_current_value',
        'args': [0],
        'type': TYPE_OVERLAY,
        'inputs': ['session_low'],
        'name': 'Low',
        'color': UI_COLOR['SESSION_LOW'],         
        'style': 'solid',
        'legendgroup': 'Session_HL_Group',
        'legendrank': 120
    },
    # Session High
    {
        'id': 'Session_High',
        'func': 'get_current_value',
        'args': [0],                 
        'type': TYPE_OVERLAY,
        'inputs': ['session_high'],   
        'name': 'High',
        'color': UI_COLOR['SESSION_HIGH'],         
        'style': 'solid',
        'legendgroup': 'Session_HL_Group',
        'legendrank': 120
    },
    # VWAP
    {
        'id': 'VWAP',
        'func': 'calc_vwap',   
        'args': [0],                   
        'type': TYPE_OVERLAY,          
        'inputs': ['cum_pv', 'cum_volume'], 
        'color': UI_COLOR['VWAP'], # Tiffany Blue
        'width': 1,
        'style': 'solid',
        'legendgroup': 'VWAP_Cost_Group',
        'legendrank': 131
    },

    # --- U-Cost / L-Cost 線(只畫線,不影響 VWAP-為心 σ 色塊) ---
    {
        'id': 'Fractal_U',
        'type': TYPE_VIRTUAL,
        'color': UI_COLOR['COST_LINE'],
        'width': 1,
        'name': 'U-Cost',
        'legendgroup': 'VWAP_Cost_Group',
        'legendrank': 132
    },
    {
        'id': 'Fractal_L',
        'type': TYPE_VIRTUAL,
        'color': UI_COLOR['COST_LINE'],
        'width': 1,
        'name': 'L-Cost',
        'legendgroup': 'VWAP_Cost_Group',
        'legendrank': 130
    },
]

# Helper to determine style based on SD
def get_band_style(sd):
    if sd == VWAP_MULTIPLIERS[0]: return UI_COLOR['BAND_1'], 1  # 1.0: Cool Gray (Noise)
    if sd == VWAP_MULTIPLIERS[1]: return UI_COLOR['BAND_2'], 1  # 2.0: Amber (Warning)
    if sd >= VWAP_MULTIPLIERS[2]: return UI_COLOR['BAND_3'], 2  # 2.5: Neon Red (Extreme)
    return '#FFFFFF', 1

for sd in VWAP_MULTIPLIERS:
    color, width = get_band_style(sd)
    
    # Bull Regime (Upper)
    INDICATORS_SETUP.append({
        'id': f'Bull_Band_{sd}',
        'type': TYPE_VIRTUAL,
        'color': 'rgba(0,0,0,0)', # [Ghost Trace] 100% 透明
        'width': 1,
        'style': 'solid',
        'name': f'+{sd}σ',
        'showlegend': False,      # 不出現在圖例
        'legendgroup': 'Regime_Upper',
        'legendrank': 170 + int(sd)
    })

    # Bear Regime (Lower)
    INDICATORS_SETUP.append({
        'id': f'Bear_Band_{sd}',
        'type': TYPE_VIRTUAL,
        'color': 'rgba(0,0,0,0)', # [Ghost Trace] 100% 透明
        'width': 1,
        'style': 'solid',
        'name': f'-{sd}σ',
        'showlegend': False,      # 不出現在圖例
        'legendgroup': 'Regime_Lower',
        'legendrank': 180 + int(sd)
    })

INDICATORS_SETUP += [
    {
        'id': 'Total_Vol',
        'func': 'get_current_value',
        'args': [0],
        'type': 'hidden',          
        'inputs': ['total_volume'],  
        'color': UI_COLOR['TEXT_MAIN'],
        'style': 'solid'
    },
    {
        'id': 'CVD',
        'func': 'calc_session_cvd',
        'args': [0],
        'type': TYPE_OSCILLATOR,
        'inputs': ['cum_buy_vol', 'cum_sell_vol'],
        'color': UI_COLOR['CVD'],
        'style': 'solid',
        'yaxis': 'y2',
        'legendrank': 210
    },
    # 🌟 小單淨量 - Time Frame Based
    {
        'id': 'Small_Lot_TF',
        'type': TYPE_OSCILLATOR,
        'name': 'Small Lot',
        'color': UI_COLOR['LOT_SMALL_UP'],
        'yaxis': 'y',
        'style': 'bar',
        'legendrank': 220
    },
    # 🌟 大單 - Time Frame Based
    {
        'id': 'Large_Lot_TF', 
        'type': TYPE_OSCILLATOR,
        'name': 'Large Lot',
        'color': UI_COLOR['LOT_LARGE_UP'],
        'yaxis': 'y',
        'style': 'bar',
        'legendrank': 230
    },
    # 🌟 特大單 - Time Frame Based
    {
        'id': 'Mega_Lot_TF',
        'type': TYPE_OSCILLATOR,
        'name': 'Mega Lot',
        'color': UI_COLOR['LOT_MEGA_UP'],
        'yaxis': 'y',
        'style': 'bar',
        'legendrank': 240
    },
    {
        'id': 'CumOFI',
        'func': 'get_current_value',
        'type': TYPE_OSCILLATOR,
        'name': 'COFI',
        'color': UI_COLOR['OFI'],  
        'inputs': ['ofi'],   
        'args': [0],
        'yaxis': 'y2',       
        'style': 'line',
        'legendrank': 240
    },
    {
        'id': 'CumOBI',
        'func': 'get_current_value',
        'type': TYPE_OSCILLATOR,
        'name': 'COBI',
        'color': UI_COLOR['OBI'],  
        'inputs': ['obi'],   
        'args': [0],
        'yaxis': 'y2',
        'style': 'line',
        'legendrank': 250
    },
]