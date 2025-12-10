
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
        # [New] Auto Load Prev Close
        prev_close = self._load_prev_close()

        cmd = [sys.executable, "-m", "gale.feed.server", 
               "--broker", self.args.broker,
               "--group", self.args.group,
               "--topic", self.args.topic,
               "--prev-close", str(prev_close)] # Pass it
        
        # [Restored] Pass History Mode Args
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
        cmd = [sys.executable, "-m", "bin.start_dashboard", 
               "--topic", self.args.topic]
        
        logger.info(f"Starting Dashboard Process: {' '.join(cmd)}")
        self.dash_process = subprocess.Popen(cmd)

    def start_strategy(self):
        """啟動 Strategy Logic (直接在當前進程跑)"""
        # 未來也可以改成 subprocess，但目前保留在 Main Process 方便 Debug
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
    parser.add_argument('--broker', type=str, default='192.168.1.50:9092')
    parser.add_argument('--group', type=str, default='gale_v1_unified')
    parser.add_argument('--topic', type=str, default='txf-tick')
    
    # [Restored] History Mode Arguments
    parser.add_argument('--mode', type=str, default='live', choices=['live', 'history'])
    parser.add_argument('--date', type=str, help='YYYY-MM-DD for history mode')
    parser.add_argument('--session', type=str, default='day', choices=['day', 'night'])
    
    args = parser.parse_args()
    
    supervisor = CoreSupervisor(args)
    supervisor.run()