
"""
gale/dashboard/chart.py

負責將數據轉換為 Plotly Figure (View)。
只負責「畫圖」，不負責「怎麼算」。
"""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
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
    繪製主副圖合併的 Subplot。
    Row 1: 價格 (Price) + Overlays
    Row 2: 動能 (Momentum) + Oscillators
    """
    if data is None:
        return create_blank_figure()
        
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
    
    # 1. Main Chart (Row 1)
    renderers.add_main_price_chart(fig, data, row=1, col=1)

    # 2. Overlays (Row 1)
    # Default OFF (Legend Only)
    DEFAULT_OFF_LEGENDS = ['SMA_3min', 'SMA_60', 'Max_250', 'Min_250']
    
    # [NEW] VWAP Bands Rendering
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
