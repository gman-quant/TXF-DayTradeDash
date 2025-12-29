
"""
gale/dashboard/chart.py

負責將數據轉換為 Plotly Figure (View)。
只負責「畫圖」，不負責「怎麼算」。
"""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
import gale.dashboard.renderers as renderers

# Helper
from config.ui_theme import UI_COLOR
from config.indicator_config import INDICATORS_SETUP, TYPE_OVERLAY, TYPE_OSCILLATOR

VP_LEGEND_GROUP = "Volume_Profile"
VP_BIN_SIZE = 1

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

def build_combined_figure(data):
    """
    [核心繪圖入口] 建構完整的主副圖 Subplot
    
    Layout Structure:
    -------------------------------------------------
    | Row 1: 主圖 (Price Chart)                      |
    |        - Candlestick (K 線)                    |
    |        - Overlays (SMA, VWAP, Bands...)        |
    |        - Volume Profile (Right Side Overlay)   |
    -------------------------------------------------
    | Row 2: 副圖 (Sub Chart)                        |
    |        - Volume / Oscillator (RSI, MACD...)    |
    |        - CVD (Order Flow)                      |
    -------------------------------------------------
    """
    if data is None:
        return create_blank_figure()
        
    # 1. 建立子圖框架 (3 Rows)
    # shared_xaxes=True: 上下圖共用 X 軸縮放
    fig = make_subplots(
        rows=3, cols=1, 
        shared_xaxes=True, 
        vertical_spacing=0.03,
        row_heights=[0.5, 0.25, 0.25],     # 高度比例 5:2.5:2.5
        specs=[
            [{"secondary_y": False}], # Row 1: Price
            [{"secondary_y": True}],  # Row 2: Volume/CVD
            [{"secondary_y": True}]   # Row 3: LOB (OBI/OFI)
        ]
    )

    # ---------------------------------------------------------
    # Row 1: 主圖 (Price)
    # ---------------------------------------------------------
    
    # 1. Main Chart (K 線圖)
    renderers.add_main_price_chart(fig, data, row=1, col=1)

    # 2. Overlays (主圖指標)
    # 預設隱藏 (Legend Only) 的指標列表
    DEFAULT_OFF_LEGENDS = ['SMA_3min', 'SMA_60', 'Max_250', 'Min_250']
    
    # [特殊處理] VWAP Bands (通道)
    if 'VWAP_Upper' in data['history']:
        # Upper Band 2.0 (Standard) - Green
        renderers.add_overlay_indicator(fig, data, {
            'id': 'VWAP_Upper', 'color': '#28B463', 'style': 'dash',
            'legendgroup': 'VWAP_Group'
        }, row=1, col=1)
        # Lower Band 2.0 (Standard) - Green
        renderers.add_overlay_indicator(fig, data, {
            'id': 'VWAP_Lower', 'color': '#28B463', 'style': 'dash',
            'legendgroup': 'VWAP_Group'
        }, row=1, col=1)

    # [NEW] Optional Bands (1.0 & 2.5) with New Colors
    # 1.0 SD (Trend Life Line) - Gold
    if 'VWAP_Upper_1' in data['history']:
        renderers.add_overlay_indicator(fig, data, {
            'id': 'VWAP_Upper_1', 'color': '#F4D03F', 'style': 'dash', 'width': 1,
            'legendgroup': 'VWAP_Group_1'
        }, row=1, col=1)
        
        renderers.add_overlay_indicator(fig, data, {
            'id': 'VWAP_Lower_1', 'color': '#F4D03F', 'style': 'dash', 'width': 1,
            'legendgroup': 'VWAP_Group_1'
        }, row=1, col=1)

    # 2.5 SD (Extreme Reversal) - Red
    if 'VWAP_Upper_2.5' in data['history']:
        renderers.add_overlay_indicator(fig, data, {
            'id': 'VWAP_Upper_2.5', 'color': '#E74C3C', 'style': 'dash', 'width': 2, # Thicker for warning
            'legendgroup': 'VWAP_Group_2.5'
        }, row=1, col=1)
        
        renderers.add_overlay_indicator(fig, data, {
            'id': 'VWAP_Lower_2.5', 'color': '#E74C3C', 'style': 'dash', 'width': 2, # Thicker for warning
            'legendgroup': 'VWAP_Group_2.5'
        }, row=1, col=1)

    # 動態渲染 Config 中的所有 Overlay 指標
    for ind in INDICATORS_SETUP:
        if ind.get('type') == TYPE_OVERLAY and ind['id'] in data['history']:
            # 記錄當前 Trace 數量，以便判斷是否成功新增
            n_traces_before = len(fig.data)
            renderers.add_overlay_indicator(fig, data, ind, row=1, col=1)
            
            # 若成功新增，設定可見性
            if len(fig.data) > n_traces_before:
                if ind['id'] in DEFAULT_OFF_LEGENDS:
                    fig.data[-1].visible = 'legendonly'
                else:
                    fig.data[-1].visible = True

    # ---------------------------------------------------------
    # Row 2: Oscillators (副圖指標)
    # ---------------------------------------------------------
    valid_indicators = [ind for ind in INDICATORS_SETUP if ind.get('type') == TYPE_OSCILLATOR and ind['id'] in data['history']]
    
    for ind in valid_indicators:
        ind_id = ind['id']
        # [Revert] 回歸單純切片法 (Simple Slicing)
        # 優先考量數據可見性與程式穩定性。
        # 註：經查核，Lot Size 屬 Rolling Window Metrics (250 ticks)，切片法在統計上是有效的。
        y_data = data['history'][ind_id][data['start_idx']::data['step']]
        x_data = data['tick_x']
        
        # 動態分派 Renderer (Dynamic Dispatch)
        method_name = f"render_{ind_id.lower()}"
        renderer = getattr(renderers.OscillatorRenderers, method_name, None)
        
        if renderer:
            renderer(fig, x_data, y_data, ind, row=2, col=1)
            
            if ind['id'] in DEFAULT_OFF_LEGENDS:
                fig.data[-1].visible = 'legendonly'

    # ---------------------------------------------------------
    # Row 3: LOB Metrics (OBI / OFI)
    # ---------------------------------------------------------
    x_data = data['tick_x']
    
    # Render OBI (Left Axis)
    if 'obi' in data['history']:
        # [Refactor] 數據已在 state.py 完成預先累加 (State Metric)
        # 這裡直接切片顯示即可。
        plot_cum_obi = data['history']['obi'][data['start_idx']::data['step']]
        renderers.OscillatorRenderers.render_obi(fig, x_data, plot_cum_obi, {}, row=3, col=1)

    # Render OFI (Right Axis)
    if 'ofi' in data['history']:
        # [Refactor] 直接切片
        plot_cum_ofi = data['history']['ofi'][data['start_idx']::data['step']]
        renderers.OscillatorRenderers.render_ofi(fig, x_data, plot_cum_ofi, {}, row=3, col=1)


    # 4. Volume Profile (Overlay on Row 1)
    # 這是一個特殊的 Trace，疊加在主圖右側
    vp = data.get('vp_data')
    if vp:
        # 計算 X 軸範圍供參考線繪製
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
        
        # 重要：Volume Profile 使用了 Stacking Trick (Green + Red Overlay)，需設定為 overlay 模式
        barmode='overlay',
        
        # --- 2. 交互行為 (Interaction) ---
        uirevision='constant',  # 鎖定狀態：防止數據更新時重置縮放
        hovermode='x',    # 懸停模式：X (Time-aligned) - 鎖定時間軸顯示所有數據
        
        # --- 3. 圖例設定 (Legend) ---
        legend=dict(
            orientation="h",       # 水平排列
            yanchor="bottom", y=1.02, # 位於圖表上方
            xanchor="center", x=0.5
        ),

        # --- 5. Y 軸配置 (Y-Axes Configuration) ---
        # [Axis 1] 主圖價格 (右軸)
        yaxis=dict(
            side='right', 
            showgrid=True, 
            gridcolor='#333', 
            tickformat=',.0f'
        ), 
        
        # [Axis 2] 副圖動能柱狀圖 (Row 2 左軸)
        yaxis2=dict(
            side='left', 
            showgrid=True, 
            gridcolor='#333'
        ),                    
        
        # [Axis 3] 副圖 CVD 線圖 (Row 2 右軸，疊加於 Axis 2)
        yaxis3=dict(
            side='right', 
            showgrid=False, 
            tickformat=',.0f',
            zeroline=True, 
            zerolinewidth=1, 
            zerolinecolor='rgba(255,255,255,0.3)',
            overlaying='y2'     # 關鍵：共享 Row 2 空間
        ),

        # [Axis 4] LOB OBI (Row 3 左軸)
        yaxis4=dict(
            side='left',
            showgrid=True,
            gridcolor='#333',
            # range=[-1.1, 1.1], # [Fix] Removed fixed range for CumOBI
            zeroline=True,
            zerolinewidth=1, 
            zerolinecolor='rgba(255,255,255,0.3)'
        ),

        # [Axis 5] LOB OFI (Row 3 右軸)
        yaxis5=dict(
            side='right',
            showgrid=False,
            overlaying='y4',
            tickformat=',.0f',
            zeroline=True, 
            zerolinewidth=1, 
            zerolinecolor='rgba(255,255,255,0.3)'
        ),
        
        # --- 6. X 軸配置 (X-Axis Base) ---
        # 註：樣式統一由 update_xaxes 處理，此處僅設定範圍與滑桿
        xaxis=dict(
            rangeslider=dict(visible=False),
            range=initial_range if initial_range else None
        ),
        
        # [Axis 4 -> Now needs to be mapped correctly for VP?]
        # Plotly internals: xaxis of trace.
        # If we use Row 1, Col 1, it uses 'x' and 'y'.
        # Our Volume Profile trace manually sets `xaxis='x3'`.
        # Wait, if we added rows, does `x3` shift?
        # `make_subplots` generates:
        # Row 1: x, y
        # Row 2: x2, y2, y3
        # Row 3: x3, y4, y5  <-- Wait! x3 is now Row 3's Axis!
        
        # VP used 'x3' manually before. This will CONFLICT with Row 3.
        # I must assign a NEW custom axis for VP, say 'x4' or 'x_vp'.
        # But `make_subplots` manages names automatically.
        # If I use `xaxis='x99'`, I need to define `xaxis99` in layout.
        
        # Let's define VP axis as 'x4' (Assuming 3 rows use x1, x2, x3).
        # Warning: `make_subplots` shared_xaxes=True means x, x2, x3 are linked.
        # VP needs an INDEPENDENT axis on Row 1.
        
        # Solution: explicit definition of xaxis4 for VP.
        
        # [Axis VP] Volume Profile X-Axis (Overlay Top of Row 1)
        xaxis4=dict(
            overlaying='x', # 疊加在主 X 軸 (Row 1)
            side='top',     
            showgrid=False,
            visible=False,  
            matches=None    
        ),
    )
    
    # [CRITICAL FIX]
    # update_xaxes matches='x' applies to x, x2, x3.
    # VP uses x4.
    
    # Wait, my VP renderer sets `xaxis='x3'`. I must update VP logic too?
    # Yes. I need to update `renderers.add_volume_profile` to use `x4` or pass axis name.
    # But `renderers.add_volume_profile` is in `renderers.py`. I didn't change it.
    # It hardcodes `xaxis='x3'`.
    # This is bad. Row 3 will likely get messed up or VP will appear on Row 3.
    
    # I MUST update `renderers.py` to allow custom xaxis name OR update it to 'x4'.
    # I should update `renderers.py` first to be safe? Or just patch it now?
    # I'll update `renderers.py` in the NEXT step (fixing the x3 conflict).
    # For now, let's write `chart.py` using `xaxis4` in layout, and I will fix renderer after.
    
    fig.update_xaxes(
        # 1. 網格與標籤
        showgrid=True,
        showticklabels=True,  # 強制主副圖皆顯示時間
        matches='x',          # 確保上下圖縮放同步
        
        # 2. 十字游標 (Crosshair / Spike Line)
        # 用於解決「想要同時看上下圖位置」的需求
        showspikes=True,
        spikemode='across',   # 貫穿模式：線條會延伸到所有子圖
        spikesnap='cursor',   # 默認模式：跟隨游標
        showline=False,       # 隱藏軸線本身，只留網格
        spikedash='dash',
        spikecolor='rgba(255, 255, 255, 0.5)',
        spikethickness=0.5,
    )
    
    # Fix for VP Axis (x4)
    # Ensure it doesn't show spikes (cleaner)
    fig.update_layout(xaxis4=dict(matches=None, showspikes=False))
    
    return fig
