
"""
gale/strategy/strategies/base.py

定義策略的基礎介面 (Interface)。
所有的策略模組都應該繼承此類別。
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
from gale.utils.log_utils import setup_logger

logger = setup_logger("BaseStrategy")

class BaseStrategy(ABC):
    """
    策略基礎類別 (Base Strategy Class)。
    """
    def __init__(self, pos_manager):
        """
        初始化策略。
        
        Args:
            pos_manager: PositionManager 實例，用於下單與部位管理。
        """
        self.pos_manager = pos_manager
        self.symbol = 'TXF'
        self.logger = logger
        
    @abstractmethod
    def on_tick(self, timestamp: float, market_data: Dict[str, Any], indicators: Dict[str, Any]) -> None:
        """
        當接收到新的 Tick 數據時觸發。
        
        Args:
            timestamp (float): 當前 Tick 的時間戳 (ms).
            market_data (Dict): 市場數據 (price, volume, etc.).
            indicators (Dict): 計算後的技術指標 (velocity, imbalance, etc.).
        """
        pass
