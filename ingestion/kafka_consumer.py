# ingestion/kafka_consumer.py

import asyncio
import logging
from datetime import datetime
from typing import Callable, Optional

from confluent_kafka import Consumer, TopicPartition, KafkaError, Message

# 假設 Protobuf 編譯後的檔案已存在 (稍後我們會處理這部分)
# from data_schemas.txf_pb2 import Tick

class GaleKafkaConsumer:
    """
    TXF 疾風引擎專用 Kafka Consumer。
    職責：
    1. 連接 Kafka。
    2. 根據時間戳 (Timestamp) 精確定位 Offset。
    3. 以 Async Iterator 方式持續產出數據。
    """

    def __init__(self, broker_url: str, group_id: str, topic: str):
        self.broker_url = broker_url
        self.group_id = group_id
        self.topic = topic
        self.consumer: Optional[Consumer] = None
        self.logger = logging.getLogger(__name__)

    def connect(self):
        """建立 Kafka Consumer 連線"""
        conf = {
            'bootstrap.servers': self.broker_url,
            'group.id': self.group_id,
            'auto.offset.reset': 'latest',  # 預設行為，雖然我們會手動 seek
            'enable.auto.commit': False,    # 追求低延遲通常關閉自動 commit，或自行控制
        }
        self.consumer = Consumer(conf)
        self.logger.info(f"Connected to Kafka broker: {self.broker_url}")

    def seek_to_time(self, start_dt: datetime):
        """
        關鍵邏輯：根據 datetime 查找並定位 Offset。
        """
        if not self.consumer:
            raise RuntimeError("Consumer not connected. Call connect() first.")

        # 1. 轉換 datetime 為 Unix Timestamp (毫秒)
        # TXF 位於 UTC+8，假設傳入的 start_dt 已經是正確的本地時間 (naive or aware)
        # 這裡為了保險，先轉為 timestamp float 再乘 1000
        ts_ms = int(start_dt.timestamp() * 1000)

        self.logger.info(f"Seeking to time: {start_dt} (Timestamp: {ts_ms})")

        # 2. 獲取該 Topic 的分區 (假設單一分區 Partition 0，或是處理所有分區)
        # 先訂閱 Topic 才能獲取元數據或進行 assign
        # 為了精確控制，我們使用 assign 而不是 subscribe，這樣可以立即進行 seek
        partition = TopicPartition(self.topic, 0) # 假設 Tick 數據在 Partition 0
        
        # 3. 查詢該時間點對應的 Offset (offsets_for_times)
        # 設定 partition 的 offset 為目標時間戳
        partition.offset = ts_ms
        
        # 返回的是帶有新 offset 的 TopicPartition 列表
        offsets_found = self.consumer.offsets_for_times([partition], timeout=10.0)
        
        target_tp = offsets_found[0]
        
        if target_tp.offset == -1:
             self.logger.warning(f"No offset found for time {start_dt}. Starting from LATEST.")
             # 如果找不到 (例如時間太新)，這時可能需要 fallback 到 latest
             # 這裡我們先保持 assign，讓它自然讀取 (視 auto.offset.reset 而定) 或手動處理
        else:
            self.logger.info(f"Found offset {target_tp.offset} for partition {target_tp.partition}")
            # 4. 執行 Seek (定位)
            self.consumer.assign([target_tp]) # Assign 該分區
            self.consumer.seek(target_tp)     # 將指針移過去
            return

        # 如果沒找到特定 offset，也必須 assign 才能開始消費
        self.consumer.assign([partition])

    async def consume_stream(self):
        """
        Async Generator: 持續產生訊息。
        配合外部的 UVLOOP 運行。
        """
        if not self.consumer:
            raise RuntimeError("Consumer not connected.")

        self.logger.info("Starting async consumption loop...")
        
        # 使用 asyncio.get_event_loop().run_in_executor 來避免 poll 阻塞主線程
        loop = asyncio.get_running_loop()

        try:
            while True:
                # 這裡使用 run_in_executor 是為了讓 blocking 的 poll() 不會卡住 asyncio loop
                # timeout 設短一點可以讓 loop 有機會響應中斷
                msg = await loop.run_in_executor(None, self.consumer.poll, 0.1)

                if msg is None:
                    continue
                
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    else:
                        self.logger.error(f"Kafka Error: {msg.error()}")
                        continue
                
                # 成功獲取消息，yield 出去給 Core Processor 處理
                # 這裡傳回原始 bytes，反序列化交給下一層做，保持 Consumer 單純
                yield msg.value()

        except asyncio.CancelledError:
            self.logger.info("Consumption loop cancelled.")
        finally:
            self.close()

    def close(self):
        if self.consumer:
            self.consumer.close()
            self.logger.info("Kafka Consumer closed.")