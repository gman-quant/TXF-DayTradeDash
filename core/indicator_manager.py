# core/indicator_manager.py
import numpy as np
from core import numba_engine
from core.numba_engine import update_volume_profile, get_profile_stats
# Numba 引擎別名
engine = numba_engine
from config.indicator_config import INDICATORS_SETUP
from config.settings import TIMEFRAMES

# ==========================================
# 📊 Snapshot Index Mapping (快照索引映射)
# ------------------------------------------
# 必須與 TxfRingBuffer.get_snapshot() 順序一致
# ==========================================
IDX_CLOSE          = 0
IDX_VOLUME         = 1
IDX_TICK_TYPE      = 2
IDX_TIMESTAMP      = 3
IDX_UNDERLYING     = 4

# 累積數據 (O(1) 快速計算用)
IDX_CUM_VOLUME     = 5
IDX_CUM_PV         = 6
IDX_CUM_CLOSE      = 7

# 狀態數據 (Stateful)
IDX_SESSION_HIGH   = 8
IDX_SESSION_LOW    = 9
IDX_TOTAL_VOLUME   = 10

# 籌碼流向 / Delta
IDX_CUM_BUY_VOL    = 11
IDX_CUM_SELL_VOL   = 12

IDX_HEAD           = 13

class IndicatorManager:
    """
    指標管理器 (全 NumPy RingBuffer 版)。
    
    核心職責：
    1. 讀取 Config，動態綁定指標計算函數。
    2. 從 Shared Memory 同步數據到本地歷史 (Local History)。
    3. 呼叫 Numba 進行高效指標運算。
    4. 實時聚合多週期 K 線 (Candle Aggregation)。
    5. 提供 O(1) 狀態查詢給 Dashboard Server。
    """
    
    def __init__(self, buffer_capacity):
        self.capacity = buffer_capacity

        # 紀錄當前的 head 位置 (與 RingBuffer 同步)
        self.current_head = 0
        
        # ==========================================
        # 1. 基礎數據容器 (Fixed Size Array)
        # ==========================================
        self.history = {
            "timestamp": np.zeros(buffer_capacity, dtype=np.int64),
            "price": np.zeros(buffer_capacity, dtype=np.int64), # 儲存整數價格
        }
        
        # Session Volume Profile (籌碼分佈)
        # 價格索引範圍 0~50000 (足以涵蓋 TXF 點數)
        self.session_profile = np.zeros(50000, dtype=np.int64)

        # 2. 多週期 K 線容器
        self.candles = {}
        self.current_candles = {} # 暫存各週期的當前 K 線
        
        # 初始化 K 線結構
        for tf_name in TIMEFRAMES:
            self.candles[tf_name] = {
                'time': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': []
            }
            self.current_candles[tf_name] = {}

        # ==========================================
        # 3. ⚡️ 預先綁定 (Pre-binding)
        # 目的：在初始化階段解析所有函數，避免 Runtime 查找開銷
        # ==========================================
        self.executors = []
        
        for ind in INDICATORS_SETUP:
            ind_id = ind['id']
            # 初始化指標歷史陣列 (Float64)
            self.history[ind_id] = np.zeros(buffer_capacity, dtype=np.float64)
            
            # A. 綁定 Numba 函數
            func_name = ind['func']
            try:
                calc_func = getattr(engine, func_name)
            except AttributeError:
                print(f"❌ 錯誤: 找不到 Numba 函數 '{func_name}'")
                continue
            
            # B. 解析輸入參數 (Input Mapping)
            input_indices = []
            for input_name in ind['inputs']:
                # 基礎數據
                if input_name == 'close':        input_indices.append(IDX_CLOSE)
                elif input_name == 'volume':     input_indices.append(IDX_VOLUME)
                elif input_name == 'type':       input_indices.append(IDX_TICK_TYPE)
                elif input_name == 'timestamp':  input_indices.append(IDX_TIMESTAMP)
                elif input_name == 'underlying_price': input_indices.append(IDX_UNDERLYING)
                
                # 累積數據
                elif input_name == 'cum_volume': input_indices.append(IDX_CUM_VOLUME)
                elif input_name == 'cum_pv':     input_indices.append(IDX_CUM_PV)
                elif input_name == 'cum_close':  input_indices.append(IDX_CUM_CLOSE)
                
                # 狀態數據
                elif input_name == 'session_high': input_indices.append(IDX_SESSION_HIGH)
                elif input_name == 'session_low':  input_indices.append(IDX_SESSION_LOW)
                elif input_name == 'total_volume': input_indices.append(IDX_TOTAL_VOLUME)
                
                # 籌碼數據
                elif input_name == 'cum_buy_vol':  input_indices.append(IDX_CUM_BUY_VOL)
                elif input_name == 'cum_sell_vol': input_indices.append(IDX_CUM_SELL_VOL)
            
            # C. 打包固定參數
            fixed_args = tuple(ind['args'] + [self.capacity])
            
            # 儲存執行單元
            self.executors.append((ind_id, calc_func, input_indices, fixed_args))

    # ==========================================
    # 🔥 Dashboard 輔助方法
    # ==========================================
    
    @property
    def count(self):
        """
        返回目前有效資料長度。
        若最後一個位置非 0，代表 Buffer 已繞一圈 (Full)，長度為 capacity。
        """
        if self.history['timestamp'][-1] != 0:
            return self.capacity
        return self.current_head

    def get_latest_timestamp(self):
        """
        快速取得最新時間戳。
        """
        cnt = self.count
        if cnt == 0:
            return 0.0
        
        # 最新數據位於 head - 1
        idx = self.current_head - 1
        if idx < 0:
            idx = self.capacity - 1
            
        return self.history['timestamp'][idx]
    
    def get_linear_snapshot(self, key):
        """
        將環狀 Buffer 解開為線性 Array (Unroll)，供前端繪圖使用。
        """
        arr = self.history[key]
        head = self.current_head
        is_full = (self.history['timestamp'][-1] != 0)
        
        if not is_full:
            return arr[:head]
        else:
            return np.concatenate((arr[head:], arr[:head]))


    def _update_candles(self, tick_time_ms, price, volume):
        """
        🆕 K 線聚合邏輯：將單筆 Tick 更新至所有週期的 K 線中。
        """
        # 🛡️ 防護機制: 忽略無效價格 (避免髒數據污染 K 線)
        if price <= 0: 
            return

        for tf_name, period_ms in TIMEFRAMES.items():
            # 計算時間桶 (Bucket Time)
            bucket_time_ms = (tick_time_ms // period_ms) * period_ms
            
            curr = self.current_candles[tf_name]
            storage = self.candles[tf_name]
            
            if not curr or curr['time'] != bucket_time_ms:
                # 1. 舊 K 線歸檔
                if curr:
                    for k, v in curr.items():
                        if k != 'new_tick': storage[k].append(v)
                
                # 2. 開立新 K 線
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
                # 3. 更新當前 K 線 (High/Low/Close/Vol)
                curr['high'] = max(curr['high'], price)
                curr['low'] = min(curr['low'], price)
                curr['close'] = price
                curr['volume'] += volume
                curr['new_tick'] = True


    def sync_from_buffer(self, ring_buffer, local_head, target_head):
        """
        🚀 批量同步 (Batch Sync): 
        從 Shared Memory 高效同步資料到本地，並觸發指標計算與 K 線更新。
        自動處理環狀寫入的 Wrap-around 情況。
        """
        start_head = self.current_head # 應該等於 local_head
        
        ranges = []
        if target_head > start_head:
            ranges.append((start_head, target_head))
        elif target_head < start_head:
            # 發生繞圈: [Start -> End] AND [0 -> Target]
            ranges.append((start_head, self.capacity))
            ranges.append((0, target_head))
        else:
            return # 無新資料
            
        # 🚧 記憶體屏障檢查 (防止 ARM 架構下的 Race Condition) 🚧
        # 檢查最後一筆欲讀取的資料是否已寫入完成 (非 0)
        last_read_idx = target_head - 1
        if last_read_idx < 0: last_read_idx = self.capacity - 1
        
        ts_val = ring_buffer.timestamp[last_read_idx]
        cl_val = ring_buffer.close[last_read_idx]
        
        if ts_val == 0 or cl_val == 0:
            print(f"⚠️ 偵測到 Race Condition (資料未就緒): Index={last_read_idx}. 等待下一輪...")
            return
            
        # 取得 Shared Memory 的視圖 (View Snapshot)
        snapshot_tuple = ring_buffer.get_snapshot()
        
        for start_idx, end_idx in ranges:
            # 1. ⚡️ 批量複製 (Vectorized Copy)
            # 將 Shared Memory 的 Float 數據複製到本地 Int64 陣列
            
            # Timestamp (Int64 -> Int64)
            self.history["timestamp"][start_idx:end_idx] = ring_buffer.timestamp[start_idx:end_idx]
            
            # Price (Float -> Int64)
            # 注意: RingBuffer 內儲存的是標準化後的浮點數 (15000.0)
            self.history["price"][start_idx:end_idx] = ring_buffer.close[start_idx:end_idx]
            
            # 🆕 更新籌碼分佈 (Volume Profile)
            update_volume_profile(
                self.session_profile,
                ring_buffer.close, 
                ring_buffer.volume,
                start_idx,
                end_idx
            )

            # 2. 🔄 循序運算迴圈 (Tick-by-Tick)
            # K 線與特定指標需要依賴順序，因此這部分無法向量化
            
            for idx in range(start_idx, end_idx):
                # A. K 線聚合
                t_val = self.history["timestamp"][idx]
                p_val = self.history["price"][idx]
                v_val = ring_buffer.volume[idx]
                
                self._update_candles(t_val, p_val, v_val)
                
                # B. Numba 指標計算
                # 計算 Numba 需要的 head 位置 (當前 idx + 1)
                numba_head = idx + 1
                if numba_head > self.capacity: numba_head = 1 
                
                # 執行所有已註冊的指標計算函數
                for ind_id, calc_func, input_indices, fixed_args in self.executors:
                    # 從 Shared Memory 快照中提取動態參數
                    dynamic_args = [snapshot_tuple[i] for i in input_indices]
                    
                    val = calc_func(*dynamic_args, numba_head, *fixed_args)
                    self.history[ind_id][idx] = val

        # 更新本地游標
        self.current_head = target_head



        
        