# analysis/dashboard_server.py

import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objects as go
import pandas as pd
import threading

# ------------------------------------------------------------
# 啟動 Dash 伺服器的封裝函數
# ------------------------------------------------------------
def start_dashboard_server(indicator_manager, port=8050):
    """
    啟動 Dash 伺服器 (Blocking Call - 需在 Thread 中運行)
    args:
        indicator_manager: 來自 CoreProcessor 的指標管理器實例 (含 .history 和 .candles)
    """
    app = dash.Dash(__name__)
    
    # --- 1. 定義佈局 (Layout) ---
    app.layout = html.Div(style={'backgroundColor': '#111111', 'color': '#7FDBFF', 'height': '100vh', 'padding': '20px'}, children=[
        
        html.H2("🚀 TXF Gale Quant Engine - Live Monitor", style={'textAlign': 'center'}),
        
        # 顯示筆數控制滑桿 (Slider)
        html.Div([
            html.Label("📊 顯示筆數 (Lookback Window - Ticks)", style={'color': '#FFFFFF', 'marginBottom': '10px', 'display': 'block'}),
            dcc.Slider(
                id='lookback-slider',
                min=500,
                max=50000,
                step=500,
                value=5000, # 預設顯示 5000 筆 Tick 的範圍
                marks={500: '500', 2000: '2K', 5000: '5K', 10000: '10K', 25000: '25K', 50000: '50K'},
                tooltip={"placement": "bottom", "always_visible": True}
            )
        ], style={'width': '60%', 'margin': '0 auto 20px auto'}),

        # 狀態列
        html.Div(id='live-status-text', style={'textAlign': 'center', 'marginBottom': '20px', 'fontSize': '18px'}),

        # 圖表區
        dcc.Graph(id='price-chart', style={'height': '60vh'}),
        dcc.Graph(id='momentum-chart', style={'height': '30vh'}),

        # 定時器 (每 1000ms = 1秒 更新一次數據)
        dcc.Interval(
            id='interval-component',
            interval=1000, 
            n_intervals=0
        )
    ])

    # --- 2. 定義回調 (Callbacks) ---
    @app.callback(
        [Output('price-chart', 'figure'),
         Output('momentum-chart', 'figure'),
         Output('live-status-text', 'children')],
        [Input('interval-component', 'n_intervals'),
         Input('lookback-slider', 'value')]
    )
    def update_graph_live(n, lookback_count):
        try:
            # 獲取數據引用
            history = indicator_manager.history
            candles = indicator_manager.candles # 歷史 K 線 (Closed)
            current_candle = indicator_manager.current_candle # 正在形成的 K 線 (Open)
            
            raw_data_len = len(history['timestamp'])
            
            if raw_data_len == 0:
                return go.Figure(), go.Figure(), "Waiting for data..."

            # =========================================================
            # 1. 處理 Raw Tick Data (VWAP / Momentum)
            # =========================================================
            
            # 決定顯示範圍
            lookback = int(lookback_count) if lookback_count is not None else 5000
            start_tick_idx = max(0, raw_data_len - lookback)
            
            # 獲取此視窗的「起始時間戳」，用於對齊 K 線
            start_timestamp_ms = history['timestamp'][start_tick_idx]
            
            # 應用降頻 (Downsampling) 以優化繪圖效能
            step = 1
            if lookback > 2000:
                step = lookback // 2000 
            
            # 提取 Tick 數據
            timestamps = history['timestamp'][start_tick_idx::step]
            vwap_50 = history['VWAP_50'][start_tick_idx::step]
            momentum = history['Momentum_180'][start_tick_idx::step]
            
            # 建立 Tick 的時間軸 (UTC+8)
            tick_x_axis = pd.to_datetime(timestamps, unit='ms') + pd.Timedelta(hours=8)

            # =========================================================
            # 2. 處理 K 線數據 (Merging & Alignment)
            # =========================================================
            
            # 複製歷史 K 線數據 (避免修改到原始數據)
            plot_candles = {k: list(candles[k]) for k in candles if k != 'volume'}
            
            # 關鍵：將「正在形成中」的 K 線合併進來
            if current_candle and current_candle.get('time'):
                for key in ['time', 'open', 'high', 'low', 'close']:
                    plot_candles[key].append(current_candle[key])
            
            # 過濾 K 線：只保留時間 >= start_timestamp_ms 的 K 線
            # 這樣能確保 K 線圖和 VWAP/Momentum 的 X 軸起點一致
            candle_start_index = 0
            if len(plot_candles['time']) > 0:
                # 簡單線性搜尋 (由於 K 線數量不多，這很快)
                for i, ts in enumerate(plot_candles['time']):
                    if ts >= start_timestamp_ms:
                        candle_start_index = i
                        break
            
            # 執行切片
            plot_candles_sliced = {k: plot_candles[k][candle_start_index:] for k in plot_candles}
            candle_len = len(plot_candles_sliced['time'])
            
            # 建立 K 線的時間軸
            candle_x = pd.to_datetime(plot_candles_sliced['time'], unit='ms') + pd.Timedelta(hours=8)

            # =========================================================
            # 3. 繪製圖表
            # =========================================================
            
            # --- 上圖：K 線 + VWAP ---
            fig_price = go.Figure()

            # K 線圖 (Candlestick)
            fig_price.add_trace(go.Candlestick(
                x=candle_x,
                open=plot_candles_sliced['open'],
                high=plot_candles_sliced['high'],
                low=plot_candles_sliced['low'],
                close=plot_candles_sliced['close'],
                name='5s Candle',
                increasing_line_color='#FF4136', # 紅漲 (TW Style)
                decreasing_line_color='#2ECC40'  # 綠跌
            ))
            
            # VWAP 線 (使用 Scattergl 加速)
            fig_price.add_trace(go.Scattergl(
                x=tick_x_axis, y=vwap_50,
                mode='lines', name='VWAP(50)',
                line=dict(color='#FFD700', width=1.5, dash='dash')
            ))

            fig_price.update_layout(
                title='Real-Time Price (5s Candle) & VWAP',
                template='plotly_dark',
                margin=dict(l=40, r=40, t=40, b=40),
                xaxis=dict(showgrid=True, rangeslider=dict(visible=False)), # 隱藏 Plotly 默認的 Range Slider
                yaxis=dict(showgrid=True, gridcolor='#333', tickformat=',.0f'), # 顯示完整數字
                paper_bgcolor='#111111',
                plot_bgcolor='#111111',
                hovermode='x unified',
                # 圖例置頂
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5)
            )

            # --- 下圖：Momentum ---
            fig_mom = go.Figure()
            
            # 設定顏色：大於0為紅，小於0為綠
            colors = ['#FF4136' if v >= 0 else '#2ECC40' for v in momentum]
            
            fig_mom.add_trace(go.Bar(
                x=tick_x_axis, y=momentum,
                marker_color=colors,
                name='Momentum(180)',
                marker_line_width=0 # 去除邊框，避免黑成一片
            ))

            fig_mom.update_layout(
                title='Momentum (180 Ticks)',
                template='plotly_dark',
                margin=dict(l=40, r=40, t=40, b=40),
                xaxis=dict(showgrid=True),
                yaxis=dict(showgrid=True, gridcolor='#333'),
                paper_bgcolor="#111111",
                plot_bgcolor="#111111",
                hovermode='x unified', # 統一 Hover 顯示
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5)
            )

            # 狀態列文字
            last_price = history['price'][raw_data_len - 1] if raw_data_len > 0 else 0
            status_text = f"Last Update: {tick_x_axis[-1].strftime('%H:%M:%S')} | Price: {last_price} | Candles: {candle_len} | Tick View: {len(tick_x_axis)} pts (Step: {step})"

            return fig_price, fig_mom, status_text

        except Exception as e:
            import traceback
            print(f"❌ Dash Callback Error:\n{traceback.format_exc()}")
            return go.Figure(), go.Figure(), f"Error: {str(e)}"

    # --- 3. 啟動 Server ---
    # 使用 app.run (新版 Dash 標準)
    app.run(debug=False, port=port, host='0.0.0.0', use_reloader=False)