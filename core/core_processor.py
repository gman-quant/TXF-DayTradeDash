# core/core_processor.py

import asyncio
import logging
import sys
import argparse
import time
import multiprocessing
import signal
import os
from datetime import datetime

# 0. 解決 PYTHONPATH 問題 (確保可以 Import Project Root)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 嘗試引入 uvloop 加速 asyncio
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

# --- 模組引入 ---
from config.txf_calendar import get_current_session_offset, get_history_range
from ingestion.kafka_consumer import GaleKafkaConsumer
from data_schemas.txf_data_pb2 import Tick
from core.ring_buffer import TxfRingBuffer
from core.indicator_manager import IndicatorManager
from analysis.dashboard_server import start_dashboard_server

# =========================================================
# ⚙️ 日誌設定 (Logging Setup)
# =========================================================
def setup_logger(name):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    return logger


# =========================================================
# 🏭 寫入進程 (Ingestion / Writer)
# =========================================================
def run_ingestion_process(args):
    """
    [Writer Process]
    負責接收 Kafka 資料，並高效寫入 Shared Memory 環狀緩衝區。
    """
    logger = setup_logger("IngestionWorker")
    logger.info("啟動資料接收進程 (Ingestion Process)...")

    # 1. 初始化 RingBuffer (建立共享記憶體)
    ring_buffer = TxfRingBuffer(capacity=200000, create_shm=True)
    ring_buffer.clear()

    write_tick = ring_buffer.write_tick

    # 2. 初始化 Kafka Consumer
    consumer = GaleKafkaConsumer(
        broker_url=args.broker,
        group_id=args.group,
        topics=[args.topic]
    )

    try:
        consumer.connect()
    except Exception as e:
        logger.error(f"Kafka 連線失敗: {e}")
        return

    current_tick = Tick()
    processed_count = 0
    
    # 3. 定義非同步消費迴圈
    async def consume_loop():
        nonlocal processed_count
        data_generator = None
        
        # 模式判定
        if args.mode == 'live':
            # 實盤模式：定位到當前開盤位置
            start_offset, session_status = get_current_session_offset()
            if session_status == 'CLOSED':
                logger.warning("目前市場已收盤")
                # return # 視需求決定是否退出

            try:
                consumer.seek_to_time(start_offset)
                logger.info(f"Seek 至開盤時間戳: {start_offset}")
            except Exception as e:
                logger.error(f"Seek 失敗: {e}")
                
            data_generator = consumer.consume_stream()
            
        elif args.mode == 'history':
            # 回測模式：讀取指定日期區間
            start_dt, end_dt = get_history_range(args.date, args.session)
            logger.info(f"回測區間: {start_dt} ~ {end_dt}")
            data_generator = consumer.consume_history(start_dt, end_dt)

        if not data_generator:
            return

        # --- 資料處理迴圈 ---
        async for raw_msg_obj in data_generator:
            raw_msg_bytes = raw_msg_obj.value() if hasattr(raw_msg_obj, 'value') else raw_msg_obj
            
            if not raw_msg_bytes: continue

            try:
                current_tick.ParseFromString(raw_msg_bytes)
                
                # [VALIDATION] 濾除無效或不完整的 Tick (避免 0 Timestamp 卡住 Reader)
                if current_tick.timestamp_ms <= 0:
                    logger.warning(f"⚠️ 忽略無效 Tick (TS=0): {current_tick}")
                    continue
                    
                write_tick(current_tick) # O(1) 寫入共享記憶體
                
                processed_count += 1
                
                # [DEBUG] 前 10 筆資料詳細打印，確保入料正確
                if processed_count <= 10:
                    logger.info(f"✅ Tick Received: TS={current_tick.timestamp_ms}, Price={current_tick.close}")
                
                if processed_count % 5000 == 0:
                     logger.info(f"已處理 {processed_count} 筆 Tick. 最新價: {current_tick.close/10000.0}")
                    
            except Exception as e:
                logger.error(f"Protobuf 解析錯誤: {e}")
                continue

        # 回測結束後保持 Process 存活，避免 SHM 被回收
        if args.mode == 'history':
            logger.info("歷史資料回放完成，進入待機模式。")
            while True:
                await asyncio.sleep(1)

    try:
        asyncio.run(consume_loop())
    except KeyboardInterrupt:
        logger.info("接收進程中斷。")
    finally:
        consumer.close()
        # 注意：Writer 不主動 unlink shared memory，以免影響 Reader

