# analysis/dash_layout.py

from dash import dcc, html

# --- Local Configuration ---
from config.ui_theme import UI_COLOR
from config.settings import TIMEFRAMES, DEFAULT_TIMEFRAME
from gale.dashboard.chart import create_blank_figure

# =============================================================================
# 🎨 UI Style Definitions (樣式定義)
# =============================================================================

# 通用標籤樣式 (靠右對齊，固定寬度)
LABEL_STYLE = {
    'color': UI_COLOR['TEXT_SUB'], 
    'display': 'inline-block', 
    'width': '75px',       
    'textAlign': 'right',  
    'marginRight': '8px'   
}

# 數據行容器樣式 (Flex 佈局，垂直置中)
ROW_STYLE = {
    'height': '22px',
    'display': 'flex',
    'alignItems': 'center'
}

# 戰情板容器樣式
PANEL_STYLE = {
    'display': 'flex', 
    'justifyContent': 'center', 
    'alignItems': 'center',
    'backgroundColor': UI_COLOR['BG_PANEL'], 
    'borderRadius': '10px',
    'padding': '15px',
    # border 會在 runtime 動態生成
}

# =============================================================================
# 🏗️ Main Layout Structure (主頁面佈局)
# =============================================================================

def create_main_layout():
    """
    建構 Dash 應用的主要 Layout 結構。
    包含：Header, Scoreboard, Controls, Main Chart, Hidden Stores
    """
    
    # 準備 Dropdown 選項
    tf_options = [{'label': k, 'value': k} for k in TIMEFRAMES.keys()]

    # 初始化空白圖表 (避免載入時白屏)
    initial_figure = create_blank_figure()

    return html.Div(
        style={
            'backgroundColor': UI_COLOR['BG_MAIN'], 
            'color': UI_COLOR['TEXT_MAIN'], 
            'height': '100vh', 
            'padding': '20px'
        }, 
        children=[
        
            # 1. 標題區 (Header)
            html.H2("🚀 TXF Gale Quant Engine", style={'textAlign': 'center', 'marginBottom': '5px'}),
            
            # 2. 戰情面板 (Live Scoreboard)
            # 內容由 Callback 動態注入
            html.Div(id='live-status-panel', style={'marginBottom': '15px'}),

            # 3. 控制區容器 (Control Bar)
            # 使用 Flex 佈局：左邊選單，右邊滑桿
            html.Div(
                style={'width': '80%', 'margin': '0 auto 20px auto', 'display': 'flex', 'alignItems': 'center'}, 
                children=[
                
                    # [左側] 週期選擇 (Dropdown)
                    html.Div(style={'width': '150px', 'marginRight': '20px'}, children=[
                        html.Label("⏱️ K線週期", style={'color': UI_COLOR['TEXT_SUB'], 'fontSize': '12px', 'marginBottom': '5px', 'display': 'block'}),
                        dcc.Dropdown(
                            id='timeframe-dropdown',
                            options=tf_options,
                            value=DEFAULT_TIMEFRAME,
                            clearable=False,
                            style={} # 預設樣式
                        )
                    ]),

                    # [右側] 顯示筆數 (Slider)
                    html.Div(style={'flex': '1'}, children=[
                        html.Label("📊 顯示筆數 (Lookback Window)", style={'color': UI_COLOR['TEXT_SUB'], 'fontSize': '12px', 'marginBottom': '5px', 'display': 'block'}),
                        dcc.Slider(
                            id='lookback-slider',
                            min=500, max=50000, step=500, value=50000,
                            marks={500: '500', 2000: '2K', 5000: '5K', 10000: '10K', 25000: '25K', 50000: '50K'},
                            tooltip={"placement": "bottom", "always_visible": True}
                        )
                    ])
                ]
            ),

            # 4. 主圖表區 (Main Chart)
            # 單一圖表，包含 Price (Row 1) 與 Momentum (Row 2)
            dcc.Graph(
                id='main-chart', 
                figure=initial_figure, 
                style={'height': '80vh'}, # 佔據剩餘高度
                config={'scrollZoom': True, 'displayModeBar': False} 
            ),

            # 5. 狀態暫存區 (Hidden Stores & Interval)
            dcc.Interval(id='interval-component', interval=1000, n_intervals=0),
            
            # 紀錄最後更新時間 (用於 Early Peek 優化)
            dcc.Store(id='last-update-timestamp', data=0),
            
            # 紀錄使用者的縮放狀態 (用於保持 Zoom Level)
            dcc.Store(id='chart-zoom-state', data=None), 
        ]
    )

# =============================================================================
# 📊 Scoreboard Component (戰情計分板)
# =============================================================================

