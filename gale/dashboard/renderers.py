
import numpy as np
import plotly.graph_objects as go
from config.ui_theme import UI_COLOR

# =============================================================================
# 🎨 Chart Renderers (繪圖渲染器)
# 封裝所有 Plotly Trace 的建立邏輯，讓 chart.py 保持乾淨。
# =============================================================================

def add_main_price_chart(fig, data, row=1, col=1):
    """
    繪製主圖價格 K 線 (Candlestick)
    
    邏輯：
    - 若週期包含 's' (秒級)，視為高頻數據，使用 OHLC 線圖 (效能較好)。
    - 否則使用標準 Candlestick (紅綠 K 棒)。
    """
    current_tf = data.get('timeframe', '1m')
    is_high_freq = 's' in current_tf
    
    if is_high_freq:
        fig.add_trace(go.Ohlc(
            x=data['candle_x'],
            open=data['candles']['open'], high=data['candles']['high'],
            low=data['candles']['low'], close=data['candles']['close'],
            name=f'{current_tf} OHLC',
            increasing_line_color=UI_COLOR['TEXT_MAIN'], decreasing_line_color=UI_COLOR['TEXT_MAIN'],
            increasing_line_width=1, decreasing_line_width=1,

            # 簡潔版 Tooltip
            hovertemplate=(
                '<b>%{x|%H:%M:%S}</b><br>' +
                'O: %{open}<br>H: %{high}<br>L: %{low}<br>C: %{close}<br>' +
                '<extra></extra>' 
            ),
            legendrank=100
        ), row=row, col=col)
    else:
        fig.add_trace(go.Candlestick(
            x=data['candle_x'],
            open=data['candles']['open'], high=data['candles']['high'],
            low=data['candles']['low'], close=data['candles']['close'],
            name=f'{current_tf} Candle',
            increasing_line_color=UI_COLOR['UP'], decreasing_line_color=UI_COLOR['DOWN'],
            increasing_fillcolor=UI_COLOR['UP'], decreasing_fillcolor=UI_COLOR['DOWN'],
            
            # 簡潔版 Tooltip
            hovertemplate=(
                '<b>%{x|%H:%M:%S}</b><br>' +
                'H: %{high}<br>C: %{close}<br>O: %{open}<br>L: %{low}<br>' +
                '<extra></extra>' 
            ),
            legendrank=100
        ), row=row, col=col)

    # [New] Add Volume Bars (Overlay on Bottom)
    # ---------------------------------------------------------
    # 使用 yaxis6 (Overlay) 將 Volume 畫在底部
    
    # [New] Add Volume Bars (Overlay on Bottom)
    # ---------------------------------------------------------
    # 使用 yaxis6 (Overlay) 將 Volume 畫在底部
    
    if 'volume' in data['candles']:
        volumes = data['candles']['volume']
        
        # [Visual Update] Monochrome Volume Bars
        # 使用單一顏色（半透明 HIGHLIGHT 黃色），營造「金山」效果。
        # 不需要計算 Open/Close，效能最佳。
        
        fig.add_trace(go.Bar(
            x=data['candle_x'],
            y=volumes,
            name='Volume',
            marker_color='rgba(255, 240, 0, 0.25)', # 對應 UI_COLOR['HIGHLIGHT'] (#FFF000) 的半透明版
            marker_line_width=0,
            xaxis='x',      # [Fix] Explicitly bind to main x-axis
            yaxis='y6',     # 指定使用我們剛定義的疊加軸
            showlegend=False, # 不顯示圖例 (節省空間)
            hovertemplate=(
                'Vol: %{y:,}<br>' +
                '<extra></extra>'
            )
        ))

