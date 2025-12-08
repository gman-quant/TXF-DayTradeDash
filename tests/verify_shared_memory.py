
import time
import multiprocessing
import numpy as np
from core.ring_buffer import TxfRingBuffer
from data_schemas.txf_data_pb2 import Tick

def writer_func():
    print("[Writer] Starting...")
    rb = TxfRingBuffer(capacity=1000, shm_name='test_shm', create_shm=True)
    rb.clear()
    
    for i in range(100):
        tick = Tick()
        tick.timestamp_ms = 1700000000000 + i
        tick.close = int((10000 + i) * 10000) # Normalized in buffer as 10000 + i
        tick.volume = 1
        tick.total_volume = i
        
        rb.write_tick(tick)
        time.sleep(0.05) # Slow down so reader can catch it
        
    print("[Writer] Done writing 100 ticks.")
    time.sleep(2) # Keep alive for reader

def reader_func():
    print("[Reader] Starting...")
    time.sleep(0.5) # Wait for writer to init
    
    try:
        rb = TxfRingBuffer(capacity=1000, shm_name='test_shm', create_shm=False)
        
        last_head = -1
        count = 0
        
        # Read for 2 seconds
        start_t = time.time()
        while time.time() - start_t < 6.0:
            rb.refresh_state()
            if rb.head != last_head:
                # Read latest data
                idx = rb.head - 1
                if idx < 0: idx += 1000
                
                price = rb.close[idx]
                ts = rb.timestamp[idx]
                
                # Check consistency
                # We expect strict ordering if we catch every tick, 
                # but in busy loop we might miss some if writer is insanely fast?
                # Actually reader busy loop is faster than writer 1ms sleep.
                
                # print(f"[Reader] Got head={rb.head}, price={price}")
                last_head = rb.head
                count += 1
            
            time.sleep(0.001)
            
        print(f"[Reader] Finished. Total updates observed: {count}")
        if count >= 90: # Allow some startup race
            print("✅ Shared Memory Test PASSED")
        else:
            print("❌ Shared Memory Test FAILED (Count too low)")
            
    except Exception as e:
        print(f"❌ Reader Error: {e}")

if __name__ == "__main__":
    multiprocessing.set_start_method('spawn', force=True)
    
    p1 = multiprocessing.Process(target=writer_func)
    p2 = multiprocessing.Process(target=reader_func)
    
    p1.start()
    p2.start()
    
    p1.join()
    p2.join()
