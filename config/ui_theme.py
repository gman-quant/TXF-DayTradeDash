# config/ui_theme.py

# =============================================================================
# [Theme System]
# 切換風格: 'GLOBAL' (綠漲紅跌) vs 'TAIWAN' (紅漲綠跌)
# =============================================================================
THEME_STYLE = 'TAIWAN' # default: TAIWAN (User Preference)

# =============================================================================
# [Raw Color Palette] - RGB Definitions (Used for Logic Mapping)
# =============================================================================

# 1. 基礎色 (Basic)
_C_WHITE        = 'rgb(255, 255, 255)'
_C_GRAY_LIGHT   = 'rgb(187, 187, 187)'
_C_BG_PANEL     = 'rgb(30, 30, 30)'
_C_BG_MAIN      = 'rgb(17, 17, 17)'

# 2. 方向性顏色 (紅/綠變化)
_C_GREEN_TEAL   = 'rgb(6, 122, 102)'    # 深青綠 (K棒跌)
_C_RED_DEEP     = 'rgb(211, 47, 47)'    # 深紅 (K棒漲)
_C_GREEN_BRIGHT = 'rgb(46, 204, 64)'    # 亮綠 (主下跌)
_C_RED_BRIGHT   = 'rgb(255, 65, 54)'    # 亮紅 (主上漲)
_C_GREEN_DK     = 'rgb(0, 78, 0)'       # 深綠 (創新低背景)
_C_RED_DK       = 'rgb(120, 0, 0)'      # 深紅 (創新高背景)

# 3. 特殊指標 (霓虹/顯著)
_C_YELLOW_BRIGHT= 'rgb(255, 240, 0)'    # 高亮
_C_YELLOW       = 'rgb(255, 255, 0)'    # 警告 / CVD
_C_GOLD         = 'rgb(255, 215, 0)'    # 金色 (OFI)
_C_CYAN_NEON    = 'rgb(0, 255, 255)'    # 青色 (OBI / 特大賣單)
_C_MAGENTA_NEON = 'rgb(251, 0, 255)'    # 洋紅 (特大買單)
_C_PURPLE       = 'rgb(127, 74, 152)'   # 紫色 (現貨價格)
_C_TIFFANY      = 'rgb(129, 216, 208)'  # Tiffany藍 (VWAP)

# 4. 功能性顏色 (填充/區間)
_C_DODGER_BLUE  = 'rgb(30, 136, 229)'   # 成本線
_C_GREEN_MED    = 'rgb(40, 180, 99)'    # 通道 1
_C_RED_SOFT     = 'rgb(231, 76, 60)'    # 通道 3

# 5. 口數特定顏色
_C_BROWN        = 'rgb(140, 91, 0)'     # 大單買進
_C_BLUE_DARK    = 'rgb(0, 109, 145)'    # 大單賣出

# =============================================================================
# [Logic Mapping]
# =============================================================================
if THEME_STYLE == 'TAIWAN':
    # --- 台股/亞股風格 (Red=Up, Green=Down) ---
    _MAIN_UP    = _C_RED_BRIGHT   # 漲: 亮紅
    _MAIN_DOWN  = _C_GREEN_BRIGHT # 跌: 亮綠
    
    _KBAR_UP    = _C_RED_DEEP     # K棒漲: 深紅
    _KBAR_DOWN  = _C_GREEN_TEAL   # K棒跌: 深青綠
    
    _SESS_HIGH  = _C_RED_DK       # 創新高背景
    _SESS_LOW   = _C_GREEN_DK     # 創新低背景

else:
    # --- 美股/國際風格 (Green=Up, Red=Down) ---
    _MAIN_UP    = _C_GREEN_BRIGHT
    _MAIN_DOWN  = _C_RED_BRIGHT
    
    _KBAR_UP    = _C_GREEN_TEAL
    _KBAR_DOWN  = _C_RED_DEEP
    
    _SESS_HIGH  = _C_GREEN_DK
    _SESS_LOW   = _C_RED_DK

