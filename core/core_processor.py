
import subprocess
import sys
import os
import signal
import time
import argparse
import logging
from core.strategy_server import StrategyServer

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("CoreSupervisor")

class CoreSupervisor:
    """
    統一入口點 (Supervisor)。
    負責同時啟動 Ingestion Process 與 Strategy Server。
    """
    def __init__(self, args):
        self.args = args
        self.ingest_process = None
        self.strategy_server = None
        
    def start_ingestion(self):
        """啟動資料接收進程 (Writer)"""
        cmd = [sys.executable, "-m", "ingestion.ingest_server", 
               "--broker", self.args.broker, 
               "--group", self.args.group, 
               "--topic", self.args.topic]
        
        logger.info(f"🚀 Launching Ingestion Process: {' '.join(cmd)}")
        self.ingest_process = subprocess.Popen(
            cmd,
            stdout=sys.stdout, # 讓輸出直接顯示在同一個終端機，或者改成 subprocess.DEVNULL 隱藏
            stderr=sys.stderr
        )
        
    def start_strategy(self):
        """啟動策略邏輯 (在本進程運行)"""
        logger.info("🚀 Launching Strategy Engine (in-process)...")
        # 直接實例化 StrategyServer 並運行
        # 注意：StrategyServer.run() 是一個阻塞呼叫 (While loop)
        self.strategy_server = StrategyServer(self.args)
        self.strategy_server.run() # This will block until KeyboardInterrupt or error

    def run(self):
        try:
            # 1. Start Ingestion
            self.start_ingestion()
            
            # 給一點時間讓 Ingestion 建立 Shared Memory (雖然 Strategy 有 retry 機制，但這樣比較乾淨)
            time.sleep(2)
            
            # 2. Start Strategy (Blocks here)
            self.start_strategy()
            
        except KeyboardInterrupt:
            logger.info("Supervisor received Ctrl+C.")
        finally:
            self.cleanup()

    def cleanup(self):
        logger.info("Initializing shutdown sequence...")
        
        # Shutdown Strategy (Already stopped if we are here, but just in case)
        # StrategyServer cleanup is handled inside its own finally block usually,
        # but here we are in Supervisor.
        
        # Shutdown Ingestion
        if self.ingest_process:
            if self.ingest_process.poll() is None: # Still running
                logger.info("Terminating Ingestion Process...")
                self.ingest_process.terminate()
                try:
                    self.ingest_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("Ingestion process unresponsive, killing...")
                    self.ingest_process.kill()
            logger.info("Ingestion Process stopped.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TXF Gale Engine (Unified Launcher)")
    parser.add_argument('--broker', type=str, default='192.168.1.50:9092')
    parser.add_argument('--group', type=str, default='gale_v1_unified')
    parser.add_argument('--topic', type=str, default='txf-tick')
    
    args = parser.parse_args()
    
    supervisor = CoreSupervisor(args)
    supervisor.run()