# core/core_processor.py

import asyncio
import logging
import sys
import threading
from datetime import datetime

# 嘗試引入 uvloop (Linux/Mac)
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

# --- 模組引入 ---
from config.txf_calendar import get_current_session_offset
from ingestion.kafka_consumer import GaleKafkaConsumer
from data_schemas.txf_data_pb2 import Tick
from core.ring_buffer import TxfRingBuffer  # <--- 新增引用
from core.indicator_manager import IndicatorManager
from analysis.dashboard_server import start_dashboard_server

# 設定 Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
logger = logging.getLogger("CoreProcessor")

class CoreProcessor:
    def __init__(self, kafka_broker: str, group_id: str, tick_topic: str):
        self.kafka_broker = kafka_broker
        self.group_id = group_id
        self.tick_topic = tick_topic
        
        # 1. 初始化 Kafka Consumer (尚未連線)
        self.consumer: GaleKafkaConsumer = None
        
        # 2. 初始化 Ring Buffer (容量 200,000)
        self.ring_buffer = TxfRingBuffer(capacity=200000)

        self.indicator_manager = IndicatorManager(buffer_capacity=200000)
        
        # 狀態標記
        self.is_running = False

    async def initialize(self):
        """
        初始化階段：智慧時間判斷 -> 建立連線 -> 定位 Offset
        """
        # A. 智慧時間判斷
        start_offset, session_status = get_current_session_offset()
        
        logger.info(f"Session Check: Status={session_status}, StartOffset={start_offset}")

        if session_status == 'CLOSED':
            logger.warning("Market is currently CLOSED. Waiting for next session...")
            return False

        # B. 初始化 Consumer
        self.consumer = GaleKafkaConsumer(
            broker_url=self.kafka_broker,
            group_id=self.group_id,
            topic=self.tick_topic
        )
        
        # C. 連線並定位 (Seek)
        self.consumer.connect()
        try:
            self.consumer.seek_to_time(start_offset)
        except Exception as e:
            logger.error(f"Failed to seek offset: {e}")
            return False
            
        return True

    async def run(self):
        """
        核心運轉迴圈 (The Reactor Loop)
        """
        if not await self.initialize():
            logger.error("Initialization failed. Exiting.")
            return

        self.is_running = True

        # ==========================================
        # 📊 啟動視覺化儀表板 (Dashboard Thread)
        # ==========================================
        logger.info("Starting Dashboard Server on port 8050...")
        dash_thread = threading.Thread(
            target=start_dashboard_server, 
            args=(self.indicator_manager, 8050),
            daemon=True  # Daemon 表示主程式結束時，它也會跟著結束
        )
        dash_thread.start()
        # ==========================================
        
        logger.info("🚀 TXF Gale Engine Core Started - Processing Stream...")
        
        # 預先實例化 Protobuf 物件 (重用物件以減少記憶體分配)
        current_tick = Tick()

        try:
            # --- 極速消費迴圈 (Hot Path) ---
            async for raw_msg_bytes in self.consumer.consume_stream():
                if not raw_msg_bytes:
                    continue

                # 1. 反序列化 (Deserialization)
                try:
                    current_tick.ParseFromString(raw_msg_bytes)
                except Exception as e:
                    logger.error(f"Protobuf Parse Error: {e}")
                    continue

                # 2. 寫入 Ring Buffer (O(1) Operation)
                #    這是數據進入計算核心的關鍵一步
                self.ring_buffer.write_tick(current_tick)

                # 🚀 3. 觸發計算 (Trigger Calculation)
                # 獲取 Buffer 快照
                snapshot = self.ring_buffer.get_snapshot()
                
                # 執行計算並更新歷史
                self.indicator_manager.on_tick(snapshot)
                
                # ==========================================
                # (Optional) 每 1000 筆 Log 一次證明活著
                count = self.ring_buffer.head
                if count % 1000 == 0:
                    # 顯示最新價格與 Buffer 頭部位置
                    logger.info(f"Tick#{count} | Price: {current_tick.close/10000.0} | TAIEX: {current_tick.underlying_price/10000.0} | TotalVol: {current_tick.total_volume}")

        except asyncio.CancelledError:
            logger.info("CoreProcessor task cancelled.")
        finally:
            if self.consumer:
                self.consumer.close()
            logger.info("CoreProcessor stopped.")

# -------------------------------------------------------
# 單獨測試入口
# -------------------------------------------------------
if __name__ == "__main__":
    # 測試參數 (請替換為真實環境)
    BROKER = "192.168.1.50:9092"
    GROUP = "gale_dev_test"
    TOPIC = "txf-tick" # 確保 Kafka 有這個 Topic

    processor = CoreProcessor(BROKER, GROUP, TOPIC)
    
    try:
        asyncio.run(processor.run())
    except KeyboardInterrupt:
        pass