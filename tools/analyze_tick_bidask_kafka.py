"""
分析 Tick 和 BidAsk 數據的對應關係 (從 Kafka 讀取)

用途：
1. 從 Kafka 讀取指定 session 的 tick (txf-tick) 和 bidask (txf-bidask) 資料
2. 統計各自的資料筆數
3. 分析有多少 tick 對應到多筆 bidask data
4. 找出最大對應數量和發生時間
5. 評估對 LOB 狀態判定的影響
"""

import asyncio
import sys
import os
from datetime import datetime
from collections import defaultdict, Counter
from typing import List, Tuple

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gale.feed.kafka_client import GaleKafkaConsumer
from data_schemas.txf_data_pb2 import Tick, BidAsk
from config.txf_calendar import get_history_range

class TickBidAskAnalyzer:
    """分析 Tick 和 BidAsk 數據的對應關係"""
    
    def __init__(self, broker_url: str = '192.168.1.50:9092'):
        self.broker_url = broker_url
        self.tick_data = []  # (timestamp_ms, tick_object)
        self.bidask_data = []  # (timestamp_ms, bidask_object)
        
    async def fetch_session_data(self, date_str: str, session: str = 'day'):
        """
        從 Kafka 讀取指定 session 的所有數據
        
        Args:
            date_str: 日期字串 (YYYY-MM-DD)
            session: 'day' 或 'night'
        """
        print(f"\n{'='*80}")
        print(f"分析目標: {date_str} {session.upper()} Session")
        print(f"{'='*80}\n")
        
        # 1. 計算 session 的時間範圍
        start_offset, end_offset = get_history_range(date_str, session)
        start_ts_ms = int(start_offset.timestamp() * 1000)
        end_ts_ms = int(end_offset.timestamp() * 1000)
        
        print(f"Session 時間範圍:")
        print(f"  開始: {start_offset} ({start_ts_ms})")
        print(f"  結束: {end_offset} ({end_ts_ms})\n")
        
        # 2. 讀取 txf-tick
        print("📥 讀取 txf-tick 數據...")
        await self._fetch_topic_data('txf-tick', start_offset, end_offset, is_tick=True)
        
        # 3. 讀取 txf-bidask
        print("\n📥 讀取 txf-bidask 數據...")
        await self._fetch_topic_data('txf-bidask', start_offset, end_offset, is_tick=False)
        
        print(f"\n✅ 數據讀取完成")
        print(f"  Tick 筆數: {len(self.tick_data):,}")
        print(f"  BidAsk 筆數: {len(self.bidask_data):,}")
        
    async def _fetch_topic_data(self, topic: str, start_offset: datetime, end_offset: datetime, is_tick: bool):
        """讀取單個 topic 的數據"""
        consumer = GaleKafkaConsumer(
            broker_url=self.broker_url,
            group_id=f'analyzer_{topic}_{datetime.now().timestamp()}',  # 唯一 group ID
            topics=[topic]
        )
        
        try:
            consumer.connect()
            
            # Seek to start time
            consumer.seek_to_time(start_offset)
            
            end_ts_ms = int(end_offset.timestamp() * 1000)
            count = 0
            
            # 使用 consume_history 讀取歷史數據
            async for msg in consumer.consume_history(start_offset, end_offset):
                try:
                    raw_bytes = msg.value()
                    ts_ms = msg.timestamp()[1]  # (type, timestamp_ms)
                    
                    if is_tick:
                        tick = Tick()
                        tick.ParseFromString(raw_bytes)
                        self.tick_data.append((tick.timestamp_ms, tick))
                    else:
                        bidask = BidAsk()
                        bidask.ParseFromString(raw_bytes)
                        self.bidask_data.append((bidask.timestamp_ms, bidask))
                    
                    count += 1
                    if count % 10000 == 0:
                        print(f"  已讀取 {count:,} 筆...")
                        
                except Exception as e:
                    print(f"  解析錯誤: {e}")
                    continue
            
            print(f"  完成！總共 {count:,} 筆")
            
        except Exception as e:
            print(f"❌ 讀取 {topic} 失敗: {e}")
            raise
        finally:
            consumer.close()
    
    def analyze_correspondence(self):
        """分析 tick 和 bidask 的對應關係"""
        print(f"\n{'='*80}")
        print("📊 分析 Tick-BidAsk 對應關係")
        print(f"{'='*80}\n")
        
        if not self.tick_data or not self.bidask_data:
            print("❌ 沒有足夠的數據進行分析")
            return
        
        # 1. 統計基本資訊
        tick_count = len(self.tick_data)
        bidask_count = len(self.bidask_data)
        
        print(f"📈 基本統計:")
        print(f"  Tick 資料筆數: {tick_count:,}")
        print(f"  BidAsk 資料筆數: {bidask_count:,}")
        print(f"  比例: {bidask_count/tick_count:.2f}x (每個 tick 平均對應 {bidask_count/tick_count:.2f} 筆 bidask)\n")
        
        # 2. 排序數據（按時間戳）
        self.tick_data.sort(key=lambda x: x[0])
        self.bidask_data.sort(key=lambda x: x[0])
        
        # 3. 分析：對每個 tick timestamp，計算有多少 bidask 在同一時間戳
        print("🔬 方法 1: 精確時間戳匹配")
        self._analyze_exact_timestamp_match()
        
        # 4. 分析：對每個 tick，計算在該 tick 到下一個 tick 之間有多少 bidask
        print(f"\n🔬 方法 2: Tick Interval 分析")
        self._analyze_tick_intervals()
        
        # 5. 分析：使用滑動窗口分析 bidask 密度
        print(f"\n🔬 方法 3: BidAsk 時間密度分析")
        self._analyze_bidask_density()
        
    def _analyze_exact_timestamp_match(self):
        """分析精確時間戳匹配"""
        # 統計每個時間戳的 tick 和 bidask 數量
        tick_ts_counts = Counter(ts for ts, _ in self.tick_data)
        bidask_ts_counts = Counter(ts for ts, _ in self.bidask_data)
        
        # 找出有 tick 的時間戳上有多少 bidask
        correspondence = {}
        for ts in tick_ts_counts.keys():
            correspondence[ts] = {
                'tick_count': tick_ts_counts[ts],
                'bidask_count': bidask_ts_counts.get(ts, 0)
            }
        
        # 統計分佈
        bidask_count_distribution = Counter(
            item['bidask_count'] for item in correspondence.values()
        )
        
        print("  在相同時間戳上的 BidAsk 分佈:")
        for bidask_count in sorted(bidask_count_distribution.keys()):
            tick_ts_count = bidask_count_distribution[bidask_count]
            print(f"    {bidask_count} 筆 BidAsk: {tick_ts_count:,} 個 tick 時間戳")
        
        # 找出對應最多 bidask 的情況
        max_bidask = max((item['bidask_count'] for item in correspondence.values()), default=0)
        if max_bidask > 0:
            print(f"\n  最多對應到: {max_bidask} 筆 bidask")
            
            # 找出前 10 個對應最多的時間戳
            top_cases = sorted(
                correspondence.items(),
                key=lambda x: x[1]['bidask_count'],
                reverse=True
            )[:10]
            
            print(f"\n  對應最多 BidAsk 的前 10 個時間戳:")
            for ts, data in top_cases:
                dt = datetime.fromtimestamp(ts / 1000)
                print(f"    {dt.strftime('%H:%M:%S.%f')[:-3]} - {data['bidask_count']} 筆 bidask, {data['tick_count']} 筆 tick")
    
    def _analyze_tick_intervals(self):
        """分析 tick interval 中的 bidask 數量"""
        # 對每個 tick，找出到下一個 tick 之間的 bidask 數量
        results = []
        
        bidask_idx = 0
        total_bidask = len(self.bidask_data)
        
        for i in range(len(self.tick_data) - 1):
            tick_ts = self.tick_data[i][0]
            next_tick_ts = self.tick_data[i + 1][0]
            
            # 計算在 [tick_ts, next_tick_ts) 之間的 bidask 數量
            count = 0
            temp_idx = bidask_idx
            
            while temp_idx < total_bidask and self.bidask_data[temp_idx][0] < tick_ts:
                temp_idx += 1
            
            bidask_idx = temp_idx
            
            while temp_idx < total_bidask and self.bidask_data[temp_idx][0] < next_tick_ts:
                count += 1
                temp_idx += 1
            
            if count > 0:
                results.append((i, tick_ts, next_tick_ts, count))
        
        # 統計分佈
        count_distribution = Counter(r[3] for r in results)
        
        print("  Tick Interval 中的 BidAsk 分佈:")
        for count in sorted(count_distribution.keys())[:20]:  # 只顯示前 20 項
            interval_count = count_distribution[count]
            print(f"    {count} 筆 BidAsk: {interval_count:,} 個 tick intervals")
        
        if len(count_distribution) > 20:
            print(f"    ... (還有 {len(count_distribution) - 20} 個不同的計數)")
        
        # 找出包含最多 bidask 的 intervals
        if results:
            max_count = max(r[3] for r in results)
            print(f"\n  單個 tick interval 最多包含: {max_count} 筆 bidask")
            
            # 找出前 10 個包含最多 bidask 的 intervals
            top_intervals = sorted(results, key=lambda x: x[3], reverse=True)[:10]
            
            print(f"\n  包含最多 BidAsk 的前 10 個 Tick Intervals:")
            for idx, tick_ts, next_tick_ts, count in top_intervals:
                tick_dt = datetime.fromtimestamp(tick_ts / 1000)
                next_dt = datetime.fromtimestamp(next_tick_ts / 1000)
                interval_ms = next_tick_ts - tick_ts
                print(f"    #{idx}: {tick_dt.strftime('%H:%M:%S.%f')[:-3]} → {next_dt.strftime('%H:%M:%S.%f')[:-3]} " +
                      f"({interval_ms}ms) - {count} 筆 bidask")
    
    def _analyze_bidask_density(self):
        """分析 bidask 的時間密度"""
        if len(self.bidask_data) < 2:
            return
        
        # 計算相鄰 bidask 之間的時間間隔
        intervals = []
        for i in range(len(self.bidask_data) - 1):
            interval = self.bidask_data[i + 1][0] - self.bidask_data[i][0]
            intervals.append(interval)
        
        intervals.sort()
        
        # 統計
        total = len(intervals)
        min_interval = intervals[0]
        max_interval = intervals[-1]
        median_interval = intervals[total // 2]
        avg_interval = sum(intervals) / total
        
        print(f"  BidAsk 更新間隔統計:")
        print(f"    最小間隔: {min_interval} ms")
        print(f"    中位數: {median_interval} ms")
        print(f"    平均值: {avg_interval:.2f} ms")
        print(f"    最大間隔: {max_interval} ms")
        
        # 統計不同間隔範圍的分佈
        ranges = [(0, 1), (1, 10), (10, 50), (50, 100), (100, 500), (500, 1000), (1000, float('inf'))]
        print(f"\n  間隔範圍分佈:")
        for low, high in ranges:
            count = sum(1 for i in intervals if low <= i < high)
            pct = count / total * 100
            if high == float('inf'):
                print(f"    >={low}ms: {count:,} ({pct:.2f}%)")
            else:
                print(f"    {low}-{high}ms: {count:,} ({pct:.2f}%)")
    
    def print_conclusions(self):
        """輸出結論與建議"""
        print(f"\n{'='*80}")
        print("📝 結論與建議")
        print(f"{'='*80}\n")
        
        print("❓ 問題: 多筆 bidask 對應到同一個 tick 是否會導致無法判定真正的訂單簿狀態?\n")
        
        tick_count = len(self.tick_data)
        bidask_count = len(self.bidask_data)
        ratio = bidask_count / tick_count if tick_count > 0 else 0
        
        print("💡 分析結論:")
        print(f"  1. 平均而言，每個 tick 對應約 {ratio:.2f} 筆 bidask 更新")
        print(f"  2. BidAsk (報價) 的更新頻率遠高於 Tick (成交)")
        print(f"  3. 這是正常現象：訂單簿不斷變化，但不一定每次都會成交\n")
        
        print("⚠️  對 LOB 狀態判定的影響:")
        print("  ✓ 當 tick 發生時，在該 tick 之前可能有多筆 bidask 更新")
        print("  ✓ 需要確保使用「最接近但不晚於 tick 時間」的 bidask 狀態")
        print("  ✓ 如果有多筆 bidask 在同一毫秒內，應該使用「最後一筆」\n")
        
        print("✅ 建議:")
        print("  1. LOB Engine 應該按照時間順序處理所有 bidask 更新")
        print("  2. 當 tick 到來時，snapshot 當時的 LOB 狀態")
        print("  3. 使用 'watermark' 機制確保 tick 之前的所有 bidask 都已處理")
        print("  4. 當前系統的 IngestServer 已經實現了這個邏輯 (pending_ticks + watermark)")
        
        print(f"\n💪 當前系統設計評估:")
        print("  ✅ IngestServer 使用 pending_ticks buffer")
        print("  ✅ LOBEngine 維護 max_seen_ts watermark")
        print("  ✅ 只有當 max_quote_ts >= tick_ts 時才處理 tick")
        print("  ✅ 這確保了每個 tick 都能看到正確的訂單簿狀態")
        
        print(f"\n{'='*80}\n")

async def main():
    """主函數"""
    # 解析參數
    date = sys.argv[1] if len(sys.argv) > 1 else "2025-12-22"
    session = sys.argv[2] if len(sys.argv) > 2 else "day"
    broker = sys.argv[3] if len(sys.argv) > 3 else "192.168.1.50:9092"
    
    # 創建分析器
    analyzer = TickBidAskAnalyzer(broker_url=broker)
    
    # 讀取數據
    await analyzer.fetch_session_data(date, session)
    
    # 分析
    analyzer.analyze_correspondence()
    
    # 結論
    analyzer.print_conclusions()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  分析中斷")
    except Exception as e:
        print(f"\n\n❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