def add_overlay_indicator(fig, data, ind_config, row=1, col=1):
    """
    繪製疊加指標 (SMA, VWAP, Bands...)
    """
    ind_id = ind_config['id']
    # 根據 State 計算的 Step 進行降頻繪製
    y_data = data['history'][ind_id][data['start_idx']::data['step']]
    
    display_name = ind_config.get('name', ind_id)
    
    trace_kwargs = dict(
        x=data['tick_x'], y=y_data, mode='lines', name=display_name,
        line=dict(color=ind_config['color'], width=1, dash=ind_config.get('style', 'solid')),
        hovertemplate='<b>%{fullData.name}</b>: %{y}<extra></extra>'
    )
    
    # 用於 Legend Group 切換 (e.g. 點擊 VWAP 可同時切換 Upper/Lower)
    if 'legendgroup' in ind_config:
        trace_kwargs['legendgroup'] = ind_config['legendgroup']

    if 'legendrank' in ind_config:
        trace_kwargs['legendrank'] = ind_config['legendrank']
    
    # 使用 WebGL 加速渲染 (Scattergl)
    fig.add_trace(go.Scattergl(**trace_kwargs), row=row, col=col)

def add_volume_profile(fig, vp_data, bin_size, legend_group, x_range=None, row=1, col=1):
    """
    繪製 Volume Profile (直方圖 + 關鍵價位)
    
    Args:
        vp_data: 包含 prices, volumes, poc, vah, val 的字典
        x_range: 用於繪製水平線的 X 軸起訖點
    """
    if not vp_data or len(vp_data['prices']) == 0:
        return

    # 1. 繪製關鍵價位線 (POC, VAH, VAL)
    if x_range:
        x_start, x_end = x_range
        levels = [
            (vp_data['poc'], 'red',    'POC', 'dash'),
            (vp_data['vah'], 'yellow', 'VAH', 'dash'),
            (vp_data['val'], 'yellow', 'VAL', 'dash')
        ]
        
        for price, color, name, style in levels:
            if price > 0:
                fig.add_trace(go.Scattergl(
                    x=[x_start, x_end], 
                    y=[price, price],
                    mode='lines',
                    line=dict(color=color, width=1, dash=style),
                    name=name,
                    legendgroup=legend_group,
                    showlegend=False, 
                    visible='legendonly', # Sync with VP Bars
                    legendrank=190,
                    hoverinfo='name+y' # 只顯示名稱與價格
                ), row=row, col=col)
    
    prices = vp_data['prices']
    volumes = vp_data['volumes']
    
    # 檢查是否有 Delta Profile 數據 (買賣分量)
    has_delta = 'buy_volumes' in vp_data and len(vp_data['buy_volumes']) > 0
    
    if has_delta:
        buy_vols = vp_data['buy_volumes']
        sell_vols = vp_data['sell_volumes']
        total_vols = vp_data['volumes'] 
        
        # 視覺疊加技巧 (Visual Stacking Trick):
        # 由於 Overlay 模式下 Bar 會互相遮擋，我們利用繪製順序來模擬 Stack 效果。
        # 1. 先畫總量 (綠色)：代表 Buy，因為 Sell 會蓋在上面，剩下的綠色就是 Buy。
        # 2. 再畫賣量 (紅色)：疊加在總量之上。
        # 視覺效果：[紅色區塊(Sell)][綠色區塊(剩餘的Buy)]
        
        # Layer 1: Total Volume (顯示為 Buy Color, Green)
        fig.add_trace(go.Bar(
            y=prices,
            x=total_vols,
            customdata=buy_vols, # 傳入真實 Buy Vol 供 tooltip 顯示正確數值
            orientation='h',
            xaxis='x4',     # [Fix] Use X4 for VP to avoid conflict with Row 3 (X3)
            yaxis='y',
            name='VP Buy Vol',
            width=bin_size * 0.95,
            marker_color='rgba(0, 230, 118, 0.1)', # 綠色 (加上透明度)
            marker_line_width=0,
            hovertemplate='<b>Buy Vol</b>: %{customdata:,}<br>Price: %{y}<extra></extra>',
            legendgroup=legend_group,
            visible='legendonly', # Default Hidden
            showlegend=True,
            legendrank=190
        ))
        
        # Layer 2: Sell Volume (顯示為 Sell Color, Red)
        fig.add_trace(go.Bar(
            y=prices,
            x=sell_vols,
            orientation='h',
            xaxis='x4',     # [Fix] Use X4
            yaxis='y',
            name='VP Sell Vol',
            width=bin_size * 0.95,
            marker_color='rgba(255, 82, 82, 0.25)', # 紅色
            marker_line_width=0,
            hovertemplate='<b>Sell Vol</b>: %{x:,}<br>Price: %{y}<extra></extra>',
            legendgroup=legend_group,
            visible='legendonly', # Default Hidden
            showlegend=True,
            legendrank=191
        ))
        
    else:
        # Fallback: 若無買賣分量，只畫單一灰階 Bar
        fig.add_trace(go.Bar(
            y=prices, 
            x=volumes,
            orientation='h',
            xaxis='x4',     # [Fix] Use X4
            yaxis='y', 
            name='Volume Profile',
            width=bin_size * 0.95,
            marker_color='rgba(100, 100, 100, 0.4)',
            marker_line_width=0,
            hoverinfo='y+x',
            legendgroup=legend_group,
            visible='legendonly', # Default Hidden
            showlegend=True,
            legendrank=192
        ))

