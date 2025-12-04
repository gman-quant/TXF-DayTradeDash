# analysis/dashboard_server.py

import dash
from dash import callback_context
from dash.dependencies import Input, Output, State
from dash.exceptions import PreventUpdate

# 引入拆分後的模組
from analysis.dash_layout import create_main_layout, create_scoreboard_html
from analysis.dash_logic import (
    create_blank_figure, 
    process_market_data, 
    build_price_figure, 
    build_momentum_figure,
    get_last_value
)

try:
    from config.settings import PREV_CLOSE_PRICE
except ImportError:
    PREV_CLOSE_PRICE = 23000.0


def start_dashboard_server(indicator_manager, port=8050):
    NO_DATA_FIGURE = create_blank_figure()

    app = dash.Dash(__name__)
    app.layout = create_main_layout()

    # --- Callback: Zoom ---
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
        
        if relayoutData:
            if 'xaxis.autorange' in relayoutData or 'yaxis.autorange' in relayoutData:
                return None, None
            xaxis_range = relayoutData.get('xaxis.range[0]'), relayoutData.get('xaxis.range[1]')
            if xaxis_range != (None, None) and xaxis_range[0] is not None:
                 return xaxis_range, None 
        raise PreventUpdate

    # --- Callback: Main Update ---
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

            # =========================================================
            # 🚀 效率優化 1: 提早檢查 (Early Peek) - RingBuffer 版
            # =========================================================
            # 查是否有資料
            if indicator_manager.count == 0:
                return NO_DATA_FIGURE, NO_DATA_FIGURE, "Waiting for data...", dash.no_update

            # 獲取最新時間戳
            current_latest_ts = indicator_manager.get_latest_timestamp()

            # ⚡️ 關鍵優化：如果是由定時器觸發，且數據沒變，立刻停止！
            # 這樣就省下了後面 process_market_data 的切片和運算成本
            if trigger_id == 'interval-component':
                if n > 0 and current_latest_ts == last_ts_stored:
                    raise PreventUpdate

            # =========================================================
            # 2. 處理數據 (只有在需要更新時才執行)
            # =========================================================
            # 注意：process_market_data 內部必須改寫以支援從 RingBuffer 切片
            # 這裡回傳的 data_pack 應該要是已經切好的 NumPy Array 或 List
            data_pack = process_market_data(indicator_manager, lookback_count, timeframe)
            
            # 雙重防呆 (理論上上面已經擋過了，但為了型別安全保留)
            if not data_pack:
                return NO_DATA_FIGURE, NO_DATA_FIGURE, "Processing Error...", dash.no_update

            # =========================================================
            # 3. 決定 X 軸範圍
            # =========================================================
            # 判斷是否需要強制重置範圍 (Force Reset)
            # 條件 A: 觸發源是「週期選單 (timeframe-dropdown)」
            # 條件 B: 使用者點兩下重置了 (xaxis_range 為空)
            force_reset = (trigger_id == 'timeframe-dropdown') or not (xaxis_range and xaxis_range[0])
            
            if force_reset:
                # 強制使用系統計算的 "最佳預設範圍" (包含右側預留空間)
                final_xaxis_range = data_pack['default_range']
            else:
                # 其他情況 (定時更新、拉滑桿)，保持使用者目前的縮放位置
                final_xaxis_range = xaxis_range

            # =========================================================
            # 4. 生成圖表
            # =========================================================
            fig_price = build_price_figure(data_pack, final_xaxis_range, yaxis_range)
            fig_mom = build_momentum_figure(data_pack, final_xaxis_range) 
            
            # =========================================================
            # 5. 生成 Scoreboard
            # =========================================================
            hist = data_pack['history']
            last_price = hist['price'][-1]
            
            scoreboard = create_scoreboard_html(
                last_price = last_price,
                change = last_price - PREV_CLOSE_PRICE,
                change_pct = ((last_price - PREV_CLOSE_PRICE)/PREV_CLOSE_PRICE)*100,
                open_price = hist['price'][0],
                high = get_last_value(hist, 'Session_High'),
                low = get_last_value(hist, 'Session_Low'),
                vol = get_last_value(hist, 'Total_Vol'),
                vwap = get_last_value(hist, 'Session_VWAP'),
                prev_close = PREV_CLOSE_PRICE,
                underlying_price = get_last_value(hist, 'Underlying_Price')
            )

            return fig_price, fig_mom, scoreboard, current_latest_ts

        except PreventUpdate:
            raise
        except Exception as e:
            import traceback
            # 這裡建議保留 print，方便除錯
            print(f"❌ Dash Error: {traceback.format_exc()}")
            return NO_DATA_FIGURE, NO_DATA_FIGURE, f"Error: {str(e)}", dash.no_update

    app.run(debug=False, port=port, host='0.0.0.0', use_reloader=False)