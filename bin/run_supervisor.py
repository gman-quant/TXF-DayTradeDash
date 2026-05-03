import subprocess
import sys
import os
import signal
import time
import argparse
from datetime import datetime, timedelta

# Fix ModuleNotFoundError
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gale.utils.log_utils import setup_logger
from config.txf_calendar import DAY_SESSION_START, NIGHT_SESSION_START
from gale.infra.db import load_prev_close
from config.settings import DATA_ROOT, SHM_CAPACITY

# Logging
logger = setup_logger("Supervisor")


# Helper for path resolution
def resolve_parquet_path(date_str, symbol):
    """
    Resolve Parquet path based on Data Lake structure:
    {ROOT}/{SYMBOL}/{YYYY}/{MM}/{YYYY-MM-DD}_{SYMBOL}_ticks.parquet
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        year = dt.strftime("%Y")
        month = dt.strftime("%m")

        # [Fix] Use centralized DATA_ROOT
        DATA_LAKE_ROOT = os.path.join(DATA_ROOT, "raw_ticks")
        
        if not os.path.exists(DATA_LAKE_ROOT):
             logger.warning(
                f"⚠️ Data Lake root not found: {DATA_LAKE_ROOT}"
            )

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
        
        # [Unique Run ID] Generate a unique ID for this execution session
        # Format: timestamp (compact)
        self.run_id = datetime.now().strftime("%H%M%S")
        logger.info(f"🆔 Session Run ID: {self.run_id}")

    def _load_prev_close(self, target_date_str=None):
        """
        [重構] 使用基礎架構模組載入昨收價 (Reference Price)。
        """
        if not target_date_str:
            target_date_str = (
                self.args.date
                if self.args.mode == "history"
                else datetime.now().strftime("%Y-%m-%d")
            )

        # 根據 模式 (Mode) 與 時間 (Time) 決定查詢邏輯
        if self.args.mode == "live" and datetime.now().time() >= NIGHT_SESSION_START:
            op = "<="
        else:
            op = "<"

        return load_prev_close(target_date_str, op=op)

    def start_ingestion(self):
        """啟動 Ingestion Process (獨立進程)"""
        if self.args.source == "parquet":
            # [Parquet Replay Mode]
            logger.info("📡 Data Source: Parquet Replay")

            txf_files = []
            tse_files = []

            # [Smart Path Resolution]
            if self.args.date:
                # Date Range Logic
                start_date_str = self.args.date
                # Check if end_date exists in args (it should if parser updated)
                end_date_str = getattr(self.args, "end_date", None)
                if not end_date_str:
                    end_date_str = start_date_str

                try:
                    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                except ValueError:
                    logger.error("❌ Invalid date format. Please use YYYY-MM-DD.")
                    sys.exit(1)

                if end_dt < start_dt:
                    logger.error("❌ End date cannot be before start date.")
                    sys.exit(1)

                delta = end_dt - start_dt
                days_count = delta.days + 1
                logger.info(
                    f"📅 Resolving data for {days_count} days: {start_date_str} to {end_date_str}"
                )

                for i in range(days_count):
                    current_date = start_dt + timedelta(days=i)
                    ymd = current_date.strftime("%Y-%m-%d")

                    # Resolve TXF
                    f_txf = resolve_parquet_path(ymd, "TXF")
                    if f_txf and os.path.exists(f_txf):
                        txf_files.append(f_txf)
                    else:
                        logger.warning(
                            f"⚠️ Warning: TXF file not found for {ymd}: {f_txf}"
                        )

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
                logger.error(
                    "❌ Error: You must provide either --file or --date for parquet replay."
                )
                sys.exit(1)

            # Check if we have valid files
            if not txf_files:
                logger.error("❌ Critical: No valid TXF parquet files found.")
                sys.exit(1)

            logger.info(f"✅ Found {len(txf_files)} TXF files.")

            # [Dynamic Capacity Calculation]
            # Use SHM_CAPACITY as base. Minimum is one SHM_CAPACITY per file.
            calc_capacity = max(SHM_CAPACITY, SHM_CAPACITY * len(txf_files))
            logger.info(f"Calculated shared memory capacity: {calc_capacity} ticks.")
            self.capacity = calc_capacity  # Store for Dashboard

            # Construct Command
            cmd = [sys.executable, "-m", "gale.feed.replay"]
            cmd.extend(txf_files)

            if tse_files:
                cmd.append("--underlying")
                cmd.extend(tse_files)

            # [新增] 針對回測起始日計算昨收價
            start_date_for_prev = None
            if self.args.date:
                start_date_for_prev = self.args.date
            elif self.args.file:
                # 嘗試從檔名 "YYYY-MM-DD_..." 解析日期
                try:
                    filename = os.path.basename(self.args.file)
                    start_date_for_prev = filename.split("_")[0]
                    # 驗證格式
                    datetime.strptime(start_date_for_prev, "%Y-%m-%d")
                except Exception:
                    start_date_for_prev = None

            if start_date_for_prev:
                try:
                    replay_prev_close = self._load_prev_close(
                        target_date_str=start_date_for_prev
                    )
                    cmd.extend(["--prev-close", str(replay_prev_close)])
                    logger.info(
                        f"✅ Replay Prev Close for {start_date_for_prev}: {replay_prev_close}"
                    )
                except Exception as e:
                    logger.warning(f"Failed to load replay prev close: {e}")

            cmd.extend(["--capacity", str(calc_capacity)])
            cmd.extend(["--topic", self.args.topic])
            cmd.extend(["--speed", str(self.args.speed)])
            
            # [Unique Run ID]
            cmd.extend(["--run-id", self.run_id])

            logger.info(f"Starting Ingestion Process: {' '.join(cmd)}")
            self.ingest_process = subprocess.Popen(cmd)

        else:
            # [Kafka Live/History Mode]
            logger.info("📡 Data Source: Kafka Consumer")
            self.capacity = SHM_CAPACITY  # Use centralized default for Kafka
            prev_close = self._load_prev_close()
            cmd = [
                sys.executable,
                "-m",
                "gale.feed.ingest",
                "--broker",
                self.args.broker,
                "--group",
                self.args.group,
                "--topic",
                self.args.topic,
                "--prev-close",
                str(prev_close),
                "--run-id",
                self.run_id,
            ]

            if self.args.mode == "history":
                cmd.extend(["--mode", "history"])
                if self.args.date:
                    cmd.extend(["--date", self.args.date])
                if self.args.session:
                    cmd.extend(["--session", self.args.session])

            logger.info(f"Starting Ingestion Process: {' '.join(cmd)}")
            self.ingest_process = subprocess.Popen(cmd)

    def start_dashboard(self):
        """啟動 Dashboard Process (獨立進程)"""
        # 1. 決定埠號
        port = 8051 if self.args.mode == "history" else 8050

        # 2. 決定容量
        capacity = getattr(self, "capacity", 200000)

        # --- [核心修改點] 動態判定時段 ---
        # 優先檢查是否為 live 模式，如果是，則根據時間自動切換
        current_session = getattr(self.args, "session", "day")  # 預設值

        if self.args.mode == "live":
            current_time = datetime.now().time()
            # 判定 logic: 14:50 以後到隔天 08:42 以前都算 night
            if current_time >= NIGHT_SESSION_START or current_time < DAY_SESSION_START:
                current_session = "night"
            else:
                current_session = "day"
            logger.info(
                f"🔄 Live Mode 偵測: 目前時間 {current_time} ({current_session} session)"
            )
        # ----------------------------

        cmd = [
            sys.executable,
            "-m",
            "bin.run_dashboard",
            "--topic",
            self.args.topic,
            "--port",
            str(port),
            "--capacity",
            str(capacity),
        ]

        if hasattr(self.args, "mode"):
            cmd.extend(["--mode", self.args.mode])
        if getattr(self.args, "date", None):
            cmd.extend(["--date", self.args.date])

        # [Unique Run ID]
        cmd.extend(["--run-id", self.run_id])

        # 使用我們剛剛動態判定的 current_session
        cmd.extend(["--session", current_session])

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

            # [Fix] Wait for Ingestion to initialize Shared Memory and unlink old resource
            # This prevents Dashboard from attaching to "zombie" memory.
            logger.info("⏳ Waiting 3s for Ingestion Server to initialize memory...")
            time.sleep(3)

            # 2. Start Dashboard Subprocess
            self.start_dashboard()

            # 3. Strategy (Optional/Disabled)
            logger.info("⚠️ Strategy Engine is disabled by user request.")

            logger.info("🎬 Engine Started. Dashboard is active.")
            logger.info("👉 Press Ctrl+C to stop and close Dashboard.")

            # --- 建立穩健的監控迴圈 ---
            ingest_finished = False

            while True:
                time.sleep(1)

                # A. 監控 Ingest (資料攝取) 進程
                # 僅檢查是否異常崩潰 (非零退出)。若正常結束 (0)，則視為回放完成，保持 Dashboard 開啟。
                if self.ingest_process and not ingest_finished:
                    ret = self.ingest_process.poll()
                    if ret is not None:
                        if ret == 0:
                            logger.info(
                                "✅ Feed Process (資料源) 已正常結束。Dashboard 保持開啟。"
                            )
                            ingest_finished = True
                        else:
                            logger.error(
                                f"❌ Feed Process 異常崩潰！Return Code: {ret}"
                            )
                            sys.exit(1)

                # B. 監控 Dashboard (儀表板) 進程
                if self.dash_process:
                    d_ret = self.dash_process.poll()
                    if d_ret is not None:
                        logger.error(
                            f"❌ Dashboard Process 異常崩潰！Return Code: {d_ret}"
                        )
                        sys.exit(1)

                # [Graceful Shutdown] Check for restart signal
                if os.path.exists(".restart_signal"):
                    logger.info("♻️ 收到重啟信號 (.restart_signal)，正在執行優雅退場...")
                    try:
                        os.remove(".restart_signal")
                    except Exception:
                        pass
                    break  # Break loop to trigger finally: cleanup()

        except KeyboardInterrupt:
            logger.info("Supervisor 收到 Ctrl+C (KeyboardInterrupt)。")
        finally:
            self.cleanup()

    def cleanup(self):
        logger.info("正在終止所有子進程...")

        def kill_proc(proc, name):
            if proc and proc.poll() is None:
                logger.info(f"正在停止 {name}...")
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        logger.warning(f"{name} 無法正常停止，強制擊殺 (Kill)...")
                        proc.kill()
                except Exception as e:
                    logger.error(f"停止 {name} 時發生錯誤: {e}")

        kill_proc(self.ingest_process, "Ingest Server")
        kill_proc(self.dash_process, "Dashboard Server")

        logger.info("所有進程已終止。")


def parse_cli_args():
    """
    解析並處理 CLI 參數。
    封裝了 Smart Auto-Detection 邏輯。
    """
    parser = argparse.ArgumentParser(description="TXF Gale Engine (Unified Launcher)")

    # [Data Source]
    parser.add_argument(
        "--source",
        type=str,
        default="kafka",
        choices=["kafka", "parquet"],
        help="Data Mode",
    )

    # [Common Args]
    parser.add_argument("--topic", type=str, help="Shared Memory Topic")

    # [Kafka Args]
    parser.add_argument("--broker", type=str, default="192.168.1.50:9092")
    parser.add_argument("--group", type=str, default="gale_v1_unified")
    parser.add_argument("--mode", type=str, default="live", choices=["live", "history"])
    parser.add_argument("--date", type=str, help="YYYY-MM-DD for history mode")
    parser.add_argument(
        "--end-date", type=str, help="End Date YYYY-MM-DD for multi-day replay"
    )
    parser.add_argument("--session", type=str, default="day", choices=["day", "night"])

    # [Parquet Args]
    parser.add_argument("--file", type=str, help="Parquet File Path")
    parser.add_argument(
        "--underlying", type=str, help="Underlying (TSE) Parquet File Path"
    )
    parser.add_argument("--speed", type=float, default=0, help="Replay Speed")

    args = parser.parse_args()

    # [Smart Logic]
    # If user provides --date but no source, assume Parquet Replay (better UX).
    # BUT, if user explicitly asked for --mode history, respect Kafka (Legacy).
    if args.date and args.source == "kafka" and args.mode == "live":
        # Check if user explicitly typed --source kafka (rare for date usage)
        if "--source" not in sys.argv:
            print("✨ Auto-Switching to Parquet Replay Mode (detected --date)")
            args.source = "parquet"

    # [Auto Default Topic]
    if not args.topic:
        args.topic = "txf-replay" if args.source == "parquet" else "txf-tick"

    # [Fix] Parquet source implies History mode logic (for Prev Close calculation)
    if args.source == "parquet":
        args.mode = "history"

    return args


if __name__ == "__main__":
    args = parse_cli_args()
    supervisor = CoreSupervisor(args)

    def handle_signal(signum, frame):
        if signum == signal.SIGINT:
            # [正常退場] 用戶手動按下 Ctrl+C
            logger.info("收到 SIGINT (Ctrl+C)，執行正常關閉流程...")
            sys.exit(0)
        elif signum == signal.SIGTERM:
            # [特殊處理] 忽略 AutoRun 於非交易時段發出的清理信號
            # 這是為了讓用戶可以在週末/夜間進行手動回測而不被強制關閉
            logger.warning(
                "收到 SIGTERM (來自 AutoRun 管家)，忽略信號以保持 Dashboard 開啟 (手動回測模式)。"
            )
            return

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    supervisor.run()
