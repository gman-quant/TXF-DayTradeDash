# analysis/dash_logic.py

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- 本地配置 ---
from config.indicator_config import INDICATORS_SETUP, TYPE_OVERLAY, TYPE_OSCILLATOR
from config.ui_theme import UI_COLOR

# --- 模組引入 ---
from analysis.data_processor import process_market_data, get_last_value
from analysis.chart_utils import create_blank_figure

# =============================================================================
# 📈 視覺化核心 (Visualization Core)
# =============================================================================

def build_combined_figure(data):
    """
    建立主副圖合併的 Plotly Figure 物件。
    
    佈局規劃 (2x2 Grid):
    [ Row 1 ] 左: K線主圖 (90%) | 右: 籌碼分佈 (10%)
    [ Row 2 ] 左: 動能副圖 (90%) | 右: 空白 (None)
    
    Args:
        data: 由 data_processor.process_market_data 返回的數據字典
    """
    # 1. 建立子圖框架
    fig = make_subplots(
        rows=2, cols=2, 
        shared_xaxes=True, # 上下兩圖共用 X 軸 (時間)
        shared_yaxes=True, # 左右兩圖共用 Y 軸 (價格)
        vertical_spacing=0.05,   # 上下間距
        horizontal_spacing=0.05, # 左右間距
        row_heights=[0.7, 0.3],     # 第一列佔 70% 高度
        column_widths=[0.90, 0.10], # 主圖佔 90% 寬度
        specs=[
            [{"secondary_y": False}, {"secondary_y": False}], 
            [{"secondary_y": True,  "colspan": 1}, None] # 副圖不需要右邊那塊，故 None
        ] 
    )

    # ---------------------------------------------------------
    # 第一列 (Row 1): 價格主圖 (Price Chart)
    # ---------------------------------------------------------
    current_tf = data.get('timeframe', '1m')
    is_high_freq = 's' in current_tf
    
    # A. 繪製 K 線 (根據週期選擇 OHLC 或 Candlestick)
    if is_high_freq:
        # 秒級圖使用 OHLC 線條模式 (效能較佳)
        fig.add_trace(go.Ohlc(
            x=data['candle_x'],
            open=data['candles']['open'], high=data['candles']['high'],
            low=data['candles']['low'], close=data['candles']['close'],
            name=f'{current_tf} OHLC',
            increasing_line_color=UI_COLOR['TEXT_MAIN'], decreasing_line_color=UI_COLOR['TEXT_MAIN'],
            increasing_line_width=1, decreasing_line_width=1
        ), row=1, col=1)
    else:
        # 分K使用標準蠟燭圖
        fig.add_trace(go.Candlestick(
            x=data['candle_x'],
            open=data['candles']['open'], high=data['candles']['high'],
            low=data['candles']['low'], close=data['candles']['close'],
            name=f'{current_tf} Candle',
            increasing_line_color=UI_COLOR['UP'], decreasing_line_color=UI_COLOR['DOWN'],
            increasing_fillcolor=UI_COLOR['UP'], decreasing_fillcolor=UI_COLOR['DOWN']
        ), row=1, col=1)

    # B. 疊加主圖指標 (Overlays: SMA, EMA, VWAP, Bollinger...)
    # 定義預設只顯示圖例但不畫線的指標 (減輕視覺干擾)
    DEFAULT_OFF_LEGENDS = ['SMA_3min', 'SMA_60', 'SMA_20', 'Max_250', 'Min_250']

    for ind in INDICATORS_SETUP:
        if ind.get('type') == TYPE_OVERLAY and ind['id'] in data['history']:
            y_data = data['history'][ind['id']][data['start_idx']::data['step']]

            # 設定可見狀態 ('legendonly' 代表點擊圖例才顯示)
            if ind['id'] in DEFAULT_OFF_LEGENDS:
                vis_state = 'legendonly'
            else:
                vis_state = True 

            fig.add_trace(go.Scattergl(
                x=data['tick_x'], y=y_data, mode='lines', name=ind['id'],
                line=dict(color=ind['color'], width=1, dash=ind.get('style', 'solid')),
                visible=vis_state,
                connectgaps=True # 允許跨越 NaN 連線 (布林通道需要)
            ), row=1, col=1)

    # ---------------------------------------------------------
    # 第二列 (Row 2): 動能副圖 (Momentum / Oscillators)
    # ---------------------------------------------------------
    valid_indicators = [ind for ind in INDICATORS_SETUP if ind.get('type') == TYPE_OSCILLATOR and ind['id'] in data['history']]

    for ind in valid_indicators:
        ind_id = ind['id']
        y_data = data['history'][ind_id][data['start_idx']::data['step']]

        # Case A: CVD (累計成交量差) - 繪製於右軸 (Secondary Y)
        if ind_id == 'CVD':
            group_name = "cvd_group"
            
            # 主線
            fig.add_trace(go.Scattergl(
                x=data['tick_x'], y=y_data, mode='lines', name=ind_id,
                line=dict(color=ind['color'], width=1.0), 
                legendgroup=group_name, showlegend=True, legendrank=4
            ), row=2, col=1, secondary_y=True)
            
            # 背景色填充 (正負區分)
            y_pos = np.maximum(0, y_data)
            y_neg = np.minimum(0, y_data)
            common_fill = dict(mode='lines', line=dict(width=0), fill='tozeroy', fillcolor='rgba(255, 215, 0, 0.05)', hoverinfo='skip', legendgroup=group_name, showlegend=False)
            
            fig.add_trace(go.Scattergl(x=data['tick_x'], y=y_pos, **common_fill), row=2, col=1, secondary_y=True)
            fig.add_trace(go.Scattergl(x=data['tick_x'], y=y_neg, **common_fill), row=2, col=1, secondary_y=True)
        
        # Case B: 散戶流向 (Retail Flow) - 繪製於左軸
        elif ind_id == 'Retail_Flow':
            bar_colors = np.where(y_data >= 0, UI_COLOR['UP'], UI_COLOR['DOWN'])
            fig.add_trace(go.Bar(
                x=data['tick_x'], y=y_data, name=f"{ind_id} (< 5)",
                marker_color=bar_colors, marker_line_width=0, opacity=1.0, legendrank=1
            ), row=2, col=1, secondary_y=False)

        # Case C: 主力流向 (Smart Money) - 繪製於左軸
        elif ind_id == 'Smart_Money':
            cols = np.where(y_data >= 0, "#8C5B00", "#006D91") # 金色/藍綠色
            fig.add_hline(y=0, line_width=1, line_color="#555", row=2, col=1)
            fig.add_trace(go.Bar(
                x=data['tick_x'], y=y_data, name=f"{ind_id} (>= 5)",
                marker_color=cols, marker_line_width=0, opacity=0.6, legendrank=2
            ), row=2, col=1, secondary_y=False)

        # Case D: 大戶核彈 (Whale Nuke) - 繪製於左軸
        elif ind_id == 'Whale_Nuke':
            cols = np.where(y_data >= 0, "#FB00FF", "#00FFFF") # 紫色/青色
            fig.add_trace(go.Bar(
                x=data['tick_x'], y=y_data, name=f"{ind_id} (>= 15)",
                marker_color=cols, marker_line_width=0, opacity=1.0, legendrank=3
            ), row=2, col=1, secondary_y=False)

        # Case E: 通用指標 (Z-Score, RSI 等) - 預設繪製於右軸
        else:
            fig.add_trace(go.Scattergl(
                x=data['tick_x'], y=y_data, mode='lines', name=ind_id,
                line=dict(color=ind['color'], width=1.0),
                legendrank=5
            ), row=2, col=1, secondary_y=True)

    # ---------------------------------------------------------
    # 第一列右側 (Row 1, Col 2): 籌碼分佈 (Volume Profile)
    # ---------------------------------------------------------
    vp_data = data.get('vp_data')
    vp_stats = data.get('vp_stats')
    
    if vp_data:
        colors = []
        opacities = []
        
        poc = vp_stats['poc']
        vah = vp_stats['vah']
        val = vp_stats['val']
        
        # 根據價格區域塗色 (POC=黃, VA=藍, 其他=灰)
        for p in vp_data['price']:
            if p == poc:
                colors.append('#FFFF00') 
                opacities.append(1.0)
            elif val <= p <= vah:
                colors.append('rgba(0, 100, 255, 0.5)')
                opacities.append(0.6)
            else:
                colors.append('rgba(100, 100, 100, 0.3)')
                opacities.append(0.3)
                
        fig.add_trace(go.Bar(
            x=vp_data['volume'], 
            y=vp_data['price'],
            orientation='h', # 水平長條圖
            marker_color=colors,
            marker_line_width=0,
            showlegend=False,
            hoverinfo='y+x',
            name='Volume Profile'
        ), row=1, col=2)
        

    # =========================================================
    # 🎨 全局版面設定 (Global Layout Configuration)
    # =========================================================
    
    initial_range = data.get('default_range')

    fig.update_layout(
        # --- 1. 基礎外觀 ---
        template='plotly_dark',
        margin=dict(l=5, r=5, t=5, b=5), # 極窄邊距
        paper_bgcolor=UI_COLOR['BG_MAIN'],
        plot_bgcolor=UI_COLOR['BG_MAIN'],
        
        # --- 2. 交互行為 ---
        uirevision='constant',  # 鎖定狀態：防止數據更新時重置縮放
        hovermode='x',          # 懸停模式
        
        # --- 3. 圖例設定 ---
        legend=dict(
            orientation="h", 
            yanchor="bottom", y=1.02, 
            xanchor="center", x=0.5
        ),

        # --- 4. 條狀圖設定 ---
        barmode='overlay',      # 允許 Bar 重疊顯示
        
        # --- 5. 初始化範圍 ---
        xaxis=dict(
            rangeslider=dict(visible=False),
            range=initial_range if initial_range else None
        ),
    )
    
    # =========================================================
    # 🔧 軸線安全設定 (Axis Safety Config)
    # =========================================================

    # 1. 強制計算 Y 軸範圍 (解決 Plotly Auto-Scale 失效問題)
    y_min, y_max = None, None
    candle_src = data['candles']
    
    if len(candle_src['low']) > 0:
        y_min = min(candle_src['low'])
        y_max = max(candle_src['high'])
            
    # 添加 10% 上下緩衝
    if y_min is not None and y_max is not None:
        spread = y_max - y_min
        if spread == 0: spread = 100
        pad = spread * 0.1 
        forced_range = [y_min - pad, y_max + pad]
    else:
        forced_range = None

    # 配置 Row 1 Y 軸 (主圖價格 - 右側顯示)
    fig.update_yaxes(
        side='right', 
        showgrid=True, gridcolor='#333', tickformat=',.0f', 
        row=1, col=1,
        range=forced_range,
        autorange=False if forced_range else True
    )
    
    # 配置 Row 1 Col 2 X 軸 (籌碼分佈 - 隱藏刻度)
    fig.update_xaxes(
        title='Vol', showgrid=False, showticklabels=False, row=1, col=2 
    ) 
    
    # 配置 Row 2 Y 軸 (副圖指標)
    # 左軸 (Left): 柱狀圖 (Flow)
    fig.update_yaxes(
        side='left', 
        showgrid=True, gridcolor='#333',
        row=2, col=1, secondary_y=False
    )
    
    # 右軸 (Right): 線圖 (CVD/Oscillator)
    fig.update_yaxes(
        side='right', 
        showgrid=False, 
        zeroline=True, zerolinewidth=1, zerolinecolor='rgba(255,255,255,0.3)',
        row=2, col=1, secondary_y=True
    )

    # =========================================================
    # 📏 樣式與十字準星 (Styling & Crosshair)
    # =========================================================
    
    fig.update_xaxes(
        showgrid=True,
        showticklabels=True,
        matches='x', # 同步上下 X 軸
    
        # 十字準星
        showspikes=True,
        spikemode='across',
        spikethickness=0.5,
        spikedash='dash',
        spikecolor=UI_COLOR['TEXT_MAIN'], 
    )
    
    return fig

