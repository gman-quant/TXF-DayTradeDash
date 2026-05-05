from dash import html
from config.ui_theme import UI_COLOR

# =============================================================================
# 🎨 Shared UI Style Definitions (共用樣式定義)
# =============================================================================

# 基礎標籤樣式
BASE_LABEL = {
    'color': UI_COLOR['TEXT_SUB'], 
    'display': 'inline-block', 
    'textAlign': 'right',  
    'marginRight': '8px',
    'whiteSpace': 'nowrap',
    'flexShrink': 0,
    'fontFamily': '"Segoe UI", sans-serif' # 標籤用比例字體
}

# 數據數值樣式 (Monospace 防止跳動)
VALUE_STYLE = {
    'fontFamily': '"JetBrains Mono", "Roboto Mono", monospace',
    'fontWeight': 'bold'
}

# 個別欄位寬度定義 (可以個別調整寬度)
LABEL_STYLE_C1 = {**BASE_LABEL, 'width': '80px'}  # 波動邊界 (High, Low, Range)
LABEL_STYLE_C2 = {**BASE_LABEL, 'width': '80px'}  # 市場參照 (Prior Close, Spot, Basis)
LABEL_STYLE_C3 = {**BASE_LABEL, 'width': '80px'}  # 開盤動態 (Open, Gap, Chg)
LABEL_STYLE_C4 = {**BASE_LABEL, 'width': '60px'}  # 量價結構 (VWAP, Vol)

# 戰情板排版間距設定 (可以統一調整間距)
LAYOUT_CONFIG = {
    'price_margin_right': '40px',  # 左側「大報價」與右側「四欄數據」的距離
    'grid_gap': '5px 30px',        # 右側四個欄位彼此的間距 (上下左右)
    'grid_columns': 'auto auto auto auto', # 欄位寬度分配 (auto=服貼內容寬度, 1fr=強迫均分)
}

# 數據行容器樣式 (Flex 佈局，垂直置中)
ROW_STYLE = {
    'height': '24px', # 稍微增加高度
    'display': 'flex',
    'alignItems': 'center'
}

# 網格列樣式 (加入垂直分割線)
COLUMN_STYLE = {
    'borderLeft': '1px solid #333',
    'paddingLeft': '20px'
}

# 戰情板容器樣式
PANEL_STYLE = {
    'display': 'flex', 
    'justifyContent': 'center', 
    'alignItems': 'center',
    'backgroundColor': UI_COLOR['BG_PANEL'], 
    'borderRadius': '10px',
    'padding': '15px 25px', # 增加左右內距
    'boxShadow': '0 4px 15px rgba(0,0,0,0.5)' # 增加陰影提升質感
}

# =============================================================================
# 📊 Scoreboard Generation (戰情計分板)
# =============================================================================

