
import asyncio
from gale.feed.adapter import GaleKafkaConsumer
from datetime import datetime
from collections import defaultdict
from data_schemas.txf_data_pb2 import BidAsk

async def analyze_timestamps():
    consumer = GaleKafkaConsumer('192.168.1.50:9092', 'analyze_ts_v1', ['txf-bidask'])
    consumer.connect()
    
    # Seek to a busy time
    start_dt = datetime(2025, 12, 15, 9, 0, 0)
    consumer.seek_to_time(start_dt)
    
    ts_counts = defaultdict(int)
    total_msgs = 0
    limit = 50000
    
    print(f"Analyzing {limit} messages from {start_dt}...")
    
    async for batch in consumer.consume_stream(batch_size=1000, running_check=lambda: True):
        for msg in batch:
            if msg.topic() == 'txf-bidask':
                # Use Exchange Timestamp if inside payload, or Kafka timestamp?
                # User asked about "Timestamp being same". Usually refers to the one used for sorting.
                # Here we check Kafka Message Timestamp (used by sort adapter) AND Payload Timestamp.
                
                kafka_ts = msg.timestamp()[1]
                payload = msg.value()
                
                if kafka_ts not in ts_counts:
                    ts_counts[kafka_ts] = []
                ts_counts[kafka_ts].append(payload)
                
                total_msgs += 1
        
        if total_msgs >= limit:
            break
            
    # Report
    rapid_update_ts = 0
    true_duplicate_msgs = 0
    
    for ts, payloads in ts_counts.items():
        if len(payloads) > 1:
            # Check for content identity
            unique_payloads = set(payloads)
            
            if len(unique_payloads) < len(payloads):
                # Has identical duplicates
                true_duplicate_msgs += (len(payloads) - len(unique_payloads))
            
            if len(unique_payloads) > 1:
                # Has multiple different updates in same ms
                rapid_update_ts += 1

    unique_ts_count = len(ts_counts)
    
    print(f"Total Messages: {total_msgs}")
    print(f"Unique Timestamps: {unique_ts_count}")
    print("--- Validation Results ---")
    print(f"Identical Duplicates (Redundant Data): {true_duplicate_msgs} ({(true_duplicate_msgs/total_msgs)*100:.2f}%)")
    print(f"Rapid Updates (Same Time, Diff Data): {rapid_update_ts} timestamps ({(rapid_update_ts/unique_ts_count)*100:.2f}%)")
    
    # Show example of crowded TS
    print("\nTop 5 Crowded Timestamps (Content Check):")
    sorted_crowds = sorted(ts_counts.items(), key=lambda x: len(x[1]), reverse=True)[:5]
    for ts, payloads in sorted_crowds:
        dt = datetime.fromtimestamp(ts/1000)
        unique_count = len(set(payloads))
        print(f"TS {ts} ({dt}): {len(payloads)} msgs (Unique Payloads: {unique_count})")

if __name__ == "__main__":
    asyncio.run(analyze_timestamps())
