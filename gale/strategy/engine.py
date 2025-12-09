
import time
import sys
import logging
import threading
import argparse
from gale.infra.memory import SharedRingBuffer
from gale.alpha.handler import IndicatorManager
from gale.strategy.position import PositionManager
from gale.strategy.strategies.chop_reversal import ChopReversalStrategy

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    stream=sys.stdout
)
# Suppress Werkzeug logs (Dashboard poll spam)
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logger = logging.getLogger("StrategyServer")

class StrategyServer:
    def __init__(self, args):
        self.args = args
        self.shm_name = f"gale_shm_{args.topic}"
        
        # 1. Connect to Shared Memory (Reader: create=False)
        # 不斷嘗試連線直到 Writer 啟動
        while True:
            try:
                self.ring_buffer = SharedRingBuffer(name=self.shm_name, capacity=200000, create=False)
                logger.info(f"✅ Connected to Shared Buffer: {self.shm_name}")
                break
            except Exception:
                logger.warning(f"Waiting for Shared Buffer '{self.shm_name}'...")
                time.sleep(2)
        
        # 2. Initialize Indicator Manager
        # IndicatorManager 會配置自己的 Local Memory 來存放指標計算結果 (RSI, MA...)
        self.manager = IndicatorManager(buffer_capacity=200000)
        
        # 3. Initialize Position Manager (Paper Trading)
        # 用於模擬下單與損益計算
        self.pos_manager = PositionManager()
        
        # 4. Initialize Modular Strategy
        self.strategy = ChopReversalStrategy(self.pos_manager)
        
        # 追蹤處理進度
        self.local_cursor = 0
        
        # 若 SharedBuffer 已經有資料 (Writer 跑了一段時間)，我們需要追趕
        # 但不能直接讀 head，因為 RingBuffer 是環狀的，必須知道是否繞圈
        # 簡單策略：總之從 local_cursor=0 開始掃描到 shared.head
        # 如果 shared.is_full，那理想上我們應該從 head 開始掃一圈，但簡單起見先從 0 掃
        # (V1.0 假設盤中重啟不超過 buffer 容量)

    def run(self):
        # 1. 啟動 Dashboard (已移除，獨立進程處理)
        # logger.info("📊 Dashboard moved to independent process.")
        
        logger.info("🚀 Strategy Server (Reader) Started. Syncing...")
        
        # 快取方法引用
        get_snapshot = self.ring_buffer.get_snapshot
        on_tick = self.manager.on_tick
        
        # 無窮迴圈 (Strategy Loop)
        try:
            while True:
                # 取得 Shared Memory 目前的寫入位置
                target_head = self.ring_buffer.head
                
                # 檢查是否有新數據
                if self.local_cursor != target_head:
                    # 追趕模式 (Catch-up)
                    # 處理從 local_cursor 到 target_head 的區間
                    # 注意跨越邊界的情況 (Wrap around)
                    
                    while self.local_cursor != target_head:
                        # 模擬 "snapshot"：雖然 IndicatorManager.on_tick 接收的是 Arrays，
                        # 但它會用最後一個參數 (scalar head) 來決定計算哪一筆。
                        # 我們必須騙它說 "現在 head 是 local_cursor + 1"
                        
                        next_cursor = (self.local_cursor + 1) % self.ring_buffer.capacity
                        
                        # 建構一個指向 Shared Memory 的參照，但把最後的 head 改成我們當前要算的 index
                        # 注意：get_snapshot() 回傳的是 Tuple，最後一項是 head INT
                        # 我們需要修改這個 INT
                        
                        # 取得原始 View Tuple
                        snap = get_snapshot()
                        
                        # 替換 head 為 next_cursor (這樣 IndicatorManager 就只會算這一筆)
                        # manager.on_tick logic: head = snapshot_tuple[-1], curr_idx = head - 1
                        # 所以傳入 next_cursor，manager 會算 next_cursor - 1 (也就是 local_cursor)
                        
                        # Tuple 是 immutable，造一個新的
                        synthetic_snap = snap[:-1] + (next_cursor,)
                        
                        on_tick(synthetic_snap)
                        
                        # [Paper Trading] Update P&L
                        # Retrieve the close price we just processed (from manager history or shared mem)
                        # We know 'curr_idx' inside manager is 'next_cursor - 1'
                        # But simpler: read from shared memory directly
                        current_close = snap[0][next_cursor-1] # 0 is close array
                        
                        self.pos_manager.update_market_price('TXF', current_close)
                        
                        # --- 策略邏輯 (Modular Strategy) ---
                        # 1. Prepare Data
                        idx = next_cursor - 1
                        market_data = {
                            'close': current_close
                        }
                        indicators = {
                            'velocity': self.manager.history['velocity'][idx],
                            'imbalance': self.manager.history['imbalance'][idx]
                        }
                        
                        # 2. Delegate to Strategy
                        # timestamp is approximate, real simulation would use tick timestamp
                        self.strategy.on_tick(time.time(), market_data, indicators)

                        # 前進一步
                        self.local_cursor = next_cursor
                    
                    # 追趕完畢 (或處理了一批)
                    # logger.debug(f"Synced to {self.local_cursor}")
                
                else:
                    # 無新數據，稍微休息避免吃滿 CPU
                    # 實務上可用 Spin-wait (time.sleep(0)) 或極短 sleep
                    time.sleep(0.001) 

        except KeyboardInterrupt:
            logger.info("Stopping Strategy Server...")
        finally:
            self.ring_buffer.shutdown()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--topic', type=str, default='txf-tick')
    args = parser.parse_args()
    
    server = StrategyServer(args)
    server.run()
