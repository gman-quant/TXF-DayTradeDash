
import numpy as np
import plotly.graph_objects as go
from config.ui_theme import UI_COLOR

# =============================================================================
# 🎨 Chart Renderers
# Encapsulates the logic for creating Plotly Traces.
# =============================================================================

def add_main_price_chart(fig, data, row=1, col=1):
    """
    Render the main price chart (Candlestick or OHLC).
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
            increasing_line_width=1, decreasing_line_width=1
        ), row=row, col=col)
    else:
        fig.add_trace(go.Candlestick(
            x=data['candle_x'],
            open=data['candles']['open'], high=data['candles']['high'],
            low=data['candles']['low'], close=data['candles']['close'],
            name=f'{current_tf} Candle',
            increasing_line_color=UI_COLOR['UP'], decreasing_line_color=UI_COLOR['DOWN'],
            increasing_fillcolor=UI_COLOR['UP'], decreasing_fillcolor=UI_COLOR['DOWN'],
            
            hovertemplate=(
                '<b>%{x|%H:%M:%S}</b><br>' +
                'O: %{open}<br>H: %{high}<br>L: %{low}<br>C: %{close}<br>' +
                'V: %{text}' +
                '<extra></extra>' 
            ),
            text=[f'{v:,}' for v in data['candles']['volume']] # Simple volume text
        ), row=row, col=col)

def add_overlay_indicator(fig, data, ind_config, row=1, col=1):
    """
    Render a line overlay (SMA, VWAP, etc.).
    """
    ind_id = ind_config['id']
    y_data = data['history'][ind_id][data['start_idx']::data['step']]
    
    # Default visibility logic can be passed in or handled here
    # For simplicity, we assume the caller handles logic or we hardcode defaults here
    # But to keep it pure, let's just render what is asked.
    # We can pass 'visible' as a kwarg if needed.
    
    trace_kwargs = dict(
        x=data['tick_x'], y=y_data, mode='lines', name=ind_id,
        line=dict(color=ind_config['color'], width=1, dash=ind_config.get('style', 'solid')),
    )
    
    # Optional Legend Group
    if 'legendgroup' in ind_config:
        trace_kwargs['legendgroup'] = ind_config['legendgroup']
        # If part of a group, we might want to toggle together.
        # Plotly default behavior: if legendgroup is set, clicking one toggles all in group.
    
    fig.add_trace(go.Scattergl(**trace_kwargs), row=row, col=col)

def add_volume_profile(fig, vp_data, bin_size, legend_group, x_range=None, row=1, col=1):
    """
    Render Volume Profile (Histogram + Levels).
    x_range: (start, end) tuple for drawing horizontal lines.
    """
    if not vp_data or len(vp_data['prices']) == 0:
        return

    # 1. Key Levels (POC, VAH, VAL) -> Lines
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
                    hoverinfo='name+y'
                ), row=row, col=col)
    
    prices = vp_data['prices']
    volumes = vp_data['volumes']
    
    # Check if Delta Profile data is available
    has_delta = 'buy_volumes' in vp_data and len(vp_data['buy_volumes']) > 0
    
    if has_delta:
        buy_vols = vp_data['buy_volumes']
        sell_vols = vp_data['sell_volumes']
        
        buy_vols = vp_data['buy_volumes']
        sell_vols = vp_data['sell_volumes']
        total_vols = vp_data['volumes'] # Need Total for the trick
        
        # Strategy: Simulate Stacking in Overlay Mode
        # Layer 1 (Bottom): Draw TOTAL Volume -> Color it GREEN (Buy)
        # Layer 2 (Top):    Draw SELL Volume  -> Color it RED (Sell)
        # Visual Result:    [ Red (covers bottom) ] [ Green (remainder) ]
        
        # 2a. Layer 1: Total (Green)
        # User sees this as "Buy Vol" (the green part sticking out)
        fig.add_trace(go.Bar(
            y=prices,
            x=total_vols,
            customdata=buy_vols, # Pass real Buy Vol for tooltip
            orientation='h',
            xaxis='x3',
            yaxis='y',
            name='Buy Vol',
            width=bin_size * 0.95,
            marker_color='rgba(0, 230, 118, 0.1)', # Green
            marker_line_width=0,
            hovertemplate='<b>Buy Vol</b>: %{customdata:,}<br>Price: %{y}<extra></extra>',
            legendgroup=legend_group,
            showlegend=True
        ))
        
        # 2b. Layer 2: Sell (Red)
        fig.add_trace(go.Bar(
            y=prices,
            x=sell_vols,
            orientation='h',
            xaxis='x3',
            yaxis='y',
            name='Sell Vol',
            width=bin_size * 0.95,
            marker_color='rgba(255, 82, 82, 0.25)', # Red opacity increased to 0.25
            marker_line_width=0,
            hovertemplate='<b>Sell Vol</b>: %{x:,}<br>Price: %{y}<extra></extra>',
            legendgroup=legend_group,
            showlegend=True
        ))
        
    else:
        # 2. Bar Chart (Total only fallback)
        fig.add_trace(go.Bar(
            y=prices, 
            x=volumes,
            orientation='h',
            xaxis='x3', 
            yaxis='y', 
            name='Volume Profile',
            width=bin_size * 0.95,
            marker_color='rgba(100, 100, 100, 0.4)',
            marker_line_width=0,
            hoverinfo='y+x',
            legendgroup=legend_group,
            showlegend=True
        ))

class OscillatorRenderers:
    """Namespace for Oscillator visualization logic."""
    
    @staticmethod
    def render_cvd(fig, x_data, y_data, config, row, col):
        group_name = "cvd_group"
        ind_id = config['id']
        
        # Main Line
        fig.add_trace(go.Scattergl(
            x=x_data, y=y_data, mode='lines', name=ind_id,
            line=dict(color=config['color'], width=1.0), 
            legendgroup=group_name, showlegend=True, legendrank=4
        ), row=row, col=col, secondary_y=True)
        
        # Fill Area
        y_pos = np.maximum(0, y_data)
        y_neg = np.minimum(0, y_data)
        common_fill = dict(mode='lines', line=dict(width=0), fill='tozeroy', fillcolor='rgba(255, 215, 0, 0.05)', hoverinfo='skip', legendgroup=group_name, showlegend=False)
        
        fig.add_trace(go.Scattergl(x=x_data, y=y_pos, **common_fill), row=row, col=col, secondary_y=True)
        fig.add_trace(go.Scattergl(x=x_data, y=y_neg, **common_fill), row=row, col=col, secondary_y=True)

    @staticmethod
    def render_retail_flow(fig, x_data, y_data, config, row, col):
        bar_colors = np.where(y_data >= 0, UI_COLOR['UP'], UI_COLOR['DOWN'])
        fig.add_trace(go.Bar(
            x=x_data, y=y_data, name=f"{config['id']} (< 5)",
            marker_color=bar_colors, marker_line_width=0, opacity=1.0, legendrank=1
        ), row=row, col=col, secondary_y=False)

    @staticmethod
    def render_smart_money(fig, x_data, y_data, config, row, col):
        cols = np.where(y_data >= 0, "#8C5B00", "#006D91")
        fig.add_hline(y=0, line_width=1, line_color="#555", row=row, col=col)
        fig.add_trace(go.Bar(
            x=x_data, y=y_data, name=f"{config['id']} (>= 5)",
            marker_color=cols, marker_line_width=0, opacity=0.6, legendrank=2
        ), row=row, col=col, secondary_y=False)

    @staticmethod
    def render_whale_nuke(fig, x_data, y_data, config, row, col):
        cols = np.where(y_data >= 0, "#FB00FF", "#00FFFF")
        fig.add_trace(go.Bar(
            x=x_data, y=y_data, name=f"{config['id']} (>= 15)",
            marker_color=cols, marker_line_width=0, opacity=1.0, legendrank=3
        ), row=row, col=col, secondary_y=False)
