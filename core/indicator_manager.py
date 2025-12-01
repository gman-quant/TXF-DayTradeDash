# core/indicator_manager.py

import numpy as np
from core.numba_engine import calc_vwap, calc_price_change

class IndicatorManager:
    """
    指標管理器。
    職責：
    1. 定義要計算哪些指標 (Configuration)。
    2. 從 RingBuffer 拿 Snapshot。
    3. 呼叫 NumbaEngine 計算。
    4. 儲存歷史結果供前端 (Dash) 使用。
    """
    def __init__(self, buffer_capacity):
        self.capacity = buffer_capacity
        
        # 歷史數據儲存庫 (Dynamic Dictionary)
        # Key: 指標名稱, Value: Python List (為了快速 append 和 Plotly 相容性)
        self.history = {
            "timestamp": [],      # X 軸
            "price": [],          # 原始價格
            "VWAP_50": [],        # 50筆 Tick VWAP
            "VWAP_200": [],       # 200筆 Tick VWAP
            "Momentum_180": [],    # 10筆 Tick 的價格變化
        }
        self.candles = {
            'time': [],
            'open': [],
            'high': [],
            'low': [],
            'close': [],
            'volume': []
        }
        self.current_candle = {} # 暫存當前正在構建的 K 線
        self.aggregation_period_ms = 60 * 1000 # 1 秒 K 線 (1000 毫秒)

    def _update_candles(self, tick_time_ms, price, volume):
        """
        根據 Tick 數據，更新當前 K 線或開新 K 線。
        """
        # 將毫秒時間戳對齊到 1 秒的開始 (例如 15:00:00.500 -> 15:00:00.000)
        bucket_time_ms = (tick_time_ms // self.aggregation_period_ms) * self.aggregation_period_ms

        if not self.current_candle or self.current_candle['time'] != bucket_time_ms:
            # 1. 關閉舊 K 線 (如果存在)
            if self.current_candle:
                # 將上一根 K 線推入最終歷史列表
                for k, v in self.current_candle.items():
                    if k != 'new_tick': # 排除暫存狀態
                        self.candles[k].append(v)
                
            # 2. 開新 K 線
            self.current_candle = {
                'time': bucket_time_ms,
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': volume,
                'new_tick': True # 標記這是新的 K 線
            }
        else:
            # 3. 更新當前 K 線
            self.current_candle['high'] = max(self.current_candle['high'], price)
            self.current_candle['low'] = min(self.current_candle['low'], price)
            self.current_candle['close'] = price
            self.current_candle['volume'] += volume
            self.current_candle['new_tick'] = True # 標記 K 線有更新

    def on_tick(self, snapshot_tuple):
        """
        當 CoreProcessor 收到新 Tick 時呼叫此函數。
        snapshot_tuple: (close, volume, tick_type, timestamp, underlying, head)
        """
        # 解包 Snapshot (必須與 RingBuffer.get_snapshot 順序一致)
        close_arr, vol_arr, type_arr, time_arr, underlying_arr, head = snapshot_tuple
        
        # 1. 取得當前最新數據 (head - 1)
        # 注意：RingBuffer 的 head 指向「下一個寫入位置」，所以最新數據在 head-1
        curr_idx = head - 1
        if curr_idx < 0: 
            curr_idx = self.capacity - 1
            
        curr_time = time_arr[curr_idx]
        curr_price = close_arr[curr_idx]
        
        # 2. 呼叫 Numba 計算指標
        # 計算 50 筆 VWAP
        vwap_50 = calc_vwap(close_arr, vol_arr, head, 50, self.capacity)
        # 計算 200 筆 VWAP
        vwap_200 = calc_vwap(close_arr, vol_arr, head, 200, self.capacity)
        # 計算 180 筆 Momentum
        mom_180 = calc_price_change(close_arr, head, 180, self.capacity)

        # 3. 更新歷史數據 (Append to List)
        # 這裡使用 Python List append，雖然不是最快，但對於前端繪圖最方便
        # 在高頻交易中，通常會在這裡做降頻 (Down-sampling)，例如每 100ms 才存一次
        # 但為了演示，我們先逐筆存
        self.history["timestamp"].append(curr_time)
        self.history["price"].append(curr_price)
        self.history["VWAP_50"].append(vwap_50)
        self.history["VWAP_200"].append(vwap_200)
        self.history["Momentum_180"].append(mom_180)

        # 2. ⬇️ 呼叫 K 線聚合邏輯
        self._update_candles(curr_time, curr_price, vol_arr[curr_idx])
        
        # 簡單的記憶體管理：限制 List 長度 (例如只存最近 200000 點給圖表用)
        # 實際專案中會在 DataHistorian 做這件事
        if len(self.history["timestamp"]) > 200000:
            for key in self.history:
                self.history[key].pop(0)

        return vwap_50, mom_180 # 回傳一些數據供 Log 用