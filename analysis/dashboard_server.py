# analysis/dashboard_server.py

import dash
from dash.dependencies import Input, Output, State
from dash.exceptions import PreventUpdate
import plotly.graph_objects as go

# 引入拆分後的模組
from analysis.dash_layout import create_main_layout, create_scoreboard_html
from analysis.dash_logic import process_market_data, build_price_figure, build_momentum_figure

try:
    from config.settings import PREV_CLOSE_PRICE
except ImportError:
    PREV_CLOSE_PRICE = 23000.0

def start_dashboard_server(indicator_manager, port=8050):
    app = dash.Dash(__name__)
    app.layout = create_main_layout()

    # --- Callback: Zoom ---
    @app.callback(
        [Output('price-xaxis-range', 'data'), Output('price-yaxis-range', 'data')],
        [Input('price-chart', 'relayoutData')]
    )
    def save_zoom_state(relayoutData):
        if relayoutData:
            if 'xaxis.autorange' in relayoutData or 'yaxis.autorange' in relayoutData:
                return None, None
            xaxis_range = relayoutData.get('xaxis.range[0]'), relayoutData.get('xaxis.range[1]')
            yaxis_range = relayoutData.get('yaxis.range[0]'), relayoutData.get('yaxis.range[1]')
            if xaxis_range != (None, None) and xaxis_range[0] is not None:
                 return xaxis_range, yaxis_range
        raise dash.exceptions.PreventUpdate

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
            # 1. 處理數據 (Logic Layer)
            data_pack = process_market_data(indicator_manager, lookback_count)
            
            if not data_pack:
                return go.Figure(), go.Figure(), "Waiting...", dash.no_update
            
            # 2. PreventUpdate 檢查
            current_latest_ts = data_pack['history']['timestamp'][-1]
            if n > 0 and current_latest_ts == last_ts_stored:
                raise PreventUpdate

            # 3. 生成圖表 (View/Logic Layer)
            fig_price = build_price_figure(data_pack, xaxis_range, yaxis_range)
            fig_mom = build_momentum_figure(data_pack, xaxis_range)
            
            # 4. 生成 Scoreboard (View Layer)
            # 從 history 提取最新數值
            hist = data_pack['history']
            last_price = hist['price'][-1]
            
            # Helper to get last value safely
            def get_val(k): return hist[k][-1] if k in hist and hist[k] else 0
            
            # 從 history 獲取 underlying price
            underlying_price = get_val('Underlying_Price')
            
            scoreboard = create_scoreboard_html(
                last_price = last_price,
                change = last_price - PREV_CLOSE_PRICE,
                change_pct = (last_price/PREV_CLOSE_PRICE - 1)*100,
                open_price = hist['price'][0],
                high = get_val('Session_High'),
                low = get_val('Session_Low'),
                vol = get_val('Total_Vol'),
                vwap = get_val('Session_VWAP'),
                prev_close = PREV_CLOSE_PRICE,
                underlying_price = underlying_price
            )

            return fig_price, fig_mom, scoreboard, current_latest_ts

        except PreventUpdate:
            raise
        except Exception as e:
            import traceback
            print(f"❌ Dash Error: {traceback.format_exc()}")
            return go.Figure(), go.Figure(), f"Error: {str(e)}", dash.no_update

    app.run(debug=False, port=port, host='0.0.0.0', use_reloader=False)