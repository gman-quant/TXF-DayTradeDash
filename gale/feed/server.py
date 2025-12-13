
import asyncio
import logging
import signal
import sys
import argparse
from datetime import datetime
from data_schemas.txf_data_pb2 import Tick
from gale.feed.adapter import GaleKafkaConsumer
from gale.infra.memory import SharedRingBuffer
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
            
            # [New] Write Reference Price to Header
            if args.prev_close > 0:
                self.ring_buffer.prev_close = args.prev_close
                logger.info(f"✅ Set Prev Close Price: {args.prev_close}")
                
        except Exception as e:
            logger.error(f"Failed to create shared buffer: {e}")
            sys.exit(1)
            
        # 2. Initialize Kafka Consumer
        # [LOB Integration] Subscribe to both Tick and BidAsk
        topics = [args.topic, 'txf-bidask']
        logger.info(f"Subscribing to topics: {topics}")
        
        self.consumer = GaleKafkaConsumer(
            broker_url=args.broker,
            group_id=args.group,
            topics=topics
        )
        
        # 3. [LOB Integration] Initialize LOB Engine
        from gale.alpha.lob import LOBEngine
        self.lob_engine = LOBEngine()
        
        # Pre-allocate Tick object for reuse
        self.current_tick = Tick()

    async def start(self):
        """Main Loop (Strict Micro-Sync Sequencer)"""
        self.consumer.connect()
        
        # Import Proto here to be safe
        from data_schemas.txf_data_pb2 import BidAsk
        
        if self.args.mode == 'history':
            # [History Mode] Seek to specific historical session
            from config.txf_calendar import get_history_range
            if not self.args.date:
                 logger.error("History mode requires --date YYYY-MM-DD")
                 sys.exit(1)
            
            start_offset, end_offset = get_history_range(self.args.date, self.args.session)
            logger.info(f"🕰 Time Machine Mode: Replaying {self.args.date} ({self.args.session})")
            
            try:
                self.consumer.seek_to_time(start_offset)
            except Exception as e:
                logger.error(f"Failed to seek history: {e}")
                
        else:
            # [Live Mode]
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
        quote_count = 0 
        forced_flush_count = 0
        
        # [Sync Buffer]
        pending_ticks = []  # Buffer for ticks waiting for quotes
        MAX_TICK_WAIT_MS = 50 # Max wait time for a tick before forcing write (Lag Safety)
        
        try:
            async for batch_msgs in self.consumer.consume_stream(running_check=lambda: self.running):
                if not self.running: break
                
                # Pre-calc end timestamp if in history mode
                end_ts_ms = None
                if self.args.mode == 'history' and 'end_offset' in locals():
                    end_ts_ms = int(end_offset.timestamp() * 1000)

                # --- 1. Process Messages (Sequencer) ---
                for msg in batch_msgs:
                    # Check completion
                    # (Simplified: check if ANY message is past end time? Or just Ticks?)
                    # Usually checking Ticks is enough for termination.
                    
                    topic = msg.topic()
                    raw_bytes = msg.value()
                    
                    try:
                        if topic == 'txf-bidask':
                            # --- Quote Handling ---
                            q = BidAsk()
                            q.ParseFromString(raw_bytes)
                            self.lob_engine.update(q)
                            quote_count += 1
                            
                        elif topic == self.args.topic: # 'txf-tick'
                            # --- Tick Handling ---
                            t = Tick()
                            t.ParseFromString(raw_bytes)
                            
                            if end_ts_ms and t.timestamp_ms > end_ts_ms:
                                logger.info(f"🛑 Reached session end time via Tick: {end_offset}.")
                                self.running = False
                                break
                            
                            # Add to pending buffer
                            pending_ticks.append(t)
                            
                    except Exception as e:
                        logger.error(f"Processing Error: {e}")

                # --- 2. Flushing Logic (Micro-Sync) ---
                # Check pending ticks against LOB Watermark
                
                ready_ticks = []
                ready_lob_metrics = [] # [Fix] Parallel list for LOB data
                remaining_ticks = []
                
                # Watermark from LOB Engine
                max_quote_ts = self.lob_engine.max_seen_ts
                
                current_forced = False
                
                for t in pending_ticks:
                    tick_ts = t.timestamp_ms
                    
                    # Condition: Safe to write if we have seen quotes BEYOND this tick
                    is_safe = (max_quote_ts >= tick_ts)
                    
                    # Force Condition: If Tick is too old compared to 'current processing'?
                    # Or simpler: if we have buffered too many ticks (e.g. > 100000), force flush to prevent OOM
                    # [Tuning] High volume bursts require larger buffer
                    is_forced = (len(pending_ticks) > 100000) 
                    
                    if is_safe or is_forced:
                        if is_forced: current_forced = True
                        
                        # [Sampling]
                        obi, ofi, lag = self.lob_engine.get_metrics(tick_ts)
                        
                        ready_ticks.append(t)
                        ready_lob_metrics.append((obi, ofi, lag)) # Store tuple
                    else:
                        remaining_ticks.append(t)
                
                if current_forced:
                    forced_flush_count += 1
                
                # Commit ready ticks
                if ready_ticks:
                    self.ring_buffer.write_batch(ready_ticks, lob_data=ready_lob_metrics)
                    processed_count += len(ready_ticks)
                    
                # Update buffer
                pending_ticks = remaining_ticks
                
                # Batch log
                if processed_count > 0 and (processed_count % 5000 < len(ready_ticks)):
                     logger.info(f"Processed Ticks: {processed_count}, Quotes: {quote_count}, Pending: {len(pending_ticks)}, Forced Flushes: {forced_flush_count}. LOB Watermark: {max_quote_ts}")

                if not self.running:
                    break
                    
            # End of loop
            logger.info("✅ Ingestion Completed.")
            self.consumer.close()
            
            # Keep alive
            while self.running:
                await asyncio.sleep(1)
                    
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
    
    # [Restored] History Mode Arguments
    parser.add_argument('--mode', type=str, default='live', choices=['live', 'history'])
    parser.add_argument('--date', type=str, help='YYYY-MM-DD for history mode')
    parser.add_argument('--session', type=str, default='day', choices=['day', 'night'])
    parser.add_argument('--prev-close', type=float, default=0.0, help='Reference price (Yesterday Close)')
    
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
