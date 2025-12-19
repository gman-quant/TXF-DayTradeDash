
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
        from collections import deque
        pending_ticks = deque()  # Buffer for ticks waiting for quotes
        MAX_TICK_WAIT_MS = 50 # Max wait time for a tick before forcing write (Lag Safety)
        
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
                            
                            # [Quote Sanitization] 報價淨化機制
                            # 目的：阻擋來自未來的異常報價 (例如 09:12 出現 21:30 的 Quote)。
                            # 效益：確保 LOBEngine.max_seen_ts (內部時鐘) 永遠不被髒資料汙染，維持高可信度。
                            if end_ts_ms and q.timestamp_ms > end_ts_ms:
                                continue 

                            self.lob_engine.update(q)
                            quote_count += 1
                            
                        elif topic == self.args.topic: # 'txf-tick'
                            # --- Tick Handling ---
                            t = Tick()
                            t.ParseFromString(raw_bytes)
                            
                            # [Outlier Protection] 異常停機防護機制
                            # 概念：採用「觸發 (Trigger) + 准許 (Permission)」雙重驗證。
                            # 1. 觸發：收到「超時 Tick」(t.timestamp > 收盤時間)，即視為潛在的結束信號。
                            # 2. 准許：檢查「系統內部時鐘」是否已進入收盤警戒區 (Permission Window)。
                            if end_ts_ms and t.timestamp_ms > end_ts_ms:
                                
                                # [Clock Source] 報價驅動時鐘 (Quote-Driven Clock)
                                # 策略：使用 max_seen_ts 作為基準。因為 Quotes 遠比 Ticks 密集，即使尾盤 Tick 無量斷流，
                                #       Quotes 依然會持續推動時間，避免 Deadlock。
                                current_stream_time = self.lob_engine.max_seen_ts
                                if current_stream_time == 0:
                                     current_stream_time = t.timestamp_ms 
                                
                                time_remaining = end_ts_ms - current_stream_time
                                
                                # [Threshold] 准許門檻：30分鐘 (1,800,000ms)
                                # 邏輯：
                                # A. 結算日相容性：結算日 13:30 收盤，距離 13:45 僅 15分鐘，符合 < 30分鐘 (PASS)。
                                # B. 尾盤無量保護：即使最後 10 分鐘沒 Tick，時鐘 (Quote) 依然會走到 13:35，符合 < 30分鐘 (PASS)。
                                # C. 早盤誤判防禦：09:xx 的 Outlier 距離收盤 > 4小時，不符合 < 30分鐘 (REJECT)。
                                if time_remaining < 1800000: 
                                    logger.info(f"🛑 Reached session end time via Tick: {end_offset}. (StreamTS: {current_stream_time})")
                                    ingestion_complete = True
                                    break
                                else:
                                    # [Reject] 拒絕異常信號
                                    # 雖然收到結束信號 (Trigger)，但內部時鐘顯示時間未到 (Permission Denied)。
                                    if processed_count % 1000 == 0:
                                         logger.warning(f"⚠️ Ignored Outlier Tick at {t.timestamp_ms} (Stream: {current_stream_time}). (Sampled Log)")
                                    
                                    continue # 丟棄此異常 Tick
                            
                            # Add to pending buffer
                            pending_ticks.append(t)
                            
                    except Exception as e:
                        logger.error(f"Processing Error: {e}")
                
                # [Revert] Removed outer loop break to allow future quotes to flush pending ticks
                # (This will cause log spam but ensures data completeness)

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
