# analysis/dash_layout.py

from dash import dcc, html

def create_main_layout():
    return html.Div(style={'backgroundColor': '#111111', 'color': '#7FDBFF', 'height': '100vh', 'padding': '20px'}, children=[
        
        html.H2("🚀 TXF Gale Quant Engine", style={'textAlign': 'center', 'marginBottom': '5px'}),
        
        # Scoreboard 容器
        html.Div(id='live-status-panel', style={'marginBottom': '15px'}),

        # Slider
        html.Div([
            html.Label("📊 顯示筆數 (Lookback Window)", style={'color': '#888', 'fontSize': '12px', 'marginBottom': '5px'}),
            dcc.Slider(
                id='lookback-slider',
                min=500, max=50000, step=500, value=25000,
                marks={500: '500', 2000: '2K', 5000: '5K', 10000: '10K', 25000: '25K', 50000: '50K'},
                tooltip={"placement": "bottom", "always_visible": True}
            )
        ], style={'width': '80%', 'margin': '0 auto 20px auto'}),

        # Charts
        dcc.Graph(id='price-chart', style={'height': '55vh'}),
        dcc.Graph(id='momentum-chart', style={'height': '25vh'}),
        
        # Stores
        dcc.Store(id='price-xaxis-range', data=None),
        dcc.Store(id='price-yaxis-range', data=None),
        dcc.Store(id='last-update-timestamp', data=0),
        
        # Interval
        dcc.Interval(id='interval-component', interval=1000, n_intervals=0)
    ])

def create_scoreboard_html(last_price, change, change_pct, open_price, high, low, vol, vwap, prev_close, underlying_price):
    """
    生成戰情面板的 HTML (從主邏輯分離出來)
    """
    main_color = '#FF4136' if change < 0 else '#2ECC40'
    sign = '+' if change >= 0 else ''

    # 1. 當盤波幅 (Range)
    day_range = high - low
    
    # 2. 開盤跳空 (Gap)
    gap = open_price - prev_close
    gap_color = '#FF4136' if gap < 0 else '#2ECC40'
    gap_sign = '+' if gap >= 0 else ''

    # ⬇️ 🆕 新增計算：
    # 1. 期現貨基差 (Basis)
    basis = last_price - underlying_price
    basis_color = '#FFF000' # 金色突顯
    basis_sign = '+' if basis >= 0 else ''
    
    # 2. VWAP 乖離率 (Deviation % from VWAP)
    vwap_deviation_pct = ((last_price / vwap) - 1) * 100 if vwap else 0.0
    vwap_dev_color = '#FF4136' if vwap_deviation_pct < -0.2 else ('#2ECC40' if vwap_deviation_pct >= 0.2 else '#888') # 偏離 0.1% 就變色
    
    # 共同的標籤寬度
    LABEL_STYLE = {
        'color': '#888', 
        'display': 'inline-block', 
        'width': '75px',       # 1. 設定剛好夠放下最長字串("VWAP Dev:")的寬度
        'textAlign': 'right',  # 2. 關鍵：文字靠右，消除短標籤的空隙
        'marginRight': '5px'   # 3. 給予標籤和數值之間一個舒適的固定間距
    }
    
    return html.Div(style={
        'display': 'flex', 
        'justifyContent': 'center', 
        'alignItems': 'center',
        'backgroundColor': '#1E1E1E', 
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

            # COLUMN 1: 價格極值 (垂直堆疊)
            html.Div(children=[
                html.Div([html.Span("最高: ", style=LABEL_STYLE), html.Span(f"{high:,.0f}", style={'color': '#2ECC40', 'fontWeight': 'bold'})]),
                html.Div([html.Span("最低: ", style=LABEL_STYLE), html.Span(f"{low:,.0f}", style={'color': '#FF4136', 'fontWeight': 'bold'})]),
                html.Div([html.Span("波幅: ", style=LABEL_STYLE), html.Span(f"{day_range:.0f}", style={'color': '#FFF000'})]),
            ]),
            
            # COLUMN 2: 初始情勢 (垂直堆疊)
            html.Div(children=[
                html.Div([html.Span("開盤: ", style=LABEL_STYLE), html.Span(f"{open_price:,.0f}", style={'color': '#FFF'})]),
                html.Div([html.Span("昨收: ", style=LABEL_STYLE), html.Span(f"{prev_close:,.0f}", style={'color': '#AAA'})]),
                html.Div([html.Span("跳空: ", style=LABEL_STYLE), html.Span(f"{gap_sign}{gap:.0f}", style={'color': gap_color})]),
            ]),

            # COLUMN 3: 成本與乖離率
            html.Div(children=[
                html.Div([html.Span("VWAP: ", style=LABEL_STYLE), html.Span(f"{vwap:,.0f}", style={'color': '#008692'})]),
                html.Div([html.Span("VWAP Dev: ", style=LABEL_STYLE), html.Span(f"{vwap_deviation_pct:.2f}%", style={'color': vwap_dev_color})]),
            ]),
            # COLUMN 4: 流量與基差
            html.Div(children=[
                html.Div([html.Span("基　差: ", style=LABEL_STYLE), html.Span(f"{basis_sign}{basis:.2f}", style={'color': basis_color, 'fontWeight': 'bold'})]),
                html.Div([html.Span("成交量: ", style=LABEL_STYLE), html.Span(f"{vol:,.0f}", style={'color': '#FFF'})]),
            ]),
        ])
    ])