
"""
gale/strategy/strategies/chop_reversal.py

實現「逆勢震盪突破策略 (Chop Reversal)」。
繼承自 BaseStrategy。
"""

from typing import Dict, Any, Optional
from .base import BaseStrategy

class ChopReversalStrategy(BaseStrategy):
    """
    逆勢震盪突破策略 (Counter-Trend Chop Reversal).
    
    適合夜盤或震盪盤勢。
    
    核心邏輯 (Core Logic):
    1. 進場 (Entry): 
       - 高流速 (Velocity > 25) 
       - 極端失衡 (Imbalance > 0.6 或 < -0.6)
       - 概念：在極速行情末端進行「摸頭猜底」。
       
    2. 出場 (Exit): 
       - 保本止損 (Breakeven Stop): 獲利 > 20 點後，止損移至成本 + 10 點。
       - 固定停利 (Fixed Target): 40 點。
       - 硬止損 (Hard Stop): 20 點。
    """
    
    def __init__(self, pos_manager):
        super().__init__(pos_manager)
        
        # 狀態變數 (State Variables)
        self.watermark: Optional[float] = None  # 紀錄持倉期間的最高/最低價
        
        # 參數設置 (Parameters)
        self.vel_threshold: float = 25.0
        self.imb_threshold: float = 0.6
        
        self.hard_stop: float = 20.0
        self.target_profit: float = 40.0
        self.breakeven_trigger: float = 20.0
        self.breakeven_cushion: float = 10.0 # 賺便當錢
        
    def on_tick(self, timestamp: float, market_data: Dict[str, Any], indicators: Dict[str, Any]) -> None:
        """
        處理每一筆 Tick 數據。
        """
        current_close = market_data['close']
        velocity = indicators['velocity']
        imbalance = indicators['imbalance']
        
        pos = self.pos_manager.get_position(self.symbol)
        
        # 1. 進場邏輯 (Entry Logic)
        if pos.qty == 0:
            if velocity > self.vel_threshold and imbalance < -self.imb_threshold:
                self.logger.info(f"🔥 Signal BUY: Vel={velocity:.1f}, Imb={imbalance:.2f}, Price={current_close}")
                oid = self.pos_manager.place_order(self.symbol, 'BUY', 1, price=current_close)
                self.pos_manager.on_fill(oid, fill_price=current_close, fill_qty=1)
                
            elif velocity > self.vel_threshold and imbalance > self.imb_threshold:
                self.logger.info(f"❄️ Signal SELL: Vel={velocity:.1f}, Imb={imbalance:.2f}, Price={current_close}")
                oid = self.pos_manager.place_order(self.symbol, 'SELL', 1, price=current_close)
                self.pos_manager.on_fill(oid, fill_price=current_close, fill_qty=1)
                
        # 2. 出場邏輯 (Exit Logic - Chop Mode)
        else:
            # Init Watermark
            if self.watermark is None:
                self.watermark = pos.avg_price
                
            # Update Watermark
            if pos.qty > 0:
                self.watermark = max(self.watermark, current_close)
            else:
                self.watermark = min(self.watermark, current_close)
                
            entry = pos.avg_price
            should_exit = False
            exit_reason = ""
            
            # Dynamic Stop Calculation
            if pos.qty > 0: # Long
                stop_price = entry - self.hard_stop
                if self.watermark >= (entry + self.breakeven_trigger):
                    stop_price = max(stop_price, entry + self.breakeven_cushion)
                    
                if current_close <= stop_price:
                    should_exit = True
                    exit_reason = f"Stop Hit ({stop_price})"
                elif current_close >= (entry + self.target_profit):
                    should_exit = True
                    exit_reason = f"Target Hit (+{self.target_profit})"
                    
            else: # Short
                stop_price = entry + self.hard_stop
                if self.watermark <= (entry - self.breakeven_trigger):
                    stop_price = min(stop_price, entry - self.breakeven_cushion)
                    
                if current_close >= stop_price:
                    should_exit = True
                    exit_reason = f"Stop Hit ({stop_price})"
                elif current_close <= (entry - self.target_profit):
                    should_exit = True
                    exit_reason = f"Target Hit (+{self.target_profit})"
            
            if should_exit:
                # 簡單計算 P&L
                pnl = (current_close - pos.avg_price) * pos.qty
                self.logger.info(f"🏃 {exit_reason}. P&L: {pnl:.1f}. Close {pos.qty} @ {current_close}")
                
                side = 'SELL' if pos.qty > 0 else 'BUY'
                oid = self.pos_manager.place_order(self.symbol, side, abs(pos.qty), price=current_close)
                self.pos_manager.on_fill(oid, fill_price=current_close, fill_qty=abs(pos.qty))
                
                # Reset
                self.watermark = None
