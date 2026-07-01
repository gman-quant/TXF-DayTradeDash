
import re
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
    current_tf = data.get('timeframe', '5s')
    is_high_freq = 's' in current_tf
    
    if is_high_freq:
        fig.add_trace(go.Ohlc(
            x=data['candle_x'],
            open=data['candles']['open'], high=data['candles']['high'],
            low=data['candles']['low'], close=data['candles']['close'],
            name=f'{current_tf} OHLC',
            increasing_line_color=UI_COLOR['TEXT_SUB'], decreasing_line_color=UI_COLOR['TEXT_SUB'],
            increasing_line_width=1, decreasing_line_width=1,

            # 簡潔版 Tooltip 加入日期與星期 (%a)
            hovertemplate=(
                '<b>%{x|%m/%d (%a) %H:%M:%S}</b><br>' +
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
            name=f'{current_tf} Candles',
            increasing_line_color=UI_COLOR['Kbar_UP'], decreasing_line_color=UI_COLOR['Kbar_DOWN'],
            increasing_fillcolor=UI_COLOR['Kbar_UP'], decreasing_fillcolor=UI_COLOR['Kbar_DOWN'],
            
            # 簡潔版 Tooltip 加入日期與星期 (%a)
            hovertemplate=(
                '<b>%{x|%m/%d (%a) %H:%M:%S}</b><br>' +
                'O: %{open}<br>H: %{high}<br>L: %{low}<br>C: %{close}<br>' +
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
        
        colors = np.where(
            np.array(data['candles']['close']) >= np.array(data['candles']['open']),
            UI_COLOR['Kbar_UP'],
            UI_COLOR['Kbar_DOWN']
        )
        
        fig.add_trace(go.Bar(
            x=data['candle_x'],
            y=volumes,
            name='Volume',
            # marker_color=UI_COLOR['VOLUME_FILL'],  # 原本顏色
            marker_color=colors,  # 根據漲跌變色
            opacity=0.5,
            
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

    # 在盤間空檔(夜→日、週末、假日)切斷折線,否則會跨空檔連一條斜線(rangebreaks 只收 x 軸空白,
    # 折線本身仍會把相鄰兩點連起來)。色帶已在 add_regime_band_fills 用同一組 breaks 拆段。
    _breaks = data.get('session_breaks') or []
    if _breaks:
        y_data = np.asarray(y_data, dtype='float64').copy()
        _n = len(y_data)
        for _b in _breaks:
            if 0 <= int(_b) < _n:
                y_data[int(_b)] = np.nan

    display_name = ind_config.get('name', ind_id)
    
    trace_kwargs = dict(
        x=data['tick_x'], y=y_data, mode='lines', name=display_name,
        line=dict(color=ind_config['color'], width=ind_config.get('width', 1), dash=ind_config.get('style', 'solid')),
        showlegend=ind_config.get('showlegend', True),
        hovertemplate='<b>%{fullData.name}</b>: %{y:.1f}<extra></extra>'
    )
    
    # 用於 Legend Group 切換 (e.g. 點擊 VWAP 可同時切換 Upper/Lower)
    if 'legendgroup' in ind_config:
        trace_kwargs['legendgroup'] = ind_config['legendgroup']

    if 'legendrank' in ind_config:
        trace_kwargs['legendrank'] = ind_config['legendrank']
    
    # 用 SVG go.Scatter(非 Scattergl):Scattergl(WebGL)不支援 x 軸 rangebreaks,
    # 一旦圖上有 rangebreaks(收合盤間空檔),所有 WebGL 折線會整條消失(COFI/COBI/VWAP…都不見)。
    # 折線點數已降頻到 ~2000,SVG 完全負擔得起。
    fig.add_trace(go.Scatter(**trace_kwargs), row=row, col=col)


def _color_to_rgba(color: str, alpha: float) -> str:
    """
    將任意 'rgb(...)' 或 'rgba(...)' 字串轉換成指定透明度的 rgba 字串。
    """
    nums = re.findall(r'[\d.]+', color)
    r, g, b = int(nums[0]), int(nums[1]), int(nums[2])
    return f'rgba({r}, {g}, {b}, {alpha})'


def add_regime_band_fills(fig, data, multipliers, hidden_zones=None, row=1, col=1):
    """
    按 sigma 層級在 Bull/Bear 線條間繪製填色帶，每個層級有獨立 Legend 條目。

    Zone 定義:
      -1σ Zone   : Bear_Band_1.0  <->  Bull_Band_1.0  (中心帶)
      -2σ Zone   : ±1σ <-> ±2σ 兩側
      -2.5σ Zone : ±2σ <-> ±2.5σ 兩側
      -3σ Zone   : ±2.5σ <-> ±3σ 兩側

    Args:
        hidden_zones : set/list of str — 外圍 zone_name，對應的 zone 預設 legendonly。
                       例如 {'σ 2.0-2.5'} 會讓對應區間預設隱藏。
    """
    from config.indicator_config import get_band_style

    tick_x = data['tick_x']
    history = data['history']
    start_idx = data['start_idx']
    step = data['step']

    # 盤切換斷點(降頻後索引)→ 每盤各自一段。每段獨立 fill(fill 不跨盤)→ 夜/日同框時
    # 中央價值區不會被跨盤填色塞滿。單盤模式沒有斷點 → 只有一段,行為與過去相同。
    _breaks = data.get('session_breaks') or []
    _seg_bounds = [0] + [int(b) for b in _breaks] + [len(tick_x)]

    FILL_ALPHA = 0.15

    def _get_y(key):
        if key not in history:
            return None
        return history[key][start_idx::step]

    def _add_fill_pair(lower_y, upper_y, fill_color, group_name, show_legend, name, visible=True):
        """在 lower_y 和 upper_y 之間填色 (fill=tonexty)——**按盤切換斷點拆成多段**,每段自成一塊
        封閉填色,不跨空檔。只第一段顯示 legend。必須用 go.Scatter(Scattergl 不支援 fill)。"""
        _first = True
        for _a, _b in zip(_seg_bounds[:-1], _seg_bounds[1:]):
            if _b - _a < 2:                     # 太短的段(1 點)跳過,不然填不出面積
                continue
            _lx = tick_x[_a:_b]
            # 底線 (透明錨點)
            fig.add_trace(go.Scatter(
                x=_lx, y=lower_y[_a:_b], mode='lines',
                line=dict(width=0, color='rgba(0,0,0,0)'),
                showlegend=False, legendgroup=group_name, hoverinfo='skip',
                name=f'_{name}_lo', visible=visible,
            ), row=row, col=col)
            # 上線 (填色至底線)
            fig.add_trace(go.Scatter(
                x=_lx, y=upper_y[_a:_b], mode='lines',
                line=dict(width=0, color='rgba(0,0,0,0)'),
                fill='tonexty', fillcolor=fill_color,
                showlegend=(show_legend and _first), legendgroup=group_name,
                name=name, hoverinfo='skip', legendrank=200, visible=visible,
            ), row=row, col=col)
            _first = False

    prev_sd = None

    for sd in multipliers:
        group_name = f'Zone_{sd}'

        bull_curr = _get_y(f'Bull_Band_{sd}')
        bear_curr = _get_y(f'Bear_Band_{sd}')

        if bull_curr is None or bear_curr is None:
            prev_sd = sd
            continue

        # 中心 ±1σ(value zone)不上色,只記住邊界 → cB 在 |.|<1 區留中性
        if prev_sd is None:
            prev_sd = sd
            continue

        # 環形帶顏色用「外緣 sd」對應色:1~2σ→黃(BAND_2)/2σ+→紅(BAND_3);中心 <1σ 留空(中性)
        color, _ = get_band_style(sd)
        fill_color = _color_to_rgba(color, FILL_ALPHA)

        is_last = (sd == multipliers[-1])
        zone_name = f'σ {prev_sd}+' if is_last else f'σ {prev_sd}-{sd}'
        is_hidden = (hidden_zones is not None and zone_name in hidden_zones)
        visible_state = 'legendonly' if is_hidden else True

        bull_prev = _get_y(f'Bull_Band_{prev_sd}')
        bear_prev = _get_y(f'Bear_Band_{prev_sd}')
        if bull_prev is None or bear_prev is None:
            prev_sd = sd
            continue

        # Bull 側 (向上，顯示 legend)。visible 直接傳進去,每段每條都會套用(不再只改最後一段)。
        _add_fill_pair(bull_prev, bull_curr, fill_color, group_name,
                       show_legend=True, name=zone_name, visible=visible_state)

        # Bear 側 (不重複顯示 legend)
        _add_fill_pair(bear_curr, bear_prev, fill_color, group_name,
                       show_legend=False, name=f'_{zone_name}_bear', visible=visible_state)

        prev_sd = sd


def add_volume_profile(fig, vp_data, bin_size, legend_group, x_range=None, visible=True, row=1, col=1):
    """
    繪製 Volume Profile (直方圖 + 關鍵價位)
    
    Args:
        vp_data: 包含 prices, volumes, poc, vah, val 的字典
        x_range: 用於繪製水平線的 X 軸起訖點
        visible: 控制初始顯示狀態 (True 或 False/legendonly)
    """
    trace_visible = True if visible else 'legendonly'

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
                fig.add_trace(go.Scatter(
                    x=[x_start, x_end], 
                    y=[price, price],
                    mode='lines',
                    line=dict(color=color, width=1, dash=style),
                    name=name,
                    legendgroup=legend_group,
                    showlegend=False, 
                    visible=trace_visible, # Sync with VP Bars
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
        
        # Layer 1: Sell Volume (顯示為 Sell Color, Green)
        fig.add_trace(go.Bar(
            y=prices,
            x=total_vols,
            customdata=sell_vols, # 傳入真實 Sell Vol 供 tooltip 顯示正確數值
            orientation='h',
            xaxis='x4',     # [Fix] Use X4
            yaxis='y',
            name='VP Sell Vol',
            width=bin_size * 0.95,
            marker_color=UI_COLOR['VP_SELL'], # 綠色 (加上透明度)
            marker_line_width=0,
            hovertemplate='<b>Sell Vol</b>: %{x:,}<br>Price: %{y}<extra></extra>',
            legendgroup=legend_group,
            visible=trace_visible,
            showlegend=True,
            legendrank=190
        ))
        # Layer 2: Total Volume (顯示為 Buy Color, Red)
        fig.add_trace(go.Bar(
            y=prices,
            x=buy_vols,
            orientation='h',
            xaxis='x4',     # [Fix] Use X4
            yaxis='y',
            name='VP Buy Vol',
            width=bin_size * 0.95,
            marker_color=UI_COLOR['VP_BUY'], # 紅色
            marker_line_width=0,
            hovertemplate='<b>Buy Vol</b>: %{customdata:,}<br>Price: %{y}<extra></extra>',
            legendgroup=legend_group,
            visible=trace_visible,
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
            visible=trace_visible,
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
        fig.add_trace(go.Scatter(
            x=x_data, y=y_data, mode='lines', name=ind_id,
            line=dict(color=config['color'], width=1.0), 
            legendgroup=group_name, showlegend=True, legendrank=200,
            hovertemplate='<b>%{fullData.name}</b>: %{y}<extra></extra>'
        ), row=row, col=col, secondary_y=True) # 使用右軸
        
        # 填充區域 (Zero Line area fill)
        y_pos = np.maximum(0, y_data)
        y_neg = np.minimum(0, y_data)
        common_fill = dict(mode='lines', line=dict(width=0), fill='tozeroy', fillcolor='rgba(255, 215, 0, 0.05)', hoverinfo='skip', legendgroup=group_name, showlegend=False, legendrank=200)
        
        fig.add_trace(go.Scatter(x=x_data, y=y_pos, **common_fill), row=row, col=col, secondary_y=True)
        fig.add_trace(go.Scatter(x=x_data, y=y_neg, **common_fill), row=row, col=col, secondary_y=True)

    @staticmethod
    def render_small_lot(fig, x_data, y_data, config, row, col):
        """繪製小單淨量 (Small Lot <5口) 柱狀圖"""
        cols = np.where(y_data >= 0, UI_COLOR['LOT_SMALL_UP'], UI_COLOR['LOT_SMALL_DOWN'])
        fig.add_trace(go.Bar(
            x=x_data, y=y_data, name=f"{config.get('name', config['id'])} (< 5)",
            marker_color=cols, marker_line_width=0, opacity=1.0, legendrank=210,
            hovertemplate='<b>%{fullData.name}</b>: %{y}<extra></extra>'
        ), row=row, col=col, secondary_y=False) # 使用左軸

    @staticmethod
    def render_large_lot(fig, x_data, y_data, config, row, col):
        """繪製大單 (Large Lot >=5口) 柱狀圖"""
        # 雙色區分：Buy=深棕色, Sell=深藍 (對比強烈且專業)
        cols = np.where(y_data >= 0, UI_COLOR['LOT_LARGE_UP'], UI_COLOR['LOT_LARGE_DOWN'])
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
        cols = np.where(y_data >= 0, UI_COLOR['LOT_MEGA_UP'], UI_COLOR['LOT_MEGA_DOWN'])
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
        fig.add_trace(go.Scatter(
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
        
        fig.add_trace(go.Scatter(x=x_data, y=y_pos, **common_fill), row=row, col=col, secondary_y=False)
        fig.add_trace(go.Scatter(x=x_data, y=y_neg, **common_fill), row=row, col=col, secondary_y=False)
        


    @staticmethod
    def render_ofi(fig, x_data, y_data, config, row, col):
        """
        繪製 OFI (Order Flow Imbalance) -> Accumulator
        Style: Gold Line with Fill (Matching CVD style)
        """
        group_name = "OFI"
        
        # Main Line
        fig.add_trace(go.Scatter(
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

        fig.add_trace(go.Scatter(x=x_data, y=y_pos, **common_fill), row=row, col=col, secondary_y=True)
        fig.add_trace(go.Scatter(x=x_data, y=y_neg, **common_fill), row=row, col=col, secondary_y=True)

