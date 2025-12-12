
import subprocess
import sys
import os
import signal
import time
import argparse
import logging
from datetime import datetime

from gale.strategy.engine import StrategyServer

from gale.utils.log_utils import setup_logger

# Logging
logger = setup_logger("Supervisor")

class CoreSupervisor:
    """
    統一入口點 (Supervisor)。
    負責同時啟動 Ingestion Process 與 Strategy Server。
    """
    def __init__(self, args):
        self.args = args
        self.ingest_process = None
        self.strategy_server = None
        self.dash_process = None
        

    def _load_prev_close(self):
        """
        [Refactored] Use Infrastructure Module to load Prev Close.
        """
        # 如果是 History Mode，不需要昨收 (或者可以設為 0)
        # 不過 Ingestion Server 還是可以收，沒 harm
        target_date_str = self.args.date if self.args.mode == 'history' else datetime.now().strftime('%Y-%m-%d')
        
        # Call DB Module with logic based on Time/Mode
        if self.args.mode == 'live' and datetime.now().hour >= 15:
            op = '<='
        else:
            op = '<'
            
        from gale.infra.db import load_prev_close
        return load_prev_close(target_date_str, op=op)

    def start_ingestion(self):
        """啟動 Ingestion Process (獨立進程)"""
        if self.args.source == 'parquet':
            # [Parquet Replay Mode]
            logger.info("📡 Data Source: Parquet Replay")
            
            # [Smart Path Resolution]
            # 如果使用者只給日期 (--date)，嘗試自動推算 Data Lake 路徑
            target_file = self.args.file
            target_underlying = self.args.underlying
            
            if self.args.date and not target_file:
                # e.g. 2025-12-01
                try:
                    dt = datetime.strptime(self.args.date, '%Y-%m-%d')
                    start_year = dt.strftime('%Y')
                    start_month = dt.strftime('%m')
                    
                    # Hardcoded Data Lake Root (User's Environment)
                    DATA_LAKE_ROOT = "/Users/gtai/Projects/txf-data-lake/data/raw_ticks"
                    
                    # TXF Path
                    target_file = f"{DATA_LAKE_ROOT}/TXF/{start_year}/{start_month}/{self.args.date}_TXF_ticks.parquet"
                    logger.info(f"🔍 Auto-Resolved TXF Path: {target_file}")
                    
                    # TSE Path (Auto-resolve if not provided)
                    if not target_underlying:
                        target_underlying = f"{DATA_LAKE_ROOT}/TSE/{start_year}/{start_month}/{self.args.date}_TSE_ticks.parquet"
                        logger.info(f"🔍 Auto-Resolved TSE Path: {target_underlying}")
                        
                except Exception as e:
                    logger.warning(f"Failed to resolve path from date: {e}")

            # [Pre-flight Check] File Existence
            if not target_file or not os.path.exists(target_file):
                logger.error(f"❌ Critical Error: Parquet file not found: {target_file}")
                logger.error("   Please check the date or provide --file manually.")
                sys.exit(1) # Early Exit
                
            if target_underlying and not os.path.exists(target_underlying):
                logger.warning(f"⚠️ Warning: Underlying file not found: {target_underlying}")
                logger.warning("   Replay will continue without Underlying Price data.")
                target_underlying = None # Disable underlying if not found

            cmd = [sys.executable, "-m", "gale.feed.parquet",
                   str(target_file),
                   "--topic", self.args.topic,
                   "--speed", str(self.args.speed)]
            
            if target_underlying:
                cmd.extend(["--underlying", target_underlying])
                
        else:
            # [Kafka Live/History Mode]
            logger.info("📡 Data Source: Kafka Consumer")
            prev_close = self._load_prev_close()
            cmd = [sys.executable, "-m", "gale.feed.server", 
                   "--broker", self.args.broker,
                   "--group", self.args.group,
                   "--topic", self.args.topic,
                   "--prev-close", str(prev_close)]
            
            if self.args.mode == 'history':
                cmd.extend(["--mode", "history"])
                if self.args.date:
                    cmd.extend(["--date", self.args.date])
                if self.args.session:
                    cmd.extend(["--session", self.args.session])
        
        logger.info(f"Starting Ingestion Process: {' '.join(cmd)}")
        self.ingest_process = subprocess.Popen(cmd)

    def start_dashboard(self):
        """啟動 Dashboard Process (獨立進程)"""
        # [Dynamic Port Selection]
        # Live/Kafka -> 8050
        # Parquet Replay -> 8051
        port = 8051 if self.args.source == 'parquet' else 8050
        
        cmd = [sys.executable, "-m", "bin.start_dashboard", 
               "--topic", self.args.topic,
               "--port", str(port)]
        
        logger.info(f"Starting Dashboard Process: {' '.join(cmd)}")
        self.dash_process = subprocess.Popen(cmd)

    def start_strategy(self):
        """啟動 Strategy Logic (直接在當前進程跑)"""
        from gale.strategy.engine import StrategyServer
        server = StrategyServer(self.args)
        server.run()

    def run(self):
        try:
            # 1. Start Ingestion Subprocess
            self.start_ingestion()
            time.sleep(1) 
            
            # 2. Start Dashboard Subprocess (New!)
            self.start_dashboard()
            time.sleep(1)

            # 3. Start Strategy (in-process, blocks here)
            self.start_strategy()
            
        except KeyboardInterrupt:
            logger.info("Supervisor received Ctrl+C.")
        finally:
            self.cleanup()

    def cleanup(self):
        logger.info("Terminating subprocesses...")
        if self.ingest_process and self.ingest_process.poll() is None:
            self.ingest_process.terminate()
            try:
                self.ingest_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ingest_process.kill()
        
        if self.dash_process and self.dash_process.poll() is None:
            self.dash_process.terminate()
            try:
                self.dash_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.dash_process.kill()
                
        logger.info("All processes terminated.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TXF Gale Engine (Unified Launcher)")
    
    # [Data Source]
    parser.add_argument('--source', type=str, default='kafka', choices=['kafka', 'parquet'], help="Data Mode")
    
    # [Common Args]
    parser.add_argument('--topic', type=str, help="Shared Memory Topic")
    
    # [Kafka Args]
    parser.add_argument('--broker', type=str, default='192.168.1.50:9092')
    parser.add_argument('--group', type=str, default='gale_v1_unified')
    parser.add_argument('--mode', type=str, default='live', choices=['live', 'history'])
    parser.add_argument('--date', type=str, help='YYYY-MM-DD for history mode')
    parser.add_argument('--session', type=str, default='day', choices=['day', 'night'])
    
    # [Parquet Args]
    parser.add_argument('--file', type=str, help="Parquet File Path")
    parser.add_argument('--underlying', type=str, help="Underlying (TSE) Parquet File Path")
    parser.add_argument('--speed', type=float, default=1.0, help="Replay Speed")
    
    args = parser.parse_args()
    
    # [Auto Default Topic]
    if not args.topic:
        args.topic = 'txf-replay' if args.source == 'parquet' else 'txf-tick'
    
    supervisor = CoreSupervisor(args)
    supervisor.run()