import argparse
import sys
import os
import subprocess
import time
from datetime import datetime, timedelta
import logging

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gale.infra.db import load_prev_close
from gale.infra.memory import SharedRingBuffer
from gale.alpha.manager import IndicatorManager
from gale.dashboard.controller import process_market_data, build_combined_figure
from gale.dashboard.data_model import get_last_value
from config.settings import DATA_ROOT, PREV_CLOSE_PRICE
from config.ui_theme import UI_COLOR

# 修正 Windows 下 cp950 無法印出 emoji 的問題
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | BatchExport | %(message)s')
logger = logging.getLogger()

def resolve_parquet_path(date_str, symbol):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt.strftime("%Y")
    month = dt.strftime("%m")
    DATA_LAKE_ROOT = os.path.join(DATA_ROOT, "raw_ticks")
    return f"{DATA_LAKE_ROOT}/{symbol}/{year}/{month}/{date_str}_{symbol}_ticks.parquet"

def get_date_range(start_date, end_date):
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end_dt < start_dt:
        raise ValueError("End date cannot be before start date.")
    delta = end_dt - start_dt
    return [ (start_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(delta.days + 1) ]

def export_html(date_str, suffix, fig, sb_data, output_dir):
    import plotly.graph_objects as go
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    filename = f"TXF-Chart-{date_str}{suffix}.html"
    filepath = os.path.join(output_dir, filename)

    # Force autosize
    fig.layout.height = None
    fig.layout.autosize = True

    plot_html = fig.to_html(
        include_plotlyjs="cdn",
        full_html=False,
        config={
            "scrollZoom": True,
            "displayModeBar": True,
            "responsive": True,
            "modeBarButtonsToAdd": ["drawline", "drawcircle", "drawrect", "eraseshape"],
        },
        default_height="100%",
        default_width="100%",
    )

    if not sb_data:
        header_html = "<div style='color:white; text-align:center'>No Data</div>"
    else:
        price = sb_data.get("last_price", 0)
        change = sb_data.get("change", 0)
        pct = sb_data.get("change_pct", 0)
        vol = sb_data.get("vol", 0)
        high = sb_data.get("high", 0)
        low = sb_data.get("low", 0)
        prev_close = sb_data.get("prev_close", 0)
        open_p = sb_data.get("open_price", 0)
        vwap = sb_data.get("vwap", 0)
        u_price = sb_data.get("underlying_price", 0)

        main_color = UI_COLOR["UP"] if change >= 0 else UI_COLOR["DOWN"]
        sign = "+" if change >= 0 else ""
        gap = open_p - prev_close
        gap_color = UI_COLOR["UP"] if gap >= 0 else UI_COLOR["DOWN"]
        gap_sign = "+" if gap >= 0 else ""
        basis = price - u_price
        basis_color = UI_COLOR["HIGHLIGHT"]
        basis_sign = "+" if basis >= 0 else ""
        chg_open = price - open_p
        chg_open_color = UI_COLOR["UP"] if chg_open >= 0 else UI_COLOR["DOWN"]
        chg_open_sign = "+" if chg_open >= 0 else ""
        day_range = high - low
        day_range_pct = (day_range / open_p * 100) if open_p else 0

        header_html = f"""
        <div style="background-color: #1E1E1E; color: white; padding: 15px; border-radius: 10px; border: 1px solid {main_color}; margin-bottom: 20px; font-family: sans-serif; display: flex; justify-content: center; align-items: center;">
            <div style="margin-right: 50px; text-align: center;">
                <div style="font-size: 48px; font-weight: bold; color: {main_color}; line-height: 1;">{price:,.0f}</div>
                <div style="font-size: 20px; color: {main_color}; margin-top: 8px;">{sign}{change:.0f} ({sign}{pct:.2f}%)</div>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 8px 40px; text-align: left; font-size: 14px; line-height: 1.6; color: #BBB;">
                <div>
                    <div><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:65px; text-align:right; margin-right:10px;">High:</span><span style="color:{UI_COLOR["UP"]}; font-weight:bold;">{high:,.0f}</span></div>
                    <div><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:65px; text-align:right; margin-right:10px;">Low:</span><span style="color:{UI_COLOR["DOWN"]}; font-weight:bold;">{low:,.0f}</span></div>
                    <div><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:65px; text-align:right; margin-right:10px;">Range:</span><span style="color:{UI_COLOR["HIGHLIGHT"]}; font-weight:bold; white-space:nowrap;">{day_range:.0f} ({day_range_pct:.2f}%)</span></div>
                </div>
                <div>
                    <div><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:65px; text-align:right; margin-right:10px;">PrevClose:</span><span style="color:{UI_COLOR["TEXT_SUB"]};">{prev_close:,.0f}</span></div>
                    <div><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:65px; text-align:right; margin-right:10px;">Spot:</span><span style="color:{UI_COLOR["TEXT_MAIN"]};">{u_price:,.0f}</span></div>
                    <div><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:65px; text-align:right; margin-right:10px;">Basis:</span><span style="color:{basis_color}; font-weight:bold;">{basis_sign}{basis:.2f}</span></div>
                </div>
                <div>
                    <div><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:65px; text-align:right; margin-right:10px;">Open:</span><span style="color:{UI_COLOR["TEXT_MAIN"]};">{open_p:,.0f}</span></div>
                    <div><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:65px; text-align:right; margin-right:10px;">OpenGap:</span><span style="color:{gap_color};">{gap_sign}{gap:.0f}</span></div>
                    <div><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:65px; text-align:right; margin-right:10px;">OpenDelta:</span><span style="color:{chg_open_color};">{chg_open_sign}{chg_open:.0f}</span></div>
                </div>
                <div>
                    <div><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:65px; text-align:right; margin-right:10px;">VWAP:</span><span style="color:{UI_COLOR["VWAP"]}; font-weight:bold;">{vwap:,.0f}</span></div>
                    <div><span style="color:{UI_COLOR["TEXT_SUB"]}; display:inline-block; width:65px; text-align:right; margin-right:10px;">Volume:</span><span style="color:{UI_COLOR["TEXT_MAIN"]};">{vol:,.0f}</span></div>
                </div>
            </div>
        </div>
        """

    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>TXF {date_str}{suffix}</title>
        <style>
            body {{ background-color: #111; color: #ddd; margin: 0; padding: 20px; font-family: sans-serif; height: 100vh; display: flex; flex-direction: column; box-sizing: border-box; }}
            h2 {{ text-align: center; color: #fff; margin: 0 0 15px 0; font-size: 24px; }}
            .plotly-graph-div {{ flex: 1; width: 100%; height: 87vh !important; }}
            .js-plotly-plot .plotly .modebar {{ top: -5px !important; right: 0px !important; }}
        </style>
    </head>
    <body>
        <h2>🇹🇼 TXF <small style='opacity: 0.6; font-weight: 300;'>SNAPSHOT</small> <span style='color: #444; margin: 0 10px; font-weight: 100;'>|</span> {date_str} {"🌙" if "0N" in suffix else "☀️"}</h2>
        {header_html}
        {plot_html}
    </body>
    </html>
    """

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(full_html)
    logger.info(f"💾 Saved HTML to: {filepath}")


def process_date(date_str, session, source, broker, group, base_topic):
    logger.info(f"==== Processing {date_str} ({session}) via {source} ====")
    
    run_id = f"batch_{datetime.now().strftime('%H%M%S')}"
    topic = base_topic
    shm_name = f"gale_shm_{topic}_{run_id}"
    capacity = 200000

    try:
        prev_close = load_prev_close(date_str, op="<")
    except Exception as e:
        logger.warning(f"Could not load prev close: {e}. Using default.")
        prev_close = PREV_CLOSE_PRICE

    cmd = []
    if source == "parquet":
        f_txf = resolve_parquet_path(date_str, "TXF")
        f_tse = resolve_parquet_path(date_str, "TSE")
        
        if not os.path.exists(f_txf):
            logger.warning(f"Skipping {date_str} {session}: TXF Parquet not found at {f_txf}")
            return
            
        cmd = [sys.executable, "-m", "gale.feed.replay", f_txf]
        if os.path.exists(f_tse):
            cmd.extend(["--underlying", f_tse])
        cmd.extend(["--prev-close", str(prev_close), "--capacity", str(capacity), "--topic", topic, "--speed", "0", "--run-id", run_id])
        
    elif source == "kafka":
        cmd = [sys.executable, "-m", "gale.feed.ingest", "--broker", broker, "--group", group, "--topic", topic, 
               "--prev-close", str(prev_close), "--run-id", run_id, "--mode", "history", "--date", date_str, "--session", session, "--auto-exit"]

    logger.info(f"🚀 Starting Ingestion: {' '.join(cmd)}")
    
    # Use PIPE to read stdout, force UTF-8 encoding
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    ingest_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', bufsize=1, env=env)
    
    completed = False
    logger.info("⏳ Waiting for Ingestion to complete...")
    for line in iter(ingest_proc.stdout.readline, ''):
        sys.stdout.write(f"  [Ingest] {line}")
        if "Replay Completed." in line or "Ingestion Completed." in line:
            completed = True
            break
            
    if not completed:
        logger.error(f"❌ Ingestion did not complete successfully for {date_str}. Skipping.")
        ingest_proc.terminate()
        return

    logger.info("✅ Ingestion Process finished. Attaching to Shared Buffer...")
    
    try:
        ring_buffer = SharedRingBuffer(name=shm_name, capacity=capacity, create=False)
    except Exception as e:
        logger.error(f"❌ Could not attach to Shared Buffer {shm_name}: {e}")
        ingest_proc.terminate()
        return

    manager = IndicatorManager(buffer_capacity=capacity)
    manager.ring_buffer = ring_buffer
    
    local_cursor = 0
    target_head = ring_buffer.head
    
    logger.info(f"🔄 Syncing IndicatorManager (0 -> {target_head}). Computing COFI/COBI...")
    
    get_snapshot = ring_buffer.get_snapshot
    on_tick = manager.on_tick
    
    count = 0
    start_sync = time.time()
    
    while local_cursor != target_head:
        next_cursor = (local_cursor + 1) % ring_buffer.capacity
        snap = get_snapshot()
        synthetic_snap = snap[:-1] + (next_cursor,)
        on_tick(synthetic_snap)
        local_cursor = next_cursor
        count += 1
        
    sync_time = time.time() - start_sync
    logger.info(f"✅ Sync complete. Processed {count} ticks in {sync_time:.2f}s. All indicators 100% updated.")
    
    if count > 0:
        # Extract Data Pack
        logger.info("📊 Generating Plotly Chart...")
        lookback = manager.count # Use all data
        data_pack = process_market_data(manager, lookback, "1m")
        fig = build_combined_figure(data_pack)
        
        # Generate Scoreboard Data
        hist = data_pack["history"]
        if len(hist["close"]) > 0:
            last_price = hist["close"][-1]
            open_p = hist["close"][0]
        else:
            last_price = prev_close
            open_p = prev_close

        change = last_price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0

        sb_data = {
            "last_price": last_price,
            "change": change,
            "change_pct": change_pct,
            "open_price": open_p,
            "high": get_last_value(hist, "Session_High"),
            "low": get_last_value(hist, "Session_Low"),
            "vol": get_last_value(hist, "Total_Vol"),
            "vwap": get_last_value(hist, "VWAP"),
            "prev_close": prev_close,
            "underlying_price": get_last_value(hist, "Underlying_Price"),
        }
        
        suffix = "-0N" if session == "night" else "-1D"
        output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "snapshots")
        export_html(date_str, suffix, fig, sb_data, output_dir)
    else:
        logger.warning(f"No ticks processed for {date_str} {session}. Skipping HTML.")
    
    # Cleanup
    ring_buffer.shutdown()
    ingest_proc.terminate()
    try:
        ingest_proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        ingest_proc.kill()
        
    logger.info(f"🧹 Cleaned up Process and Buffer {shm_name}\n")


def main():
    parser = argparse.ArgumentParser(description="Headless HTML Batch Export")
    parser.add_argument("--start-date", required=True, help="Start Date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End Date (YYYY-MM-DD), default is start-date")
    parser.add_argument("--session", choices=["day", "night", "both"], default="both", help="Session to export")
    parser.add_argument("--source", choices=["parquet", "kafka"], default="kafka", help="Data source")
    parser.add_argument("--broker", default="192.168.1.50:9092", help="Kafka broker")
    parser.add_argument("--group", default="gale_batch_html", help="Kafka group")
    parser.add_argument("--topic", default="txf-tick", help="Base topic name")
    
    args = parser.parse_args()
    
    if not args.end_date:
        args.end_date = args.start_date
        
    dates = get_date_range(args.start_date, args.end_date)
    sessions = ["day", "night"] if args.session == "both" else [args.session]
    
    logger.info(f"Starting Batch Export: {len(dates)} days, {len(sessions)} sessions per day. Source: {args.source}")
    
    for d in dates:
        for s in sessions:
            process_date(d, s, args.source, args.broker, args.group, args.topic)

    logger.info("🎉 All batch exports completed successfully!")

if __name__ == "__main__":
    main()
