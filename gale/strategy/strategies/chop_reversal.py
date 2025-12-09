
from .base import BaseStrategy

class ChopReversalStrategy(BaseStrategy):
    """
    逆勢震盪突破策略 (Counter-Trend Chop Reversal).
    適合夜盤或震盪盤勢。
    邏輯：
    1. Entry: High Velocity + High Imbalance (Fading the move).
    2. Exit: Breakeven Stop + Fixed Target.
    """
    def __init__(self, pos_manager):
        super().__init__(pos_manager)
        self.watermark = None
        
        # Parameters
        self.vel_threshold = 25
        self.imb_threshold = 0.6
        
        self.hard_stop = 20
        self.target_profit = 40
        self.breakeven_trigger = 20
        self.breakeven_cushion = 10 # 賺便當錢
        
    def on_tick(self, timestamp, market_data, indicators):
        current_close = market_data['close']
        velocity = indicators['velocity']
        imbalance = indicators['imbalance']
        
        pos = self.pos_manager.get_position(self.symbol)
        
        # 1. Entry Logic
        if pos.qty == 0:
            if velocity > self.vel_threshold and imbalance < -self.imb_threshold:
                self.logger.info(f"🔥 Signal BUY: Vel={velocity:.1f}, Imb={imbalance:.2f}, Price={current_close}")
                oid = self.pos_manager.place_order(self.symbol, 'BUY', 1, price=current_close)
                self.pos_manager.on_fill(oid, fill_price=current_close, fill_qty=1)
                
            elif velocity > self.vel_threshold and imbalance > self.imb_threshold:
                self.logger.info(f"❄️ Signal SELL: Vel={velocity:.1f}, Imb={imbalance:.2f}, Price={current_close}")
                oid = self.pos_manager.place_order(self.symbol, 'SELL', 1, price=current_close)
                self.pos_manager.on_fill(oid, fill_price=current_close, fill_qty=1)
                
        # 2. Exit Logic (Chop Mode)
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
                pnl = (current_close - pos.avg_price) * pos.qty
                self.logger.info(f"🏃 {exit_reason}. P&L: {pnl:.1f}. Close {pos.qty} @ {current_close}")
                
                side = 'SELL' if pos.qty > 0 else 'BUY'
                oid = self.pos_manager.place_order(self.symbol, side, abs(pos.qty), price=current_close)
                self.pos_manager.on_fill(oid, fill_price=current_close, fill_qty=abs(pos.qty))
                
                # Reset
                self.watermark = None
