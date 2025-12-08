import plotly.graph_objects as go
from config.ui_theme import UI_COLOR

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
