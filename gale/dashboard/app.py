# analysis/dashboard_server.py

import time
import traceback
import dash
from dash import dcc, html, callback_context, no_update
from dash.dependencies import Input, Output, State
from dash.exceptions import PreventUpdate

# --- Local Modules ---
from gale.dashboard.layout import create_main_layout, create_scoreboard_html
from gale.dashboard.controller import (
    process_market_data, 
    build_combined_figure
)
from gale.dashboard.chart import create_blank_figure
from gale.dashboard.data_model import get_last_value
from config.txf_calendar import DAY_SESSION_START, NIGHT_SESSION_START
from config.ui_theme import UI_COLOR

# --- Configuration ---
try:
    from config.settings import PREV_CLOSE_PRICE
except ImportError:
    PREV_CLOSE_PRICE = 23000.0

# =============================================================================
# 🚀 Dashboard Server Entry Point
# =============================================================================

def start_dashboard_server(indicator_manager, port=8050, args=None):
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
    # [Dynamic Lookback] Pass max buffer capacity to layout for slider config
    app.layout = create_main_layout(max_capacity=indicator_manager.capacity)

    # [New] Store to track the last user-interacted shape index
    dcc.Store(id='active-shape-store', data=None),

    # =========================================================================
    # ⚡ Clientside Callback: Drawing Config & Shape Editing
    # =========================================================================
    app.clientside_callback(
        """
        function(width, color, relayoutData, currentActiveIndex) {
            const graph = document.getElementById('main-chart');
            if (!graph) return window.dash_clientside.no_update;
            
            const ctx = dash_clientside.callback_context;
            const triggered = ctx.triggered.map(t => t.prop_id);
            
            // 1. Determine if this call was triggered by a Shape Interaction (Drag/Resize)
            //    If so, we just want to update our 'active shape index' and do NOTHING else.
            if (triggered.some(t => t.includes('main-chart.relayoutData'))) {
                if (!relayoutData) return window.dash_clientside.no_update;
                
                // Parse keys like "shapes[2].x0" to extract index "2"
                let newIndex = null;
                for (const key in relayoutData) {
                    if (key.startsWith('shapes[')) {
                        const match = key.match(/shapes\[(\d+)\]/);
                        if (match) {
                            newIndex = parseInt(match[1]);
                            break; 
                        }
                    }
                }
                
                // If we found a shape index, update the store. 
                // Don't relayout graph (avoid infinite loop).
                if (newIndex !== null) {
                    return newIndex; 
                }
                return window.dash_clientside.no_update;
            }
            
            // 2. If triggered by Dropdowns (Width/Color), apply style to:
            //    A. Defaults for NEW shapes (newshape)
            //    B. The currently ACTIVE shape (if one exists)
            
            let update = {
                'newshape.line.width': width,
                'newshape.line.color': color
            };
            
            // If we have a valid active shape index, try to update it too
            // Note: We need to be careful. If the shape was deleted, this might error, but Plotly handles it gracefully usually.
            if (currentActiveIndex !== null && currentActiveIndex !== undefined) {
                // Construct dynamic keys for the specific shape
                update[`shapes[${currentActiveIndex}].line.width`] = width;
                update[`shapes[${currentActiveIndex}].line.color`] = color;
            }
            
            try {
                Plotly.relayout(graph, update);
            } catch (e) {
                console.error("Relayout Error:", e);
            }
            
            return window.dash_clientside.no_update;
        }
        """,
        Output('active-shape-store', 'data'),
        [Input('drawing-width-dropdown', 'value'),
         Input('drawing-color-dropdown', 'value'),
         Input('main-chart', 'relayoutData')],
        [State('active-shape-store', 'data')]
    )

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
         Input('chart-zoom-state', 'data'),
         Input('session-static-store', 'data'),
         Input('drawing-width-dropdown', 'value'),
         Input('drawing-color-dropdown', 'value')],
        [State('last-update-timestamp', 'data')]
    )
    def update_dashboard(n, lookback_count, timeframe, saved_zoom_range, session_static, line_width, line_color, last_ts_stored):
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
            # [Optimization] Static store update should trigger refresh regardless of timestamp
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
            
            # [Debug] Verify drawing config
            print(f"DEBUG: Applying Drawing Config -> Width: {line_width}, Color: {line_color}")

            fig.update_layout(
                uirevision='constant', # 告訴 Plotly 盡量保留 UI 狀態 (如 Legend 開關)
                xaxis=dict(
                    range=final_range  # 強制套用我們計算出的範圍
                ),
                # [Drawing Config] Apply user settings for new shapes
                newshape=dict(
                    line=dict(
                        color=line_color,
                        width=line_width
                    )
                )
            )

            # --- 4. Scoreboard Calculation (戰情板計算) ---
            hist = data_pack['history']
            
            # [Optimization] Use Cached Static Data (O(1)) instead of re-reading/re-calculating
            # This also fixes the 'Floating Open Price' bug when lookback changes
            if session_static and session_static.get('open') is not None:
                current_prev_close = session_static.get('prev_close', PREV_CLOSE_PRICE)
                open_p = session_static.get('open', 0)
                # Fallback if open is 0 (uninitialized)
                if open_p == 0 and len(hist['close']) > 0:
                    open_p = hist['close'][0]
            else:
                # Initial Fallback (Should rarely happen after first callback)
                current_prev_close = PREV_CLOSE_PRICE
                if len(hist['close']) > 0:
                    open_p = hist['close'][0]
                else:
                    open_p = current_prev_close

            if len(hist['close']) > 0:
                last_price = hist['close'][-1]
            else:
                last_price = current_prev_close
            
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
    # 🧠 Callback 2.5: Static Data Manager (靜態數據快取)
    # =========================================================================
    @app.callback(
        Output('session-static-store', 'data'),
        Input('interval-component', 'n_intervals'),
        State('session-static-store', 'data')
    )
    def update_static_data(n, current_data):
        """
        每秒檢查一次 Static Data，若尚未填入或為 0 則嘗試填入。
        一旦填入成功，只要 Session 不變，原則上不需更新 (除了換日 resets)。
        
        目前邏輯：
        1. 若 current_data 為空或 open=0 -> 嘗試讀取
        2. 若 indicator_manager 有重置 (Head rewinds) -> 可能需要重讀?
           目前簡化：每 N 秒強制 refresh 一次，或者就每秒 check 代價很低。
        """
        # 簡單策略：每秒都去 peek 一下。因為只是從 memory 讀兩個 float，開銷極低。
        # 這樣可以確保若盤中換日 (日盤轉夜盤)，UI 也能自動更新 PrevClose/Open。
        
        # 取得靜態數據 (Session Open, Prev Close)
        from gale.dashboard.data_model import get_session_static_data
        static_data = get_session_static_data(indicator_manager)
        
        # 若數據沒變，不需要觸發 Output update (避免連鎖反應)
        if current_data == static_data:
            raise PreventUpdate
            
        return static_data

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
        from datetime import datetime, timedelta
        
        try:
            # 1. Sanitize Data (Fix: Remove 'yaxisN' keys from rangeslider causing ValueError)
            # Plotly.js sometimes sends back rangeslider state with invalid keys like 'yaxis6'
            if 'layout' in fig_data:
                layout = fig_data['layout']
                # Iterate over all keys that might be x-axes (xaxis, xaxis2, xaxis3...)
                for key in layout:
                    if key.startswith('xaxis'):
                        axis = layout[key]
                        if isinstance(axis, dict) and 'rangeslider' in axis:
                            rs = axis['rangeslider']
                            if isinstance(rs, dict):
                                keys_to_remove = [k for k in rs.keys() if k.startswith('yaxis') and k != 'yaxis']
                                for k in keys_to_remove:
                                    del rs[k]

            # 2. 重建 Figure 物件
            fig = go.Figure(fig_data)
            
            # 3. 生成檔名 (Session Logic)
            now = datetime.now()
            ts_str = now.strftime('%Y-%m-%d %H:%M:%S') # [Fix] Define ts_str for HTML template
            
            # ┌──────────────────────────────────────────────────────────────────┐
            # │ 檔案命名邏輯 (Filename Logic) - 依據交易日 (Trade Date)              │
            # │ ---------------------------------------------------------------- │
            # │ 1. Day Session (08:45~13:45) -> T日 (e.g., "2023-10-01")         │
            # │ 2. Night Session (15:00~05:00) -> T+1日 (e.g., "2023-10-02-n")   │
            # │    (夜盤交易歸屬於「次一交易日」)                                     │
            # └──────────────────────────────────────────────────────────────────┘
            
            # [Mode A] History Replay
            if args and args.mode == 'history' and args.date:
                date_str = args.date
                suffix = '-n' if args.session == 'night' else ''

            # [Mode B] Live / Forward Test
            # Case 1: 凌晨 (00:00 ~ 08:45) -> 仍屬於夜盤 (Trade Date = Today)
            # 例如: 10/02 04:00 -> 屬於 10/02 的夜盤 (接續 10/01 15:00 開盤的場次)
            elif now.time() < DAY_SESSION_START:
                date_str = now.strftime('%Y-%m-%d')
                suffix = '-n'
                
            # Case 2: 下午/晚上 (15:00 ~ 23:59) -> 屬於「隔日」夜盤 (Trade Date = Tomorrow)
            # 例如: 10/01 20:00 -> 歸屬為 10/02 的夜盤
            elif now.time() >= NIGHT_SESSION_START:
                date_str = (now + timedelta(days=1)).strftime('%Y-%m-%d')
                suffix = '-n'
                
            # Case 3: 日盤 (08:45 ~ 13:45) -> 屬於今日日盤
            else:
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
                    config={
                        'scrollZoom': True, 
                        'displayModeBar': True, 
                        'responsive': True,
                        'modeBarButtonsToAdd': [
                            'drawline',
                            'drawcircle',
                            'drawrect',
                            'eraseshape'
                        ]
                    }, 
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
                    main_color = UI_COLOR['UP'] if change >= 0 else UI_COLOR['DOWN']
                    sign = '+' if change >= 0 else ''
                    
                    gap = open_p - prev_close
                    gap_color = UI_COLOR['UP'] if gap >= 0 else UI_COLOR['DOWN']
                    gap_sign = '+' if gap >= 0 else ''
                    
                    basis = price - u_price
                    basis_color = UI_COLOR['HIGHLIGHT']
                    basis_sign = '+' if basis >= 0 else ''
                    
                    chg_open = price - open_p
                    chg_open_color = UI_COLOR['UP'] if chg_open >= 0 else UI_COLOR['DOWN']
                    chg_open_sign = '+' if chg_open >= 0 else ''
                    
                    day_range = high - low

                    # --- HTML 模板 (Dark Context) ---
                    header_html = f"""
                    <div style="background-color: #1E1E1E; color: white; padding: 15px; border-radius: 10px; border: 1px solid {main_color}; margin-bottom: 20px; font-family: sans-serif; display: flex; justify-content: center; align-items: center;">
                        
                        <!-- [Left] Price Block -->
                        <div style="margin-right: 50px; text-align: center;">
                            <div style="font-size: 48px; font-weight: bold; color: {main_color}; line-height: 1;">{price:,.0f}</div>
                            <div style="font-size: 20px; color: {main_color}; margin-top: 8px;">{sign}{change:.0f} ({sign}{pct:.2f}%)</div>
                        </div>
                        
                        <!-- [Right] Full Info Grid (4 Columns) -->
                        <div style="display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 8px 40px; text-align: left; font-size: 14px; line-height: 1.6; color: #BBB;">
                            
                            <!-- Col 1: Range (波動邊界) -->
                            <div>
                                <div><span style="color:{UI_COLOR['TEXT_SUB']}; display:inline-block; width:85px; text-align:right; margin-right:10px;">High:</span><span style="color:{UI_COLOR['UP']}; font-weight:bold;">{high:,.0f}</span></div>
                                <div><span style="color:{UI_COLOR['TEXT_SUB']}; display:inline-block; width:85px; text-align:right; margin-right:10px;">Low:</span><span style="color:{UI_COLOR['DOWN']}; font-weight:bold;">{low:,.0f}</span></div>
                                <div><span style="color:{UI_COLOR['TEXT_SUB']}; display:inline-block; width:85px; text-align:right; margin-right:10px;">Range:</span><span style="color:{UI_COLOR['HIGHLIGHT']}; font-weight:bold;">{day_range:.0f}</span></div>
                            </div>

                            <!-- Col 2: Context (市場參照) -->
                            <div>
                                <div><span style="color:{UI_COLOR['TEXT_SUB']}; display:inline-block; width:85px; text-align:right; margin-right:10px;">PrevClose:</span><span style="color:{UI_COLOR['TEXT_SUB']};">{prev_close:,.0f}</span></div>
                                <div><span style="color:{UI_COLOR['TEXT_SUB']}; display:inline-block; width:85px; text-align:right; margin-right:10px;">Spot:</span><span style="color:{UI_COLOR['TEXT_MAIN']};">{u_price:,.0f}</span></div>
                                <div><span style="color:{UI_COLOR['TEXT_SUB']}; display:inline-block; width:85px; text-align:right; margin-right:10px;">Basis:</span><span style="color:{basis_color}; font-weight:bold;">{basis_sign}{basis:.2f}</span></div>
                            </div>

                            <!-- Col 3: Opening (開盤動態) -->
                            <div>
                                <div><span style="color:{UI_COLOR['TEXT_SUB']}; display:inline-block; width:85px; text-align:right; margin-right:10px;">Open:</span><span style="color:{UI_COLOR['TEXT_MAIN']};">{open_p:,.0f}</span></div>
                                <div><span style="color:{UI_COLOR['TEXT_SUB']}; display:inline-block; width:85px; text-align:right; margin-right:10px;">OpenGap:</span><span style="color:{gap_color};">{gap_sign}{gap:.0f}</span></div>
                                <div><span style="color:{UI_COLOR['TEXT_SUB']}; display:inline-block; width:85px; text-align:right; margin-right:10px;">OpenDelta:</span><span style="color:{chg_open_color};">{chg_open_sign}{chg_open:.0f}</span></div>
                            </div>

                            <!-- Col 4: Cost & Volume (量價結構) -->
                            <div>
                                <div><span style="color:{UI_COLOR['TEXT_SUB']}; display:inline-block; width:85px; text-align:right; margin-right:10px;">VWAP:</span><span style="color:{UI_COLOR['VWAP']}; font-weight:bold;">{vwap:,.0f}</span></div>
                                <div><span style="color:{UI_COLOR['TEXT_SUB']}; display:inline-block; width:85px; text-align:right; margin-right:10px;">Volume:</span><span style="color:{UI_COLOR['TEXT_MAIN']};">{vol:,.0f}</span></div>
                            </div>
                        </div>
                    </div>
                    """
                    
                return f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="utf-8">
                    <title>TXF {date_str}{suffix}</title>
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
                            height: 87vh !important; 
                        }}

                        /* [Fix] Modebar Position for Saved HTML */
                        .js-plotly-plot .plotly .modebar {{
                            top: -5px !important;
                            right: 0px !important;
                        }}
                    </style>
                </head>
                <body>
                    <h2>🇹🇼 TXF <small style='opacity: 0.6; font-weight: 300;'>SNAPSHOT</small> <span style='color: #444; margin: 0 10px; font-weight: 100;'>|</span> {date_str} {'🌙' if suffix else '☀️'}</h2>
                    {header_html}
                    {plot_html}
                </body>
                </html>
                """
                
            return dict(content=write_full_html(), filename=filename)

        except Exception as e:
            # 捕獲所有異常並輸出到控制台
            error_msg = traceback.format_exc()
            print(f"❌ Save HTML Error: {error_msg}")
            
            # 回傳錯誤日誌檔案給使用者，而不是無反應
            return dict(
                content=f"Error exporting HTML snapshot:\n\n{error_msg}",
                filename="snapshot_error.txt"
            )

    # 啟動 Flask Server
    app.run(debug=False, port=port, host='0.0.0.0', use_reloader=False)