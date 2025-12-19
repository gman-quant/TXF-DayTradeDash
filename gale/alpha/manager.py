# core/indicator_manager.py

import numpy as np
import gale.alpha.numba_lib as engine
from config.indicator_config import INDICATORS_SETUP
from config.settings import TIMEFRAMES
from gale.alpha.profile import VolumeProfileEngine
from gale.alpha.microstructure import MicrostructureEngine

class IndicatorManager:
    """
    指標管理器 (全 NumPy RingBuffer 版)
    
    核心職責：
    1. 動態配置 (Config Driven): 讀取 `INDICATORS_SETUP`，決定要計算哪些指標。
    2. 高效計算 (Numba Integration): 接收 RingBuffer 快照，呼叫編譯過的 Numba 函數進行計算。
    3. 即時聚合 (Real-time Aggregation): 維護各週期 (1K, 5K...) 的 OHLC K 線狀態。
    4. 狀態查詢 (O(1) Access): 提供 Dashboard Server 快速查詢 Count、Latest Timestamp 與快照。
    """
    
    def __init__(self, buffer_capacity):
        self.capacity = buffer_capacity

        # 紀錄當前的 head 位置 (CoreProcessor 傳過來的寫入游標)
        # 用於追踪 RingBuffer 的寫入進度
        self.current_head = 0
        
        # ==========================================
        # 1. 基礎歷史數據容器 (固定長度 NumPy Array)
        # ==========================================
        # 這些 Array 與 SharedMemory 是分離的，用於存儲指標計算結果與本地快照
        self.history = {
            "timestamp": np.zeros(buffer_capacity, dtype=np.int64),
            "close": np.zeros(buffer_capacity, dtype=np.int64),
            "volume": np.zeros(buffer_capacity, dtype=np.int64),
        }
        
        # 2. 🆕 多週期 K 線容器
        # 用於即時繪製 K 線圖，無需每次重算
        self.candles = {}
        self.current_candles = {} # 暫存各週期的當前 K 線 (尚未收盤的 Bar)
        
        # 初始化所有週期 (依據 settings.TIMEFRAMES)
        for tf_name in TIMEFRAMES:
            self.candles[tf_name] = {
                'time': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': []
            }
            self.current_candles[tf_name] = {}

        # 2.5 初始化引擎 (Alpha Engines)
        # Volume Profile: 負責價格分佈計算 (POC/VA)
        self.vp_engine = VolumeProfileEngine()
        # Microstructure: 負責高頻微結構計算 (Velocity, Imbalance)
        self.micro_engine = MicrostructureEngine(window_seconds=3) 
        
        # 新增微結構指標容器
        self.history['velocity'] = np.zeros(buffer_capacity, dtype=np.float64)
        self.history['imbalance'] = np.zeros(buffer_capacity, dtype=np.float64)
        
        # [LOB Integration] Initialize History Arrays
        self.history['obi'] = np.zeros(buffer_capacity, dtype=np.float64)
        self.history['ofi'] = np.zeros(buffer_capacity, dtype=np.float64)
        self.history['lob_lag'] = np.zeros(buffer_capacity, dtype=np.float64)
        
        # 🆕 有效量 (Effective Volume)
        # 用於存儲「重組後」的成交量。
        # 拆單 (Split Orders) 會被合併到第一筆，其餘設為 0。
        # 🆕 有效量 (Effective Volume) - [Core Feature: Volume Conservation]
        # 用於存儲「重組後」的成交量。這是本系統最核心的邏輯之一。
        # 原理：當偵測到微秒級拆單 (Split Orders) 時，系統會將量「歸戶」到第一筆，
        # 並將後續的量設為 0，確保總量守恆 (Conservation of Volume) 且不重複計算。
        # 這樣 Retail Flow 就不會被騙，Whale Nuke 也能精準抓到。
        self.history['effective_volume'] = np.zeros(buffer_capacity, dtype=np.int64) 

        # 重組狀態變數
        self.rec_last_time = 0
        self.rec_last_side = 0
        self.rec_last_idx = 0 # 紀錄當前事件最開始的那個 Index

        # ==========================================
        # 3. ⚡️ 預先綁定 (Pre-binding) 邏輯
        # 目的：在 __init__ 階段解析所有函數與參數，避免在 on_tick 迴圈中重複查找
        # ==========================================
        self.executors = []
        
        for ind in INDICATORS_SETUP:
            ind_id = ind['id']
            # 為每個指標分配存儲空間
            self.history[ind_id] = np.zeros(buffer_capacity, dtype=np.float64)
            
            # A. 預先抓取 Numba 函數
            func_name = ind['func']
            try:
                calc_func = getattr(engine, func_name)
            except AttributeError:
                print(f"❌ Error: Function '{func_name}' not found in numba_engine.")
                continue
            
            # B. 預先解析輸入參數映射 (Input Mapping)
            # 將 Config 中的字串名稱 (e.g. 'close') 映射到 snapshot_tuple 的 index
            input_indices = []
            for input_name in ind['inputs']:
                # --- 基礎數據 (Snapshot Data) ---
                if input_name == 'close':        input_indices.append(0)
                elif input_name == 'volume':     input_indices.append(1)
                elif input_name == 'type':       input_indices.append(2)
                elif input_name == 'timestamp':  input_indices.append(3)
                elif input_name == 'underlying_price': input_indices.append(4)
                
                # --- 累積數據 (Cumulative Data for O(1) calc) ---
                elif input_name == 'cum_volume': input_indices.append(5)
                elif input_name == 'cum_pv':     input_indices.append(6)
                elif input_name == 'cum_close':  input_indices.append(7)
                
                # --- 狀態數據 (Stateful Data) ---
                elif input_name == 'session_high': input_indices.append(8)
                elif input_name == 'session_low':  input_indices.append(9)
                elif input_name == 'total_volume': input_indices.append(10)
                
                # --- 籌碼數據 (Order Flow Data) ---
                elif input_name == 'cum_buy_vol':  input_indices.append(11)
                elif input_name == 'cum_sell_vol': input_indices.append(12)
                
                # 🆕 Local History Mapping
                elif input_name == 'effective_volume': input_indices.append(-1) # Special Flag
            
            # C. 預先準備固定參數 (e.g. window size)
            # Tuple 結構比較快，這一步將靜態參數打包
            fixed_args = tuple(ind['args'] + [self.capacity])
            
            # 將執行所需資訊打包存入 executors 列表
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
        [內部方法] 更新所有時間週期的 K 線狀態
        
        邏輯：
        1. 根據時間戳將 Tick 歸類到對應的 Bucket (e.g. 10:00:05 -> 10:00:00 Bucket)
        2. 檢查是否需要換 K 線 (bucket_time != current_metric.time)
        3. 聚合 OHLCV 數據
        """
        for tf_name, period_ms in TIMEFRAMES.items():
            # 計算該週期的 Bucket Time (向下取整)
            bucket_time_ms = (tick_time_ms // period_ms) * period_ms
            
            curr = self.current_candles[tf_name]
            storage = self.candles[tf_name]
            
            if not curr or curr['time'] != bucket_time_ms:
                # [狀態切換] 新的 K 線週期開始
                
                # 1. 結算上一根 K 線 (如果存在)
                if curr:
                    for k, v in curr.items():
                        # 'new_tick' 只是標記，不存入歷史陣列
                        if k != 'new_tick': storage[k].append(v)
                
                # 2. 初始化新 K 線
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
                # [狀態更新] 更新當前 K 線
                curr['high'] = max(curr['high'], price)
                curr['low'] = min(curr['low'], price)
                curr['close'] = price
                curr['volume'] += volume
                curr['new_tick'] = True


    def on_tick(self, snapshot_tuple):
        """
        核心事件處理：當收到新 Tick 時被觸發
        
        Args:
            snapshot_tuple (tuple): 從 SharedMemory 讀取的快照，包含所有指針與累積變數。
                                    這是為了避免 GIL 鎖競爭，一次性傳入所有數據。
        
        Process Flow:
            1. Unpack Snapshot -> 取得當前指針 (Head) 與基礎數據 (Close, Vol, Time)
            2. Update History -> 寫入本地 RingBuffer
            3. Run Numba Indicators -> 執行所有預編譯的技術指標計算
            4. Aggregate Candles -> 更新 OHLC
            5. Update Engines -> 觸發 Volume Profile 與 Microstructure 計算
        """
        # snapshot_tuple 的最後一個是 head 指針
        head = snapshot_tuple[-1]
        
        # 更新內部的 head 紀錄，供 get_linear_snapshot (Dashboard View) 使用
        # 這樣前端在切片時才知道哪裡是終點
        self.current_head = head
        
        # 計算寫入位置 (RingBuffer 邏輯：snapshot 裡的 head 是"下一個空位"，所以當前數據在 head-1)
        curr_idx = head - 1
        # 處理邊界條件：如果 head=0，代表剛寫滿或是剛 wrap around，最新數據在最後一格
        if curr_idx < 0: curr_idx = self.capacity - 1
        
        # 從 snapshot 拿出基礎數據 (直接用 index 取，不做解包變數，以節省 Python 層的 Overhead)
        # index 常數對照：0=close, 1=volume, 2=type, 3=time
        # [Fix Overflow] Cast to native Python types immediately to avoid NumPy Scalar overflow in accumulators
        close_val = float(snapshot_tuple[0][curr_idx])
        time_val  = int(snapshot_tuple[3][curr_idx])
        vol_val   = int(snapshot_tuple[1][curr_idx])
        
        # type=1 (Buy), type=2 (Sell), type=0 (Unknown)
        type_val = int(snapshot_tuple[2][curr_idx])
        # print(f"DEBUG: Vol={snapshot_tuple[1][curr_idx]}, Type={snapshot_tuple[2][curr_idx]}")

        # ==========================================
        # 1. 更新基礎歷史數據 (Local RingBuffer Write)
        # ==========================================
        self.history["timestamp"][curr_idx] = time_val
        self.history["close"][curr_idx]     = close_val
        self.history["volume"][curr_idx]    = vol_val
        
        # [LOB Integration] Copy from Snapshot
        # Indices based on memory.py get_snapshot: 
        # 13=obi, 14=ofi, 15=lob_lag
        self.history["obi"][curr_idx]     = float(snapshot_tuple[13][curr_idx])
        self.history["ofi"][curr_idx]     = float(snapshot_tuple[14][curr_idx])
        self.history["lob_lag"][curr_idx] = float(snapshot_tuple[15][curr_idx])
        
        # ==========================================
        # 5. 大單重組 (Whale Reconstruction) - Effective Volume Logic
        # [核心演算法：微秒級拆單還原]
        # ==========================================
        # 預設先填入當前量 (Assume authentic trade)
        self.history['effective_volume'][curr_idx] = vol_val
        
        # [Step 1] 檢查是否為「同一事件延續」 (同時間 ms + 同方向)
        # 注意：排除 type=0 (Unknown)
        is_same_event = (time_val == self.rec_last_time) and (type_val == self.rec_last_side) and (type_val != 0)
        
        if is_same_event:
            # [Case A: 偵測到拆單 (Split Order)]
            # 這是同一波攻擊的後續部隊。
            
            # 1. 搬運法 (Volume Transfer)：把當前這筆量，加回「事件起始點 (Leader)」
            # 這樣 Leader (即 rec_last_idx) 的量會越來越大，還原出真正的大單規模。
            self.history['effective_volume'][self.rec_last_idx] += vol_val
            
            # 2. 歸零法 (Zeroing)：把當前這筆設為 0 (Follower 消失)
            # 這是為了「總量守恆」。因為量已經搬給 Leader 了，這裡必須消失，
            # 否則 K 線圖的成交量會變成兩倍 (Double Counting)。
            # 副作用：依賴 effective_volume 的指標 (如 Retail Flow) 會看到 0，自動忽略此雜訊。
            self.history['effective_volume'][curr_idx] = 0
            
        else:
            # [Case B: 新事件 (New Event)]
            # 時間不同 或 方向不同 -> 視為獨立的新單。
            self.rec_last_time = time_val
            self.rec_last_side = type_val
            self.rec_last_idx = curr_idx
            # effective_volume 已經在上面預設為 vol_val 了，不用動

        # ==========================================
        # 2. 執行 Numba 技術指標計算
        # ==========================================
        for ind_id, calc_func, input_indices, fixed_args in self.executors:
            # A. 動態組裝參數
            # [Fix] Support Local Arrays via Special Index (-1)
            dynamic_args = []
            for i in input_indices:
                if i == -1:
                    dynamic_args.append(self.history['effective_volume'])
                else:
                    dynamic_args.append(snapshot_tuple[i]) 
            
            # B. 呼叫 JIT 編譯函數 (極速運算)
            # 注意：這裡只計算當前這一個點 (Incremental Calculation)
            val = calc_func(*dynamic_args, head, *fixed_args)
            
            # C. 寫入結果
            self.history[ind_id][curr_idx] = val

        # ==========================================
        # 3. 更新 K 線聚合 (Aggregator)
        # ==========================================
        self._update_candles(time_val, close_val, vol_val)
        
        # ==========================================
        # 4. 更新高階分析引擎 (Alpha Engines)
        # ==========================================
        
        # Volume Profile: 價格分佈 (不依賴時間)
        self.vp_engine.update(close_val, vol_val, tick_type=type_val)
        
        # Microstructure: 速度與訂單流不平衡 (依賴時間窗)
        self.micro_engine.update(time_val, vol_val, type_val)
        
        # 取得微結構指標並存檔
        vel, imb = self.micro_engine.get_metrics()
        self.history['velocity'][curr_idx] = vel
        self.history['imbalance'][curr_idx] = imb
        
        # ==========================================
        # 5. [已移動] 大單重組邏輯已移至上方 (Step 2 之前)
        # ==========================================
        
        