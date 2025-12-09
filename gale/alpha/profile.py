
import numpy as np
from numba import njit
import threading

# =============================================================================
# ⚡️ Numba Logic (High Performance)
# =============================================================================

@njit(nogil=True, cache=True)
def _update_histogram(histogram, price_int, volume):
    """
    Update histogram at index `price_int`.
    Assume price_int is within bounds (0 ~ 50000).
    """
    if 0 <= price_int < len(histogram):
        histogram[price_int] += volume

@njit(nogil=True, cache=True)
def _calc_distribution(histogram, total_volume, value_area_pct=0.70):
    """
    Calculate POC, VAH, VAL from histogram.
    Returns: (poc_price_int, vah_price_int, val_price_int)
    """
    # 1. Find POC (Mode)
    poc_idx = np.argmax(histogram)
    
    # 2. Calculate Value Area
    # Strategy: Start from POC, expand outwards until 70% volume is covered
    target_vol = total_volume * value_area_pct
    current_vol = histogram[poc_idx]
    
    left = poc_idx
    right = poc_idx
    max_idx = len(histogram) - 1
    
    while current_vol < target_vol:
        # Check boundaries
        can_go_left = (left > 0)
        can_go_right = (right < max_idx)
        
        if not can_go_left and not can_go_right:
            break
            
        # Greedy expansion: pick the side with higher volume
        vol_left = histogram[left - 1] if can_go_left else -1
        vol_right = histogram[right + 1] if can_go_right else -1
        
        if vol_left >= vol_right:
            left -= 1
            current_vol += vol_left
        else:
            right += 1
            current_vol += vol_right
            
    return poc_idx, right, left # right is VAH, left is VAL

# =============================================================================
# 📦 Python Wrapper (State Management)
# =============================================================================

class VolumeProfileEngine:
    """
    Manages the Volume Profile state.
    Thread-safe implementation for concurrent Read (Dash) / Write (Strategy) access.
    """
    def __init__(self, size=40000):
        # Index = Price (Points). e.g. Index 15000 means price 15000.
        # TXF price is usually ~20000. 40000 is safe.
        self.histogram = np.zeros(size, dtype=np.int64)
        self.total_volume = 0
        self.lock = threading.Lock()
        
        # Cache for expensive calculation
        self.cached_levels = (0, 0, 0) # POC, VAH, VAL
    
    def update(self, price, volume):
        """
        Thread-safe update.
        Price should be standard integer price (e.g. 23500).
        """
        p_int = int(price)
        with self.lock:
            _update_histogram(self.histogram, p_int, volume)
            self.total_volume += volume
            
    def calculate(self):
        """
        Thread-safe calculation of levels.
        """
        with self.lock:
            # Copy specific parts to avoid holding lock during heavy calc?
            # Actually Numba is fast enough to hold lock.
            # But to be super safe, we pass a copy or just call it.
            # Since histogram is just an array, it's fine.
            if self.total_volume == 0:
                return (0, 0, 0)
            
            poc, vah, val = _calc_distribution(self.histogram, self.total_volume)
            self.cached_levels = (poc, vah, val)
            return self.cached_levels
            
    def get_distribution(self, bin_size=1):
        """
        Return raw arrays for Plotting.
        Thread-safe copy!
        
        Args:
            bin_size (int): Aggregate prices into bins of this size.
        """
        with self.lock:
            # Return sparse representation: (Prices, Volumes)
            # Filter out zeros to save bandwidth
            non_zeros = np.nonzero(self.histogram)[0]
            if len(non_zeros) == 0:
                return np.array([]), np.array([])
            
            prices = non_zeros
            volumes = self.histogram[non_zeros]
            
            # --- Binning Logic ---
            if bin_size > 1:
                # 1. Floor division to get bin index
                binned_prices = prices // bin_size * bin_size
                
                # 2. Re-aggregate (Sum volumes by bin)
                # Using np.unique is efficient for sparse data
                unique_bins, inverse_indices = np.unique(binned_prices, return_inverse=True)
                binned_volumes = np.zeros_like(unique_bins, dtype=np.int64)
                
                # Calculate sum for each bin
                np.add.at(binned_volumes, inverse_indices, volumes)
                
                return unique_bins.copy(), binned_volumes.copy()
            
            # Return COPIES
            return prices.copy(), volumes.copy()
