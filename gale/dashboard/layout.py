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
    'width': '85px',       
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

def create_main_layout(max_capacity=200000, update_interval_ms=1000):
    """
    建構 Dash 應用的主要 Layout 結構。
    包含：Header, Scoreboard, Controls, Main Chart, Hidden Stores
    
    Args:
        max_capacity: Slider 的最大範圍 (對應 Shared Memory 容量)
        update_interval_ms: UI 更新頻率 (毫秒)
    """
    
    # 準備 Dropdown 選項
    tf_options = [{'label': k, 'value': k} for k in TIMEFRAMES.keys()]

    # 初始化空白圖表 (避免載入時白屏)
    initial_figure = create_blank_figure()
    
    # [User Request] Lookback Window Max should be half of capacity
    # (Capacity is over-provisioned, e.g. 200k for 1 day, but actual ticks ~50-80k)
    slider_max = max_capacity // 2
    
    # [Dynamic Slider Marks]
    # 根據 slider_max 自動計算刻度
    marks = {}
    steps = [0.02, 0.1, 0.25, 0.5, 0.75, 1.0] # 2%, 10%, 25%, 50%, 75%, 100%
    for s in steps:
        val = int(slider_max * s)
        # Simplify label (K/M)
        if val >= 1_000_000:
            label = f"{val/1_000_000:.1f}M"
        elif val >= 1000:
            label = f"{val/1000:.0f}K"
        else:
            label = str(val)
        marks[val] = label
        
    start_val = min(2000, slider_max)

    return html.Div(
        style={
            'backgroundColor': UI_COLOR['BG_MAIN'], 
            'color': UI_COLOR['TEXT_MAIN'], 
            'height': '100vh', 
            'padding': '20px'
        }, 
        children=[
        
            # 1. 標題區 (Header)
            # html.H2("🇹🇼 TXF Gale Quant Engine", style={'textAlign': 'center', 'marginBottom': '5px'}),
            
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
                        # html.Label("⏱️ Timeframe", style={'color': UI_COLOR['TEXT_SUB'], 'fontSize': '12px', 'marginBottom': '5px', 'display': 'block'}),
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
                        # html.Label("📊 Lookback Window", style={'color': UI_COLOR['TEXT_SUB'], 'fontSize': '12px', 'marginBottom': '5px', 'display': 'block'}),
                        dcc.Slider(
                            id='lookback-slider',
                            min=start_val, max=slider_max, step=2000, value=slider_max,
                            marks=marks,
                            tooltip={"placement": "bottom", "always_visible": True}
                        )
                    ]),
                    
                    # [新增] 畫筆粗細設定
                    html.Div(style={'width': '80px', 'marginLeft': '20px', 'marginRight': '0px'}, children=[
                        # html.Label("✏️ Width", style={'color': UI_COLOR['TEXT_SUB'], 'fontSize': '12px', 'marginBottom': '5px', 'display': 'block'}),
                        dcc.Dropdown(
                            id='drawing-width-dropdown',
                            options=[
                                {'label': '1px', 'value': 1},
                                {'label': '2px', 'value': 2},
                                {'label': '3px', 'value': 3},
                                {'label': '5px', 'value': 5},
                                {'label': '8px', 'value': 8},
                            ],
                            value=1, # Default
                            clearable=False,
                            style={'fontSize': '12px'}
                        )
                    ]),
                    
                    # [新增] 畫筆顏色設定
                    html.Div(style={'width': '80px', 'marginLeft': '10px'}, children=[
                        # html.Label("🎨 Color", style={'color': UI_COLOR['TEXT_SUB'], 'fontSize': '12px', 'marginBottom': '5px', 'display': 'block'}),
                        dcc.Dropdown(
                            id='drawing-color-dropdown',
                            options=[
                                {'label': '🟡', 'value': '#FFE100'}, # Yellow
                                {'label': '⚪', 'value': '#FFFFFF'}, # White
                                {'label': '🔴', 'value': '#FF4136'}, # Red
                                {'label': '🟢', 'value': '#2ECC40'}, # Green
                                {'label': '🔵', 'value': '#0074D9'}, # Blue
                            ],
                            value='#2ECC40', # Default Green
                            clearable=False,
                            style={'fontSize': '12px'}
                        )
                    ]),
                    
                    # [最右側] 截圖按鈕 (Snapshot)
                    html.Div(style={'marginLeft': '20px'}, children=[
                        html.Button("📸 Save HTML", id='btn-snapshot', n_clicks=0, style={
                            'backgroundColor': '#666666', # [User Request] Gray Button
                            'color': '#FFF',              # White Text
                            'border': 'none',
                            'padding': '8px 15px',
                            'borderRadius': '5px',
                            'fontWeight': 'bold',
                            'cursor': 'pointer'
                        }),
                        dcc.Download(id="download-snapshot")
                    ])
                ]
            ),

            # 4. 主圖表區 (Main Chart)
            # 單一圖表，包含 Price (Row 1) 與 Momentum (Row 2)
            dcc.Graph(
                id='main-chart', 
                figure=initial_figure, 
                style={'height': '89vh'}, # 佔據剩餘高度
                config={
                    'scrollZoom': True, 
                    'displayModeBar': True,
                    'modeBarButtonsToAdd': [
                        'drawline',
                        # 'drawopenpath',
                        # 'drawclosedpath',
                        'drawcircle',
                        'drawrect',
                        'eraseshape'
                    ]
                } 
            ),

            # 5. 狀態暫存區 (Hidden Stores & Interval)
            dcc.Interval(id='interval-component', interval=update_interval_ms, n_intervals=0),
            
            # 紀錄最後更新時間 (用於 Early Peek 優化)
            dcc.Store(id='last-update-timestamp', data=0),
            
            # 紀錄使用者的縮放狀態 (用於保持 Zoom Level)
            dcc.Store(id='chart-zoom-state', data=None), 

            # [Fix] Missing Store caused callback to fail
            dcc.Store(id='scoreboard-state', data={}),

            # [New] Session Static Data Store (PrevClose, Open)
            dcc.Store(id='session-static-store', data={}),
            
            # [New] Dummy store for clientside callback
            dcc.Store(id='drawing-config-store', data={}),

            # [Fix] Added missing store for active shape index
            dcc.Store(id='active-shape-store', data=None),
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

    # 開盤漲跌 (Intraday Change)
    change_from_open = last_price - open_price
    change_open_color = UI_COLOR['UP'] if change_from_open >= 0 else UI_COLOR['DOWN']
    change_open_sign = '+' if change_from_open >= 0 else ''
    
    # 當日波幅
    day_range = high - low
    day_range_pct = day_range / open_price * 100

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
        
        # [Right] 詳細數據網格 (Grid Layout 4 Columns - Optimized 4x3)
        html.Div(style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr 1fr 1fr', 'gap': '5px 30px', 'textAlign': 'left', 'fontSize': '14px'}, children=[

            # Column 1: Range (波動邊界)
            # 針對今日戰場範圍：High, Low, Range
            html.Div(children=[
                html.Div([html.Span("High:", style=LABEL_STYLE), html.Span(f"{high:,.0f}", style={'color': UI_COLOR['UP'], 'fontWeight': 'bold'})], style=ROW_STYLE),
                html.Div([html.Span("Low:", style=LABEL_STYLE), html.Span(f"{low:,.0f}", style={'color': UI_COLOR['DOWN'], 'fontWeight': 'bold'})], style=ROW_STYLE),
                html.Div([html.Span("Range:", style=LABEL_STYLE), html.Span(f"{day_range:.0f} ({day_range_pct:.2f}%)", style={'color': UI_COLOR['HIGHLIGHT']})], style=ROW_STYLE),
            ]),
            
            # Column 2: Context (市場參照)
            # 針對外部參考：PrevClose, Spot, Basis
            html.Div(children=[
                html.Div([html.Span("PrevClose:", style=LABEL_STYLE), html.Span(f"{prev_close:,.0f}", style={'color': UI_COLOR['TEXT_SUB']})], style=ROW_STYLE),
                html.Div([html.Span("Spot:", style=LABEL_STYLE), html.Span(f"{underlying_price:,.0f}", style={'color': UI_COLOR['TEXT_MAIN']})], style=ROW_STYLE),
                html.Div([html.Span("Basis:", style=LABEL_STYLE), html.Span(f"{basis_sign}{basis:.2f}", style={'color': basis_color, 'fontWeight': 'bold'})], style=ROW_STYLE),
            ]),
            
            # Column 3: Opening (開盤動態)
            # 針對開局表現：Open, OpenGap, OpenDelta
            html.Div(children=[
                html.Div([html.Span("Open:", style=LABEL_STYLE), html.Span(f"{open_price:,.0f}", style={'color': UI_COLOR['TEXT_MAIN']})], style=ROW_STYLE),
                html.Div([html.Span("OpenGap:", style=LABEL_STYLE), html.Span(f"{gap_sign}{gap:.0f}", style={'color': gap_color})], style=ROW_STYLE),
                html.Div([html.Span("OpenDelta:", style=LABEL_STYLE), html.Span(f"{change_open_sign}{change_from_open:.0f}", style={'color': change_open_color})], style=ROW_STYLE),
            ]),
            
            # Column 4: Volume & Cost (量價結構)
            # 針對成本與動能：VWAP、Volume
            html.Div(children=[
                html.Div([html.Span("VWAP:", style=LABEL_STYLE), html.Span(f"{vwap:,.0f}", style={'color': UI_COLOR['VWAP']})], style=ROW_STYLE),
                html.Div([html.Span("Volume:", style=LABEL_STYLE), html.Span(f"{vol:,.0f}", style={'color': UI_COLOR['TEXT_MAIN']})], style=ROW_STYLE),
            ]),
        ])
    ])