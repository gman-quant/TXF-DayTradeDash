
import sys
import os

# Ensure we can import config
sys.path.append(os.getcwd())

from config.settings import DATA_ROOT
from gale.infra.db import load_prev_close

print(f"Checking DATA_ROOT: {DATA_ROOT}")
# Trying to find a file we know exists from the directory search (e.g. 2024 parquet)
# D:/txf-data/kbars/1d/TXF/TXF_1d_2024.parquet

# Using db.py logic (target_date_str)
# It searches for years based on the date provided.
# If we provide '2025-01-17', it checks 2025 and 2024.
# We saw TXF_1d_2025.parquet in the directory earlier.

print("Testing load_prev_close('2025-01-01')...")
try:
    val = load_prev_close('2025-01-01', op="<=")
    print(f"Result: {val}")
except Exception as e:
    print(f"Error: {e}")
