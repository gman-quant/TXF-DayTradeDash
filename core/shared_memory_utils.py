
import multiprocessing.shared_memory as sm
import numpy as np
import struct

# =========================================================
# 🏗️ Memory Layout Definition
# =========================================================

# RingBuffer Capacity
CAPACITY = 200000

# Define columns and their data types
# Order matters! This defines the physical layout in memory.
SCHEMA = [
    ('timestamp',        'int64'),
    ('close',            'float64'),
    ('volume',           'int32'),
    ('total_volume',     'int32'),
    ('tick_type',        'int32'),
    ('underlying_price', 'float64'),
    
    # Stateful Data
    ('session_high',     'float64'),
    ('session_low',      'float64'),
    
    # Cumulative Data (O(1) Calc)
    ('cum_volume',       'int64'),
    ('cum_pv',           'float64'),
    ('cum_close',        'float64'),
    ('cum_buy_vol',      'int64'),
    ('cum_sell_vol',     'int64')
]

# Header Size (for metadata like 'head', 'is_full')
# layout: [head(int64), is_full(int32), padding(4 bytes)] -> 16 bytes
HEADER_SIZE = 16 

def get_dtype_size(dtype_str: str) -> int:
    """Returns size in bytes for a given numpy dtype string."""
    if dtype_str == 'int64': return 8
    if dtype_str == 'float64': return 8
    if dtype_str == 'int32': return 4
    if dtype_str == 'float32': return 4
    return 8 # Default

def calculate_layout():
    """
    Calculates the memory offsets and total size required.
    Returns: (total_size, offsets_dict)
    """
    current_offset = HEADER_SIZE
    offsets = {}
    
    for name, dtype in SCHEMA:
        offsets[name] = current_offset
        # Align to 8 bytes boundary for safety/performance
        array_size = CAPACITY * get_dtype_size(dtype)
        current_offset += array_size
        
        # Padding to 64-byte cache line alignment (Optional but good)
        # padding = (64 - (current_offset % 64)) % 64
        # current_offset += padding
        
    return current_offset, offsets

def init_shared_memory(name: str, create: bool = False, size: int = 0):
    """
    Allocates or Attaches to a Shared Memory block.
    """
    if create:
        try:
            # Try to unlink if exists (cleanup previous run)
            sm_obj = sm.SharedMemory(name=name, create=False)
            sm_obj.unlink()
        except FileNotFoundError:
            pass

        # Create new
        shm = sm.SharedMemory(name=name, create=True, size=size)
        return shm
    else:
        # Attach existing
        try:
            shm = sm.SharedMemory(name=name, create=False)
            return shm
        except FileNotFoundError:
            raise FileNotFoundError(f"Shared Memory '{name}' not found. Start the writer first.")

class SharedBufferWrapper:
    """
    Helper to create numpy views over the shared memory block.
    """
    def __init__(self, shm: sm.SharedMemory, offsets: dict):
        self.shm = shm
        self.offsets = offsets
        self.views = {}
        
        # Create views for each column
        for name, dtype in SCHEMA:
            offset = offsets[name]
            dtype_size = get_dtype_size(dtype)
            byte_length = CAPACITY * dtype_size
            
            # Create numpy array backed by shared memory buffer
            # Note: order='C' (Row-major) is default
            self.views[name] = np.ndarray(
                (CAPACITY,), 
                dtype=dtype, 
                buffer=shm.buf, 
                offset=offset
            )

    def get_header(self):
        """Read Head and IsFull from header region."""
        # Using struct to unpack first 12 bytes
        # q = int64 (head), i = int32 (is_full)
        data = self.shm.buf[:12]
        head, is_full = struct.unpack('qi', data)
        return head, bool(is_full)

    def set_header(self, head: int, is_full: bool):
        """Write Head and IsFull to header region."""
        struct.pack_into('qi', self.shm.buf, 0, head, int(is_full))

    def close(self):
        self.shm.close()
    
    def unlink(self):
        self.shm.unlink()
