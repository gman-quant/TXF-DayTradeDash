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

    def __init__(self, broker_url: str, group_id: str, topics: list):
        self.broker_url = broker_url
        self.group_id = group_id
        self.topics = topics
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
        """(Live 模式用) 定位到指定時間"""
        if not self.consumer: raise RuntimeError("Not connected")
        
        ts_ms = int(start_dt.timestamp() * 1000)
        self.logger.info(f"Seeking to time: {start_dt} (Timestamp: {ts_ms})")
        
        partitions = [TopicPartition(t, 0) for t in self.topics]
        for p in partitions: p.offset = ts_ms
            
        offsets_found = self.consumer.offsets_for_times(partitions, timeout=10.0)
        
        for tp in offsets_found:
            if tp.offset != -1:
                self.consumer.assign([tp])
                self.consumer.seek(tp)
                self.logger.info(f"Topic {tp.topic} seek to offset {tp.offset}")
            else:
                self.logger.warning(f"Topic {tp.topic} offset not found for time {start_dt}")
                self.consumer.assign([tp])

    def close(self):
        if self.consumer:
            self.consumer.close()
            self.logger.info("Kafka Consumer closed.")


    async def consume_stream(self, batch_size: int = 500):
        """
        Async Generator: 持續產生訊息列表 (Batch processing)。
        配合外部的 UVLOOP 運行。
        """
        if not self.consumer:
            raise RuntimeError("Consumer not connected.")

        self.logger.info(f"Starting async consumption loop (Batch Size: {batch_size})...")
        
        # 使用 asyncio.get_event_loop().run_in_executor 來避免 poll 阻塞主線程
        loop = asyncio.get_running_loop()

        try:
            while True:
                # 使用 consume 批次獲取，大幅減少 Context Switch
                # run_in_executor 仍然是必要的，因為 consume 在 C 層面是 blocking 的
                msgs = await loop.run_in_executor(None, self.consumer.consume, batch_size, 0.1)

                if not msgs:
                    # 沒訊息，繼續
                    continue
                
                valid_batch = []
                for msg in msgs:
                    if msg.error():
                        if msg.error().code() == KafkaError._PARTITION_EOF:
                            continue
                        else:
                            self.logger.error(f"Kafka Error: {msg.error()}")
                            continue
                    
                    # 收集有效訊息
                    valid_batch.append(msg.value())
                
                if valid_batch:
                    yield valid_batch

        except asyncio.CancelledError:
            self.logger.info("Consumption loop cancelled.")
        finally:
            self.close()


    # 歷史區間消費模式
    async def consume_history(self, start_dt: datetime, end_dt: datetime):
        """
        歷史回測模式：讀取 [start_dt, end_dt] 區間內的數據。
        """
        if not self.consumer: raise RuntimeError("Not connected")
        
        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = int(end_dt.timestamp() * 1000)
        
        self.logger.info(f"📜 History Mode: {start_dt} ~ {end_dt}")

        # 1. 準備 Partitions
        partitions = [TopicPartition(t, 0) for t in self.topics]
        
        # 2. 查找起點
        for p in partitions: p.offset = start_ts
        start_offsets = self.consumer.offsets_for_times(partitions)
        
        # 3. 查找終點
        for p in partitions: p.offset = end_ts
        end_offsets = self.consumer.offsets_for_times(partitions)
        
        end_offset_map = {}
        
        for tp in end_offsets:
            if tp.offset != -1:
                # 情況 A: 找到了具體的結束 Offset (代表資料在該時間點之後還有更多)
                end_offset_map[tp.topic] = tp.offset
            else:
                # 情況 B: 沒找到 (回傳 -1)，代表請求的結束時間比最新的資料還晚
                # ⚡️ 修正：查詢該 Partition 的 High Watermark (最末端 Offset)
                try:
                    # get_watermark_offsets 回傳 (low, high) tuple
                    _, high_watermark = self.consumer.get_watermark_offsets(tp)
                    end_offset_map[tp.topic] = high_watermark
                    self.logger.info(f"Topic {tp.topic} end time > latest data. Set end offset to High Watermark: {high_watermark}")
                except Exception as e:
                    self.logger.error(f"Failed to get watermark for {tp.topic}: {e}")

        # 4. 定位到起點 (Seek)
        to_assign = []
        for tp in start_offsets:
            if tp.offset != -1:
                to_assign.append(tp)
                target_end = end_offset_map.get(tp.topic, "Unknown")
                self.logger.info(f"Topic {tp.topic} start: {tp.offset} -> end: {target_end}")
        
        if not to_assign:
            self.logger.error("No data found for the start time.")
            return

        self.consumer.assign(to_assign)
        for tp in to_assign:
            self.consumer.seek(tp)

        # 5. 全速讀取迴圈
        loop = asyncio.get_running_loop()
        BATCH_SIZE = 2000
        
        # 確保 active_topics 是基於起點存在的 Topic，且我們知道終點在哪
        active_topics = set([tp.topic for tp in to_assign if tp.topic in end_offset_map])
        
        try:
            while active_topics:
                msgs = await loop.run_in_executor(None, self.consumer.consume, BATCH_SIZE, 0.1)
                
                if not msgs:
                    # 防止無窮迴圈：如果 active_topics 還有，但一直讀不到資料 (可能剛好卡在邊界)
                    # 可以在這裡加入超時判斷，或者簡單地讓它繼續嘗試 (視 Kafka 穩定性而定)
                    continue

                for msg in msgs:
                    if msg.error(): continue
                    
                    topic = msg.topic()
                    offset = msg.offset()
                    
                    # 檢查是否達到終點
                    if topic in end_offset_map and offset >= end_offset_map[topic]:
                        if topic in active_topics:
                            active_topics.remove(topic)
                            self.logger.info(f"🏁 Topic {topic} reached end offset {offset}.")
                        continue 
                    
                    yield msg

        finally:
            self.close()