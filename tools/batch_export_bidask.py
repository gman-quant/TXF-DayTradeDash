import argparse
import sys
import os
import subprocess
from datetime import datetime, timedelta
import logging

# 修正 Windows 下 cp950 無法印出 emoji 的問題
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | BatchExportBidask | %(message)s')
logger = logging.getLogger()

def get_date_range(start_date, end_date):
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end_dt < start_dt:
        raise ValueError("End date cannot be before start date.")
    delta = end_dt - start_dt
    return [ (start_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(delta.days + 1) ]

def main():
    parser = argparse.ArgumentParser(description="Batch Export Raw BidAsk Data from Kafka")
    parser.add_argument("--start-date", default="2025-12-01", help="Start Date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End Date (YYYY-MM-DD), default is today")
    parser.add_argument("--session", choices=["day", "night", "both"], default="both", help="Session to export")
    parser.add_argument("--broker", default="192.168.1.50:9092", help="Kafka broker")
    
    args = parser.parse_args()
    
    if not args.end_date:
        args.end_date = datetime.now().strftime("%Y-%m-%d")
        
    try:
        dates = get_date_range(args.start_date, args.end_date)
    except Exception as e:
        logger.error(f"Invalid date format: {e}")
        sys.exit(1)
    
    logger.info(f"Starting Batch Export BidAsk: {len(dates)} days (from {args.start_date} to {args.end_date})")
    
    export_script = os.path.join(os.path.dirname(__file__), "export_raw_bidask.py")
    
    for d in dates:
        logger.info(f"🚀 Processing date: {d} (Session: {args.session})")
        
        cmd = [
            sys.executable, export_script,
            "--date", d,
            "--session", args.session,
            "--broker", args.broker
        ]
        
        try:
            result = subprocess.run(cmd, check=True)
            if result.returncode == 0:
                logger.info(f"✅ Successfully exported {d}\n")
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Failed to export {d}. Exit code: {e.returncode}\n")
        except KeyboardInterrupt:
            logger.warning("\n⏹️ Batch process interrupted by user.")
            sys.exit(1)
            
    logger.info("🎉 All batch exports completed!")

if __name__ == "__main__":
    main()
