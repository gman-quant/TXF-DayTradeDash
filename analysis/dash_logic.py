# analysis/dash_logic.py

import numpy as np
import plotly.graph_objects as go
import bisect
from config.indicator_config import INDICATORS_SETUP, TYPE_OVERLAY, TYPE_OSCILLATOR
from config.ui_theme import UI_COLOR
from config.settings import TIMEFRAMES

# =============================================================================
# 🛠️ 輔助函數：空白圖表
# =============================================================================
def create_blank_figure():
    """
    生成一個預設為黑色背景的空白 Plotly Figure。
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

# =============================================================================
# 🛠️ 輔助函數：從字典裡頭取最新值
# =============================================================================
def get_last_value(history_dict, key, default=0):
    """
    安全地從歷史數據字典中取得最新一筆值。
    如果 key 不存在或列表為空，回傳 default。
    """
    if key in history_dict and len(history_dict[key]) > 0:
        # 這裡支援 List 或 NumPy Array
        return history_dict[key][-1]
    return default

# =============================================================================
# 🧠 核心邏輯：數據處理 (Data Processing - Full NumPy Version)
# =============================================================================
def process_market_data(indicator_manager, lookback_count, timeframe):
    """
    處理原始數據：解環 (Unroll)、切片 (Slicing)、降頻 (Downsampling)。
    
    Returns:
        dict: 包含繪圖所需的所有數據陣列與狀態參數。
    """
    
    # 🔥 步驟 1: 從 RingBuffer 取得「線性化」的時間軸
    # 因為 RingBuffer 頭尾相接，直接 slice 會出錯，必須先解開
    linear_timestamps = indicator_manager.get_linear_snapshot("timestamp")
    
    raw_len = len(linear_timestamps)
    if raw_len == 0: return None

    # --- 1. 決定顯示範圍 (Scope) ---
    tf_key = timeframe if timeframe in indicator_manager.candles else '10s'
    period_ms = TIMEFRAMES.get(tf_key, 10000)
    
    lookback = int(lookback_count) if lookback_count else 25000
    if lookback > raw_len: lookback = raw_len
    
    start_idx = max(0, raw_len - lookback)
    
    # 這裡的 start_ts 用於 K 線定位，取的是線性陣列的值
    start_ts = linear_timestamps[start_idx] 
    
    # --- 2. 智慧降頻邏輯 (Downsampling) ---
    TARGET_POINTS = 1000
    step = 1
    if lookback > TARGET_POINTS: 
        step = lookback // TARGET_POINTS
    
    # --- 3. 準備 Tick 數據 (指標用) ---
    # 🔥 優化：直接對 NumPy Array 切片 (View)，速度極快
    timestamps_slice = linear_timestamps[start_idx::step]
    
    # 🔥 優化：完全移除 Pandas，使用 NumPy 向量化運算處理時間
    # int64 -> datetime64[ms] -> + 8hr (UTC+8)
    # 注意：RingBuffer 裡是 int64，可以直接轉型
    tick_x_axis = timestamps_slice.astype('datetime64[ms]') + np.timedelta64(8, 'h')
    
    # --- 4. 準備 K 線數據 ---
    candles = indicator_manager.candles[tf_key]
    current_candle = indicator_manager.current_candles[tf_key]
    
    # 二分搜尋定位 K 線
    temp_idx = bisect.bisect_left(candles['time'], start_ts)
    candle_start_idx = max(0, temp_idx - 1)
    
    # 建立 K 線數據字典 (轉為 List 以便 append)
    plot_candles = {
        'time': candles['time'][candle_start_idx:], # 這裡是 List
        'open': candles['open'][candle_start_idx:],
        'high': candles['high'][candle_start_idx:],
        'low': candles['low'][candle_start_idx:],
        'close': candles['close'][candle_start_idx:]
    }
    
    # 手動合併當前 K 線
    if current_candle and current_candle.get('time'):
        for k in plot_candles:
            plot_candles[k].append(current_candle[k])

    # 🔥 優化：K 線時間軸向量化運算
    # 1. 轉為 NumPy Array (int64)
    # 2. 加上 period_ms (平移到 Close Time)
    # 3. 轉為 datetime64[ms] 並加上時區
    raw_candle_time = np.array(plot_candles['time'], dtype=np.int64)
    candle_x = (raw_candle_time + period_ms).astype('datetime64[ms]') + np.timedelta64(8, 'h')

    # --- 5. 準備所有指標的線性數據 (View History) ---
    # 🔥 關鍵：我們不能直接傳 indicator_manager.history 給繪圖函數
    # 因為裡面的數據是環狀的。我們需要建立一個線性的 View。
    view_history = {}
    
    # 遍歷所有指標 (包含價格等)，全部解環
    # 這一步雖然有 copy，但對於 Dash 1秒一次的頻率來說，開銷可忽略
    for key in indicator_manager.history:
        view_history[key] = indicator_manager.get_linear_snapshot(key)

    # --- 6. 計算預設縮放範圍 (Zoom Sync) ---
    if len(tick_x_axis) > 0:
        last_visible_ts = timestamps_slice[-1]
        current_candle_end_ts = (last_visible_ts // period_ms) * period_ms + period_ms
        x_max_ts = current_candle_end_ts + period_ms // 2
        
        # 🔥 優化：直接取 NumPy Array 的第一個元素 (最小值)
        x_min = tick_x_axis[0]
        # 🔥 優化：純量運算去 Pandas
        x_max = np.datetime64(int(x_max_ts), 'ms') + np.timedelta64(8, 'h')
        
        default_range = [x_min, x_max]
    else:
        default_range = None

    return {
        'tick_x': tick_x_axis,
        'candle_x': candle_x,
        'candles': plot_candles,
        'start_idx': start_idx,
        'step': step,
        'history': view_history, # 🔥 傳入線性化的數據
        'raw_len': raw_len,
        'default_range': default_range,
        'timeframe': tf_key
    }

# =============================================================================
# 📈 繪圖邏輯：主圖 (Price & Overlays)
# =============================================================================
def build_price_figure(data, xaxis_range, yaxis_range):
    fig = create_blank_figure()

    current_tf = data.get('timeframe', '1m')
    is_high_freq = 's' in current_tf
    
    if is_high_freq:
        fig.add_trace(go.Ohlc(
            x=data['candle_x'],
            open=data['candles']['open'], high=data['candles']['high'],
            low=data['candles']['low'], close=data['candles']['close'],
            name=f'{current_tf} OHLC',
            increasing_line_color=UI_COLOR['TEXT_MAIN'], 
            decreasing_line_color=UI_COLOR['TEXT_MAIN'], 
            increasing_line_width=1, decreasing_line_width=1
        ))
    else:
        fig.add_trace(go.Candlestick(
            x=data['candle_x'],
            open=data['candles']['open'], high=data['candles']['high'],
            low=data['candles']['low'], close=data['candles']['close'],
            name=f'{current_tf} Candlestick',
            increasing_line_color=UI_COLOR['UP'], 
            decreasing_line_color=UI_COLOR['DOWN'],
            increasing_fillcolor=UI_COLOR['UP'],
            decreasing_fillcolor=UI_COLOR['DOWN']
        ))
    
    # 2. 動態繪製 Overlays
    for ind in INDICATORS_SETUP:
        # 注意：這裡的 data['history'] 已經是線性化的 NumPy Array 了
        if ind.get('type') == TYPE_OVERLAY and ind['id'] in data['history']:
            y_data = data['history'][ind['id']][data['start_idx']::data['step']]
            
            fig.add_trace(go.Scattergl(
                x=data['tick_x'], y=y_data, 
                mode='lines', name=ind['id'],
                line=dict(color=ind['color'], width=1, dash=ind.get('style', 'solid'))
            ))

    # 3. Layout
    fig.update_layout(
        template='plotly_dark',
        margin=dict(l=40, r=40, t=10, b=10),
        uirevision='constant',
        hovermode='x unified',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        
        xaxis=dict(visible=True, showgrid=True, rangeslider=dict(visible=False), range=xaxis_range),
        yaxis=dict(visible=True, showgrid=True, gridcolor='#333', 
                   tickformat=',.0f', side='right',
                   range=yaxis_range if yaxis_range else None)
    )
    return fig

# =============================================================================
# 📊 繪圖邏輯：副圖 (Oscillators)
# =============================================================================
def build_momentum_figure(data, xaxis_range):
    fig = create_blank_figure()

    valid_indicators = [
        ind for ind in INDICATORS_SETUP 
        if ind.get('type') == TYPE_OSCILLATOR and ind['id'] in data['history']
    ]

    for ind in valid_indicators:
        # =========================================================
        # Layer 1: CVD 背景填充 (修正顏色與邏輯)
        # =========================================================
        if ind['id'] == 'Session_CVD':
            # 這裡 y_data 已經是 NumPy Array (View)
            y_data = data['history'][ind['id']][data['start_idx']::data['step']]
            target_yaxis = ind.get('yaxis', 'y2')

            group_name = "cvd_group"

            # 1. 主線 (金色)
            fig.add_trace(go.Scattergl(
                x=data['tick_x'], y=y_data, 
                mode='lines', name=ind['id'],
                line=dict(color=ind['color'], width=0.5), 
                yaxis=target_yaxis,
                legendgroup=group_name,
                showlegend=True,
            ))
            
            # 2. 計算正負值 (NumPy 向量化運算)
            y_pos = np.maximum(0, y_data)
            y_neg = np.minimum(0, y_data)

            # 🔥 視覺修正：改用淡金色背景，避免與 Delta 紅綠柱混淆
            common_fill = dict(
                mode='lines',
                line=dict(width=0),
                fill='tozeroy',
                fillcolor='rgba(255, 215, 0, 0.08)', # 淡金色 (Gold)
                hoverinfo='skip',
                yaxis=target_yaxis,
                legendgroup=group_name,
                showlegend=False
            )

            # 3. 繪製填充
            fig.add_trace(go.Scattergl(x=data['tick_x'], y=y_pos, **common_fill))
            fig.add_trace(go.Scattergl(x=data['tick_x'], y=y_neg, **common_fill))

        # =========================================================
        # Layer 2: Delta 柱狀圖
        # =========================================================
        if ind['id'] == 'Delta_180':
            y_data = data['history'][ind['id']][data['start_idx']::data['step']]
            target_yaxis = ind.get('yaxis', 'y')
            
            # Vectorized color assignment (NumPy where)
            # 如果 y_data 是 array，這樣寫會比 list comprehension 快
            cols = np.where(y_data >= 0, UI_COLOR['UP'], UI_COLOR['DOWN'])
            
            fig.add_trace(go.Bar(
                x=data['tick_x'], y=y_data, 
                marker_color=cols, name=ind['id'], 
                marker_line_width=0, opacity=0.9, 
                yaxis=target_yaxis
            ))

    # =========================================================
    # Layout 設定 (確保 barmode='overlay')
    # =========================================================
    fig.update_layout(
        template='plotly_dark',
        margin=dict(l=40, r=40, t=10, b=10),
        uirevision='constant',
        barmode='overlay', # 🔥 關鍵：防止Bar與Area堆疊
        
        xaxis=dict(visible=True, showgrid=True, range=xaxis_range),
        
        yaxis=dict(
            visible=True, showgrid=True, gridcolor='#333',
            side='left'
        ),
        
        yaxis2=dict(
            visible=True, showgrid=False, 
            overlaying='y', side='right', tickformat=',.0f',
            zeroline=True, zerolinewidth=1, zerolinecolor='rgba(255,255,255,0.3)'
        ),
        
        hovermode='x unified', 
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        showlegend=True
    )
    return fig