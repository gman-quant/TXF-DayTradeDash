# analysis/dash_layout.py

from dash import dcc, html

# --- Local Configuration ---
from config.ui_theme import UI_COLOR
from config.settings import TIMEFRAMES, DEFAULT_TIMEFRAME, SHM_CAPACITY
from gale.dashboard.chart import create_blank_figure

# =============================================================================
# 🏗️ Main Layout Structure (主頁面佈局)
# =============================================================================


def create_main_layout(max_capacity=SHM_CAPACITY, update_interval_ms=1000):
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
    slider_max = max_capacity
    
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
                    
                    # 2026-07-21:「畫筆粗細 / 畫筆顏色」兩個 dropdown 已移除
                    # (用戶確認不在本看板畫線)。畫線功能本身仍在 —— Plotly modebar
                    # 的繪圖工具照用,樣式改讀 config/ui_theme.py 的 DRAWING_STYLE。

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

            # [New] Session Static Data Store (Prior Close, Open)
            dcc.Store(id='session-static-store', data={}),
            
        ]
    )
