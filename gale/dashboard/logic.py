# analysis/dash_logic.py

import bisect
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- Local Configuration ---
from config.indicator_config import INDICATORS_SETUP, TYPE_OVERLAY, TYPE_OSCILLATOR
from config.ui_theme import UI_COLOR
from config.settings import TIMEFRAMES

VP_LEGEND_GROUP = "Volume_Profile"
VP_BIN_SIZE = 1

# =============================================================================
# 🛠️ Helper Functions (輔助工具)
# =============================================================================

def create_blank_figure() -> go.Figure:
    """
    生成一個初始為黑色背景的空白 Plotly Figure。
    用於初始化或無數據時的佔位顯示。
    """
    return go.Figure(
        layout=go.Layout(
            paper_bgcolor=UI_COLOR['BG_MAIN'],
            plot_bgcolor=UI_COLOR['BG_MAIN'],
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            margin=dict(l=0, r=0, t=0, b=0)
        )
    )

def get_last_value(history_dict: dict, key: str, default=0):
    """
    安全地從歷史數據字典中取得最新一筆值。
    """
    if key in history_dict and len(history_dict[key]) > 0:
        return history_dict[key][-1]
    return default

# =============================================================================
# 🧠 Core Logic: Data Processing (核心數據處理)
# =============================================================================

