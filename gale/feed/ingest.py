
import asyncio
import logging
import signal
import sys
import argparse
from datetime import datetime
from data_schemas.txf_data_pb2 import Tick
from gale.feed.kafka_client import GaleKafkaConsumer
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
        from gale.alpha.orderbook import LOBEngine
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
        from collections import deque
        pending_ticks = deque()  # Buffer for ticks waiting for quotes
        MAX_TICK_WAIT_MS = 50 # Max wait time for a tick before forcing write (Lag Safety)
        
        # [Drain Mode] Variables
        tick_ingestion_complete = False
        ingestion_complete = False

        # Pre-calc end timestamp if in history mode
        end_ts_ms = None
        if self.args.mode == 'history' and 'end_offset' in locals():
            end_ts_ms = int(end_offset.timestamp() * 1000)
            logger.info(f"🛑 Session End Timestamp set to: {end_ts_ms} ({end_offset})")

        try:
            async for batch_msgs in self.consumer.consume_stream(running_check=lambda: self.running):
                if not self.running: break
                
                if ingestion_complete:
                    break

                # --- 1. Process Messages (Sequencer) ---
                for msg in batch_msgs:
                    topic = msg.topic()
                    raw_bytes = msg.value()
                    
                    try:
                        if topic == 'txf-bidask':
                            # --- Quote Handling ---
                            q = BidAsk()
                            q.ParseFromString(raw_bytes)
                            
                            # [Quote Sanitization] 
                            if end_ts_ms and q.timestamp_ms > end_ts_ms:
                                # In Drain Mode, if we run out of valid quotes (past end time), we stop.
                                if tick_ingestion_complete:
                                     logger.info(f"🛑 Quote stream reached session end ({q.timestamp_ms}). Drain complete.")
                                     ingestion_complete = True # Real exit
                                     break
                                continue 

                            self.lob_engine.update(q)
                            quote_count += 1
                            
                        elif topic == self.args.topic: # 'txf-tick'
                            # [Drain Mode] Ignore new ticks if we are draining
                            if tick_ingestion_complete:
                                continue

                            # --- Tick Handling ---
                            t = Tick()
                            t.ParseFromString(raw_bytes)
                            
                            # [Outlier Protection] and [Session End Trigger]
                            if end_ts_ms and t.timestamp_ms > end_ts_ms:
                                current_stream_time = self.lob_engine.max_seen_ts
                                if current_stream_time == 0: current_stream_time = t.timestamp_ms 
                                
                                time_remaining = end_ts_ms - current_stream_time
                                
                                if time_remaining < 1800000: 
                                    logger.info(f"🛑 Reached session end time via Tick: {end_offset}. (StreamTS: {current_stream_time})")
                                    logger.info("🌊 Entering Drain Mode: Waiting for quotes to flush pending ticks...")
                                    tick_ingestion_complete = True
                                    # DO NOT BREAK LOOP HERE. Continue to consume quotes.
                                    continue 
                                else:
                                    # Outlier
                                    if processed_count % 1000 == 0:
                                         logger.warning(f"⚠️ Ignored Outlier Tick at {t.timestamp_ms} (Stream: {current_stream_time}).")
                                    continue
                            
                            # Add to pending buffer
                            pending_ticks.append(t)
                            
                    except Exception as e:
                        logger.error(f"Processing Error: {e}")
                
                # Check for Drain Completion
                # If we are in drain mode and pending buffer is empty, we are done.
                if tick_ingestion_complete and not pending_ticks:
                    logger.info("✅ Drain Mode Empty: All ticks processed.")
                    ingestion_complete = True
                
                if ingestion_complete:
                    break

                # --- 2. Flushing Logic (Micro-Sync Optimized) ---
                # Check pending ticks against LOB Watermark
                # [Optimization] Use peek/popleft to avoid full list scan (O(N) -> O(k))
                
                ready_ticks = []
                ready_lob_metrics = [] 
                
                # Watermark from LOB Engine
                max_quote_ts = self.lob_engine.max_seen_ts
                
                current_forced = False
                
                # Iterate while there are ticks AND (safe OR forced)
                while pending_ticks:
                    t = pending_ticks[0] # Peek
                    tick_ts = t.timestamp_ms
                    
                    is_safe = (max_quote_ts >= tick_ts)
                    # [Tuning] High volume bursts require larger buffer
                    is_forced = (len(pending_ticks) > 100000)
                    
                    if is_safe or is_forced:
                         if is_forced: current_forced = True
                         
                         # Pop only when ready to process
                         pending_ticks.popleft()
                         
                         # [Sampling]
                         obi, ofi, lag = self.lob_engine.get_metrics(tick_ts)
                         
                         ready_ticks.append(t)
                         ready_lob_metrics.append((obi, ofi, lag))
                    else:
                         # Strict ordering: if Head is unsafe, and Ticks are ordered, Tail is also unsafe.
                         # Stop checking to save CPU.
                         break
                
                if current_forced:
                    forced_flush_count += 1
                
                # Commit ready ticks
                if ready_ticks:
                    self.ring_buffer.write_batch(ready_ticks, lob_data=ready_lob_metrics)
                    processed_count += len(ready_ticks)
                
                # Batch log
                log_interval = 5000 if self.args.mode == 'history' else 2000
                if processed_count > 0 and (processed_count % log_interval < len(ready_ticks)):
                     # Calculate average lag for this batch
                     batch_lags = [m[2] for m in ready_lob_metrics]
                     avg_lag = sum(batch_lags) / len(batch_lags) if batch_lags else 0.0
                     
                     logger.info(f"Processed Ticks: {processed_count}, Quotes: {quote_count}, Pending: {len(pending_ticks)}. LOB Lag: {avg_lag:.1f}ms. Watermark: {max_quote_ts}")

                # [Optimization] Yield to event loop to reduce CPU usage
                await asyncio.sleep(0.001)

                if not self.running or ingestion_complete:
                    break
                    
            # End of loop
            
            # [Final Flush] Ensure all pending data is written
            if pending_ticks:
                logger.info(f"🧹 Final Flush: Writing {len(pending_ticks)} pending ticks...")
                ready_ticks = []
                ready_lob_metrics = []
                
                while pending_ticks:
                    t = pending_ticks.popleft()
                    # Calculate metrics with current state (Force)
                    obi, ofi, lag = self.lob_engine.get_metrics(t.timestamp_ms)
                    
                    ready_ticks.append(t)
                    ready_lob_metrics.append((obi, ofi, lag))
                    
                self.ring_buffer.write_batch(ready_ticks, lob_data=ready_lob_metrics)
                processed_count += len(ready_ticks)
                logger.info(f"✅ Final Flush Written. Total Processed: {processed_count}")

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
