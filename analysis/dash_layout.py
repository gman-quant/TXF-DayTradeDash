# analysis/dash_layout.py

from dash import dcc, html
from config.ui_theme import UI_COLOR
from config.settings import TIMEFRAMES, DEFAULT_TIMEFRAME
from analysis.dash_logic import create_blank_figure

# =============================================================================
# 🎨 樣式模組 (應用層：這些樣式是 Scoreboard 專用的)
# =============================================================================

# 樣式模組：標籤 (Label)
LABEL_STYLE = {
    'color': UI_COLOR['TEXT_SUB'], 
    'display': 'inline-block', 
    'width': '75px',       # 固定寬度
    'textAlign': 'right',  # 靠右對齊
    'marginRight': '8px'   # 間距
}

# 樣式模組：行 (Row) - 強制固定高度以對齊
ROW_STYLE = {
    'height': '22px',
    'display': 'flex',
    'alignItems': 'center'
}

# =============================================================================
# 布局函數
# =============================================================================

def create_main_layout():
    # 準備 Dropdown 選項
    tf_options = [{'label': k, 'value': k} for k in TIMEFRAMES.keys()]

    INITIAN_DARK_FIGURE = create_blank_figure()

    return html.Div(style={'backgroundColor': UI_COLOR['BG_MAIN'], 'color': UI_COLOR['TEXT_MAIN'], 'height': '100vh', 'padding': '20px'}, children=[
        
        html.H2("🚀 TXF Gale Quant Engine", style={'textAlign': 'center', 'marginBottom': '5px'}),
        
        # 1. 戰情面板
        html.Div(id='live-status-panel', style={'marginBottom': '15px'}),

        # 2. 控制區容器 (Grid 佈局：左邊選單，右邊滑桿)
        html.Div(style={'width': '80%', 'margin': '0 auto 20px auto', 'display': 'flex', 'alignItems': 'center'}, children=[
            
            # 左側：週期選擇 (Dropdown)
            html.Div(style={'width': '150px', 'marginRight': '20px'}, children=[
                html.Label("⏱️ K線週期", style={'color': UI_COLOR['TEXT_SUB'], 'fontSize': '12px', 'marginBottom': '5px', 'display': 'block'}),
                dcc.Dropdown(
                    id='timeframe-dropdown',
                    options=tf_options,
                    value=DEFAULT_TIMEFRAME,
                    clearable=False,
                    style={} 
                )
            ]),

            # 右側：顯示筆數 (Slider)
            html.Div(style={'flex': '1'}, children=[
                html.Label("📊 顯示筆數 (Lookback Window)", style={'color': UI_COLOR['TEXT_SUB'], 'fontSize': '12px', 'marginBottom': '5px', 'display': 'block'}),
                dcc.Slider(
                    id='lookback-slider',
                    # ... (參數不變) ...
                    min=500, max=50000, step=500, value=25000,
                    marks={500: '500', 2000: '2K', 5000: '5K', 10000: '10K', 25000: '25K', 50000: '50K'},
                    tooltip={"placement": "bottom", "always_visible": True}
                )
            ])
        ]),

        # Charts
        dcc.Graph(id='price-chart', figure=INITIAN_DARK_FIGURE, style={'height': '55vh'}),
        dcc.Graph(id='momentum-chart', figure=INITIAN_DARK_FIGURE, style={'height': '25vh'}),
        
        # Stores
        dcc.Store(id='price-xaxis-range', data=None),
        dcc.Store(id='price-yaxis-range', data=None),
        dcc.Store(id='last-update-timestamp', data=0),
        
        # Interval
        dcc.Interval(id='interval-component', interval=1000, n_intervals=0)
    ])