def _calculate_scoreboard_logic(sb_data):
    """內部共用的顏色與數值計算邏輯"""
    price = sb_data.get("last_price", 0)
    change = sb_data.get("change", 0)
    pct = sb_data.get("change_pct", 0)
    vol = sb_data.get("vol", 0)
    high = sb_data.get("high", 0)
    low = sb_data.get("low", 0)
    prev_close = sb_data.get("prev_close", 0)
    open_p = sb_data.get("open_price", 0)
    vwap = sb_data.get("vwap", 0)
    u_price = sb_data.get("underlying_price", 0)

    # 主色調 (綠漲紅跌)
    main_color = UI_COLOR['UP'] if change >= 0 else UI_COLOR['DOWN']
    sign = '+' if change >= 0 else ''

    # 跳空邏輯
    gap = open_p - prev_close
    gap_color = UI_COLOR['UP'] if gap >= 0 else UI_COLOR['DOWN']
    gap_sign = '+' if gap >= 0 else ''

    # 基差邏輯 (期貨 - 現貨)
    basis = price - u_price
    # 台灣市場慣性：逆價差(負值)通常用綠色或黃色，這裡依建議連動
    basis_color = UI_COLOR['UP'] if basis >= 0 else UI_COLOR['DOWN']
    basis_sign = '+' if basis >= 0 else ''

    # 開盤漲跌 (Intraday Change)
    chg_open = price - open_p
    chg_open_color = UI_COLOR['UP'] if chg_open >= 0 else UI_COLOR['DOWN']
    chg_open_sign = '+' if chg_open >= 0 else ''
    
    # 當日波幅
    day_range = high - low
    day_range_pct = (day_range / open_p * 100) if open_p else 0

    return {
        "price": price, "change": change, "pct": pct, "vol": vol, 
        "high": high, "low": low, "prev_close": prev_close, "open_p": open_p, 
        "vwap": vwap, "u_price": u_price,
        "main_color": main_color, "sign": sign,
        "gap": gap, "gap_color": gap_color, "gap_sign": gap_sign,
        "basis": basis, "basis_color": basis_color, "basis_sign": basis_sign,
        "chg_open": chg_open, "chg_open_color": chg_open_color, "chg_open_sign": chg_open_sign,
        "day_range": day_range, "day_range_pct": day_range_pct
    }

# --- 1. 即時監控模式 (Dash Components) ---
def create_dash_scoreboard(**sb_data):
    """
    生成即時監控面板的 Dash HTML 組件。
    """
    if not sb_data:
        return html.Div("No Data", style={'color': 'white', 'textAlign': 'center'})

    data = _calculate_scoreboard_logic(sb_data)
    
    # 動態設定邊框顏色
    dynamic_panel_style = PANEL_STYLE.copy()
    dynamic_panel_style['border'] = f"1px solid {data['main_color']}"

    return html.Div(style=dynamic_panel_style, children=[
        
        # [Left] 大字體報價
        html.Div(style={'marginRight': LAYOUT_CONFIG['price_margin_right'], 'textAlign': 'center'}, children=[
            html.Div(f"{data['price']:,.0f}", style={**VALUE_STYLE, 'color': data['main_color'], 'fontSize': '48px', 'lineHeight': '1'}),
            html.Div(f"{data['sign']}{data['change']:.0f} ({data['sign']}{data['pct']:.2f}%)", style={**VALUE_STYLE, 'color': data['main_color'], 'fontSize': '18px', 'marginTop': '5px'})
        ]),
        
        # [Right] 詳細數據網格 (Grid Layout 4 Columns)
        html.Div(style={'display': 'grid', 'gridTemplateColumns': LAYOUT_CONFIG['grid_columns'], 'gap': LAYOUT_CONFIG['grid_gap'], 'textAlign': 'left', 'fontSize': '14px'}, children=[

            # Column 1: Range (波動邊界)
            html.Div(children=[
                html.Div([html.Span("High:", style=LABEL_STYLE_C1), html.Span(f"{data['high']:,.0f}", style={**VALUE_STYLE, 'color': UI_COLOR['UP']})], style=ROW_STYLE),
                html.Div([html.Span("Low:", style=LABEL_STYLE_C1), html.Span(f"{data['low']:,.0f}", style={**VALUE_STYLE, 'color': UI_COLOR['DOWN']})], style=ROW_STYLE),
                html.Div([html.Span("Range (%):", style=LABEL_STYLE_C1), html.Span(f"{data['day_range']:.0f} ({data['day_range_pct']:.2f}%)", style={**VALUE_STYLE, 'color': UI_COLOR['HIGHLIGHT']})], style=ROW_STYLE),
            ]),
            
            # Column 2: Context (市場參照)
            html.Div(style=COLUMN_STYLE, children=[
                html.Div([html.Span("Prior Close:", style=LABEL_STYLE_C2), html.Span(f"{data['prev_close']:,.0f}", style={**VALUE_STYLE, 'color': UI_COLOR['TEXT_SUB']})], style=ROW_STYLE),
                html.Div([html.Span("TAIEX:", style=LABEL_STYLE_C2), html.Span(f"{data['u_price']:,.0f}", style={**VALUE_STYLE, 'color': UI_COLOR['SPOT_PRICE']})], style=ROW_STYLE),
                html.Div([html.Span("Basis:", style=LABEL_STYLE_C2), html.Span(f"{data['basis_sign']}{data['basis']:.2f}", style={**VALUE_STYLE, 'color': data['basis_color']})], style=ROW_STYLE),
            ]),
            
            # Column 3: Opening (開盤動態)
            html.Div(style=COLUMN_STYLE, children=[
                html.Div([html.Span("Open:", style=LABEL_STYLE_C3), html.Span(f"{data['open_p']:,.0f}", style={**VALUE_STYLE, 'color': UI_COLOR['TEXT_MAIN']})], style=ROW_STYLE),
                html.Div([html.Span("Gap:", style=LABEL_STYLE_C3), html.Span(f"{data['gap_sign']}{data['gap']:.0f}", style={**VALUE_STYLE, 'color': data['gap_color']})], style=ROW_STYLE),
                html.Div([html.Span("Chg (Open):", style=LABEL_STYLE_C3), html.Span(f"{data['chg_open_sign']}{data['chg_open']:.0f}", style={**VALUE_STYLE, 'color': data['chg_open_color']})], style=ROW_STYLE),
            ]),
            
            # Column 4: Volume & Cost (量價結構)
            html.Div(style=COLUMN_STYLE, children=[
                html.Div([html.Span("VWAP:", style=LABEL_STYLE_C4), html.Span(f"{data['vwap']:,.0f}", style={**VALUE_STYLE, 'color': UI_COLOR['VWAP']})], style=ROW_STYLE),
                html.Div([html.Span("Vol:", style=LABEL_STYLE_C4), html.Span(f"{data['vol']:,.0f}", style={**VALUE_STYLE, 'color': UI_COLOR['TEXT_MAIN']})], style=ROW_STYLE),
            ]),
        ])
    ])

