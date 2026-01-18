
import sys
import os
from unittest.mock import MagicMock

# Mock duckdb before importing gale.infra.db
primary_mock = MagicMock()
sys.modules["duckdb"] = primary_mock

# Ensure project root is in path
sys.path.append(os.getcwd())

try:
    from config.settings import DATA_ROOT
    from gale.infra.db import load_prev_close
    
    print(f"DATA_ROOT loaded: {DATA_ROOT}")
    
    if DATA_ROOT == "D:/txf-data":
        print("DATA_ROOT is correctly set to D:/txf-data")
    else:
        print(f"DATA_ROOT is {DATA_ROOT}, expected D:/txf-data")

    # Manually verify path logic that was added
    target_date_str = "2025-01-01"
    year = 2025
    expected_base = os.path.join(DATA_ROOT, "kbars", "1d", "TXF")
    expected_parquet = f"{expected_base}/TXF_1d_{year}.parquet"
    
    print(f"Expected Base Path: {expected_base}")
    print(f"Expected Parquet Path: {expected_parquet}")
    
except Exception as e:
    print(f"Verification failed: {e}")
