import time
import sys
import os
import logging
import threading
import argparse
import signal

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gale.infra.memory import SharedRingBuffer
from gale.alpha.manager import IndicatorManager
from gale.dashboard.app import start_dashboard_server

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    stream=sys.stdout
)
# Suppress Werkzeug logs
logging.getLogger('werkzeug').setLevel(logging.ERROR)

logger = logging.getLogger("DashboardRunner")

class DashboardRunner:
    def __init__(self, args):
        self.args = args
        self.shm_name = f"gale_shm_{args.topic}"
        self.running = True
        
        # 1. Connect to Shared Memory (Reader)
        while self.running:
            try:
                # [Multi-Day] Use dynamic capacity from args
                self.ring_buffer = SharedRingBuffer(name=self.shm_name, capacity=self.args.capacity, create=False)
                logger.info(f"✅ Dashboard Connected to Shared Buffer: {self.shm_name} (Cap: {self.args.capacity})")
                break
            except Exception:
                logger.warning(f"Waiting for Shared Buffer '{self.shm_name}'...")
                time.sleep(2)
        
        # 2. Initialize Indicator Manager (Independent Instance)
        # 這裡會維護一份自己的指標運算狀態，與 Strategy 分開
        self.manager = IndicatorManager(buffer_capacity=self.args.capacity)
        self.manager.ring_buffer = self.ring_buffer # [New] Attach for access to metadata (prev_close)
        self.local_cursor = 0

    def _sync_loop(self):
        """
        後台數據同步迴圈：
        從 Shared Memory 讀取最新 Ticks -> 推送給 IndicatorManager -> 更新 K 線與指標
        """
        logger.info("🔄 Dashboard Sync Loop Started.")
        get_snapshot = self.ring_buffer.get_snapshot
        on_tick = self.manager.on_tick
        
        try:
            while self.running:
                target_head = self.ring_buffer.head
                
                if self.local_cursor != target_head:
                    # Catch-up Mode
                    while self.local_cursor != target_head:
                        next_cursor = (self.local_cursor + 1) % self.ring_buffer.capacity
                        
                        # Synthetic Snapshot
                        snap = get_snapshot()
                        synthetic_snap = snap[:-1] + (next_cursor,)
                        
                        on_tick(synthetic_snap)
                        
                        self.local_cursor = next_cursor
                else:
                    time.sleep(0.01) # UI 不需要像 Strategy 那麼即時，10ms 延遲可接受，省 CPU
                    
        except Exception as e:
            logger.error(f"Sync Loop Error: {e}")
            self.running = False
        finally:
            self.ring_buffer.shutdown()

    def run(self):
        # 1. 啟動後台同步執行緒
        sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        sync_thread.start()
        
        # 2. 啟動 Dashboard (Blocking Main Thread)
        logger.info(f"📊 Starting Dashboard Server on port {self.args.port}...")
        try:
            # 這裡會卡住 Main Thread 直到結束
            start_dashboard_server(self.manager, port=self.args.port, args=self.args)
        except Exception as e:
            logger.error(f"Dashboard Server Error: {e}")
        finally:
            self.running = False
            # Explicitly shutdown to unregister resource tracker in Main Thread
            if hasattr(self, 'ring_buffer'):
                self.ring_buffer.shutdown()
            logger.info("Dashboard Runner Exiting.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--topic', type=str, default='txf-tick')
    parser.add_argument('--port', type=int, default=8050, help="Dashboard port")
    # [Multi-Day] Add capacity argument
    parser.add_argument('--capacity', type=int, default=200000, help="RingBuffer Capacity")
    
    # [History Context]
    parser.add_argument('--mode', type=str, default='live')
    parser.add_argument('--date', type=str, help='History Date')
    parser.add_argument('--session', type=str, help='History Session')
    args = parser.parse_args()
    
    runner = DashboardRunner(args)
    
    # Simple signal handler to set running flag
    def handle_exit(sig, frame):
        runner.running = False
        sys.exit(0)
        
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    
    runner.run()
