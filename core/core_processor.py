# core/core_processor.py

import asyncio
import logging
import sys
import threading
import argparse # 🆕 新增：用於解析命令列參數
from datetime import datetime

# 嘗試引入 uvloop 加速 asyncio
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

# --- 模組引入 ---
# 🆕 新增 get_history_range
from config.txf_calendar import get_current_session_offset, get_history_range
from ingestion.kafka_consumer import GaleKafkaConsumer
from data_schemas.txf_data_pb2 import Tick
from core.ring_buffer import TxfRingBuffer
from core.indicator_manager import IndicatorManager
from analysis.dashboard_server import start_dashboard_server

# =========================================================
# ⚙️ Logging 設定
# =========================================================
logging.getLogger('werkzeug').setLevel(logging.ERROR)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("CoreProcessor")


class CoreProcessor:
    def __init__(self, args):
        """
        args: argparse 的 Namespace 物件，包含所有啟動設定
        """
        self.args = args
        self.mode = args.mode # 'live' or 'history'
        
        # Kafka 設定
        self.kafka_broker = args.broker
        self.group_id = args.group
        # 注意：Consumer 支援 list topic，這裡先轉成 list
        self.topics = [args.topic] 
        
        # 1. 初始化 Kafka Consumer (尚未連線)
        self.consumer: GaleKafkaConsumer = None
        
        # 2. 初始化 Ring Buffer (容量 200,000)
        self.ring_buffer = TxfRingBuffer(capacity=200000)
        
        # ⚡️ 安全性優化：強制清空記憶體
        self.ring_buffer.clear()

        # 3. 初始化 Indicator Manager
        self.indicator_manager = IndicatorManager(buffer_capacity=200000)
        
        # 狀態標記
        self.is_running = False

    async def initialize_consumer(self):
        """初始化並連線 Consumer"""
        self.consumer = GaleKafkaConsumer(
            broker_url=self.kafka_broker,
            group_id=self.group_id,
            topics=self.topics
        )
        self.consumer.connect()

    async def run(self):
        """
        核心運轉迴圈 (The Reactor Loop)
        """
        # 1. 建立連線
        await self.initialize_consumer()
        self.is_running = True

        # 2. 啟動 Dashboard
        logger.info("Starting Dashboard Server on port 8050...")
        dash_thread = threading.Thread(
            target=start_dashboard_server, 
            args=(self.indicator_manager, 8050),
            daemon=True
        )
        dash_thread.start()
        
        logger.info(f"🚀 Engine Started | Mode: {self.mode.upper()}")

        # 3. 準備變數與方法綁定 (Pre-binding)
        current_tick = Tick()
        write_tick = self.ring_buffer.write_tick
        get_snapshot = self.ring_buffer.get_snapshot
        calc_indicators = self.indicator_manager.on_tick
        processed_count = 0
        
        # ==========================================
        # 🔀 核心分流：決定數據生成器 (Data Generator)
        # ==========================================
        data_generator = None
        
        if self.mode == 'live':
            # --- A. 實時模式 ---
            start_offset, session_status = get_current_session_offset()
            logger.info(f"Session Check: Status={session_status}, StartOffset={start_offset}")

            if session_status == 'CLOSED':
                logger.warning("Market is currently CLOSED. (Live Mode)")
                # 在實盤中，這裡可能選擇等待，但在測試時直接 return
                # return 

            # 定位到開盤時間
            try:
                self.consumer.seek_to_time(start_offset)
            except Exception as e:
                logger.error(f"Failed to seek: {e}")
                return

            # 獲取無限串流生成器
            data_generator = self.consumer.consume_stream()

        elif self.mode == 'history':
            # --- B. 歷史模式 ---
            target_date = self.args.date
            session_type = self.args.session # 'day' or 'night'
            
            # 計算該日期的起訖時間
            start_dt, end_dt = get_history_range(target_date, session_type)
            logger.info(f"📜 History Range: {start_dt} ~ {end_dt}")
            
            # 獲取有限區間生成器
            data_generator = self.consumer.consume_history(start_dt, end_dt)

        # ==========================================
        # 🔄 統一消費迴圈 (Universal Loop)
        # ==========================================
        try:
            if data_generator:
                async for raw_msg_obj in data_generator:
                    # 注意：我們修改了 Consumer 讓它回傳 Message 物件以便檢查 Topic
                    # 如果您的 Consumer 還是只回傳 value bytes，請改回 raw_msg_bytes
                    
                    # 這裡假設 consume_history/stream 回傳的是 Message 物件
                    # 如果您還沒改 Consumer，這裡可能是 bytes，請根據您的 Consumer 實作調整
                    
                    # 為了相容您目前的 consumer 寫法 (只 yield value):
                    raw_msg_bytes = raw_msg_obj.value() if hasattr(raw_msg_obj, 'value') else raw_msg_obj
                    
                    if not raw_msg_bytes:
                        continue

                    # 1. 反序列化
                    try:
                        current_tick.ParseFromString(raw_msg_bytes)
                    except Exception as e:
                        logger.error(f"Protobuf Error: {e}")
                        continue

                    # 2. 寫入與計算
                    write_tick(current_tick)
                    calc_indicators(get_snapshot())
                    
                    # 3. Log 心跳
                    processed_count += 1
                    if processed_count % 5000 == 0:
                        logger.info(
                            f"Tick#{processed_count} | "
                            f"Price: {current_tick.close/10000.0:.0f} | "
                            f"TotalVol: {current_tick.total_volume}"
                        )
            else:
                logger.error("Failed to initialize data generator.")

            # ==========================================
            # ⬇️ 🆕 關鍵修正：歷史模式跑完後，進入掛機等待
            # ==========================================
            if self.mode == 'history':
                logger.info("🎉 Backtest Replay Finished! (Data stream ended)")
                logger.info("📊 Dashboard is still running at http://localhost:8050")
                logger.info("🛑 Press CTRL+C to exit.")
                # 讓主程式進入無限睡眠，保活 Dashboard Thread
                while True:
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("Task cancelled.")
        finally:
            if self.consumer:
                self.consumer.close()
            logger.info("Stopped.")

# -------------------------------------------------------
# 🚀 升級版入口點 (支援參數)
# -------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TXF Gale Quant Engine")
    
    # 參數定義
    parser.add_argument('--mode', type=str, default='live', choices=['live', 'history'], help='Running mode')
    parser.add_argument('--date', type=str, help='Target date (YYYY-MM-DD) for history mode')
    parser.add_argument('--session', type=str, default='whole', choices=['day', 'night'], help='Trading session')
    parser.add_argument('--broker', type=str, default='192.168.1.50:9092', help='Kafka Broker IP:Port')
    parser.add_argument('--group', type=str, default='gale_core_v1', help='Kafka Group ID')
    parser.add_argument('--topic', type=str, default='txf-tick', help='Kafka Topic Name')

    args = parser.parse_args()

    # 參數檢查
    if args.mode == 'history' and not args.date:
        print("❌ Error: History mode requires --date argument (e.g., --date 2025-12-01)")
        sys.exit(1)

    # 初始化並執行
    processor = CoreProcessor(args)
    
    try:
        asyncio.run(processor.run())
    except KeyboardInterrupt:
        pass