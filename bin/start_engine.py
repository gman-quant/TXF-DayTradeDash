
import subprocess
import sys
import os
import signal
import time
import argparse
import logging
from datetime import datetime, timedelta

# Fix ModuleNotFoundError
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gale.strategy.engine import StrategyServer

from gale.utils.log_utils import setup_logger

# Logging
logger = setup_logger("Supervisor")

# Helper for path resolution
def resolve_parquet_path(date_str, symbol):
    """
    Resolve Parquet path based on Data Lake structure:
    {ROOT}/{SYMBOL}/{YYYY}/{MM}/{YYYY-MM-DD}_{SYMBOL}_ticks.parquet
    """
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        year = dt.strftime('%Y')
        month = dt.strftime('%m')
        
        # Hardcoded Data Lake Root
        DATA_LAKE_ROOT = "/Users/gtai/Projects/txf-data-lake/data/raw_ticks"
        
        path = f"{DATA_LAKE_ROOT}/{symbol}/{year}/{month}/{date_str}_{symbol}_ticks.parquet"
        logger.info(f"🔍 Resolving {symbol} {date_str} -> {path}")
        return path
    except Exception as e:
        logger.warning(f"Path resolution failed for {date_str}: {e}")
        return None

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
            
            txf_files = []
            tse_files = []
            
            # [Smart Path Resolution]
            if self.args.date:
                # Date Range Logic
                start_date_str = self.args.date
                # Check if end_date exists in args (it should if parser updated)
                end_date_str = getattr(self.args, 'end_date', None)
                if not end_date_str: end_date_str = start_date_str
                
                try:
                    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                except ValueError:
                    logger.error(f"❌ Invalid date format. Please use YYYY-MM-DD.")
                    sys.exit(1)
                    
                if end_dt < start_dt:
                    logger.error("❌ End date cannot be before start date.")
                    sys.exit(1)
                    
                delta = end_dt - start_dt
                days_count = delta.days + 1
                logger.info(f"📅 Resolving data for {days_count} days: {start_date_str} to {end_date_str}")
                
                for i in range(days_count):
                    current_date = start_dt + timedelta(days=i)
                    ymd = current_date.strftime("%Y-%m-%d")
                    
                    # Resolve TXF
                    f_txf = resolve_parquet_path(ymd, "TXF")
                    if f_txf and os.path.exists(f_txf):
                        txf_files.append(f_txf)
                    else:
                        logger.warning(f"⚠️ Warning: TXF file not found for {ymd}: {f_txf}")
                        
                    # Resolve TSE (Underlying)
                    f_tse = resolve_parquet_path(ymd, "TSE") 
                    if f_tse and os.path.exists(f_tse):
                        tse_files.append(f_tse)
                    
            elif self.args.file:
                # Manual single file
                txf_files.append(self.args.file)
                if self.args.underlying:
                    tse_files.append(self.args.underlying)
            else:
                logger.error("❌ Error: You must provide either --file or --date for parquet replay.")
                sys.exit(1)
                
            # Check if we have valid files
            if not txf_files:
                logger.error("❌ Critical: No valid TXF parquet files found.")
                sys.exit(1)
                
            logger.info(f"✅ Found {len(txf_files)} TXF files.")
            
            # [Dynamic Capacity Calculation]
            # Default 200k per day is safe.
            # We calculate: 200,000 * num_files. Minimum 200,000.
            calc_capacity = max(200000, 200000 * len(txf_files))
            logger.info(f"Calculated shared memory capacity: {calc_capacity} ticks.")
            self.capacity = calc_capacity # Store for Dashboard

            # Construct Command
            cmd = [sys.executable, "-m", "gale.feed.parquet"]
            cmd.extend(txf_files) 
            
            if tse_files:
                cmd.append("--underlying")
                cmd.extend(tse_files) 
                
            cmd.extend(["--capacity", str(calc_capacity)])
            cmd.extend(["--topic", self.args.topic])
            cmd.extend(["--speed", str(self.args.speed)])
            
            logger.info(f"Starting Ingestion Process: {' '.join(cmd)}")
            self.ingest_process = subprocess.Popen(cmd)

        else:
            # [Kafka Live/History Mode]
            logger.info("📡 Data Source: Kafka Consumer")
            self.capacity = 200000 # Default for Kafka
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
        
        # [Capacity] Use self.capacity calculated in start_ingestion
        capacity = getattr(self, 'capacity', 200000)

        cmd = [sys.executable, "-m", "bin.start_dashboard", 
               "--topic", self.args.topic,
               "--port", str(port),
               "--capacity", str(capacity)]
        
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
            
            # [Health Check] Wait and see if Feed crashes immediately
            time.sleep(2)
            if self.ingest_process.poll() is not None:
                logger.error(f"❌ Feed Process Crashed! Return Code: {self.ingest_process.returncode}")
                # We can't easily read stderr here without pipe, but user should see it in terminal.
                sys.exit(1)
            
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
    parser.add_argument('--end-date', type=str, help='End Date YYYY-MM-DD for multi-day replay')
    parser.add_argument('--session', type=str, default='day', choices=['day', 'night'])
    
    # [Parquet Args]
    parser.add_argument('--file', type=str, help="Parquet File Path")
    parser.add_argument('--underlying', type=str, help="Underlying (TSE) Parquet File Path")
    parser.add_argument('--speed', type=float, default=0, help="Replay Speed")
    
    args = parser.parse_args()
    
    # [Auto Default Topic]
    if not args.topic:
        args.topic = 'txf-replay' if args.source == 'parquet' else 'txf-tick'
    
    supervisor = CoreSupervisor(args)
    supervisor.run()