
import multiprocessing
import time
import os
import sys
import numpy as np
from data_schemas.txf_data_pb2 import Tick
from core.shared_memory import SharedRingBuffer

# Configuration
SHM_NAME = "test_shm_robustness"
TOTAL_TICKS = 5000
KILL_AT_TICK = 2000

def writer_process(barrier):
    """
    Writer: 0 ~ TOTAL_TICKS
    """
    try:
        buf = SharedRingBuffer(name=SHM_NAME, capacity=10000, create=True)
        print(f"[Writer] Shared Memory created.")
        barrier.wait() 
        print(f"[Writer] Start writing {TOTAL_TICKS} ticks...")
        
        tick = Tick()
        for i in range(TOTAL_TICKS):
            tick.timestamp_ms = int(time.time() * 1000)
            
            # Use INT for price to avoid float errors if protobuf schema expects int64
            # Assuming schema: int64 close
            tick.close = int(i * 10000) 
            tick.volume = 1
            tick.total_volume = int(i)
            tick.tick_type = 1
            tick.underlying_price = int(i * 10000)
            
            buf.write_tick(tick)
            
            if i % 1000 == 0:
                print(f"[Writer] Written {i}")
            
            time.sleep(0.0002) 
            
        print("[Writer] Done.")
        time.sleep(2)
        buf.shutdown()
        
    except Exception as e:
        print(f"[Writer] Error: {e}")
        import traceback
        traceback.print_exc()

def reader_process(output_queue, counter, start_delay=0):
    """
    Reader
    """
    time.sleep(start_delay)
    try:
        # Retry connect logic
        buf = None
        for _ in range(20):
            try:
                buf = SharedRingBuffer(name=SHM_NAME, capacity=10000, create=False)
                break
            except:
                time.sleep(0.5)
        
        if not buf:
            print(f"[Reader-{os.getpid()}] Failed to connect to SHM")
            return

        print(f"[Reader-{os.getpid()}] Connected.")
        
        local_cursor = 0
        
        # 模擬 Strategy Server 的 Sync 邏輯
        while True:
            target_head = buf.head
            
            if local_cursor != target_head:
                while local_cursor != target_head:
                    next_cursor = (local_cursor + 1) % buf.capacity
                    
                    idx_to_read = next_cursor - 1
                    if idx_to_read < 0: idx_to_read = buf.capacity - 1
                    
                    val = buf.close[idx_to_read]
                    output_queue.put(val)
                    
                    with counter.get_lock():
                        counter.value += 1
                    
                    local_cursor = next_cursor
            
            # Exit check
            if buf.timestamp[target_head-1 if target_head>0 else 10000-1] > 0:
                 latest_idx = target_head - 1
                 if latest_idx < 0: latest_idx = buf.capacity - 1
                 
                 # Price was i * 10000
                 current_latest_price = buf.close[latest_idx]
                 expected_last_price = (TOTAL_TICKS - 1) * 10000.0
                 
                 if current_latest_price >= expected_last_price:
                     if local_cursor == target_head:
                         break

            time.sleep(0.001)
            
        print(f"[Reader-{os.getpid()}] Finished.")
        buf.shutdown()
        
    except Exception as e:
        print(f"[Reader] Error: {e}")
        import traceback
        traceback.print_exc()

def run_test():
    try:
        from multiprocessing.shared_memory import SharedMemory
        SharedMemory(name=SHM_NAME).unlink()
        print("Cleaned up old SHM")
    except: pass

    barrier = multiprocessing.Barrier(2)
    result_queue = multiprocessing.Queue()
    counter = multiprocessing.Value('i', 0)
    
    # 1. Start Writer
    p_writer = multiprocessing.Process(target=writer_process, args=(barrier,))
    p_writer.start()
    
    # 2. Start Reader 1
    p_reader1 = multiprocessing.Process(target=reader_process, args=(result_queue, counter, 0))
    p_reader1.start()
    
    # Barrier ensures Writer creates SHM before Reader 1 continues (because Writer waits on barrier AFTER create)
    # Wait... in writer_process: buf = SharedRingBuffer(..., create=True); barrier.wait()
    # In main thread: process starts...
    # Barrier is passed to writer.
    # Where does Reader wait? Reader doesn't wait on barrier. Reader 1 starts immediately.
    # But Reader 1 has retry logic! This is fine.
    
    # Wait for writer manually? No, process start is async.
    # Actually, Writer calls barrier.wait() after creation.
    # Who calls wait on the other side?
    # Ah, I removed barrier for Reader1.
    # So I need main thread to trigger the barrier? Or Reader 1?
    # Writer waits for barrier(2). 
    # If Reader 1 does not use barrier, Writer will hang forever!
    # I MUST fix the barrier logic.
    pass

if __name__ == "__main__":
    # Redefine run_test logic to fix barrier issue
    try:
        from multiprocessing.shared_memory import SharedMemory
        SharedMemory(name=SHM_NAME).unlink()
    except: pass

    # Barrier for Writer + MainThread (to signal "Let's go")
    barrier = multiprocessing.Barrier(2)
    
    result_queue = multiprocessing.Queue()
    counter = multiprocessing.Value('i', 0)

    p_writer = multiprocessing.Process(target=writer_process, args=(barrier,))
    p_writer.start()
    
    # Allow writer to create SHM and reach barrier
    print("Waiting for Writer to initialize SHM...")
    barrier.wait() 
    print("Writer initialized. Starting Readers.")

    p_reader1 = multiprocessing.Process(target=reader_process, args=(result_queue, counter, 0))
    p_reader1.start()

    while True:
        c = 0
        with counter.get_lock():
            c = counter.value
        
        if c > KILL_AT_TICK:
            print(f"🧨 Killing Reader 1 at {c} ticks...")
            p_reader1.terminate()
            p_reader1.join()
            break
        time.sleep(0.1)

    print("🚑 Starting Reader 2 (Recovery)...")
    result_queue_2 = multiprocessing.Queue()
    counter_2 = multiprocessing.Value('i', 0)
    p_reader2 = multiprocessing.Process(target=reader_process, args=(result_queue_2, counter_2, 0))
    p_reader2.start()
    
    p_writer.join()
    p_reader2.join()
    
    results = []
    while not result_queue_2.empty():
        results.append(result_queue_2.get())
        
    print(f"Total collected by Reader 2: {len(results)}")
    arr = np.array(results)
    
    # Check
    expected_prices = np.arange(TOTAL_TICKS, dtype=np.float64) * 10000.0
    
    if len(arr) == TOTAL_TICKS and np.allclose(arr, expected_prices):
         print("✅ SUCCESS: Reader 2 recovered FULL sequence perfectly!")
    else:
         print(f"❌ FAIL. Len: {len(arr)}")
         if len(arr) > 0:
             print(f"First: {arr[0]}, Last: {arr[-1]}")