# =============================================================================
# [Final Configuration Export]
# =============================================================================
UI_COLOR = {
    # 核心漲跌色 (由 Logic Mapping 決定)
    'UP': _MAIN_UP,
    'DOWN': _MAIN_DOWN,
    
    # 輔助色
    'TEXT_MAIN': _C_WHITE,
    'TEXT_SUB': _C_GRAY_LIGHT,
    'HIGHLIGHT': _C_YELLOW_BRIGHT,
    'BG_PANEL': _C_BG_PANEL,
    'BG_MAIN': _C_BG_MAIN,
    
    # K棒主體
    'Kbar_UP': _KBAR_UP,
    'Kbar_DOWN': _KBAR_DOWN,

    # 關鍵支撐壓力
    'VWAP': _C_TIFFANY,
    'COST_LINE': _C_DODGER_BLUE.replace('rgb', 'rgba').replace(')', ', 0.7)'),
    
    # 標準差通道 (Risk Regimes)
    'BAND_1': _C_GREEN_MED.replace('rgb', 'rgba').replace(')', ', 0.7)'),
    'BAND_2': _C_YELLOW.replace('rgb', 'rgba').replace(')', ', 0.7)'),
    'BAND_3': _C_RED_SOFT.replace('rgb', 'rgba').replace(')', ', 0.7)'),
    
    # 區間極值
    'SESSION_HIGH': _SESS_HIGH,
    'SESSION_LOW': _SESS_LOW,
    
    # 籌碼指標 (Market Internals)
    'SPOT_PRICE': _C_PURPLE,
    'CVD': _C_GOLD,      # 使用 Gold 統一
    'OFI': _C_GOLD,
    'OBI': _C_CYAN_NEON,
    
    # Lot Sizes
    # 散戶/小單 (< 5口): 跟隨主要方向
    'LOT_SMALL_UP': _C_GREEN_BRIGHT,  # 若使用者希望顏色嚴格區分，可以使用原始綠色
                                      # 邏輯隱含特定顏色：第87/88行原本是硬編碼的綠/紅。
                                      # 假設註解中的「User Preference」意味著嚴格匹配當前值。
    'LOT_SMALL_DOWN': _C_RED_BRIGHT,

    # 大單 (>= 5口)
    'LOT_LARGE_UP': _C_BROWN,
    'LOT_LARGE_DOWN': _C_BLUE_DARK,
    
    # 特大單 (>= 15口)
    'LOT_MEGA_UP': _C_MAGENTA_NEON,
    'LOT_MEGA_DOWN': _C_CYAN_NEON,
    
    # 其他填充色
    'VOLUME_FILL': _C_YELLOW.replace('rgb', 'rgba').replace(')', ', 0.3)'),
    
    # Volume Profile (Buy/Sell)
    # Visual Stacking: Layer 1 (Total) = Buy Color, Layer 2 (Sell) = Sell Color
    # Original Colors Swapped: Buy=Red, Sell=Green
    'VP_BUY': 'rgba(255, 82, 82, 0.25)',    # Red (Original RGB)
    'VP_SELL': 'rgba(0, 230, 118, 0.1)',  # Green (Original RGB)
}

# =============================================================================
# 畫線工具的預設樣式(2026-07-21)
# 原本由控制列的「畫筆粗細 / 畫筆顏色」兩個 dropdown 即時調整,用戶確認不在
# 本看板畫線後移除該 UI(見 git log fix/remove drawing dropdowns)。
# 畫線功能本身保留(Plotly modebar 的繪圖工具仍可用),只是樣式改為此固定值。
# 要改回可調:恢復兩個 dcc.Dropdown + update_dashboard 的對應 Input 即可。
# =============================================================================
DRAWING_STYLE = {
    'color': '#2ECC40',   # 綠(原 dropdown 的預設值)
    'width': 1,           # px(原 dropdown 的預設值)
}
