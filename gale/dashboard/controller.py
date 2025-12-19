
"""
gale.dashboard.controller.py

功能：
1. 作為 Controller 串接 State (Model) 與 Chart (View)。
2. 提供給 server.py 呼叫的單一入口。

Refactored: 2025-12-10
"""

import logging
from . import data_model
from . import chart

# 設置 Logger
logger = logging.getLogger("DashLogic")

def process_market_data(indicator_manager, lookback_count, timeframe):
    """
    從 RingBuffer 取得數據並進行預處理。
    Delegate to: gale.dashboard.data_model.process_market_data
    """
    try:
        data = data_model.process_market_data(indicator_manager, lookback_count, timeframe)
        return data
    except Exception as e:
        logger.error(f"Error processing market data: {e}", exc_info=True)
        return None

def build_combined_figure(data):
    """
    根據處理後的數據繪製 Plotly 圖表。
    Delegate to: gale.dashboard.chart.build_combined_figure
    """
    try:
        fig = chart.build_combined_figure(data)
        return fig
    except Exception as e:
        logger.error(f"Error building figure: {e}", exc_info=True)
        return chart.create_blank_figure()
