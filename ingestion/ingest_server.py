
import asyncio
import logging
import signal
import sys
import argparse
from datetime import datetime
from data_schemas.txf_data_pb2 import Tick
from ingestion.kafka_consumer import GaleKafkaConsumer
from core.shared_memory import SharedRingBuffer
from config.txf_calendar import get_current_session_offset

# Try to use uvloop for better performance
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("IngestServer")

class IngestServer:
    def __init__(self, args):
        self.args = args
        self.running = True
        
        # 1. Initialize Shared Memory (Writer Mode: create=True)
        # 命名約定: "gale_shm_{topic}"
        self.shm_name = f"gale_shm_{args.topic}"
        try:
            self.ring_buffer = SharedRingBuffer(name=self.shm_name, capacity=200000, create=True)
            logger.info(f"✅ Shared Buffer Created: {self.shm_name}")
        except Exception as e:
            logger.error(f"Failed to create shared buffer: {e}")
            sys.exit(1)
            
        # 2. Initialize Kafka Consumer
        self.consumer = GaleKafkaConsumer(
            broker_url=args.broker,
            group_id=args.group,
            topics=[args.topic]
        )
        
        # Pre-allocate Tick object for reuse
        self.current_tick = Tick()

    async def start(self):
        """Main Loop"""
        self.consumer.connect()
        
        # 🛑 邏輯修正：自動 Seek 到當前盤別的開盤點
        # 這樣才能確保從開盤開始的資料都有被寫入，而不只是從現在開始
        start_offset, session_status = get_current_session_offset()
        logger.info(f"Session Check: Status={session_status}, StartOffset={start_offset}")

        if session_status == 'CLOSED':
             logger.warning("Market is currently CLOSED. Waiting for new data...")
        else:
             try:
                 self.consumer.seek_to_time(start_offset)
             except Exception as e:
                 logger.error(f"Failed to seek: {e}")
        
        logger.info("🚀 Ingestion Server (Writer) Started...")
        
        processed_count = 0
        write_tick = self.ring_buffer.write_tick # Bound method cache
        
        try:
            async for batch_msgs in self.consumer.consume_stream():
                if not self.running: break
                
                # Parse all messages in batch
                valid_ticks = []
                for raw_bytes in batch_msgs:
                    try:
                        t = Tick()
                        t.ParseFromString(raw_bytes)
                        valid_ticks.append(t)
                        processed_count += 1
                    except Exception as e:
                        logger.error(f"Processing Error: {e}")
                
                # 🔥 Vectorized Write
                if valid_ticks:
                    self.ring_buffer.write_batch(valid_ticks)
                
                # Batch log
                n_batch = len(valid_ticks)
                if n_batch > 0 and (processed_count % 10000 < n_batch):
                     logger.info(f"Written batch {n_batch} ticks. Total: {processed_count}. Latest: {valid_ticks[-1].close/10000.0}")
                    
        except asyncio.CancelledError:
            logger.info("Ingestion task cancelled.")
        finally:
            self.shutdown()

    def shutdown(self):
        self.running = False
        logger.info("Shutting down...")
        if self.consumer:
            self.consumer.close()
        
        # 重要的資源釋放
        if self.ring_buffer:
            self.ring_buffer.shutdown() # Unlink shared memory
            logger.info("Shared Memory unlinked.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TXF Ingestion Server (Writer)")
    parser.add_argument('--broker', type=str, default='192.168.1.50:9092')
    parser.add_argument('--group', type=str, default='gale_ingest_v1')
    parser.add_argument('--topic', type=str, default='txf-tick')
    
    args = parser.parse_args()
    
    server = IngestServer(args)
    
    # Handle Signals
    def signal_handler(sig, frame):
        if server.running:
            logger.info(f"Signal {sig} received, stopping IngestServer...")
            server.running = False
            # Force wake up loop if stuck (optional, but good practice)
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        asyncio.run(server.start())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.error(f"Unexpected Exit: {e}")
