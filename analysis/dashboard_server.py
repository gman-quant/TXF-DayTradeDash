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
    build_momentum_figure
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
         Input('price-xaxis-range', 'data'),
         Input('price-yaxis-range', 'data')],
        [State('last-update-timestamp', 'data')]
    )
    def update_dashboard(n, lookback_count, xaxis_range, yaxis_range, last_ts_stored):
        try:
            ctx = callback_context
            trigger_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else 'interval-component'

            # =========================================================
            # 🚀 效率優化 1: 提早檢查 (Early Peek)
            # =========================================================
            # 直接讀取 Manager 的原始列表長度，這是 O(1) 操作，極快
            raw_history = indicator_manager.history
            if not raw_history['timestamp']:
                return NO_DATA_FIGURE, NO_DATA_FIGURE, "Waiting for data...", dash.no_update

            # 直接讀取最新時間戳 (不經過 process_market_data)
            current_latest_ts = raw_history['timestamp'][-1]

            # ⚡️ 關鍵優化：如果是由定時器觸發，且數據沒變，立刻停止！
            # 這樣就省下了後面 process_market_data 的切片和運算成本
            if trigger_id == 'interval-component':
                if n > 0 and current_latest_ts == last_ts_stored:
                    raise PreventUpdate

            # =========================================================
            # 2. 處理數據 (只有在需要更新時才執行)
            # =========================================================
            data_pack = process_market_data(indicator_manager, lookback_count)
            
            # 雙重防呆 (理論上上面已經擋過了，但為了型別安全保留)
            if not data_pack:
                return NO_DATA_FIGURE, NO_DATA_FIGURE, "Processing Error...", dash.no_update

            # =========================================================
            # 3. 決定 X 軸範圍
            # =========================================================
            # 如果是點兩下重置 (xaxis_range is None)，就用 data_pack 算出的全範圍
            final_xaxis_range = xaxis_range if (xaxis_range and xaxis_range[0]) else data_pack['default_range']

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
            
            def get_val(k): return hist[k][-1] if k in hist and hist[k] else 0
            
            scoreboard = create_scoreboard_html(
                last_price = last_price,
                change = last_price - PREV_CLOSE_PRICE,
                change_pct = ((last_price - PREV_CLOSE_PRICE)/PREV_CLOSE_PRICE)*100,
                open_price = hist['price'][0],
                high = get_val('Session_High'),
                low = get_val('Session_Low'),
                vol = get_val('Total_Vol'),
                vwap = get_val('Session_VWAP'),
                prev_close = PREV_CLOSE_PRICE,
                underlying_price = get_val('Underlying_Price')
            )

            return fig_price, fig_mom, scoreboard, current_latest_ts

        except PreventUpdate:
            raise
        except Exception as e:
            import traceback
            # 這裡建議保留 print，方便除錯
            # print(f"❌ Dash Error: {traceback.format_exc()}")
            return NO_DATA_FIGURE, NO_DATA_FIGURE, f"Error: {str(e)}", dash.no_update

    app.run(debug=False, port=port, host='0.0.0.0', use_reloader=False)