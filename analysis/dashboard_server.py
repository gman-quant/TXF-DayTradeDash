# analysis/dashboard_server.py

import time
import traceback
import dash
from dash import callback_context, no_update
from dash.dependencies import Input, Output, State
from dash.exceptions import PreventUpdate

# --- Local Modules ---
from analysis.dash_layout import create_main_layout, create_scoreboard_html
from analysis.dash_logic import (
    create_blank_figure, 
    process_market_data, 
    build_combined_figure, 
    get_last_value
)

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
         Output('last-update-timestamp', 'data')],
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
                return NO_DATA_FIGURE, "Waiting for data...", no_update

            # 檢查數據是否有更新 (Timestamp Check)
            current_latest_ts = indicator_manager.get_latest_timestamp()

            # 若由定時器觸發且數據時間未變 -> 跳過運算，節省 CPU
            if trigger_id == 'interval-component' and n > 0 and current_latest_ts == last_ts_stored:
                raise PreventUpdate

            # --- 2. Data Processing (數據處理) ---
            data_pack = process_market_data(indicator_manager, lookback_count, timeframe)
            
            if not data_pack:
                return NO_DATA_FIGURE, "Processing Error...", no_update
            
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
            if len(hist['price']) > 0:
                last_price = hist['price'][-1]
                open_p = hist['price'][0]
            else:
                last_price = PREV_CLOSE_PRICE
                open_p = PREV_CLOSE_PRICE
            
            # 計算漲跌幅
            change = last_price - PREV_CLOSE_PRICE
            change_pct = (change / PREV_CLOSE_PRICE * 100) if PREV_CLOSE_PRICE else 0

            scoreboard = create_scoreboard_html(
                last_price = last_price,
                change = change,
                change_pct = change_pct,
                open_price = open_p,
                high = get_last_value(hist, 'Session_High'),
                low  = get_last_value(hist, 'Session_Low'),
                vol  = get_last_value(hist, 'Total_Vol'),
                vwap = get_last_value(hist, 'Session_VWAP'),
                prev_close = PREV_CLOSE_PRICE,
                underlying_price = get_last_value(hist, 'Underlying_Price')
            )

            return fig, scoreboard, current_latest_ts

        except PreventUpdate:
            raise
        except Exception as e:
            # 捕捉未預期錯誤，打印 Traceback 但不讓 Server 崩潰
            print(f"❌ Dash Error: {traceback.format_exc()}")
            return NO_DATA_FIGURE, f"System Error: {str(e)}", no_update

    # 啟動 Flask Server
    app.run(debug=False, port=port, host='0.0.0.0', use_reloader=False)