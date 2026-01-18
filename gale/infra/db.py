"""
gale/infra/db.py

負責處理 DuckDB 資料庫連接與查詢。
"""

import duckdb
import os
from datetime import datetime
from gale.utils.log_utils import setup_logger
from config.settings import DATA_ROOT

logger = setup_logger("InfraDB")


def load_history_data(parquet_path, date_str, session="day"):
    """
    從 Parquet 載入指定日期的歷史 Tick 資料 (DuckDB Version)。
    """
    logger.info(f"Connecting to DuckDB for {parquet_path}...")
    con = None
    try:
        con = duckdb.connect()

        # 1. Base Query
        query = f"SELECT * FROM '{parquet_path}' WHERE Date = '{date_str}'"

        # 2. Session Filter
        if session == "day":
            # 08:45 ~ 13:45
            query += " AND Time >= '08:45:00' AND Time <= '13:45:00'"
        elif session == "night":
            # 15:00 ~ 05:00 (Next Day)
            query += " AND (Time >= '15:00:00' OR Time <= '05:00:00')"

        # 3. Ordering
        query += " ORDER BY Time ASC"

        logger.info(f"Executing Query: {query}")
        df = con.execute(query).df()

        if df.empty:
            logger.warning(f"No data found for {date_str} ({session})")
            return None

        logger.info(f"Loaded {len(df)} ticks.")
        return df

    except Exception as e:
        logger.error(f"DuckDB Error: {e}")
        return None
    finally:
        if con:
            con.close()


def load_prev_close(target_date_str, op="<"):
    """
    從日 Summary Parquet (TXF_1d_YYYY.parquet) 取得參考用「昨收價」。
    (DuckDB Implementation)
    """
    try:
        target_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
        years_to_check = [target_dt.year, target_dt.year - 1]

        for year in years_to_check:
            # [Fix] Use centralized DATA_ROOT from config
            BASE_PATH = os.path.join(DATA_ROOT, "kbars", "1d", "TXF")
            
            if not os.path.exists(BASE_PATH):
                logger.warning(
                    f"⚠️ Data path not found: {BASE_PATH}"
                )
                continue

            parquet_path = f"{BASE_PATH}/TXF_1d_{year}.parquet"

            if not os.path.exists(parquet_path):
                continue

            # SQL: 找尋 {op} target_date 的最新一筆 Day Close
            query = f"""
                SELECT Close, Date 
                FROM '{parquet_path}' 
                WHERE Date {op} '{target_date_str}'
                  AND lower(Session) = 'day'
                ORDER BY Date DESC 
                LIMIT 1
            """

            try:
                con = duckdb.connect()
                try:
                    result = con.execute(query).fetchone()

                    if result:
                        prev_close = float(result[0])
                        ref_date = result[1]
                        logger.info(
                            f"✅ Found Prev Close: {prev_close} (Date: {ref_date})"
                        )
                        return prev_close
                finally:
                    con.close()

            except Exception as e:
                # 容錯：如果沒有 Session 欄位，嘗試不篩選 Session
                if "Session" in str(e):
                    logger.warning(
                        "Column 'Session' not found, retrying without session filter..."
                    )
                    query_fallback = f"""
                        SELECT Close, Date 
                        FROM '{parquet_path}' 
                        WHERE Date {op} '{target_date_str}'
                        ORDER BY Date DESC 
                        LIMIT 1
                    """
                    con_fallback = None
                    try:
                        con_fallback = duckdb.connect()
                        result = con_fallback.execute(query_fallback).fetchone()
                        if result:
                            prev_close = float(result[0])
                            ref_date = result[1]
                            logger.info(
                                f"✅ Found Prev Close (Fallback): {prev_close} (Date: {ref_date})"
                            )
                            return prev_close
                    except Exception as e2:
                        logger.warning(f"Fallback failed: {e2}")
                    finally:
                        if con_fallback:
                            con_fallback.close()

                logger.warning(f"DuckDB Error reading {parquet_path}: {e}")
                continue

        logger.warning(f"⚠️ Could not find Prev Close for {target_date_str} (op='{op}')")
        return 0.0

    except Exception as e:
        logger.error(f"Failed to load Prev Close: {e}")
        return 0.0