# =========================================================
# 🧠 策略與介面進程 (Strategy & UI / Reader)
# =========================================================
def run_strategy_process(args):
    """
    [Reader Process]
    從 Shared Memory 讀取最新數據，計算指標，並驅動 Dashboard 更新。
    """
    logger = setup_logger("StrategyWorker")
    logger.info("啟動策略與 UI 進程...")
    
    # 稍作等待確保 Writer 已建立 SHM
    time.sleep(1) 
    
    try:
        # 1. 連接 RingBuffer (Attach 模式)
        ring_buffer = TxfRingBuffer(capacity=200000, create_shm=False)
        get_snapshot = ring_buffer.get_snapshot
        
        # 2. 初始化指標管理器
        indicator_manager = IndicatorManager(buffer_capacity=200000)

        # 3. 啟動 Dashboard 伺服器 (Thread)
        import threading
        dash_thread = threading.Thread(
            target=start_dashboard_server, 
            args=(indicator_manager, 8050),
            daemon=True
        )
        dash_thread.start()
        logger.info("Dashboard 伺服器已啟動在此 Process 中。")

        # 4. 主迴圈 (極速同步)
        local_head = -1
        sync_count = 0
        
        while True:
            # 必須手動刷新 Shared Memory 狀態，否則永遠讀不到變更
            ring_buffer.refresh_state()
            
            # 獲取当前 Writer 的寫入位置
            target_head = ring_buffer.head
            
            # 若有新資料
            if target_head != local_head:
                try:
                    # 同步資料區塊到本地指標管理器
                    indicator_manager.sync_from_buffer(ring_buffer, local_head, target_head)
                    local_head = target_head
                    
                    sync_count += 1
                    if sync_count % 100 == 0:
                        logger.info(f"Reader Sync: Dispatched {sync_count} batches. Current Head: {target_head}")
                        
                except Exception as e:
                    logger.error(f"指標計算錯誤: {e}")
                    import traceback
                    traceback.print_exc()

            # 短暫休眠讓出 CPU (頻率可調)
            time.sleep(0.001) 

    except FileNotFoundError:
        logger.error("找不到 Shared Memory 文件，請先啟動 Writer (Ingestion Process)。")
    except KeyboardInterrupt:
        logger.info("策略進程中斷。")
    except Exception as e:
        logger.error(f"策略進程發生未知錯誤: {e}")

# =========================================================
# 🚀 主程式入口 (Main Entry)
# =========================================================
if __name__ == "__main__":
    # 解析命令列參數
    parser = argparse.ArgumentParser(description="TXF Gale Engine v2.0 (High Performance)")
    
    # 必要參數 (現在預設為 live)
    parser.add_argument('--mode', choices=['live', 'history'], default='live', help="運行模式: live (實盤) 或 history (回測)")
    
    # Kafka 設定
    parser.add_argument('--broker', default='192.168.1.50:9092', help="Kafka Broker 地址")
    parser.add_argument('--topic', default='txf-tick', help="Kafka Topic 名稱")
    parser.add_argument('--group', default='gale_engine_v1', help="Consumer Group ID")
    
    # 回測參數
    parser.add_argument('--date', help="回測日期 (YYYY-MM-DD)")
    parser.add_argument('--session', default='regular', choices=['regular', 'afterhours', 'full'], help="回測時段")

    args = parser.parse_args()

    # 優雅關閉信號處理
    def signal_handler(sig, frame):
        print("\n[系統] 接收到關閉信號，正在停止所有 Process...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("=========================================")
    print("   🌪️  TXF GALE ENGINE v2.0 LAUNCHING   ")
    print("=========================================")

    # 啟動多進程架構 (Writer + Reader)
    # 必須使用 'spawn' 模式 (macOS 預設但明確指定較好)
    try:
        multiprocessing.set_start_method('spawn', force=True)
    except RuntimeError:
        pass

    p_writer = multiprocessing.Process(target=run_ingestion_process, args=(args,))
    p_reader = multiprocessing.Process(target=run_strategy_process, args=(args,))
    
    p_writer.start()
    p_reader.start()
    
    try:
        p_writer.join()
        p_reader.join()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        p_writer.terminate()
        p_reader.terminate()
        p_writer.join()
        p_reader.join()
        print("Done.")