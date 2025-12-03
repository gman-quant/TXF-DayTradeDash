# analysis/dash_logic.py

import pandas as pd
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
    用途：在數據尚未載入或發生錯誤時回傳，防止 Dash 前端崩潰 (White Flash)。
    """
    return go.Figure(
        layout=go.Layout(
            paper_bgcolor=UI_COLOR['BG_MAIN'], # 使用主題中的背景色
            plot_bgcolor=UI_COLOR['BG_MAIN'],
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            margin=dict(l=0, r=0, t=0, b=0)
        )
    )

# =============================================================================
# 🧠 核心邏輯：數據處理 (Data Processing)
# =============================================================================
def process_market_data(indicator_manager, lookback_count, timeframe):
    """
    處理原始數據：切片 (Slicing)、降頻 (Downsampling)、K線對齊 (Alignment)。
    
    Returns:
        dict: 包含繪圖所需的所有數據陣列與狀態參數。
    """
    history = indicator_manager.history

    # 預設 fallback 到 '1m' 以防萬一
    tf_key = timeframe if timeframe in indicator_manager.candles else '10s'
    # ⬇️ 獲取當前週期的毫秒數 (例如 15m = 900,000 ms)
    period_ms = TIMEFRAMES.get(tf_key, 10000)
    candles = indicator_manager.candles[tf_key]
    current_candle = indicator_manager.current_candles[tf_key]
    
    raw_len = len(history['timestamp'])
    if raw_len == 0: return None

    # --- 1. 決定顯示範圍 (Scope) ---
    # 限制 lookback 不超過實際數據長度
    lookback = int(lookback_count) if lookback_count else 5000
    if lookback > raw_len: lookback = raw_len
    
    start_idx = max(0, raw_len - lookback)
    start_ts = history['timestamp'][start_idx] # 視窗起始時間戳
    
    # --- 2. 智慧降頻邏輯 (Downsampling) ---
    # 目標：限制最終輸出點數在 1000 點左右，確保瀏覽器渲染流暢
    TARGET_POINTS = 1000
    step = 1
    if lookback > TARGET_POINTS: 
        step = lookback // TARGET_POINTS
    
    # --- 3. 準備 Tick 數據 (指標用) ---
    # 應用 step 進行切片
    timestamps = history['timestamp'][start_idx::step]
    tick_x_axis = pd.to_datetime(timestamps, unit='ms') + pd.Timedelta(hours=8)
    
    # --- 4. 準備 K 線數據 (Zero-Copy & Merge) ---
    # 使用二分搜尋法快速定位 K 線起始點 (O(log N))
    temp_idx = bisect.bisect_left(candles['time'], start_ts)
    candle_start_idx = max(0, temp_idx - 1) # 強制往回退一格，確保包含起始時間所在的 K 線
    
    # 切片提取 (Slicing)
    plot_candles = {
        'time': candles['time'][candle_start_idx:],
        'open': candles['open'][candle_start_idx:],
        'high': candles['high'][candle_start_idx:],
        'low': candles['low'][candle_start_idx:],
        'close': candles['close'][candle_start_idx:]
    }
    
    # 手動合併當前正在形成的 K 線 (Current Candle)
    if current_candle and current_candle.get('time'):
        for k in plot_candles:
            plot_candles[k].append(current_candle[k])

    
    # =========================================================
    # ⬇️ 2. 關鍵修正：將 K 線時間軸平移 (Shift Right)
    # =========================================================
    # 原本是 plot_candles['time'] (Open Time)
    # 現在加上 period_ms，變成 Close Time
    shifted_time = [t + period_ms for t in plot_candles['time']]
    candle_x = pd.to_datetime(shifted_time, unit='ms') + pd.Timedelta(hours=8)

    # --- 5. 計算預設縮放範圍 (Zoom Sync) ---
    # 用於 "點兩下" 重置時，強制主副圖同步到此範圍
    if len(tick_x_axis) > 0:
        # 1. 找出視野內最後一筆 Tick 的時間
        last_visible_ts = timestamps[-1]
        
        # 2. 計算該 Tick 所屬 K 棒的「結束時間」
        # 公式：(Tick // Period) * Period + Period
        current_candle_end_ts = (last_visible_ts // period_ms) * period_ms + period_ms
        
        # 3. 轉換為與圖表一致的格式 (UTC+8)
        x_max_ts = current_candle_end_ts + period_ms // 2
        x_min = tick_x_axis.min()
        x_max = pd.to_datetime(x_max_ts, unit='ms') + pd.Timedelta(hours=8)
        
        default_range = [x_min, x_max]
    else:
        default_range = None

    return {
        'tick_x': tick_x_axis,
        'candle_x': candle_x,
        'candles': plot_candles,
        'start_idx': start_idx,
        'step': step,
        'history': history, # 傳遞引用
        'raw_len': raw_len,
        'default_range': default_range,
        'timeframe': tf_key
    }

# =============================================================================
# 📈 繪圖邏輯：主圖 (Price & Overlays)
# =============================================================================
def build_price_figure(data, xaxis_range, yaxis_range):
    """繪製主圖：K線 + 疊加指標 (VWAP/SMA)"""
    fig = create_blank_figure()

    # ⬇️ 判斷週期類型
    # 如果 timeframe 字串包含 's' (例如 '5s', '30s')，視為高頻 -> OHLC
    # 否則 (例如 '1m', '5m', '1H') -> Candlestick
    current_tf = data.get('timeframe', '1m')
    is_high_freq = 's' in current_tf
    
    if is_high_freq:
        # --- 模式 A: OHLC (單色/白色) ---
        # 適合秒級週期，線條乾淨，適合看細微結構
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
        # --- 模式 B: Candlestick (紅漲綠跌) ---
        # 適合分級週期，實體顏色能快速反映多空力道
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
    
    # 2. 動態繪製 Overlays (依據 Config)
    for ind in INDICATORS_SETUP:
        if ind.get('type') == TYPE_OVERLAY and ind['id'] in data['history']:
            # 應用降頻 step
            y_data = data['history'][ind['id']][data['start_idx']::data['step']]
            
            fig.add_trace(go.Scattergl(
                x=data['tick_x'], y=y_data, 
                mode='lines', name=ind['id'],
                line=dict(color=ind['color'], width=1, dash=ind.get('style', 'solid'))
            ))

    # 3. Layout 設定
    fig.update_layout(
        template='plotly_dark',
        margin=dict(l=40, r=40, t=10, b=10),
        uirevision='constant', # 保持使用者縮放狀態
        hovermode='x unified',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        
        # 應用傳入的 Range (實現雙向同步)
        xaxis=dict(visible=True, showgrid=True, rangeslider=dict(visible=False), range=xaxis_range),
        yaxis=dict(visible=True, showgrid=True, gridcolor='#333', 
                   tickformat=',.0f', side='right',
                   range=yaxis_range if yaxis_range and yaxis_range[0] else None)
    )
    return fig

# =============================================================================
# 📊 繪圖邏輯：副圖 (Oscillators)
# =============================================================================
def build_momentum_figure(data, xaxis_range):
    """繪製副圖：CVD (右軸) + Delta (左軸)"""
    fig = create_blank_figure()

    valid_indicators = [
        ind for ind in INDICATORS_SETUP 
        if ind.get('type') == TYPE_OSCILLATOR and ind['id'] in data['history']
    ]

    # =========================================================
    # Layer 1: CVD 背景填充 (紅綠分色)
    # =========================================================
    for ind in valid_indicators:
        if ind['id'] == 'Session_CVD':
            y_data = data['history'][ind['id']][data['start_idx']::data['step']]
            target_yaxis = ind.get('yaxis', 'y')
            
            # 1. 製作正值數據 (小於0的部分設為0)
            y_pos = [max(0, v) for v in y_data]
            # 2. 製作負值數據 (大於0的部分設為0)
            y_neg = [min(0, v) for v in y_data]

            # 🟢 正值填充 (綠色)
            fig.add_trace(go.Scatter(
                x=data['tick_x'], y=y_pos,
                mode='lines',
                line=dict(width=0), # 不顯示邊線，只顯示填充
                fill='tozeroy',
                fillcolor='rgba(46, 204, 64, 0.15)', # UI_COLOR['UP'] with opacity
                hoverinfo='skip', # 滑鼠經過不顯示資訊 (避免干擾)
                yaxis=target_yaxis,
                showlegend=False
            ))

            # 🔴 負值填充 (紅色)
            fig.add_trace(go.Scatter(
                x=data['tick_x'], y=y_neg,
                mode='lines',
                line=dict(width=0),
                fill='tozeroy',
                fillcolor='rgba(255, 65, 54, 0.15)', # UI_COLOR['DOWN'] with opacity
                hoverinfo='skip',
                yaxis=target_yaxis,
                showlegend=False
            ))

    # =========================================================
    # Layer 2: Delta 柱狀圖 (保持不變)
    # =========================================================
    for ind in valid_indicators:
        if ind.get('color') == 'dynamic':
            y_data = data['history'][ind['id']][data['start_idx']::data['step']]
            target_yaxis = ind.get('yaxis', 'y')
            cols = [UI_COLOR['UP'] if v >= 0 else UI_COLOR['DOWN'] for v in y_data] # 綠漲紅跌
            
            fig.add_trace(go.Bar(
                x=data['tick_x'], y=y_data, 
                marker_color=cols, name=ind['id'], 
                marker_line_width=0, opacity=0.9, 
                yaxis=target_yaxis
            ))

    # =========================================================
    # Layer 3: CVD 主線與其他指標 (最上層)
    # =========================================================
    for ind in valid_indicators:
        # 跳過已經畫過的動態柱狀圖
        if ind.get('color') == 'dynamic': continue

        y_data = data['history'][ind['id']][data['start_idx']::data['step']]
        target_yaxis = ind.get('yaxis', 'y')

        if ind['id'] == 'Session_CVD': 
            # 🟡 CVD 金色主線 (不填充，因為 Layer 1 已經填了)
            fig.add_trace(go.Scatter(
                x=data['tick_x'], y=y_data, 
                mode='lines', name=ind['id'],
                line=dict(color=UI_COLOR['HIGHLIGHT'], width=0.5), # 金色實線
                yaxis=target_yaxis
            ))
        else:
            # 其他普通線圖
            fig.add_trace(go.Scattergl(
                x=data['tick_x'], y=y_data, mode='lines', name=ind['id'],
                line=dict(color=ind['color'], width=1.5),
                yaxis=target_yaxis
            ))

    # =========================================================
    # Layout 設定
    # =========================================================
    fig.update_layout(
        template='plotly_dark',
        margin=dict(l=40, r=40, t=10, b=10),
        uirevision='constant',
        
        xaxis=dict(visible=True, showgrid=True, range=xaxis_range),
        
        # 左軸 (Delta)
        yaxis=dict(
            visible=True, showgrid=True, gridcolor='#333',
            #title=dict(text='Delta', font=dict(color=UI_COLOR['TEXT_MAIN'], size=10)),
            side='left'
        ),
        
        # 右軸 (CVD)
        yaxis2=dict(
            visible=True, showgrid=False, 
            #title=dict(text='CVD', font=dict(color=UI_COLOR['TEXT_MAIN'], size=10)),
            overlaying='y', side='right', tickformat=',.0f',
            # ⬇️ 關鍵：加上一條白色的 0 軸線，區隔多空
            zeroline=True, zerolinewidth=1, zerolinecolor='rgba(255,255,255,0.3)'
        ),
        
        hovermode='x unified', 
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        showlegend=True
    )
    return fig