class OscillatorRenderers:
    """
    [Namespace] 副圖指標渲染器集合
    chart.py 會使用 getattr 動態呼叫這些方法。
    """
    
    @staticmethod
    def render_cvd(fig, x_data, y_data, config, row, col):
        """繪製 CVD (累積成交量差)"""
        group_name = "cvd_group"
        ind_id = config['id']
        
        # 主線
        fig.add_trace(go.Scattergl(
            x=x_data, y=y_data, mode='lines', name=ind_id,
            line=dict(color=config['color'], width=1.0), 
            legendgroup=group_name, showlegend=True, legendrank=200,
            hovertemplate='<b>%{fullData.name}</b>: %{y}<extra></extra>'
        ), row=row, col=col, secondary_y=True) # 使用右軸
        
        # 填充區域 (Zero Line area fill)
        y_pos = np.maximum(0, y_data)
        y_neg = np.minimum(0, y_data)
        common_fill = dict(mode='lines', line=dict(width=0), fill='tozeroy', fillcolor='rgba(255, 215, 0, 0.05)', hoverinfo='skip', legendgroup=group_name, showlegend=False, legendrank=200)
        
        fig.add_trace(go.Scattergl(x=x_data, y=y_pos, **common_fill), row=row, col=col, secondary_y=True)
        fig.add_trace(go.Scattergl(x=x_data, y=y_neg, **common_fill), row=row, col=col, secondary_y=True)

    @staticmethod
    def render_small_lot(fig, x_data, y_data, config, row, col):
        """繪製小單淨量 (Small Lot <5口) 柱狀圖"""
        bar_colors = np.where(y_data >= 0, UI_COLOR['UP'], UI_COLOR['DOWN'])
        fig.add_trace(go.Bar(
            x=x_data, y=y_data, name=f"{config.get('name', config['id'])} (< 5)",
            marker_color=bar_colors, marker_line_width=0, opacity=1.0, legendrank=210,
            hovertemplate='<b>%{fullData.name}</b>: %{y}<extra></extra>'
        ), row=row, col=col, secondary_y=False) # 使用左軸

    @staticmethod
    def render_large_lot(fig, x_data, y_data, config, row, col):
        """繪製大單 (Large Lot >=5口) 柱狀圖"""
        # 雙色區分：Buy=深棕色, Sell=深藍 (對比強烈且專業)
        cols = np.where(y_data >= 0, '#8C5B00', '#006D91')
        fig.add_hline(y=0, line_width=1, line_color="#555", row=row, col=col)
        fig.add_trace(go.Bar(
            x=x_data, y=y_data, name=f"{config.get('name', config['id'])} (>= 5)",
            marker_color=cols, marker_line_width=0, opacity=0.6, legendrank=220,
            hovertemplate='<b>%{fullData.name}</b>: %{y}<extra></extra>'
        ), row=row, col=col, secondary_y=False)

    @staticmethod
    def render_mega_lot(fig, x_data, y_data, config, row, col):
        """繪製特大單 (Mega Lot >=15口) 柱狀圖"""
        # 雙色區分：Buy=洋紅(Neon), Sell=青色(Neon) -> 極度醒目
        cols = np.where(y_data >= 0, '#FB00FF', '#00FFFF')
        fig.add_trace(go.Bar(
            x=x_data, y=y_data, name=f"{config.get('name', config['id'])} (>= 15)",
            marker_color=cols, marker_line_width=0, opacity=1.0, legendrank=230,
            hovertemplate='<b>%{fullData.name}</b>: %{y}<extra></extra>'
        ), row=row, col=col, secondary_y=False)

    @staticmethod
    def render_obi(fig, x_data, y_data, config, row, col):
        """
        繪製 CumOBI (Cumulative Order Book Imbalance)
        Style: Cyan Line with Fill
        """
        group_name = "OBI"
        
        # Main Line
        fig.add_trace(go.Scattergl(
            x=x_data, y=y_data, 
            mode='lines', 
            name=config.get('name', 'COBI'),
            line=dict(width=1.0, color='cyan'),
            legendgroup=group_name, showlegend=True,
            legendrank=250,
            hovertemplate='<b>%{fullData.name}</b>: %{y}<extra></extra>'
        ), row=row, col=col, secondary_y=False)

        # Fill Area
        y_pos = np.maximum(0, y_data)
        y_neg = np.minimum(0, y_data)
        
        common_fill = dict(
            mode='lines', 
            line=dict(width=0), 
            fill='tozeroy', 
            fillcolor='rgba(0, 255, 255, 0.1)', # Cyan tint
            hoverinfo='skip', 
            legendgroup=group_name, 
            showlegend=False,
            legendrank=250
        )
        
        fig.add_trace(go.Scattergl(x=x_data, y=y_pos, **common_fill), row=row, col=col, secondary_y=False)
        fig.add_trace(go.Scattergl(x=x_data, y=y_neg, **common_fill), row=row, col=col, secondary_y=False)
        


    @staticmethod
    def render_ofi(fig, x_data, y_data, config, row, col):
        """
        繪製 OFI (Order Flow Imbalance) -> Accumulator
        Style: Gold Line with Fill (Matching CVD style)
        """
        group_name = "OFI"
        
        # Main Line
        fig.add_trace(go.Scattergl(
            x=x_data, y=y_data,
            mode='lines',
            name=config.get('name', 'COFI'),
            line=dict(color='gold', width=1.0),
            legendgroup=group_name, showlegend=True,
            legendrank=240,
            hovertemplate='<b>%{fullData.name}</b>: %{y}<extra></extra>'
        ), row=row, col=col, secondary_y=True) 

        # Fill Area (Zero Line)
        # Using same trick as CVD for consistent look
        y_pos = np.maximum(0, y_data)
        y_neg = np.minimum(0, y_data)
        common_fill = dict(
            mode='lines', 
            line=dict(width=0), 
            fill='tozeroy', 
            fillcolor='rgba(255, 215, 0, 0.1)', # Gold tint
            hoverinfo='skip', 
            legendgroup=group_name, 
            showlegend=False,
            legendrank=240
        )
        
        fig.add_trace(go.Scattergl(x=x_data, y=y_pos, **common_fill), row=row, col=col, secondary_y=True)
        fig.add_trace(go.Scattergl(x=x_data, y=y_neg, **common_fill), row=row, col=col, secondary_y=True)

