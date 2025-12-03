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
        default_range = [tick_x_axis.min(), tick_x_axis.max()]
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

    for ind in INDICATORS_SETUP:
        if ind.get('type') == TYPE_OSCILLATOR and ind['id'] in data['history']:
            y_data = data['history'][ind['id']][data['start_idx']::data['step']]
            
            # 判斷是否使用右軸 (y2)
            target_yaxis = ind.get('yaxis', 'y') 

            # A. Delta (柱狀圖)：紅綠變色
            if ind.get('color') == 'dynamic':
                cols = [UI_COLOR['DOWN'] if v < 0 else UI_COLOR['UP'] for v in y_data]
                fig.add_trace(go.Bar(
                    x=data['tick_x'], y=y_data, 
                    marker_color=cols, 
                    name=ind['id'], 
                    marker_line_width=0,
                    opacity=0.8, 
                    yaxis=target_yaxis
                ))
            
            # B. CVD (累積線)：面積圖風格
            elif ind['id'] == 'Session_CVD': 
                # 使用 go.Scatter (SVG) 以確保 fill 效果正確 (WebGL fill 有時會破圖)
                fig.add_trace(go.Scatter(
                    x=data['tick_x'], y=y_data, 
                    mode='lines', 
                    name=ind['id'],
                    line=dict(color=UI_COLOR['HIGHLIGHT'], width=1.5), # 金色
                    fill='tozeroy',                        # 填滿至 0 軸
                    fillcolor='rgba(255, 215, 0, 0.1)',    # 淡金背景
                    yaxis=target_yaxis
                ))
                
            # C. 其他普通線圖
            else:
                fig.add_trace(go.Scattergl(
                    x=data['tick_x'], y=y_data, 
                    mode='lines', name=ind['id'],
                    line=dict(color=ind['color'], width=1.5),
                    yaxis=target_yaxis
                ))

    # Layout 設定 (雙軸)
    fig.update_layout(
        template='plotly_dark',
        margin=dict(l=40, r=40, t=10, b=10),
        uirevision='constant',
        
        # 強制應用主圖的 X 軸範圍 (同步)
        xaxis=dict(visible=True, showgrid=True, range=xaxis_range),
        
        # 左側 Y 軸 (Delta)
        yaxis=dict(
            visible=True, showgrid=True, gridcolor='#333',
            title=dict(text='Delta', font=dict(color=UI_COLOR['TEXT_MAIN'], size=16)),
            side='left'
        ),
        
        # 右側 Y 軸 (CVD)
        yaxis2=dict(
            visible=True, showgrid=False, 
            title=dict(text='CVD', font=dict(color=UI_COLOR['TEXT_MAIN'], size=16)),
            overlaying='y', # 疊加
            side='right', 
            tickformat=',.0f'
        ),
        
        hovermode='x unified', 
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        showlegend=True # 強制顯示圖例 (即使只有一條線)
    )
    return fig