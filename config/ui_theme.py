# config/ui_theme.py

# =============================================================================
# [Theme System]
# 切換風格: 'GLOBAL' (綠漲紅跌) vs 'TAIWAN' (紅漲綠跌)
# =============================================================================
THEME_STYLE = 'TAIWAN' # default: TAIWAN (User Preference)

# =============================================================================
# [Raw Color Palette] - RGB Definitions (Used for Logic Mapping)
# =============================================================================
_C_GREEN_TEAL   = 'rgb(6, 122, 102)'    # 深青綠
_C_RED_DEEP     = 'rgb(211, 47, 47)'    # 深紅色

_C_GREEN_BRIGHT = 'rgb(46, 204, 64)'    # 鮮綠
_C_RED_BRIGHT   = 'rgb(255, 65, 54)'    # 亮紅

_C_DK_GREEN     = 'rgb(0, 78, 0)'       # 深綠
_C_DK_RED       = 'rgb(120, 0, 0)'      # 深紅

# =============================================================================
# [Logic Mapping]
# =============================================================================
if THEME_STYLE == 'TAIWAN':
    # --- 台股/亞股風格 (Red=Up, Green=Down) ---
    _MAIN_UP    = _C_RED_BRIGHT   # 漲: 亮紅
    _MAIN_DOWN  = _C_GREEN_BRIGHT # 跌: 亮綠
    
    _KBAR_UP    = _C_RED_DEEP     # K棒漲: 深紅
    _KBAR_DOWN  = _C_GREEN_TEAL   # K棒跌: 深青綠 (用 Teal 取代純綠更專業)

    _SESS_HIGH  = _C_DK_RED       # 創新高背景: 深紅
    _SESS_LOW   = _C_DK_GREEN     # 創新低背景: 深綠
    

else:
    # --- 美股/國際風格 (Green=Up, Red=Down) ---
    _MAIN_UP    = _C_GREEN_BRIGHT
    _MAIN_DOWN  = _C_RED_BRIGHT
    
    _KBAR_UP    = _C_GREEN_TEAL
    _KBAR_DOWN  = _C_RED_DEEP
    
    _SESS_HIGH  = _C_DK_GREEN
    _SESS_LOW   = _C_DK_RED

# =============================================================================
# [Final Configuration Export]
# =============================================================================
UI_COLOR = {
    # 核心漲跌色 (由 Logic Mapping 決定)
    'UP': _MAIN_UP,
    'DOWN': _MAIN_DOWN,
    
    # 輔助色 (Static RGBA)
    'TEXT_MAIN': 'rgb(255, 255, 255)', # 主要文字 (白)
    'TEXT_SUB': 'rgb(187, 187, 187)',  # 次要文字 (淺灰)
    'HIGHLIGHT': 'rgb(255, 240, 0)',   # 高亮突顯 (亮黃)
    'BG_PANEL': 'rgb(30, 30, 30)',     # 面板背景色 (深灰)
    'BG_MAIN': 'rgb(17, 17, 17)',      # 網頁主背景 (極深灰)
    
    # K棒主體 (由 Logic Mapping 決定)
    'Kbar_UP': _KBAR_UP,
    'Kbar_DOWN': _KBAR_DOWN,

    # 關鍵支撐壓力 (Static RGBA)
    'VWAP': 'rgb(129, 216, 208)',      # Tiffany Blue (公允價值核心)
    'COST_LINE': 'rgb(30, 136, 229)',  # Dodger Blue (成本結構線)
    
    # 標準差通道 (Risk Regimes)
    'BAND_1': 'rgb(40, 180, 99)',     # Green (常態噪音區 - Safe)
    'BAND_2': 'rgb(241, 196, 15)',    # Yellow (警戒區 - Warning)
    'BAND_3': 'rgb(231, 76, 60)',     # Red (極端行情 - Extreme)
    
    # 區間極值 (由 Logic Mapping 決定)
    'SESSION_HIGH': _SESS_HIGH,
    'SESSION_LOW': _SESS_LOW,
    
    # 籌碼指標 (Market Internals - Static RGBA)
    'SPOT_PRICE': 'rgb(127, 74, 152)', # 棕紫色 (現貨 TAIEX)
    'CVD': 'rgb(255, 240, 0)',         # 黃色 (累積成交量差)
    'OFI': 'rgb(255, 215, 0)',         # 金色 (Order Flow Imbalance)
    'OBI': 'rgb(0, 255, 255)',         # 青色 (Order Book Imbalance)
    
    # Lot Sizes
    # 散戶/小單 (< 5口): 固定為 綠漲/紅跌 (使用者指定)
    'LOT_SMALL_UP': 'rgb(46, 204, 64)',    # Green
    'LOT_SMALL_DOWN': 'rgb(255, 65, 54)',  # Red
    
    # 大單 (>= 5口): 固定顏色
    'LOT_LARGE_UP': 'rgb(140, 91, 0)',     # 深棕 (主力吸籌)
    'LOT_LARGE_DOWN': 'rgb(0, 109, 145)',  # 深藍 (主力調節)
    
    # 特大單 (>= 15口): 固定顏色
    'LOT_MEGA_UP': 'rgb(251, 0, 255)',     # 洋紅 (極端買進 - Neon)
    'LOT_MEGA_DOWN': 'rgb(0, 255, 255)',   # 青色 (極端賣出 - Neon)
    
    # 其他填充色
    'VOLUME_FILL': 'rgba(255, 240, 0, 0.25)' # 半透明亮黃 (成交量)
}