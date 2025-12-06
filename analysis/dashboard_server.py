# analysis/dashboard_server.py

import dash
import traceback
from dash import callback_context
from dash.dependencies import Input, Output, State
from dash.exceptions import PreventUpdate

# --- Local Modules ---
from analysis.dash_layout import create_main_layout, create_scoreboard_html
from analysis.dash_logic import (
    create_blank_figure, 
    process_market_data, 
    build_price_figure, 
    build_momentum_figure,
    get_last_value
)

# --- Configuration ---
try:
    from config.settings import PREV_CLOSE_PRICE
except ImportError:
    PREV_CLOSE_PRICE = 23000.0


def start_dashboard_server(indicator_manager, port=8050):
    """
    啟動 Dash 儀表板伺服器。
    
    Args:
        indicator_manager: 負責管理 RingBuffer 數據的核心物件
        port: 伺服器端口 (預設 8050)
    """
    NO_DATA_FIGURE = create_blank_figure()

    app = dash.Dash(__name__)
    app.layout = create_main_layout()

    # =========================================================================
    # 🎮 Callback 1: Zoom State Management (縮放狀態管理)
    # 負責處理使用者手動縮放、拖曳以及「重置視野」的邏輯
    # =========================================================================
    @app.callback(
        [Output('price-xaxis-range', 'data'), Output('price-yaxis-range', 'data')],
        [Input('price-chart', 'relayoutData'),
         Input('momentum-chart', 'relayoutData')]
    )
    def save_zoom_state(price_relayout, mom_relayout):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate
            
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
        relayoutData = price_relayout if trigger_id == 'price-chart' else mom_relayout
        
        if not relayoutData:
            raise PreventUpdate
            
        # 🐞 Debug: 如果還是不動，您可以取消註解這行看終端機印出什麼
        # print(f"🔍 Relayout Data: {relayoutData}")

        # --- 1. 處理重置 (Autorange / Reset) ---
        if 'xaxis.autorange' in relayoutData or 'yaxis.autorange' in relayoutData or 'autosize' in relayoutData:
            return None, None

        # --- 2. 處理 X 軸縮放 (支援多種格式) ---
        x_min, x_max = None, None

        # 格式 A: xaxis.range[0] (最常見)
        if 'xaxis.range[0]' in relayoutData and 'xaxis.range[1]' in relayoutData:
            x_min = relayoutData['xaxis.range[0]']
            x_max = relayoutData['xaxis.range[1]']
            
        # 格式 B: xaxis.range (陣列格式)
        elif 'xaxis.range' in relayoutData:
            x_min = relayoutData['xaxis.range'][0]
            x_max = relayoutData['xaxis.range'][1]
            
        # 格式 C: 透過形狀拖曳 (Drag Shapes) - 較少見但防呆
        # (略，通常上面兩種就夠了)

        # 只有當我們成功解析出 X 軸範圍時，才更新 Store
        if x_min is not None and x_max is not None:
            # 回傳 [x_min, x_max], None (我們不鎖定 Y 軸，讓它自動適應)
            return [x_min, x_max], None 
            
        # 如果只是滑鼠移動或其他無關事件，不觸發更新
        raise PreventUpdate

    # =========================================================================
    # 🔄 Callback 2: Main Dashboard Update (核心更新邏輯)
    # 負責定時從 RingBuffer 拉取數據、計算指標並更新圖表
    # =========================================================================
    @app.callback(
        [Output('price-chart', 'figure'),
         Output('momentum-chart', 'figure'),
         Output('live-status-panel', 'children'),
         Output('last-update-timestamp', 'data')],
        [Input('interval-component', 'n_intervals'),
         Input('lookback-slider', 'value'),
         Input('timeframe-dropdown', 'value'),
         Input('price-xaxis-range', 'data'),
         Input('price-yaxis-range', 'data')],
        [State('last-update-timestamp', 'data')]
    )
    def update_dashboard(n, lookback_count, timeframe, xaxis_range, yaxis_range, last_ts_stored):
        try:
            ctx = callback_context
            trigger_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else 'interval-component'

            # ---------------------------------------------------------
            # 1. 🚀 效能優化: Early Peek (提早檢查)
            # ---------------------------------------------------------
            # 檢查 RingBuffer 是否有資料 (O(1) 操作)
            if indicator_manager.count == 0:
                return NO_DATA_FIGURE, NO_DATA_FIGURE, "Waiting for data...", dash.no_update

            # 獲取最新資料時間戳 (O(1) 操作)
            current_latest_ts = indicator_manager.get_latest_timestamp()

            # 如果是由定時器觸發，且數據時間沒變，直接跳過不運算
            if trigger_id == 'interval-component':
                # 注意：這裡使用 float 比較，建議確保精度一致
                if n > 0 and current_latest_ts == last_ts_stored:
                    raise PreventUpdate

            # ---------------------------------------------------------
            # 2. 數據處理 (Data Processing)
            # ---------------------------------------------------------
            # 從 RingBuffer 切片並降頻，取得繪圖所需陣列
            data_pack = process_market_data(indicator_manager, lookback_count, timeframe)
            
            if not data_pack:
                return NO_DATA_FIGURE, NO_DATA_FIGURE, "Processing Error...", dash.no_update

            # ---------------------------------------------------------
            # 3. 視野控制策略 (Viewport Strategy)
            # ---------------------------------------------------------
            # 判斷是否需要強制重置範圍
            # - 切換週期 (timeframe) 時強制重置
            # - 使用者觸發了 Reset (xaxis_range 變成 None) 時強制重置
            force_reset = (trigger_id == 'timeframe-dropdown') or not (xaxis_range and xaxis_range[0])
            
            if force_reset:
                # [自動跟隨模式]
                # 使用系統計算的最佳範圍 (包含右側留白)
                final_xaxis_range = data_pack['default_range']
                
                # 🔥 關鍵修正：打破 'constant' 鎖定
                # 當進入自動模式時，使用動態的 revision (時間戳)，強迫副圖跟隨主圖重繪
                current_uirevision = str(current_latest_ts)
            else:
                # [手動縮放模式]
                # 保持使用者目前的縮放位置
                final_xaxis_range = xaxis_range
                
                # 使用 'constant' 鎖定狀態，避免定時更新導致畫面跳動
                current_uirevision = 'constant'

            # ---------------------------------------------------------
            # 4. 生成圖表 (Visualization)
            # ---------------------------------------------------------
            # 🔥 務必傳入 uirevision 參數
            fig_price = build_price_figure(
                data_pack, final_xaxis_range, yaxis_range, 
                uirevision=current_uirevision
            )
            
            fig_mom = build_momentum_figure(
                data_pack, final_xaxis_range, 
                uirevision=current_uirevision
            )
            
            # ---------------------------------------------------------
            # 5. 生成計分板 (Scoreboard)
            # ---------------------------------------------------------
            hist = data_pack['history']
            last_price = hist['price'][-1]
            
            scoreboard = create_scoreboard_html(
                last_price = last_price,
                change = last_price - PREV_CLOSE_PRICE,
                change_pct = ((last_price - PREV_CLOSE_PRICE)/PREV_CLOSE_PRICE)*100,
                open_price = hist['price'][0],
                high = get_last_value(hist, 'Session_High'),
                low  = get_last_value(hist, 'Session_Low'),
                vol  = get_last_value(hist, 'Total_Vol'),
                vwap = get_last_value(hist, 'Session_VWAP'),
                prev_close = PREV_CLOSE_PRICE,
                underlying_price = get_last_value(hist, 'Underlying_Price')
            )

            return fig_price, fig_mom, scoreboard, current_latest_ts

        except PreventUpdate:
            raise
        except Exception as e:
            # 捕捉未預期錯誤並打印，避免 Server 崩潰
            print(f"❌ Dash Error: {traceback.format_exc()}")
            return NO_DATA_FIGURE, NO_DATA_FIGURE, f"Error: {str(e)}", dash.no_update

    app.run(debug=False, port=port, host='0.0.0.0', use_reloader=False)