def create_scoreboard_html(last_price, change, change_pct, open_price, high, low, vol, vwap, prev_close, underlying_price):
    """
    生成戰情面板 (使用 UI_COLOR 統一配色)
    """
    # 1. 決定主色調 (綠漲紅跌)
    # change >= 0 -> UP (Green), else -> DOWN (Red)
    main_color = UI_COLOR['UP'] if change >= 0 else UI_COLOR['DOWN']
    sign = '+' if change >= 0 else ''

    # 2. 計算輔助數據
    day_range = high - low
    
    gap = open_price - prev_close
    gap_color = UI_COLOR['UP'] if gap >= 0 else UI_COLOR['DOWN']
    gap_sign = '+' if gap >= 0 else ''

    basis = last_price - underlying_price
    basis_color = UI_COLOR['HIGHLIGHT'] # 基差用黃色突顯
    basis_sign = '+' if basis >= 0 else ''
    
    # VWAP 乖離率
    vwap_deviation_pct = ((last_price / vwap) - 1) * 100 if vwap else 0.0
    
    # 乖離顏色邏輯：
    # > 0.2% (正乖離/高) -> Green (漲)
    # < -0.2% (負乖離/低) -> Red (跌)
    if vwap_deviation_pct >= 0.2:
        vwap_dev_color = UI_COLOR['UP']
    elif vwap_deviation_pct <= -0.2:
        vwap_dev_color = UI_COLOR['DOWN']
    else:
        vwap_dev_color = UI_COLOR['TEXT_SUB'] # 中性灰

    # 開盤漲跌計算 (用於判斷盤中趨勢強弱)
    change_from_open = last_price - open_price
    change_open_color = UI_COLOR['UP'] if change_from_open >= 0 else UI_COLOR['DOWN']
    change_open_sign = '+' if change_from_open >= 0 else ''
    
    return html.Div(style={
        'display': 'flex', 
        'justifyContent': 'center', 
        'alignItems': 'center',
        'backgroundColor': UI_COLOR['BG_PANEL'], 
        'borderRadius': '10px',
        'padding': '15px',
        'border': f'1px solid {main_color}'
    }, children=[
        
        # 左側大字體
        html.Div(style={'marginRight': '40px', 'textAlign': 'center'}, children=[
            html.Div(f"{last_price:,.0f}", style={'color': main_color, 'fontSize': '42px', 'fontWeight': 'bold', 'lineHeight': '1'}),
            html.Div(f"{sign}{change:.0f} ({sign}{change_pct:.2f}%)", style={'color': main_color, 'fontSize': '18px', 'marginTop': '5px'})
        ]),
        
        # 右側詳細數據
        html.Div(style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr 1fr 1fr', 'gap': '5px 30px', 'textAlign': 'left', 'fontSize': '14px'}, children=[

            # COLUMN 1: 價格極值與區間 (Boundary & Volatility)
            html.Div(children=[
                html.Div([html.Span("最高: ", style=LABEL_STYLE), html.Span(f"{high:,.0f}", style={'color': UI_COLOR['UP'], 'fontWeight': 'bold'})], style=ROW_STYLE),
                html.Div([html.Span("最低: ", style=LABEL_STYLE), html.Span(f"{low:,.0f}", style={'color': UI_COLOR['DOWN'], 'fontWeight': 'bold'})], style=ROW_STYLE),
                html.Div([html.Span("波幅: ", style=LABEL_STYLE), html.Span(f"{day_range:.0f}", style={'color': UI_COLOR['HIGHLIGHT']})], style=ROW_STYLE),
            ]),
            
            # COLUMN 2: 基準與跳空 (Anchors & Gaps)
            html.Div(children=[
                html.Div([html.Span("昨收: ", style=LABEL_STYLE), html.Span(f"{prev_close:,.0f}", style={'color': UI_COLOR['TEXT_SUB']})], style=ROW_STYLE),
                html.Div([html.Span("開盤: ", style=LABEL_STYLE), html.Span(f"{open_price:,.0f}", style={'color': UI_COLOR['TEXT_MAIN']})], style=ROW_STYLE),
                html.Div([html.Span("跳空: ", style=LABEL_STYLE), html.Span(f"{gap_sign}{gap:.0f}", style={'color': gap_color})], style=ROW_STYLE),
            ]),

            # ⬇️ COLUMN 3: 價值與盤中動能 (Cost & Intraday Momentum)
            html.Div(children=[
                html.Div([html.Span("VWAP: ", style=LABEL_STYLE), html.Span(f"{vwap:,.0f}", style={'color': UI_COLOR['CYAN']})], style=ROW_STYLE),
                # 關鍵：將開盤漲跌移到這裡，與成本線 (VWAP) 放在一起
                html.Div([html.Span("開盤漲跌: ", style=LABEL_STYLE), html.Span(f"{change_open_sign}{change_from_open:.0f}", style={'color': change_open_color})], style=ROW_STYLE),
                html.Div([html.Span("VWAP Dev: ", style=LABEL_STYLE), html.Span(f"{vwap_deviation_pct:.2f}%", style={'color': vwap_dev_color})], style=ROW_STYLE),
            ]),
            
            # ⬇️ COLUMN 4: 跨市與流量 (Cross-Market & Magnitude)
            html.Div(children=[
                html.Div([html.Span("現貨價: ", style=LABEL_STYLE), html.Span(f"{underlying_price:,.0f}", style={'color': UI_COLOR['TEXT_MAIN']})], style=ROW_STYLE),
                html.Div([html.Span("價　差: ", style=LABEL_STYLE), html.Span(f"{basis_sign}{basis:.2f}", style={'color': basis_color, 'fontWeight': 'bold'})], style=ROW_STYLE),
                html.Div([html.Span("總　量: ", style=LABEL_STYLE), html.Span(f"{vol:,.0f}", style={'color': UI_COLOR['TEXT_MAIN']})], style=ROW_STYLE),
            ]),
        ])
    ])