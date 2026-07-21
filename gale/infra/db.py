"""
gale/infra/db.py

負責處理 DuckDB 資料庫連接與查詢。
"""

import duckdb
import math
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


# 沒有「每日結算價」概念的商品(現貨指數):其參考價本來就是指數收盤,不可套結算價口徑。
_INDEX_SYMBOLS = {"TSE"}


def _daily_settlement_from_1m(date_str, symbol):
    """從該日 1m parquet 取**官方每日結算價** = 日盤「收盤前 1 分鐘」的成交量加權均價。

    為什麼不能用 1d bar:
      ‧ 1d 的 `close` 是**最後一筆成交價**,不是結算價(實測價差可達 120 點,收盤前波動越大差越多)。
      ‧ 1d 的 `true_pv_sum/volume` 是**整個日盤的 VWAP**,更不是結算價。
      → 官方口徑是「收盤前 1 分鐘」的 VWAP,故必須讀 1m 檔取末根(volume>0)。
    **取位:無條件捨去**(不是四捨五入)。已對期交所官方數字驗證:
      2026-07-20 TX 202608 官方結算價 = 42,662;我們算出 VWAP = 42662.8892
        → 捨去 42662 ✅ 與官方一字不差 / 四捨五入 42663 ❌ 差 1 點
      (同期官方「最後成交價」= 42,641,與結算價差 21 點 —— 這也是不能用 close 的理由。)
      實測 6 個交易日中有 3 日「捨去 vs 四捨五入」會差 1 點,非罕見。
    註:txf-quant-platform 的 ref_settle 刻意**不取位**(保留原始 VWAP,標示那是我們自算的值,
        且圖上不足 1 點看不出差異);此處要對上券商/期交所的漲跌基準,故取位。

    取不到(檔案缺/無成交)回 None,由呼叫端 fallback 回 1d close。"""
    try:
        path = os.path.join(DATA_ROOT, "kbars", "1m", symbol,
                            date_str[:4], f"{date_str}_{symbol}_1m.parquet")
        if not os.path.exists(path):
            return None
        con = duckdb.connect()
        try:
            row = con.execute(f"""
                SELECT true_pv_sum / volume
                FROM '{path}'
                WHERE lower(session) = 'day' AND volume > 0
                ORDER BY ts DESC
                LIMIT 1
            """).fetchone()
        finally:
            con.close()
        if not row or row[0] is None:
            return None
        return float(math.floor(float(row[0])))     # 官方取位:無條件捨去
    except Exception as e:
        logger.warning(f"每日結算價計算失敗({symbol} {date_str}): {e}")
        return None


def load_prev_close(target_date_str, op="<", symbol="TXF"):
    """
    從日 Summary Parquet 取得參考用「昨收價」。
    (DuckDB Implementation)
    """
    try:
        target_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
        years_to_check = [target_dt.year, target_dt.year - 1]

        for year in years_to_check:
            # [Fix] Use centralized DATA_ROOT from config
            BASE_PATH = os.path.join(DATA_ROOT, "kbars", "1d", symbol)
            
            if not os.path.exists(BASE_PATH):
                logger.warning(
                    f"⚠️ Data path not found: {BASE_PATH}"
                )
                continue

            parquet_path = f"{BASE_PATH}/{symbol}_1d_{year}.parquet"

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
                        # 期貨:改用官方每日結算價(= 日盤末分鐘 VWAP),與券商/期交所的漲跌基準一致,
                        # 也與 txf-quant-platform 的「前日結」同口徑。指數(TSE)無結算價概念 → 維持收盤。
                        # 名稱仍叫 prev_close:期貨的官方「收盤價」本就是結算價,此為業界慣例用法。
                        if symbol not in _INDEX_SYMBOLS:
                            d = ref_date.strftime("%Y-%m-%d") if hasattr(ref_date, "strftime") else str(ref_date)[:10]
                            settle = _daily_settlement_from_1m(d, symbol)
                            if settle is not None:
                                logger.info(
                                    f"✅ Found Prev Close(每日結算價): {settle:.2f} "
                                    f"(Date: {ref_date};1d 最後成交 {prev_close})"
                                )
                                return settle
                            logger.warning(
                                f"⚠️ {symbol} {d} 取不到每日結算價,fallback 用 1d 最後成交 {prev_close}"
                            )
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
