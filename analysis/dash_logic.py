# analysis/dash_logic.py

import pandas as pd
import plotly.graph_objects as go
import bisect
from config.indicator_config import INDICATORS_SETUP, TYPE_OVERLAY, TYPE_OSCILLATOR

def process_market_data(indicator_manager, lookback_count):
    """
    處理原始數據：切片、降頻、合併 K 線
    回傳: (prepared_data_dict, status_metrics)
    """
    history = indicator_manager.history
    candles = indicator_manager.candles
    current_candle = indicator_manager.current_candle
    
    raw_len = len(history['timestamp'])
    if raw_len == 0: return None, None

    # 1. 決定範圍
    lookback = int(lookback_count) if lookback_count else 5000
    start_idx = max(0, raw_len - lookback)
    start_ts = history['timestamp'][start_idx]
    
    # 2. 降頻邏輯
    step = 1
    if lookback > 3000: step = lookback // 3000
    
    # 3. 準備 Tick 數據
    timestamps = history['timestamp'][start_idx::step]
    tick_x_axis = pd.to_datetime(timestamps, unit='ms') + pd.Timedelta(hours=8)
    
    # 4. 準備 K 線數據 (Zero-Copy & Merge)
    candle_start_idx = bisect.bisect_left(candles['time'], start_ts)
    
    # 這裡只取需要的欄位做切片，避免全量複製
    plot_candles = {
        'time': candles['time'][candle_start_idx:],
        'open': candles['open'][candle_start_idx:],
        'high': candles['high'][candle_start_idx:],
        'low': candles['low'][candle_start_idx:],
        'close': candles['close'][candle_start_idx:]
    }
    
    if current_candle and current_candle.get('time'):
        for k in plot_candles:
            plot_candles[k].append(current_candle[k])
            
    candle_x = pd.to_datetime(plot_candles['time'], unit='ms') + pd.Timedelta(hours=8)

    return {
        'tick_x': tick_x_axis,
        'candle_x': candle_x,
        'candles': plot_candles,
        'start_idx': start_idx,
        'step': step,
        'history': history, # 傳遞引用
        'raw_len': raw_len
    }

def build_price_figure(data, xaxis_range, yaxis_range):
    """繪製主圖"""
    fig = go.Figure()
    
    # OHLC
    fig.add_trace(go.Ohlc(
        x=data['candle_x'],
        open=data['candles']['open'], high=data['candles']['high'],
        low=data['candles']['low'], close=data['candles']['close'],
        name='5s OHLC',
        increasing_line_color="#FFFFFF", decreasing_line_color='#FFFFFF',
        increasing_line_width=1, decreasing_line_width=1
    ))
    
    # Overlays
    for ind in INDICATORS_SETUP:
        if ind.get('type') == TYPE_OVERLAY and ind['id'] in data['history']:
            y_data = data['history'][ind['id']][data['start_idx']::data['step']]
            fig.add_trace(go.Scattergl(
                x=data['tick_x'], y=y_data, mode='lines', name=ind['id'],
                line=dict(color=ind['color'], width=1, dash=ind.get('style', 'solid'))
            ))

    fig.update_layout(
        template='plotly_dark',
        margin=dict(l=40, r=40, t=10, b=10),
        paper_bgcolor='#111111', plot_bgcolor='#111111',
        hovermode='x unified',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        xaxis=dict(showgrid=True, rangeslider=dict(visible=False), range=xaxis_range if xaxis_range and xaxis_range[0] else None),
        yaxis=dict(showgrid=True, gridcolor='#333', tickformat=',.0f', range=yaxis_range if yaxis_range and yaxis_range[0] else None)
    )
    return fig

def build_momentum_figure(data, xaxis_range):
    """繪製副圖"""
    fig = go.Figure()
    for ind in INDICATORS_SETUP:
        if ind.get('type') == TYPE_OSCILLATOR and ind['id'] in data['history']:
            y_data = data['history'][ind['id']][data['start_idx']::data['step']]
            
            if ind.get('color') == 'dynamic':
                cols = ['#FF4136' if v >= 0 else '#2ECC40' for v in y_data]
                fig.add_trace(go.Bar(
                    x=data['tick_x'], y=y_data, marker_color=cols, name=ind['id'], marker_line_width=0
                ))
            else:
                fig.add_trace(go.Scattergl(
                    x=data['tick_x'], y=y_data, mode='lines', name=ind['id'],
                    line=dict(color=ind['color'], width=1.5)
                ))

    fig.update_layout(
        template='plotly_dark',
        margin=dict(l=40, r=40, t=10, b=40),
        xaxis=dict(showgrid=True, range=xaxis_range if xaxis_range and xaxis_range[0] else None),
        yaxis=dict(showgrid=True, gridcolor='#333'),
        paper_bgcolor="#111111", plot_bgcolor="#111111",
        hovermode='x unified', 
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5)
    )
    return fig