def create_scoreboard_html(last_price, change, change_pct, open_price, high, low, vol, vwap, prev_close, underlying_price):
    """
    生成戰情面板 HTML 組件。
    
    Args:
        last_price, change, change_pct: 最新價格與漲跌幅
        open_price, high, low, vol: OHLCV 基礎數據
        vwap: 成交量加權平均價
        prev_close: 昨日收盤價
        underlying_price: 現貨價格 (用於計算基差)
    """
    
    # --- 1. 顏色邏輯計算 ---
    
    # 主色調 (綠漲紅跌)
    main_color = UI_COLOR['UP'] if change >= 0 else UI_COLOR['DOWN']
    sign = '+' if change >= 0 else ''

    # 跳空邏輯
    gap = open_price - prev_close
    gap_color = UI_COLOR['UP'] if gap >= 0 else UI_COLOR['DOWN']
    gap_sign = '+' if gap >= 0 else ''

    # 基差邏輯 (期貨 - 現貨)
    basis = last_price - underlying_price
    basis_color = UI_COLOR['HIGHLIGHT'] # 黃色突顯
    basis_sign = '+' if basis >= 0 else ''
    
    # VWAP 乖離率邏輯
    # > 0.2% (正乖離/高) -> Green (漲)
    # < -0.2% (負乖離/低) -> Red (跌)
    vwap_deviation_pct = ((last_price / vwap) - 1) * 100 if vwap else 0.0
    if vwap_deviation_pct >= 0.2:
        vwap_dev_color = UI_COLOR['UP']
    elif vwap_deviation_pct <= -0.2:
        vwap_dev_color = UI_COLOR['DOWN']
    else:
        vwap_dev_color = UI_COLOR['TEXT_SUB'] # 中性灰

    # 開盤漲跌 (Intraday Change)
    change_from_open = last_price - open_price
    change_open_color = UI_COLOR['UP'] if change_from_open >= 0 else UI_COLOR['DOWN']
    change_open_sign = '+' if change_from_open >= 0 else ''
    
    # 當日波幅
    day_range = high - low

    # --- 2. 組件生成 ---
    
    # 動態設定邊框顏色
    dynamic_panel_style = PANEL_STYLE.copy()
    dynamic_panel_style['border'] = f'1px solid {main_color}'

    return html.Div(style=dynamic_panel_style, children=[
        
        # [Left] 大字體報價
        html.Div(style={'marginRight': '40px', 'textAlign': 'center'}, children=[
            html.Div(f"{last_price:,.0f}", style={'color': main_color, 'fontSize': '42px', 'fontWeight': 'bold', 'lineHeight': '1'}),
            html.Div(f"{sign}{change:.0f} ({sign}{change_pct:.2f}%)", style={'color': main_color, 'fontSize': '18px', 'marginTop': '5px'})
        ]),
        
        # [Right] 詳細數據網格 (Grid Layout 4 Columns)
        html.Div(style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr 1fr 1fr', 'gap': '5px 30px', 'textAlign': 'left', 'fontSize': '14px'}, children=[

            # Column 1: 極值與波幅 (Boundary)
            html.Div(children=[
                html.Div([html.Span("最高: ", style=LABEL_STYLE), html.Span(f"{high:,.0f}", style={'color': UI_COLOR['UP'], 'fontWeight': 'bold'})], style=ROW_STYLE),
                html.Div([html.Span("最低: ", style=LABEL_STYLE), html.Span(f"{low:,.0f}", style={'color': UI_COLOR['DOWN'], 'fontWeight': 'bold'})], style=ROW_STYLE),
                html.Div([html.Span("波幅: ", style=LABEL_STYLE), html.Span(f"{day_range:.0f}", style={'color': UI_COLOR['HIGHLIGHT']})], style=ROW_STYLE),
            ]),
            
            # Column 2: 基準點 (Anchors)
            html.Div(children=[
                html.Div([html.Span("昨收: ", style=LABEL_STYLE), html.Span(f"{prev_close:,.0f}", style={'color': UI_COLOR['TEXT_SUB']})], style=ROW_STYLE),
                html.Div([html.Span("開盤: ", style=LABEL_STYLE), html.Span(f"{open_price:,.0f}", style={'color': UI_COLOR['TEXT_MAIN']})], style=ROW_STYLE),
                html.Div([html.Span("跳空: ", style=LABEL_STYLE), html.Span(f"{gap_sign}{gap:.0f}", style={'color': gap_color})], style=ROW_STYLE),
            ]),

            # Column 3: 成本與動能 (Cost & Momentum)
            html.Div(children=[
                html.Div([html.Span("VWAP: ", style=LABEL_STYLE), html.Span(f"{vwap:,.0f}", style={'color': UI_COLOR['CYAN']})], style=ROW_STYLE),
                html.Div([html.Span("開盤漲跌: ", style=LABEL_STYLE), html.Span(f"{change_open_sign}{change_from_open:.0f}", style={'color': change_open_color})], style=ROW_STYLE),
                html.Div([html.Span("VWAP Dev: ", style=LABEL_STYLE), html.Span(f"{vwap_deviation_pct:.2f}%", style={'color': vwap_dev_color})], style=ROW_STYLE),
            ]),
            
            # Column 4: 跨市與總量 (Market Context)
            html.Div(children=[
                html.Div([html.Span("現貨價: ", style=LABEL_STYLE), html.Span(f"{underlying_price:,.0f}", style={'color': UI_COLOR['TEXT_MAIN']})], style=ROW_STYLE),
                html.Div([html.Span("基　差: ", style=LABEL_STYLE), html.Span(f"{basis_sign}{basis:.2f}", style={'color': basis_color, 'fontWeight': 'bold'})], style=ROW_STYLE),
                html.Div([html.Span("總　量: ", style=LABEL_STYLE), html.Span(f"{vol:,.0f}", style={'color': UI_COLOR['TEXT_MAIN']})], style=ROW_STYLE),
            ]),
        ])
    ])