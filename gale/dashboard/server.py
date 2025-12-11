# analysis/dashboard_server.py

import time
import traceback
import dash
from dash import callback_context, no_update
from dash.dependencies import Input, Output, State
from dash.exceptions import PreventUpdate

# --- Local Modules ---
from gale.dashboard.layout import create_main_layout, create_scoreboard_html
from gale.dashboard.logic import (
    process_market_data, 
    build_combined_figure
)
from gale.dashboard.chart import create_blank_figure
from gale.dashboard.state import get_last_value

# --- Configuration ---
try:
    from config.settings import PREV_CLOSE_PRICE
except ImportError:
    PREV_CLOSE_PRICE = 23000.0

# =============================================================================
# 🚀 Dashboard Server Entry Point
# =============================================================================

def start_dashboard_server(indicator_manager, port=8050):
    """
    啟動 Dash 儀表板伺服器。
    整合 Layout、Logic 與 Callbacks，負責即時數據的前後端交互。
    
    Args:
        indicator_manager: 數據核心 (RingBuffer Manager)
        port: 服務端口 (Default: 8050)
    """
    
    # 初始化空白圖表 (用於錯誤或無數據時顯示)
    NO_DATA_FIGURE = create_blank_figure()

    app = dash.Dash(__name__)
    app.layout = create_main_layout()

    # =========================================================================
    # 🎮 Callback 1: Viewport & Zoom Management (視野控制)
    # =========================================================================
    @app.callback(
        Output('chart-zoom-state', 'data'),
        Input('main-chart', 'relayoutData')
    )
    def save_zoom_state(relayoutData):
        """
        監聽前端圖表的縮放事件，將使用者的視野範圍 (Range) 存入 dcc.Store。
        當圖表更新時，優先使用此儲存的範圍，以防止畫面跳動。
        """
        if not relayoutData:
            raise PreventUpdate
            
        # 1. 處理自動縮放/重置 (Autoscale / Reset Axes)
        # 當使用者雙擊圖表重置時，清空儲存狀態 -> 回歸系統自動跟隨
        if 'xaxis.autorange' in relayoutData or 'autosize' in relayoutData:
            return None

        # 2. 捕捉 X 軸範圍 (Pan / Zoom)
        # Plotly 的 relayoutData 格式可能不同，需多重判斷
        x_min, x_max = None, None

        if 'xaxis.range[0]' in relayoutData and 'xaxis.range[1]' in relayoutData:
            x_min = relayoutData['xaxis.range[0]']
            x_max = relayoutData['xaxis.range[1]']
        elif 'xaxis.range' in relayoutData:
            x_min = relayoutData['xaxis.range'][0]
            x_max = relayoutData['xaxis.range'][1]

        if x_min is not None and x_max is not None:
            return [x_min, x_max]
            
        raise PreventUpdate

    # =========================================================================
    # 🔄 Callback 2: Core Dashboard Update Loop (核心更新循環)
    # =========================================================================
    @app.callback(
        [Output('main-chart', 'figure'),
         Output('live-status-panel', 'children'),
         Output('last-update-timestamp', 'data'),
         Output('scoreboard-state', 'data')],
        [Input('interval-component', 'n_intervals'),
         Input('lookback-slider', 'value'),
         Input('timeframe-dropdown', 'value'),
         Input('chart-zoom-state', 'data')],
        [State('last-update-timestamp', 'data')]
    )
    def update_dashboard(n, lookback_count, timeframe, saved_zoom_range, last_ts_stored):
        """
        定時觸發的主更新函數：
        1. 檢查是否有新數據
        2. 處理數據 (Data Processing)
        3. 繪製圖表 (Visualization)
        4. 計算戰情板數據 (Scoreboard)
        """
        try:
            ctx = callback_context
            trigger_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else 'interval-component'

            # --- 1. Early Peek (效能優化) ---
            # 如果 RingBuffer 為空，顯示等待訊息
            if indicator_manager.count == 0:
                return NO_DATA_FIGURE, "Waiting for data...", no_update, no_update

            # 檢查數據是否有更新 (Timestamp Check)
            current_latest_ts = indicator_manager.get_latest_timestamp()

            # 若由定時器觸發且數據時間未變 -> 跳過運算，節省 CPU
            if trigger_id == 'interval-component' and n > 0 and current_latest_ts == last_ts_stored:
                raise PreventUpdate

            # --- 2. Data Processing (數據處理) ---
            data_pack = process_market_data(indicator_manager, lookback_count, timeframe)
            
            if not data_pack:
                return NO_DATA_FIGURE, "Processing Error...", no_update, no_update
            
            # --- 3. Figure Generation (圖表繪製) ---
            fig = build_combined_figure(data_pack)
            
            # [Zoom Persistence Logic]
            # 決定最終使用的 X 軸範圍
            if saved_zoom_range:
                # Case A: 使用者有手動縮放 -> 尊重使用者，鎖定範圍
                final_range = saved_zoom_range
            else:
                # Case B: 無縮放紀錄 (或重置) -> 使用系統計算的最新範圍 (跟隨最新報價)
                final_range = data_pack.get('default_range')
            
            fig.update_layout(
                uirevision='constant', # 告訴 Plotly 盡量保留 UI 狀態 (如 Legend 開關)
                xaxis=dict(
                    range=final_range  # 強制套用我們計算出的範圍
                )
            )

            # --- 4. Scoreboard Calculation (戰情板計算) ---
            hist = data_pack['history']
            
            # 安全取得 OHLC (避免剛啟動時數據不足)
            # [New] Get PREV_CLOSE_PRICE from SHM Header
            try:
                # ring_buffer is dynamically attached in DashboardRunner
                shm_prev_close = getattr(indicator_manager, 'ring_buffer', None).prev_close
                if shm_prev_close and shm_prev_close > 0:
                    current_prev_close = shm_prev_close
                else:
                    current_prev_close = PREV_CLOSE_PRICE
            except Exception:
                current_prev_close = PREV_CLOSE_PRICE

            if len(hist['close']) > 0:
                last_price = hist['close'][-1]
                open_p = hist['close'][0]
            else:
                last_price = current_prev_close
                open_p = current_prev_close
            
            # 計算漲跌幅
            change = last_price - current_prev_close
            change_pct = (change / current_prev_close * 100) if current_prev_close else 0

            # 準備數據包供 HTML 生成
            sb_data = {
                'last_price': last_price,
                'change': change,
                'change_pct': change_pct,
                'open_price': open_p,
                'high': get_last_value(hist, 'Session_High'),
                'low': get_last_value(hist, 'Session_Low'),
                'vol': get_last_value(hist, 'Total_Vol'),
                'vwap': get_last_value(hist, 'VWAP'),
                'prev_close': current_prev_close,
                'underlying_price': get_last_value(hist, 'Underlying_Price')
            }

            scoreboard = create_scoreboard_html(**sb_data)

            # [Update] Return 4 outputs
            return fig, scoreboard, current_latest_ts, sb_data

        except PreventUpdate:
            raise
        except Exception as e:
            # 捕捉未預期錯誤，打印 Traceback 但不讓 Server 崩潰
            print(f"❌ Dash Error: {traceback.format_exc()}")
            return NO_DATA_FIGURE, f"System Error: {str(e)}", no_update

    # =========================================================================
    # 📸 Callback 3: Snapshot Export (HTML 存檔)
    # =========================================================================
    @app.callback(
        Output("download-snapshot", "data"),
        Input("btn-snapshot", "n_clicks"),
        [State("main-chart", "figure"),
         State("scoreboard-state", "data")],
        prevent_initial_call=True
    )
    def export_html_snapshot(n_clicks, fig_data, sb_data):
        """
        當按下「Save HTML」按鈕時，將當前圖表匯出為獨立 HTML 檔案。
        [Enhanced] 現在會包含上方的戰情板數據 (Scoreboard)！
        """
        if not fig_data or n_clicks is None:
            raise PreventUpdate
            
        import plotly.graph_objects as go
        from datetime import datetime
        
        # 1. 重建 Figure 物件
        fig = go.Figure(fig_data)
        
        # 2. 生成檔名 (Session Logic)
        from datetime import timedelta
        now = datetime.now()
        ts_str = now.strftime('%Y-%m-%d %H:%M:%S') # [Fix] Define ts_str for HTML template
        
        # 判斷盤別 (Day vs Night)
        # Night Session: 15:00 ~ 05:00 (of next day)
        if now.hour < 8:
            # 凌晨時段屬於前一天的夜盤
            date_str = (now - timedelta(days=1)).strftime('%Y-%m-%d')
            suffix = '-n'
        elif now.hour >= 15:
            # 下午 3 點後屬於當天的夜盤
            date_str = now.strftime('%Y-%m-%d')
            suffix = '-n'
        else:
            # 日盤 (08:45 ~ 13:45)
            date_str = now.strftime('%Y-%m-%d')
            suffix = ''
            
        filename = f"TXF-Chart-{date_str}{suffix}.html"
        
        # 3. 建構 HTML 內容 (Header + Plot)
        def write_full_html():
            # [Revert] 回復為全黑模式 (Dark Theme)
            # 強制設定圖表高度與 RWD
            fig.layout.height = None
            fig.layout.autosize = True
            
            plot_html = fig.to_html(
                include_plotlyjs='cdn', 
                full_html=False, 
                config={'scrollZoom': True, 'displayModeBar': True, 'responsive': True}, 
                default_height='100%',
                default_width='100%'
            )
            
            # B. 產生 Scoreboard HTML (完整版)
            if not sb_data:
                header_html = "<div style='color:white; text-align:center'>No Data</div>"
            else:
                # --- 數據準備 ---
                price = sb_data.get('last_price', 0)
                change = sb_data.get('change', 0)
                pct = sb_data.get('change_pct', 0)
                vol = sb_data.get('vol', 0)
                high = sb_data.get('high', 0)
                low = sb_data.get('low', 0)
                prev_close = sb_data.get('prev_close', 0)
                open_p = sb_data.get('open_price', 0)
                vwap = sb_data.get('vwap', 0)
                u_price = sb_data.get('underlying_price', 0)
                
                # --- 邏輯計算 (Dark Mode) ---
                main_color = '#2ECC40' if change >= 0 else '#FF4136'
                sign = '+' if change >= 0 else ''
                
                gap = open_p - prev_close
                gap_color = '#2ECC40' if gap >= 0 else '#FF4136'
                gap_sign = '+' if gap >= 0 else ''
                
                basis = price - u_price
                basis_color = '#FFF000'
                basis_sign = '+' if basis >= 0 else ''
                
                vwap_dev_pct = ((price / vwap) - 1) * 100 if vwap else 0.0
                if vwap_dev_pct >= 0.2: dev_color = '#2ECC40'
                elif vwap_dev_pct <= -0.2: dev_color = '#FF4136'
                else: dev_color = '#BBBBBB'
                
                chg_open = price - open_p
                chg_open_color = '#2ECC40' if chg_open >= 0 else '#FF4136'
                chg_open_sign = '+' if chg_open >= 0 else ''
                
                day_range = high - low

                # --- HTML 模板 (Dark Context) ---
                header_html = f"""
                <div style="background-color: #1E1E1E; color: white; padding: 15px; border-radius: 10px; border: 1px solid {main_color}; margin-bottom: 20px; font-family: sans-serif; display: flex; justify-content: center; align-items: center;">
                    
                    <!-- [Left] Price Block -->
                    <div style="margin-right: 50px; text-align: center;">
                        <div style="font-size: 48px; font-weight: bold; color: {main_color}; line-height: 1;">{price:,.0f}</div>
                        <div style="font-size: 20px; color: {main_color}; margin-top: 8px;">{sign}{change:.0f} ({sign}{pct:.2f}%)</div>
                        <div style="font-size: 12px; color: #888; margin-top: 8px;">{ts_str}</div>
                    </div>
                    
                    <!-- [Right] Full Info Grid (4 Columns) -->
                    <div style="display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 8px 40px; text-align: left; font-size: 14px; line-height: 1.6; color: #BBB;">
                        
                        <!-- Col 1: Boundary -->
                        <div>
                            <div><span style="color:#BBB;">最高: </span><span style="color:#2ECC40; font-weight:bold;">{high:,.0f}</span></div>
                            <div><span style="color:#BBB;">最低: </span><span style="color:#FF4136; font-weight:bold;">{low:,.0f}</span></div>
                            <div><span style="color:#BBB;">波幅: </span><span style="color:#FFF000; font-weight:bold;">{day_range:.0f}</span></div>
                        </div>

                        <!-- Col 2: Anchors -->
                        <div>
                            <div><span style="color:#BBB;">昨收: </span><span style="color:#BBB;">{prev_close:,.0f}</span></div>
                            <div><span style="color:#BBB;">開盤: </span><span style="color:#FFF;">{open_p:,.0f}</span></div>
                            <div><span style="color:#BBB;">跳空: </span><span style="color:{gap_color};">{gap_sign}{gap:.0f}</span></div>
                        </div>

                        <!-- Col 3: Momentum -->
                        <div>
                            <div><span style="color:#BBB;">VWAP: </span><span style="color:#008692; font-weight:bold;">{vwap:,.0f}</span></div>
                            <div><span style="color:#BBB;">開盤漲跌: </span><span style="color:{chg_open_color};">{chg_open_sign}{chg_open:.0f}</span></div>
                            <div><span style="color:#BBB;">VWAP Dev: </span><span style="color:{dev_color};">{vwap_dev_pct:.2f}%</span></div>
                        </div>

                        <!-- Col 4: Context -->
                        <div>
                            <div><span style="color:#BBB;">現貨價: </span><span style="color:#FFF;">{u_price:,.0f}</span></div>
                            <div><span style="color:#BBB;">基　差: </span><span style="color:{basis_color}; font-weight:bold;">{basis_sign}{basis:.2f}</span></div>
                            <div><span style="color:#BBB;">總　量: </span><span style="color:#FFF;">{vol:,.0f}</span></div>
                        </div>
                    </div>
                </div>
                """
                
            return f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>Gale Snapshot {ts_str}</title>
                <style>
                    body {{ 
                        background-color: #111; 
                        color: #ddd; 
                        margin: 0; 
                        padding: 20px; 
                        font-family: sans-serif; 
                        height: 100vh; 
                        display: flex; 
                        flex-direction: column; 
                        box-sizing: border-box; 
                    }}
                    h2 {{ text-align: center; color: #fff; margin: 0 0 15px 0; font-size: 24px; }}
                    
                    /* Force plot to take remaining space (Responsive) */
                    .plotly-graph-div {{ 
                        flex: 1; 
                        width: 100%; 
                        height: 85vh !important; 
                    }}
                </style>
            </head>
            <body>
                <h2>🚀 TXF Gale Snapshot</h2>
                {header_html}
                {plot_html}
            </body>
            </html>
            """
            return full_html
            
        return dict(content=write_full_html(), filename=filename)

    # 啟動 Flask Server
    app.run(debug=False, port=port, host='0.0.0.0', use_reloader=False)