# core/indicator_manager.py

import numpy as np
import core.numba_engine as engine
from config.indicator_config import INDICATORS_SETUP
from config.settings import TIMEFRAMES

class IndicatorManager:
    """
    指標管理器 (全 NumPy RingBuffer 版)。
    
    核心職責：
    1. 讀取 Config，動態決定要計算哪些指標。
    2. 接收 RingBuffer 快照，進行指標計算 (使用 Numba)。
    3. 進行 K 線 (Candle) 的實時聚合。
    4. 提供 O(1) 的狀態查詢 (count, latest_timestamp) 給 Dashboard Server。
    """
    
    def __init__(self, buffer_capacity):
        self.capacity = buffer_capacity

        # 紀錄當前的 head 位置 (CoreProcessor 傳過來的寫入游標)
        self.current_head = 0
        
        # ==========================================
        # 1. 基礎歷史數據容器 (固定長度 Array)
        # ==========================================
        self.history = {
            "timestamp": np.zeros(buffer_capacity, dtype=np.int64),
            "price": np.zeros(buffer_capacity, dtype=np.int64),
        }
        
        # 2. 🆕 多週期 K 線容器
        # 結構變更： self.candles['1m']['open'] ...
        self.candles = {}
        self.current_candles = {} # 暫存各週期的當前 K 線
        
        # 初始化所有週期
        for tf_name in TIMEFRAMES:
            self.candles[tf_name] = {
                'time': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': []
            }
            self.current_candles[tf_name] = {}

        # ==========================================
        # 3. ⚡️ 預先綁定 (Pre-binding) 邏輯
        # 目的：在 __init__ 階段解析所有函數與參數
        # ==========================================
        self.executors = []
        
        for ind in INDICATORS_SETUP:
            ind_id = ind['id']
            self.history[ind_id] = np.zeros(buffer_capacity, dtype=np.float64)
            
            # A. 預先抓取 Numba 函數
            func_name = ind['func']
            try:
                calc_func = getattr(engine, func_name)
            except AttributeError:
                print(f"❌ Error: Function '{func_name}' not found in numba_engine.")
                continue
            
            # B. 預先解析輸入參數映射 (Input Mapping)
            input_indices = []
            for input_name in ind['inputs']:
                # --- 基礎數據 ---
                if input_name == 'close':        input_indices.append(0)
                elif input_name == 'volume':     input_indices.append(1)
                elif input_name == 'type':       input_indices.append(2)
                elif input_name == 'timestamp':  input_indices.append(3)
                elif input_name == 'underlying_price': input_indices.append(4)
                
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

    # ==========================================
    # 🔥 新增 Helper Methods 供 Dashboard 使用
    # ==========================================
    
    @property
    def count(self):
        """
        返回目前有效資料的長度。
        邏輯：檢查最後一個位置是否為 0 (假設 timestamp 0 代表空值)。
        如果最後一個位置有值，代表 Buffer 已滿 (Wrapped)，長度為 capacity。
        否則長度為 current_head。
        """
        if self.history['timestamp'][-1] != 0:
            return self.capacity
        return self.current_head

    def get_latest_timestamp(self):
        """
        快速取得最新時間戳，不需複製整個 Array。
        """
        cnt = self.count
        if cnt == 0:
            return 0.0
        
        # 最新數據在 head - 1 的位置
        # 如果 head 是 0 (且 buffer 滿了)，最新數據就在 capacity - 1
        idx = self.current_head - 1
        if idx < 0:
            idx = self.capacity - 1
            
        return self.history['timestamp'][idx]
    
    def get_linear_snapshot(self, key):
        """
        將環狀 RingBuffer 解開為線性的 Array 供前端繪圖。
        這是一個 View Copy 操作，Dash Server 需要它。
        """
        arr = self.history[key]
        head = self.current_head
        
        # 判斷是否滿載 (最後一個位置有值)
        is_full = (self.history['timestamp'][-1] != 0)
        
        if not is_full:
            # 沒滿，直接回傳前面的部分
            return arr[:head]
        else:
            # 滿了，把 [head:] (舊) 和 [:head] (新) 接起來
            return np.concatenate((arr[head:], arr[:head]))


    def _update_candles(self, tick_time_ms, price, volume):
        """
        🆕 同時更新所有週期的 K 線
        """
        for tf_name, period_ms in TIMEFRAMES.items():
            # 計算該週期的 Bucket Time
            bucket_time_ms = (tick_time_ms // period_ms) * period_ms
            
            curr = self.current_candles[tf_name]
            storage = self.candles[tf_name]
            
            if not curr or curr['time'] != bucket_time_ms:
                # 1. 關閉舊 K 線
                if curr:
                    for k, v in curr.items():
                        if k != 'new_tick': storage[k].append(v)
                
                # 2. 開新 K 線
                self.current_candles[tf_name] = {
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
                curr['high'] = max(curr['high'], price)
                curr['low'] = min(curr['low'], price)
                curr['close'] = price
                curr['volume'] += volume
                curr['new_tick'] = True


    def on_tick(self, snapshot_tuple):
        """
        當 CoreProcessor 收到新 Tick 時呼叫此函數。
        
        def get_snapshot(self):
            return (
                self.close,             # 0
                self.volume,            # 1
                self.tick_type,         # 2
                self.timestamp,         # 3
                self.underlying_price,  # 4
                self.cum_volume,        # 5
                self.cum_pv,            # 6
                self.cum_close,         # 7
                self.session_high,      # 8
                self.session_low,       # 9
                self.total_volume,      # 10
                self.cum_buy_vol,       # 11
                self.cum_sell_vol,      # 12
                self.head               # 13
            )
        """
        # snapshot_tuple 的最後一個是 head
        head = snapshot_tuple[-1]
        
        # 更新內部的 head 紀錄，供 get_linear_snapshot 使用
        self.current_head = head
        
        # 計算寫入位置 (snapshot 裡的 head 是"下一個空位"，所以當前數據在 head-1)
        curr_idx = head - 1
        if curr_idx < 0: curr_idx = self.capacity - 1
        
        # 從 snapshot 拿出基礎數據 (直接用 index 取，不做解包變數，省效能)
        # index 0=close, 3=time
        close_val = snapshot_tuple[0][curr_idx]
        time_val  = snapshot_tuple[3][curr_idx]
        vol_val   = snapshot_tuple[1][curr_idx]
        # print(f"DEBUG: Vol={snapshot_tuple[1][curr_idx]}, Type={snapshot_tuple[2][curr_idx]}")

        # ==========================================
        # 3. 更新基礎歷史數據 (直接寫入 Array)
        # ==========================================
        self.history["timestamp"][curr_idx] = time_val
        self.history["price"][curr_idx]     = close_val
        
        # ==========================================
        # 4. 執行 Numba 計算 (直接寫入 Array)
        # ==========================================
        for ind_id, calc_func, input_indices, fixed_args in self.executors:
            # 動態組裝參數 (從 snapshot 裡拿 Array 參照)
            dynamic_args = [snapshot_tuple[i] for i in input_indices]
            
            # 呼叫 Numba
            val = calc_func(*dynamic_args, head, *fixed_args)
            
            # 🔥 直接填坑
            self.history[ind_id][curr_idx] = val

        # ==========================================
        # 5. 更新 K 線 (邏輯不變)
        # ==========================================
        self._update_candles(time_val, close_val, vol_val)
        
        