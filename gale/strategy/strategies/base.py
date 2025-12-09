
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("Strategy")

class BaseStrategy(ABC):
    def __init__(self, pos_manager):
        self.pos_manager = pos_manager
        self.symbol = 'TXF'
        self.logger = logger
        
    @abstractmethod
    def on_tick(self, timestamp, market_data, indicators):
        """
        Subclasses must implement this.
        market_data: dict containing current price, volume etc.
        indicators: dict containing calculated indicators (velocity, imbalance, etc.)
        """
        pass
