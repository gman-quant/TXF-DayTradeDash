from collections import deque
import threading
import numpy as np

class MicrostructureEngine:
    """
    實時計算市場微結構指標 (Velocity & Imbalance)。
    使用 Rolling Window (Sliding Window) 演算法。
    """
    def __init__(self, window_seconds: int = 3):
        self.window_seconds = window_seconds
        self.window_ms = window_seconds * 1000
        
        # Deque storing tuples: (timestamp_ms, volume, tick_type)
        self.ticks = deque()
        
        # Rolling Sums (State)
        self.current_volume = 0
        self.current_buy_vol = 0
        self.current_sell_vol = 0
        
        self.lock = threading.Lock()
        
        # Calculated Metrics
        self.velocity = 0.0      # Volume per second
        self.imbalance = 0.0     # -1.0 to 1.0
        
    def update(self, timestamp_ms: int, volume: int, tick_type: int):
        """
        Thread-safe update.
        """
        with self.lock:
            # 1. Add new tick
            self.ticks.append((timestamp_ms, volume, tick_type))
            
            # Update sums
            self.current_volume += volume
            if tick_type == 1: # Buy
                self.current_buy_vol += volume
            elif tick_type == 2: # Sell
                self.current_sell_vol += volume
                
            # 2. Key Step: Remove expired ticks from left
            # (Sliding Window)
            cutoff_time = timestamp_ms - self.window_ms
            
            while self.ticks and self.ticks[0][0] < cutoff_time:
                old_ts, old_vol, old_type = self.ticks.popleft()
                
                # Deduct from sums
                self.current_volume -= old_vol
                if old_type == 1:
                    self.current_buy_vol -= old_vol
                elif old_type == 2:
                    self.current_sell_vol -= old_vol
                    
            # 3. Calculate Metrics
            # Velocity = Total Volume in Window / Window Seconds
            # Note: This is a "moving average of speed"
            self.velocity = self.current_volume / self.window_seconds
            
            # Imbalance = (Buy - Sell) / (Buy + Sell)
            total_active = self.current_buy_vol + self.current_sell_vol
            if total_active > 0:
                self.imbalance = (self.current_buy_vol - self.current_sell_vol) / total_active
            else:
                self.imbalance = 0.0

    def get_metrics(self):
        """
        Return (velocity, imbalance)
        """
        with self.lock:
            return self.velocity, self.imbalance
