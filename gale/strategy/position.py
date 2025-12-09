
import logging
from dataclasses import dataclass, field
from typing import List, Optional
import datetime

logger = logging.getLogger("PositionManager")

@dataclass
class Order:
    id: str
    symbol: str
    side: str      # 'BUY' or 'SELL'
    order_type: str # 'MARKET' or 'LIMIT'
    price: float   # Request price (0 for market)
    qty: int
    status: str    # 'PENDING', 'FILLED', 'CANCELLED'
    created_at: float
    filled_at: float = 0.0
    fill_price: float = 0.0

@dataclass
class Position:
    symbol: str
    qty: int = 0          # + for Long, - for Short
    avg_price: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    
    def update(self, side: str, fill_qty: int, fill_price: float):
        """
        Update position based on a filled execution.
        Calculates Realized P&L if reducing position.
        Updates Avg Price if increasing position.
        """
        fill_qty_signed = fill_qty if side == 'BUY' else -fill_qty
        
        # Case 1: Increasing Position (or opening)
        # Same sign or current qty is 0
        if self.qty == 0 or (self.qty > 0 and fill_qty_signed > 0) or (self.qty < 0 and fill_qty_signed < 0):
            total_cost = (self.qty * self.avg_price) + (fill_qty_signed * fill_price)
            self.qty += fill_qty_signed
            if self.qty != 0:
                self.avg_price = total_cost / self.qty
            else:
                self.avg_price = 0.0
                
        # Case 2: Reducing Position (or closing/reversing)
        else:
            # We are closing some magnitude.
            # Realized P&L = (Exit Price - Entry Price) * Qty * Direction
            # Direction is defined by the position we are CLOSING.
            # If Long (qty>0), we Sell (fill<0). P&L = (SellPrice - AvgPrice) * Abs(FillQty)
            # If Short (qty<0), we Buy (fill>0). P&L = (AvgPrice - BuyPrice) * Abs(FillQty)
            
            # Determine how much is closing vs reversing
            # Example: Long 10, Sell 15. Close 10, Open Short 5.
            
            qty_to_close = 0
            qty_to_open = 0
            
            if abs(fill_qty_signed) <= abs(self.qty):
                qty_to_close = abs(fill_qty_signed)
            else:
                qty_to_close = abs(self.qty)
                qty_to_open = abs(fill_qty_signed) - abs(self.qty)
                # Sign of new open is same as fill
            
            # Calc P&L on closed portion
            trade_pnl = 0
            if self.qty > 0: # Long closing
                trade_pnl = (fill_price - self.avg_price) * qty_to_close
            else: # Short closing
                trade_pnl = (self.avg_price - fill_price) * qty_to_close
            
            # TXF Multiplier = 200 TWD per point (Simplification: assume 1:1 for now, or inject multiplier)
            # Let's assume raw points for now, user can multiply later.
            self.realized_pnl += trade_pnl
            
            # Update Qty
            self.qty += fill_qty_signed # Simple addition handles the math correctly for the qty itself
            
            # If we reversed, the avg price resets to this fill price for the remainder
            if (self.qty > 0 and fill_qty_signed > 0) or (self.qty < 0 and fill_qty_signed < 0):
                 # Meaning we flipped side
                 self.avg_price = fill_price

class PositionManager:
    def __init__(self, multiplier: float = 200.0):
        self.positions = {} # symbol -> Position
        self.orders = {}    # id -> Order
        self.multiplier = multiplier # TXF = 200
        
    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]
    
    def on_fill(self, order_id: str, fill_price: float, fill_qty: int):
        if order_id not in self.orders:
            logger.error(f"Order {order_id} not found")
            return
        
        order = self.orders[order_id]
        order.status = 'FILLED'
        order.fill_price = fill_price
        order.filled_at = datetime.datetime.now().timestamp()
        
        pos = self.get_position(order.symbol)
        pos.update(order.side, fill_qty, fill_price)
        
        logger.info(f"Filled {order.side} {fill_qty} @ {fill_price}. New Pos: {pos.qty} @ {pos.avg_price:.2f}. P&L: {pos.realized_pnl:.2f}")

    def place_order(self, symbol: str, side: str, qty: int, order_type: str = 'MARKET', price: float = 0.0) -> str:
        # Simple ID generation
        import uuid
        order_id = str(uuid.uuid4())[:8]
        
        order = Order(
            id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            price=price,
            qty=qty,
            status='PENDING',
            created_at=datetime.datetime.now().timestamp()
        )
        self.orders[order_id] = order
        logger.info(f"Order Placed: {side} {qty} {symbol} @ {order_type} {price}")
        return order_id

    def update_market_price(self, symbol: str, current_price: float):
        """
        Update Unrealized P&L based on current market price.
        """
        if symbol not in self.positions:
            return
            
        pos = self.positions[symbol]
        if pos.qty == 0:
            pos.unrealized_pnl = 0
            return
            
        if pos.qty > 0: # Long
            diff = current_price - pos.avg_price
        else: # Short
            diff = pos.avg_price - current_price
            
        pos.unrealized_pnl = diff * abs(pos.qty) * self.multiplier
