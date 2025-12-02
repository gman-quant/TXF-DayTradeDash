# core/indicator_manager.py

import numpy as np
import core.numba_engine as engine
from config.indicator_config import INDICATORS_SETUP

class IndicatorManager:
    """
    指標管理器 (效能優化版)。
    """
    def __init__(self, buffer_capacity):
        self.capacity = buffer_capacity
        
        self.history = {
            "timestamp": [],
            "price": [],
        }
        
        self.candles = {
            'time': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': []
        }
        self.current_candle = {}
        self.aggregation_period_ms = 5 * 1000 

        self.executors = []
        
        for ind in INDICATORS_SETUP:
            ind_id = ind['id']
            self.history[ind_id] = []
            
            func_name = ind['func']
            try:
                calc_func = getattr(engine, func_name)
            except AttributeError:
                print(f"❌ Error: Function '{func_name}' not found.")
                continue
            
            # ⬇️ 修正索引映射 (必須與 on_tick 的 data_sources 順序一致)
            input_indices = []
            for input_name in ind['inputs']:
                if input_name == 'close': input_indices.append(0)
                elif input_name == 'volume': input_indices.append(1)
                elif input_name == 'type': input_indices.append(2)
                elif input_name == 'timestamp': input_indices.append(3)
                elif input_name == 'underlying_price': input_indices.append(4)
                
                # 補上累積欄位 (用於 O(1) 計算)
                elif input_name == 'cum_volume': input_indices.append(5)
                elif input_name == 'cum_pv': input_indices.append(6)
                elif input_name == 'cum_close': input_indices.append(7)
                
                # 補上狀態欄位
                elif input_name == 'session_high': input_indices.append(8)
                elif input_name == 'session_low': input_indices.append(9)
                elif input_name == 'total_volume': input_indices.append(10)
            
            fixed_args = tuple(ind['args'] + [self.capacity])
            self.executors.append((ind_id, calc_func, input_indices, fixed_args))

    def _update_candles(self, tick_time_ms, price, volume):
        bucket_time_ms = (tick_time_ms // self.aggregation_period_ms) * self.aggregation_period_ms
        if not self.current_candle or self.current_candle['time'] != bucket_time_ms:
            if self.current_candle:
                for k, v in self.current_candle.items():
                    if k != 'new_tick': self.candles[k].append(v)
            self.current_candle = {'time': bucket_time_ms, 'open': price, 'high': price, 'low': price, 'close': price, 'volume': volume, 'new_tick': True}
        else:
            self.current_candle['high'] = max(self.current_candle['high'], price)
            self.current_candle['low'] = min(self.current_candle['low'], price)
            self.current_candle['close'] = price
            self.current_candle['volume'] += volume
            self.current_candle['new_tick'] = True

    def on_tick(self, snapshot_tuple):
        """
        極速版 on_tick
        """
        # ⬇️ 修正解包：必須完整接收 RingBuffer 送來的所有欄位 (共 12 個)
        (close_arr, vol_arr, type_arr, time_arr, underlying_arr, 
         cum_vol_arr, cum_pv_arr, cum_close_arr, # <--- 補上這三個
         session_high_arr, session_low_arr, total_vol_arr, 
         head) = snapshot_tuple
        
        # ⬇️ 修正 Data Sources Tuple (順序對應 __init__)
        data_sources = (
            close_arr,       # 0
            vol_arr,         # 1
            type_arr,        # 2
            time_arr,        # 3
            underlying_arr,  # 4
            cum_vol_arr,     # 5 (新)
            cum_pv_arr,      # 6 (新)
            cum_close_arr,   # 7 (新)
            session_high_arr,# 8
            session_low_arr, # 9
            total_vol_arr    # 10
        )
        
        curr_idx = head - 1
        if curr_idx < 0: curr_idx = self.capacity - 1
            
        self.history["timestamp"].append(time_arr[curr_idx])
        self.history["price"].append(close_arr[curr_idx])
        
        for ind_id, calc_func, input_indices, fixed_args in self.executors:
            dynamic_args = [data_sources[i] for i in input_indices]
            val = calc_func(*dynamic_args, head, *fixed_args)
            self.history[ind_id].append(val)

        self._update_candles(time_arr[curr_idx], close_arr[curr_idx], vol_arr[curr_idx])
        
        if len(self.history["timestamp"]) > 200000:
            for key in self.history:
                self.history[key].pop(0)
