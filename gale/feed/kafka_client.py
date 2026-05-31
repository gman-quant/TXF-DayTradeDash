# ingestion/kafka_consumer.py

import asyncio
import logging
import gc  # [新增] 匯入垃圾回收模組
from datetime import datetime
from typing import Callable, Optional

from confluent_kafka import Consumer, TopicPartition, KafkaError, Message

# 假設 Protobuf 編譯後的檔案已存在
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
            'auto.offset.reset': 'latest',  
            'enable.auto.commit': False,    
            
            # [HFT Optimization] 極致低延遲調校
            'fetch.min.bytes': 1,           
            'fetch.wait.max.ms': 1,        # [修改] 配合 Zero-Copy，將 10ms 降至 1ms，毫不等待
            'socket.nagle.disable': True,  # [修改] 關閉 Nagle 演算法，封包不准排隊
            'fetch.error.backoff.ms': 0,   # [新增] 發生微小錯誤時立刻重試
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
        
        to_assign = []
        for tp in offsets_found:
            to_assign.append(tp)
        
        if to_assign:
            self.consumer.assign(to_assign)
            
        for tp in offsets_found:
            if tp.offset != -1:
                self.consumer.seek(tp)
                self.logger.info(f"Topic {tp.topic} seek to offset {tp.offset}")
            else:
                self.logger.warning(f"Topic {tp.topic} offset not found for time {start_dt}")

    def close(self):
        if self.consumer:
            self.consumer.close()
            self.logger.info("Kafka Consumer closed.")
            self.consumer = None 


    async def consume_stream(self, batch_size: int = 500, running_check: Callable[[], bool] = None):
        """
        Async Generator: 持續產生訊息列表 (Batch processing)。
        配合外部的 UVLOOP 運行。
        """
        if not self.consumer:
            raise RuntimeError("Consumer not connected.")

        self.logger.info(f"Starting async consumption loop (Batch Size: {batch_size})...")
        loop = asyncio.get_running_loop()

        # [新增] 進入熱徑前，強制關閉垃圾回收機制，避免盤中快市時發生 Latency Spike
        gc.disable()
        self.logger.info("⚡ Garbage Collection disabled for Hot Path.")

        try:
            while True:
                if running_check and not running_check():
                    self.logger.info("Running check returned False. Stopping loop.")
                    break
                    
                msgs = await loop.run_in_executor(None, self.consumer.consume, batch_size, 0.1)

                if not msgs:
                    yield []
                    continue
                
                valid_batch = []
                for msg in msgs:
                    if msg.error():
                        if msg.error().code() == KafkaError._PARTITION_EOF:
                            continue
                        else:
                            self.logger.error(f"Kafka Error: {msg.error()}")
                            continue
                    
                    valid_batch.append(msg)
                
                yield valid_batch

        except asyncio.CancelledError:
            self.logger.info("Consumption loop cancelled.")
        finally:
            self.close()
            # [新增] 退出熱徑迴圈後，恢復垃圾回收並手動清理一次
            gc.enable()
            gc.collect()
            self.logger.info("🧹 Garbage Collection re-enabled and memory cleaned.")


    async def consume_history(self, start_dt: datetime, end_dt: datetime):
        """
        歷史回測模式：讀取 [start_dt, end_dt] 區間內的數據。
        (此處刻意不關閉 GC，因為歷史回補會瞬間產生海量物件，需讓 GC 正常運作避免 OOM)
        """
        if not self.consumer: raise RuntimeError("Not connected")
        
        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = int(end_dt.timestamp() * 1000)
        
        self.logger.info(f"📜 History Mode: {start_dt} ~ {end_dt}")

        partitions = [TopicPartition(t, 0) for t in self.topics]
        
        for p in partitions: p.offset = start_ts
        start_offsets = self.consumer.offsets_for_times(partitions)
        
        for p in partitions: p.offset = end_ts
        end_offsets = self.consumer.offsets_for_times(partitions)
        
        end_offset_map = {}
        
        for tp in end_offsets:
            if tp.offset != -1:
                end_offset_map[tp.topic] = tp.offset
            else:
                try:
                    _, high_watermark = self.consumer.get_watermark_offsets(tp)
                    end_offset_map[tp.topic] = high_watermark
                    self.logger.info(f"Topic {tp.topic} end time > latest data. Set end offset to High Watermark: {high_watermark}")
                except Exception as e:
                    self.logger.error(f"Failed to get watermark for {tp.topic}: {e}")

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

        loop = asyncio.get_running_loop()
        BATCH_SIZE = 2000
        
        active_topics = set([tp.topic for tp in to_assign if tp.topic in end_offset_map])
        
        idle_count = 0
        try:
            while active_topics:
                msgs = await loop.run_in_executor(None, self.consumer.consume, BATCH_SIZE, 0.1)
                
                if not msgs:
                    idle_count += 1
                    if idle_count > 30:
                        self.logger.info(f"⏳ No more historical data (Idle timeout). Forcing completion.")
                        break
                    continue
                else:
                    idle_count = 0

                for msg in msgs:
                    if msg.error(): continue
                    
                    topic = msg.topic()
                    offset = msg.offset()
                    
                    if topic in end_offset_map and offset >= end_offset_map[topic]:
                        if topic in active_topics:
                            active_topics.remove(topic)
                            self.logger.info(f"🏁 Topic {topic} reached end offset {offset}.")
                        continue 
                    
                    yield msg

        finally:
            self.close()