# --- 2. 靜態導出模式 (HTML String) ---
def create_html_scoreboard_string(sb_data):
    """
    生成純 HTML 字串的戰情面板 (用於 Batch Export 與 Save HTML)。
    """
    if not sb_data:
        return "<div style='color:white; text-align:center'>No Data</div>"
        
    data = _calculate_scoreboard_logic(sb_data)

    # 定義 HTML 內的 inline styles
    mono_font = "'JetBrains Mono', 'Roboto Mono', monospace"
    col_style = "border-left: 1px solid #333; padding-left: 20px;"
    val_base = f"font-family: {mono_font}; font-weight: bold; white-space: nowrap;"

    return f"""
    <div style="background-color: #1E1E1E; color: white; padding: 15px 25px; border-radius: 10px; border: 1px solid {data['main_color']}; margin-bottom: 20px; font-family: 'Segoe UI', sans-serif; display: flex; justify-content: center; align-items: center; box-shadow: 0 4px 15px rgba(0,0,0,0.5);">
        <div style="margin-right: {LAYOUT_CONFIG['price_margin_right']}; text-align: center;">
            <div style="font-size: 48px; {val_base} color: {data['main_color']}; line-height: 1;">{data['price']:,.0f}</div>
            <div style="font-size: 20px; {val_base} color: {data['main_color']}; margin-top: 8px;">{data['sign']}{data['change']:.0f} ({data['sign']}{data['pct']:.2f}%)</div>
        </div>
        <div style="display: grid; grid-template-columns: {LAYOUT_CONFIG['grid_columns']}; gap: {LAYOUT_CONFIG['grid_gap']}; text-align: left; font-size: 14px; line-height: 1.6; color: #BBB;">
            <div>
                <div style="display: flex; align-items: center; height: 24px;"><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:{LABEL_STYLE_C1['width']}; text-align:right; margin-right:10px; white-space:nowrap; flex-shrink: 0;">High:</span><span style="color:{UI_COLOR["UP"]}; {val_base}">{data['high']:,.0f}</span></div>
                <div style="display: flex; align-items: center; height: 24px;"><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:{LABEL_STYLE_C1['width']}; text-align:right; margin-right:10px; white-space:nowrap; flex-shrink: 0;">Low:</span><span style="color:{UI_COLOR["DOWN"]}; {val_base}">{data['low']:,.0f}</span></div>
                <div style="display: flex; align-items: center; height: 24px;"><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:{LABEL_STYLE_C1['width']}; text-align:right; margin-right:10px; white-space:nowrap; flex-shrink: 0;">Range (%):</span><span style="color:{UI_COLOR["HIGHLIGHT"]}; {val_base}">{data['day_range']:.0f} ({data['day_range_pct']:.2f}%)</span></div>
            </div>
            <div style="{col_style}">
                <div style="display: flex; align-items: center; height: 24px;"><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:{LABEL_STYLE_C2['width']}; text-align:right; margin-right:10px; white-space:nowrap; flex-shrink: 0;">Prior Close:</span><span style="color:{UI_COLOR["TEXT_SUB"]}; {val_base}">{data['prev_close']:,.0f}</span></div>
                <div style="display: flex; align-items: center; height: 24px;"><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:{LABEL_STYLE_C2['width']}; text-align:right; margin-right:10px; white-space:nowrap; flex-shrink: 0;">TAIEX:</span><span style="color:{UI_COLOR["SPOT_PRICE"]}; {val_base}">{data['u_price']:,.0f}</span></div>
                <div style="display: flex; align-items: center; height: 24px;"><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:{LABEL_STYLE_C2['width']}; text-align:right; margin-right:10px; white-space:nowrap; flex-shrink: 0;">Basis:</span><span style="color:{data['basis_color']}; {val_base}">{data['basis_sign']}{data['basis']:.2f}</span></div>
            </div>
            <div style="{col_style}">
                <div style="display: flex; align-items: center; height: 24px;"><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:{LABEL_STYLE_C3['width']}; text-align:right; margin-right:10px; white-space:nowrap; flex-shrink: 0;">Open:</span><span style="color:{UI_COLOR["TEXT_MAIN"]}; {val_base}">{data['open_p']:,.0f}</span></div>
                <div style="display: flex; align-items: center; height: 24px;"><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:{LABEL_STYLE_C3['width']}; text-align:right; margin-right:10px; white-space:nowrap; flex-shrink: 0;">Gap:</span><span style="color:{data['gap_color']}; {val_base}">{data['gap_sign']}{data['gap']:.0f}</span></div>
                <div style="display: flex; align-items: center; height: 24px;"><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:{LABEL_STYLE_C3['width']}; text-align:right; margin-right:10px; white-space:nowrap; flex-shrink: 0;">Chg (Open):</span><span style="color:{data['chg_open_color']}; {val_base}">{data['chg_open_sign']}{data['chg_open']:.0f}</span></div>
            </div>
            <div style="{col_style}">
                <div style="display: flex; align-items: center; height: 24px;"><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:{LABEL_STYLE_C4['width']}; text-align:right; margin-right:10px; white-space:nowrap; flex-shrink: 0;">VWAP:</span><span style="color:{UI_COLOR["VWAP"]}; {val_base}">{data['vwap']:,.0f}</span></div>
                <div style="display: flex; align-items: center; height: 24px;"><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:{LABEL_STYLE_C4['width']}; text-align:right; margin-right:10px; white-space:nowrap; flex-shrink: 0;">Vol:</span><span style="color:{UI_COLOR["TEXT_MAIN"]}; {val_base}">{data['vol']:,.0f}</span></div>
            </div>
        </div>
    </div>
    """
