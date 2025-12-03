# core/core_processor.py

import asyncio
import logging
import sys
import threading
from datetime import datetime

# 嘗試引入 uvloop 加速 asyncio (Linux/Mac 環境強烈建議)
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

# --- 模組引入 ---
from config.txf_calendar import get_current_session_offset
from ingestion.kafka_consumer import GaleKafkaConsumer
from data_schemas.txf_data_pb2 import Tick
from core.ring_buffer import TxfRingBuffer
from core.indicator_manager import IndicatorManager
from analysis.dashboard_server import start_dashboard_server

# =========================================================
# ⚙️ Logging 設定
# =========================================================
# 靜音 Werkzeug (Dash 伺服器) 的 HTTP 請求日誌
logging.getLogger('werkzeug').setLevel(logging.ERROR)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    stream=sys.stdout
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
        
        # ⚡️ 安全性優化：強制清空記憶體，避免殘留數據導致指標計算錯誤
        self.ring_buffer.clear()

        # 3. 初始化 Indicator Manager
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
        
        logger.info("🚀 TXF Gale Engine Core Started - Processing Stream...")
        
        # 預先實例化 Protobuf 物件 (重用物件以減少記憶體分配)
        current_tick = Tick()
        
        # ⚡️ 效能優化：方法預綁定 (Method Pre-binding)
        # 在高頻迴圈中，減少 self.xxx.yyy 的屬性查找開銷
        write_tick = self.ring_buffer.write_tick
        get_snapshot = self.ring_buffer.get_snapshot
        calc_indicators = self.indicator_manager.on_tick
        
        # 總處理筆數計數器
        processed_count = 0

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
                write_tick(current_tick)

                # 3. 觸發計算 (Trigger Calculation)
                # 獲取 Buffer 快照並傳遞給 Manager
                calc_indicators(get_snapshot())
                
                # ==========================================
                # (Optional) Log 心跳包
                # ==========================================
                processed_count += 1
                if processed_count % 5000 == 0: # 建議每 5000 筆印一次，減少 I/O 影響
                    logger.info(
                        f"Tick#{processed_count} | "
                        f"Price: {current_tick.close/10000.0:.0f} | "
                        f"TotalVol: {current_tick.total_volume}"
                    )

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
    TOPIC = "txf-tick" 

    processor = CoreProcessor(BROKER, GROUP, TOPIC)
    
    try:
        asyncio.run(processor.run())
    except KeyboardInterrupt:
        pass