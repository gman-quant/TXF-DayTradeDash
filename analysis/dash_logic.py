# analysis/dash_logic.py

import bisect
import numpy as np
import plotly.graph_objects as go

# --- Local Configuration ---
from config.indicator_config import INDICATORS_SETUP, TYPE_OVERLAY, TYPE_OSCILLATOR
from config.ui_theme import UI_COLOR
from config.settings import TIMEFRAMES

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
    
    Args:
        history_dict: 包含各類指標數據的字典 (Value 可能是 List 或 NumPy Array)
        key: 目標數據的鍵名
        default: 若取不到值時的回傳預設值
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
        indicator_manager: 負責管理 RingBuffer 的物件
        lookback_count: 回溯的 Tick 數量 (Slider 值)
        timeframe: 當前選擇的 K 線週期
    
    Returns:
        dict: 包含繪圖所需的所有數據陣列與狀態參數 (None 若無數據)
    """
    
    # ---------------------------------------------------------
    # 1. 數據解環 (Unrolling)
    # ---------------------------------------------------------
    # 從 RingBuffer 取得「線性化」的時間軸
    # 因為 RingBuffer 底層是頭尾相接的，直接 Slice 會導致順序錯誤，必須先轉為線性 View/Copy
    linear_timestamps = indicator_manager.get_linear_snapshot("timestamp")
    
    raw_len = len(linear_timestamps)
    if raw_len == 0:
        return None

    # ---------------------------------------------------------
    # 2. 決定顯示範圍 (Scope Calculation)
    # ---------------------------------------------------------
    tf_key = timeframe if timeframe in indicator_manager.candles else '10s'
    period_ms = TIMEFRAMES.get(tf_key, 10000)
    
    # 防呆：確保 lookback 不超過現有數據長度
    lookback = int(lookback_count) if lookback_count else 25000
    if lookback > raw_len:
        lookback = raw_len
    
    start_idx = max(0, raw_len - lookback)
    start_ts = linear_timestamps[start_idx]  # 用於 K 線定位
    
    # ---------------------------------------------------------
    # 3. 智慧降頻 (Smart Downsampling)
    # ---------------------------------------------------------
    # 為了前端效能，限制最大繪圖點數
    TARGET_POINTS = 3000
    step = 1
    if lookback > TARGET_POINTS: 
        step = lookback // TARGET_POINTS
    
    # ---------------------------------------------------------
    # 4. Tick 數據準備 (Vectorized Operations)
    # ---------------------------------------------------------
    # 🔥 優化 A: NumPy 切片 (View) - 速度極快，無記憶體複製
    timestamps_slice = linear_timestamps[start_idx::step]
    
    # 🔥 優化 B: 向量化時間轉換 - 取代 Pandas，大幅降低 CPU 負載
    # int64 -> datetime64[ms] -> +8hr (UTC+8)
    tick_x_axis = timestamps_slice.astype('datetime64[ms]') + np.timedelta64(8, 'h')
    
    # ---------------------------------------------------------
    # 5. K 線數據準備 (Candlestick Prep)
    # ---------------------------------------------------------
    candles = indicator_manager.candles[tf_key]
    current_candle = indicator_manager.current_candles[tf_key]
    
    # 使用二分搜尋快速定位 K 線起始點
    temp_idx = bisect.bisect_left(candles['time'], start_ts)
    candle_start_idx = max(0, temp_idx - 1)
    
    plot_candles = {
        'time': candles['time'][candle_start_idx:],
        'open': candles['open'][candle_start_idx:],
        'high': candles['high'][candle_start_idx:],
        'low':  candles['low'][candle_start_idx:],
        'close': candles['close'][candle_start_idx:]
    }
    
    # 合併當前尚未收盤的 K 線 (Real-time candle)
    if current_candle and current_candle.get('time'):
        for k in plot_candles:
            plot_candles[k].append(current_candle[k])

    # K 線時間軸向量化處理 (平移至 K 線結束時間)
    raw_candle_time = np.array(plot_candles['time'], dtype=np.int64)
    candle_x = (raw_candle_time + period_ms).astype('datetime64[ms]') + np.timedelta64(8, 'h')

    # ---------------------------------------------------------
    # 6. 指標數據解環 (Indicator View)
    # ---------------------------------------------------------
    view_history = {}
    for key in indicator_manager.history:
        view_history[key] = indicator_manager.get_linear_snapshot(key)

    # ---------------------------------------------------------
    # 7. 計算預設縮放範圍 (Auto-Range Logic)
    # ---------------------------------------------------------
    # 計算包含最後一根 K 線及右側留白的範圍
    if len(tick_x_axis) > 0:
        last_visible_ts = timestamps_slice[-1]
        current_candle_end_ts = (last_visible_ts // period_ms) * period_ms + period_ms
        x_max_ts = current_candle_end_ts + period_ms // 2
        
        x_min = tick_x_axis[0]
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
        'history': view_history,
        'raw_len': raw_len,
        'default_range': default_range,
        'timeframe': tf_key
    }

# =============================================================================
# 📈 Visualization: Main Chart (主圖：價格與疊加指標)
# =============================================================================

def build_price_figure(data, xaxis_range, yaxis_range, uirevision='constant'):
    """
    建構主價格圖表 (Candlestick/OHLC + Overlays)。
    """
    fig = create_blank_figure()

    current_tf = data.get('timeframe', '1m')
    is_high_freq = 's' in current_tf
    
    # 1. 繪製 K 線 (根據週期自動切換 OHLC 或 Candlestick)
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
    
    # 2. 動態繪製疊加指標 (Overlays)
    for ind in INDICATORS_SETUP:
        ind_id = ind['id']
        if ind.get('type') == TYPE_OVERLAY and ind_id in data['history']:
            # 使用 step 進行降頻切片
            y_data = data['history'][ind_id][data['start_idx']::data['step']]
            
            fig.add_trace(go.Scattergl(
                x=data['tick_x'], y=y_data, 
                mode='lines', name=ind_id,
                line=dict(color=ind['color'], width=1, dash=ind.get('style', 'solid'))
            ))

    # 3. Layout 設定
    fig.update_layout(
        template='plotly_dark',
        margin=dict(l=40, r=40, t=10, b=10),
        
        # [全局鎖定]: 固定 Legend 狀態，避免重置時圖例被還原
        uirevision='Static_Layout_Key',
        
        hovermode='x unified',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        
        xaxis=dict(
            visible=True, showgrid=True, rangeslider=dict(visible=False), 
            range=xaxis_range,
            # [局部動態]: 只有 X 軸範圍會隨 Reset 事件強制更新
            uirevision=uirevision
        ),
        
        yaxis=dict(
            visible=True, showgrid=True, gridcolor='#333', 
            tickformat=',.0f', side='right',
            range=yaxis_range if yaxis_range else None
        )
    )
    return fig

# =============================================================================
# 📊 Visualization: Momentum Chart (副圖：震盪指標)
# =============================================================================

def build_momentum_figure(data, xaxis_range, uirevision='constant'):
    """
    建構副圖表 (CVD, Delta, Volatility 等震盪指標)。
    """
    fig = create_blank_figure()

    valid_indicators = [
        ind for ind in INDICATORS_SETUP 
        if ind.get('type') == TYPE_OSCILLATOR and ind['id'] in data['history']
    ]

    for ind in valid_indicators:
        ind_id = ind['id']
        y_data = data['history'][ind_id][data['start_idx']::data['step']]

        # -----------------------------------------------------
        # CVD (累積買賣量)
        # -----------------------------------------------------
        if ind_id == 'CVD':
            target_yaxis = ind.get('yaxis', 'y2')
            group_name = "cvd_group"

            # 主線 (Line)
            fig.add_trace(go.Scattergl(
                x=data['tick_x'], y=y_data, 
                mode='lines', name=ind_id,
                line=dict(color=ind['color'], width=1.0), 
                yaxis=target_yaxis,
                legendgroup=group_name,
                showlegend=True,
                legendrank=4
            ))
            
            # 背景填充 (Area Fill) - 使用向量化運算分離正負值
            y_pos = np.maximum(0, y_data)
            y_neg = np.minimum(0, y_data)
            
            common_fill = dict(
                mode='lines',
                line=dict(width=0),
                fill='tozeroy',
                fillcolor='rgba(255, 215, 0, 0.05)', # 淡金色
                hoverinfo='skip',
                yaxis=target_yaxis,
                legendgroup=group_name,
                showlegend=False
            )
            fig.add_trace(go.Scattergl(x=data['tick_x'], y=y_pos, **common_fill))
            fig.add_trace(go.Scattergl(x=data['tick_x'], y=y_neg, **common_fill))
        
        # 如有新增指標 (如 Trade_Imbalance)，可在此處擴充 elif 區塊

        # -----------------------------------------------------
        # 1. 🟢 螞蟻搬象 (Retail Flow): 散戶 (< 5口)
        # -----------------------------------------------------
        if ind_id == 'Retail_Flow':
            target_yaxis = ind.get('yaxis', 'y')
            
            # 使用 UI_COLOR (通常是 紅/綠)
            bar_colors = np.where(y_data >= 0, UI_COLOR['UP'], UI_COLOR['DOWN'])
            
            fig.add_trace(go.Bar(
                x=data['tick_x'], y=y_data, 
                name=f"{ind_id} (< 5)",
                marker_color=bar_colors, 
                marker_line_width=0, 
                opacity=1.0, 
                yaxis=target_yaxis,
                legendrank=1
            ))

        # -----------------------------------------------------
        # 2. 🟡 主力部隊 (Smart Money): 中實戶 (>= 5口)
        # -----------------------------------------------------
        elif ind_id == 'Smart_Money':
            target_yaxis = ind.get('yaxis', 'y')
            
            # 你原本設定的暗金色與深藍色
            cols = np.where(y_data >= 0, "#8C5B00", "#006D91")
            
            fig.add_hline(y=0, line_width=1, line_color="#555")

            fig.add_trace(go.Bar(
                x=data['tick_x'], y=y_data,
                name=f"{ind_id} (>= 5)",
                marker_color=cols,
                marker_line_width=0, 
                opacity=0.6, # 半透明，當背景
                yaxis=target_yaxis,
                legendrank=2
            ))

        # -----------------------------------------------------
        # 3. 🔴 巨鯨核彈 (Whale Nuke): 極端大單 (>= 20口)
        # -----------------------------------------------------
        elif ind_id == 'Whale_Nuke':
            target_yaxis = ind.get('yaxis', 'y')
            
            # 🔥 警示色：亮紅 (Buy) vs 亮青 (Sell)
            # 這種單出現是送分題，必須最顯眼
            cols = np.where(y_data >= 0, "#FF0000", "#00FFFF")

            fig.add_trace(go.Bar(
                x=data['tick_x'], y=y_data,
                name=f"{ind_id} (>= 20)",
                marker_color=cols,
                marker_line_width=0, 
                opacity=1.0, # 完全不透明，覆蓋在最上層
                yaxis=target_yaxis,
                legendrank=3
            ))


    # Layout 設定
    fig.update_layout(
        template='plotly_dark',
        margin=dict(l=40, r=40, t=10, b=10),
        
        # [全局鎖定]
        uirevision='Static_Layout_Key',
        barmode='overlay', # 防止 Bar 與 Area 互相堆疊擠壓
        
        xaxis=dict(
            visible=True, showgrid=True, 
            range=xaxis_range,
            # [局部動態]
            uirevision=uirevision
        ),
        
        # 左軸 (通常用於 Delta Bar)
        yaxis=dict(
            visible=True, showgrid=True, gridcolor='#333',
            side='left',
        ),
        
        # 右軸 (通常用於 CVD Line)
        yaxis2=dict(
            visible=True, showgrid=False, 
            overlaying='y', side='right', tickformat=',.0f',
            zeroline=True, zerolinewidth=1, zerolinecolor='rgba(255,255,255,0.3)',
        ),
        
        hovermode='x unified', 
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        showlegend=True
    )
    return fig