def process_market_data(indicator_manager, lookback_count, timeframe):
    """
    處理原始數據：執行解環 (Unroll)、切片 (Slicing)、降頻 (Downsampling) 與向量化運算。
    
    Args:
        indicator_manager: RingBuffer 管理物件
        lookback_count: 回溯 Tick 數 (Slider)
        timeframe: K 線週期
    Returns:
        dict: 包含繪圖數據的字典 (None if no data)
    """
    
    # 1. 數據解環 (RingBuffer -> Linear Array)
    linear_timestamps = indicator_manager.get_linear_snapshot("timestamp")
    raw_len = len(linear_timestamps)
    
    if raw_len == 0:
        return None

    # 2. 決定顯示範圍 (Scope Calculation)
    tf_key = timeframe if timeframe in indicator_manager.candles else '10s'
    period_ms = TIMEFRAMES.get(tf_key, 10000)
    
    # 防呆：確保 lookback 合理
    lookback = int(lookback_count) if lookback_count else 50000
    if lookback > raw_len:
        lookback = raw_len
    
    start_idx = max(0, raw_len - lookback)
    start_ts = linear_timestamps[start_idx] 
    
    # 3. 智慧降頻 (Smart Downsampling)
    # 前端效能優化：限制最大繪圖點數
    TARGET_POINTS = 2000
    step = 1
    if lookback > TARGET_POINTS: 
        step = lookback // TARGET_POINTS
    
    # 4. Tick 數據準備 (Vectorized)
    timestamps_slice = linear_timestamps[start_idx::step]
    # int64 -> datetime64[ms] -> +8hr (UTC+8)
    tick_x_axis = timestamps_slice.astype('datetime64[ms]') + np.timedelta64(8, 'h')
    
    # 5. K 線數據準備 (Candlesticks)
    candles = indicator_manager.candles[tf_key]
    current_candle = indicator_manager.current_candles[tf_key]
    
    # 二分搜尋定位
    temp_idx = bisect.bisect_left(candles['time'], start_ts)
    candle_start_idx = max(0, temp_idx - 1)
    
    plot_candles = {
        'time': candles['time'][candle_start_idx:],
        'open': candles['open'][candle_start_idx:],
        'high': candles['high'][candle_start_idx:],
        'low':  candles['low'][candle_start_idx:],
        'close': candles['close'][candle_start_idx:]
    }
    
    # 合併即時 K 線
    if current_candle and current_candle.get('time'):
        for k in plot_candles:
            plot_candles[k].append(current_candle[k])

    # K 線 X 軸計算 (平移至 K 線結束時間)
    raw_candle_time = np.array(plot_candles['time'], dtype=np.int64)
    candle_x = (raw_candle_time + period_ms).astype('datetime64[ms]') + np.timedelta64(8, 'h')

    # 6. 指標數據解環
    view_history = {}
    for key in indicator_manager.history:
        view_history[key] = indicator_manager.get_linear_snapshot(key)

    # [NEW] VWAP Bands Calculation (On-the-fly)
    # Since we don't have cum_pv_sq in RingBuffer, we calc from linear history.
    if 'close' in view_history and 'volume' in view_history:
        import gale.alpha.engine as ne
        close_arr = view_history['close']
        vol_arr = view_history['volume']
        
        # Calculate +2.0 SD Bands
        vwap, upper, lower = ne.calc_vwap_bands_linear(close_arr, vol_arr, 2.0)
        
        # Inject into history so renderers can find it
        # Note: 'VWAP' might already exist from RingBuffer, 
        # but recalculating it ensures consistency with Bands.
        # Let's trust the Band calculation for the bands, but keep original VWAP.
        view_history['VWAP_Upper'] = upper
        view_history['VWAP_Lower'] = lower

    # 7. 計算預設縮放範圍 (Auto-Range)
    if len(tick_x_axis) > 0:
        last_visible_ts = timestamps_slice[-1]
        current_candle_end_ts = (last_visible_ts // period_ms) * period_ms + period_ms
        x_max_ts = current_candle_end_ts + period_ms // 2
        
        x_min = tick_x_axis[0]
        x_max = np.datetime64(int(x_max_ts), 'ms') + np.timedelta64(8, 'h')
        default_range = [x_min, x_max]
    else:
        default_range = None
        
    # 8. Extract Volume Profile Data (with Binning)
    # User requested 20-point aggregation for better visibility and performance
    vp_prices, vp_volumes = indicator_manager.vp_engine.get_distribution(bin_size=VP_BIN_SIZE)
    poc, vah, val = indicator_manager.vp_engine.calculate()

    return {
        'tick_x': tick_x_axis,
        'candle_x': candle_x,
        'candles': plot_candles,
        'start_idx': start_idx,
        'step': step,
        'history': view_history,
        'raw_len': raw_len,
        'default_range': default_range,
        'timeframe': tf_key,
        'vp_data': {
            'prices': vp_prices,
            'volumes': vp_volumes,
            'poc': poc,
            'vah': vah,
            'val': val
        }
    }

# =============================================================================
# 📈 Visualization: Combined Chart (主副圖合併)
# =============================================================================

def build_combined_figure(data):
    """
    繪製主副圖合併的 Subplot。
    Row 1: 價格 (Price) + Overlays
    Row 2: 動能 (Momentum) + Oscillators
    """
    # 1. 建立子圖框架
    fig = make_subplots(
        rows=2, cols=1, 
        shared_xaxes=True, 
        vertical_spacing=0.05,
        row_heights=[0.7, 0.3], 
        specs=[[{"secondary_y": False}], [{"secondary_y": True}]] 
    )

    # ---------------------------------------------------------
    # Row 1: 主圖 (Price)
    # ---------------------------------------------------------
    # =========================================================
    # 📉 Chart Renderers (Modularized)
    # =========================================================
    import gale.dashboard.renderers as renderers

    # 1. Main Chart (Row 1)
    renderers.add_main_price_chart(fig, data, row=1, col=1)

    # 2. Overlays (Row 1)
    # Default OFF (Legend Only)
    DEFAULT_OFF_LEGENDS = ['SMA_3min', 'SMA_60', 'Max_250', 'Min_250']
    
    # [NEW] VWAP Bands Rendering
    # Manually added since they are not in config.indicator_config
    if 'VWAP_Upper' in data['history']:
        # Upper Band
        renderers.add_overlay_indicator(fig, data, {
            'id': 'VWAP_Upper', 'color': '#008692', 'style': 'dash',
            'legendgroup': 'VWAP_Group'
        }, row=1, col=1)
        # Lower Band
        renderers.add_overlay_indicator(fig, data, {
            'id': 'VWAP_Lower', 'color': '#008692', 'style': 'dash',
            'legendgroup': 'VWAP_Group'
        }, row=1, col=1)

    for ind in INDICATORS_SETUP:
        if ind.get('type') == TYPE_OVERLAY and ind['id'] in data['history']:
            # Start count
            n_traces_before = len(fig.data)
            renderers.add_overlay_indicator(fig, data, ind, row=1, col=1)
            # The new trace is at -1
            if len(fig.data) > n_traces_before:
                if ind['id'] in DEFAULT_OFF_LEGENDS:
                    fig.data[-1].visible = 'legendonly'
                else:
                    fig.data[-1].visible = True

    # 3. Oscillators (Row 2)
    valid_indicators = [ind for ind in INDICATORS_SETUP if ind.get('type') == TYPE_OSCILLATOR and ind['id'] in data['history']]
    
    for ind in valid_indicators:
        ind_id = ind['id']
        y_data = data['history'][ind_id][data['start_idx']::data['step']]
        x_data = data['tick_x']
        
        # Dynamic Dispatch logic
        # e.g. render_cvd, render_retail_flow
        method_name = f"render_{ind_id.lower()}"
        renderer = getattr(renderers.OscillatorRenderers, method_name, None)
        
        if renderer:
            renderer(fig, x_data, y_data, ind, row=2, col=1)
        else:
            # Fallback (Simple Line) if no specific renderer found
            pass

    # 4. Volume Profile (Overlay on Row 1)
    vp = data.get('vp_data')
    if vp:
        # Prepare X range for lines
        x_range = None
        if len(data['tick_x']) > 0:
            x_range = (data['tick_x'][0], data['tick_x'][-1])
            
        renderers.add_volume_profile(fig, vp, VP_BIN_SIZE, VP_LEGEND_GROUP, x_range=x_range, row=1, col=1)

    # =========================================================
    # 🎨 Global Layout Configuration (全局版面設定)
    # =========================================================
    
    initial_range = data.get('default_range')

    fig.update_layout(
        # --- 1. 基礎外觀 (Appearance) ---
        template='plotly_dark',
        margin=dict(l=40, r=40, t=10, b=10),
        paper_bgcolor=UI_COLOR['BG_MAIN'],
        plot_bgcolor=UI_COLOR['BG_MAIN'],
        
        # --- 2. 交互行為 (Interaction) ---
        uirevision='constant',  # 鎖定狀態：防止數據更新時重置縮放
        hovermode='x',          # 懸停模式
        
        # --- 3. 圖例設定 (Legend) ---
        legend=dict(
            orientation="h", 
            yanchor="bottom", y=1.02, 
            xanchor="center", x=0.5
        ),

        # --- 4. 條狀圖設定 (Bar Mode) ---
        barmode='overlay',      # 關鍵：允許不同 Bar 重疊而非並排擠壓
        bargap=0,               # [Fix] Remove gap between bars

        # --- 5. Y 軸配置 (Y-Axes Configuration) ---
        # [Axis 1] 主圖價格 (右軸)
        yaxis=dict(
            side='right', 
            showgrid=True, 
            gridcolor='#333', 
            tickformat=',.0f'
        ), 
        
        # [Axis 2] 副圖動能柱狀圖 (左軸)
        yaxis2=dict(
            side='left', 
            showgrid=True, 
            gridcolor='#333'
        ),                    
        
        # [Axis 3] 副圖 CVD 線圖 (右軸，疊加於 Axis 2)
        yaxis3=dict(
            side='right', 
            showgrid=False, 
            tickformat=',.0f',
            zeroline=True, 
            zerolinewidth=1, 
            zerolinecolor='rgba(255,255,255,0.3)',
            overlaying='y2'     # 關鍵：共享副圖空間
        ),
        
        # --- 6. X 軸配置 (X-Axis Base) ---
        # 註：樣式統一由 update_xaxes 處理，此處僅設定範圍與滑桿
        xaxis=dict(
            rangeslider=dict(visible=False),
            range=initial_range if initial_range else None
        ),
        
        # [Axis 4] Volume Profile X-Axis (Overlay Top)
        xaxis3=dict(
            overlaying='x', # Overlay on main X axis
            side='top',     # Put labels on top (or hidden)
            showgrid=False,
            visible=False,  # Hide axis to reduce clutter
            matches=None    # Crucial: Do not sync with time axis
        ),
    )
    
    # =========================================================
    # 📏 Axis Styling & Crosshair (軸線樣式與十字準星)
    # =========================================================
    
    fig.update_xaxes(
        # 1. 網格與標籤
        showgrid=True,
        showticklabels=True,  # 強制主副圖皆顯示時間
        matches='x',          # 確保上下圖縮放同步
    
        # 2. 十字準星 (Spikes)
        showspikes=True,
        spikemode='across',       # 橫跨模式：貫穿整個繪圖區
        spikethickness=0.5,       # 線條粗細
        spikedash='dash',         # 線條樣式：虛線
        spikecolor=UI_COLOR['TEXT_MAIN'], 
    )
    
    # [CRITICAL FIX]
    # update_xaxes(matches='x') is aggressive and overwrites xaxis3.matches.
    # We must explicitly reset typical overlay axes to None AFTER the global call.
    fig.update_layout(xaxis3=dict(matches=None))
    
    return fig
