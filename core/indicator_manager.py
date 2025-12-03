# core/indicator_manager.py

import numpy as np
import core.numba_engine as engine
from config.indicator_config import INDICATORS_SETUP

class IndicatorManager:
    """
    指標管理器 (效能優化與全欄位版)。
    
    核心職責：
    1. 讀取 Config，動態決定要計算哪些指標。
    2. 接收 RingBuffer 快照，進行指標計算 (使用 Numba)。
    3. 進行 K 線 (Candle) 的實時聚合。
    4. 管理歷史數據列表 (History List) 供前端繪圖。
    """
    
    def __init__(self, buffer_capacity):
        self.capacity = buffer_capacity
        
        # ==========================================
        # 1. 基礎歷史數據容器
        # ==========================================
        self.history = {
            "timestamp": [],
            "price": [],
        }
        
        # ==========================================
        # 2. K 線相關設定
        # ==========================================
        self.candles = {
            'time': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': []
        }
        self.current_candle = {} # 暫存當前正在形成的 K 線
        # K 線週期：在此設定 (例如 15分鐘 = 15 * 60 * 1000)
        self.aggregation_period_ms = 5 * 1000 

        # ==========================================
        # 3. ⚡️ 預先綁定 (Pre-binding) 邏輯
        # 目的：在 __init__ 階段解析所有函數與參數
        # ==========================================
        self.executors = []
        
        for ind in INDICATORS_SETUP:
            ind_id = ind['id']
            self.history[ind_id] = []
            
            # A. 預先抓取 Numba 函數
            func_name = ind['func']
            try:
                calc_func = getattr(engine, func_name)
            except AttributeError:
                print(f"❌ Error: Function '{func_name}' not found in numba_engine.")
                continue
            
            # B. 預先解析輸入參數映射 (Input Mapping)
            # ⚠️ 這裡的 Index 必須與 on_tick 中的 data_sources Tuple 順序完全一致！
            input_indices = []
            for input_name in ind['inputs']:
                # --- 基礎數據 ---
                if input_name == 'close':        input_indices.append(0)
                elif input_name == 'volume':     input_indices.append(1)
                elif input_name == 'type':       input_indices.append(2)
                elif input_name == 'timestamp':  input_indices.append(3)
                elif input_name == 'underlying_price': input_indices.append(4) # 注意 Config 命名
                elif input_name == 'underlying': input_indices.append(4)       # 兼容寫法
                
                # --- 累積數據 (O(1) 計算用) ---
                elif input_name == 'cum_volume': input_indices.append(5)
                elif input_name == 'cum_pv':     input_indices.append(6)
                elif input_name == 'cum_close':  input_indices.append(7)
                
                # --- 狀態數據 (Stateful) ---
                elif input_name == 'session_high': input_indices.append(8)
                elif input_name == 'session_low':  input_indices.append(9)
                elif input_name == 'total_volume': input_indices.append(10)
                
                # --- 籌碼數據 (CVD/Delta) ---
                elif input_name == 'cum_buy_vol':  input_indices.append(11)
                elif input_name == 'cum_sell_vol': input_indices.append(12)
            
            # C. 預先準備固定參數
            fixed_args = tuple(ind['args'] + [self.capacity])
            
            # 將執行所需資訊打包
            self.executors.append((ind_id, calc_func, input_indices, fixed_args))

    def _update_candles(self, tick_time_ms, price, volume):
        """
        K 線聚合邏輯
        """
        bucket_time_ms = (tick_time_ms // self.aggregation_period_ms) * self.aggregation_period_ms
        
        if not self.current_candle or self.current_candle['time'] != bucket_time_ms:
            # 1. 關閉並儲存舊 K 線
            if self.current_candle:
                for k, v in self.current_candle.items():
                    if k != 'new_tick': self.candles[k].append(v)
            
            # 2. 初始化新 K 線
            self.current_candle = {
                'time': bucket_time_ms,
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': volume,
                'new_tick': True 
            }
        else:
            # 3. 更新當前 K 線
            self.current_candle['high'] = max(self.current_candle['high'], price)
            self.current_candle['low'] = min(self.current_candle['low'], price)
            self.current_candle['close'] = price
            self.current_candle['volume'] += volume
            self.current_candle['new_tick'] = True

    def on_tick(self, snapshot_tuple):
        """
        當 CoreProcessor 收到新 Tick 時呼叫此函數。
        """
        # ==========================================
        # 1. 解包 Snapshot
        # ⚠️ 順序必須與 RingBuffer.get_snapshot() 完全一致
        # ==========================================
        (close_arr, vol_arr, type_arr, time_arr, underlying_arr, 
         cum_vol_arr, cum_pv_arr, cum_close_arr, 
         session_high_arr, session_low_arr, total_vol_arr, 
         cum_buy_arr, cum_sell_arr, # Index 11, 12
         head) = snapshot_tuple
        
        # ==========================================
        # 2. 建立數據源 Tuple
        # ⚠️ 順序必須與 __init__ 的 input_indices 映射一致
        # ==========================================
        data_sources = (
            close_arr,       # Index 0
            vol_arr,         # Index 1
            type_arr,        # Index 2
            time_arr,        # Index 3
            underlying_arr,  # Index 4
            cum_vol_arr,     # Index 5
            cum_pv_arr,      # Index 6
            cum_close_arr,   # Index 7
            session_high_arr,# Index 8
            session_low_arr, # Index 9
            total_vol_arr,   # Index 10
            cum_buy_arr,     # Index 11
            cum_sell_arr     # Index 12
        )
        
        # 計算當前指針位置
        curr_idx = head - 1
        if curr_idx < 0: curr_idx = self.capacity - 1
            
        # ==========================================
        # 3. 更新基礎歷史數據
        # ==========================================
        self.history["timestamp"].append(time_arr[curr_idx])
        self.history["price"].append(close_arr[curr_idx])
        
        # ==========================================
        # 4. ⚡️ 執行 Numba 計算 (極速迴圈)
        # ==========================================
        for ind_id, calc_func, input_indices, fixed_args in self.executors:
            # 動態組裝參數
            dynamic_args = [data_sources[i] for i in input_indices]
            
            # 呼叫 Numba
            val = calc_func(*dynamic_args, head, *fixed_args)
            
            # 存入歷史
            self.history[ind_id].append(val)

        # ==========================================
        # 5. 更新 K 線與記憶體管理
        # ==========================================
        self._update_candles(time_arr[curr_idx], close_arr[curr_idx], vol_arr[curr_idx])
        
        if len(self.history["timestamp"]) > 200000:
            for key in self.history:
                self.history[key].pop(0)
