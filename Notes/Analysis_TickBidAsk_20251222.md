# 2025-12-22 Tick-BidAsk 對應關係分析報告

## 執行摘要

本報告分析了 2025-12-22 日盤（day session）的 tick（成交）和 bidask（報價）數據對應關係，旨在評估多筆 bidask 對應到同一個 tick 的情況是否會影響 LOB（限價訂單簿）狀態的準確判定。

## 數據概況

### Session 資訊
- **日期**: 2025-12-22
- **時段**: Day Session (08:30:00 - 13:45:05)
- **數據來源**: Kafka (topics: `txf-tick`, `txf-bidask`)

### 數據統計

| 指標 | 數值 |
|------|------|
| Tick 總筆數 | 28,621 |
| BidAsk 總筆數 | 117,802 |
| 比例 | 4.12x (平均每個 tick 對應 4.12 筆 bidask) |

## 分析方法與發現

### 方法 1: 精確時間戳匹配

分析在**相同毫秒時間戳**上有多少 tick 和 bidask 同時發生。

#### 關鍵發現

**在相同時間戳上的 BidAsk 分佈**:

| BidAsk 筆數 | Tick 時間戳數量 | 百分比 |
|------------|----------------|--------|
| 1 筆 | 21,768 | 85.4% |
| 2 筆 | 2,297 | 9.0% |
| 3 筆 | 417 | 1.6% |
| 4 筆 | 134 | 0.5% |
| 5 筆 | 52 | 0.2% |
| 6-12 筆 | 52 | 0.2% |
| **最大** | **12 筆** | - |

**對應最多 BidAsk 的時間點**:

1. **08:45:00.829** - 12 筆 bidask, 12 筆 tick (開盤瞬間)
2. **09:26:27.750** - 11 筆 bidask, 11 筆 tick
3. **10:17:36.113** - 11 筆 bidask, 11 筆 tick

> **觀察**: 開盤時刻（08:45:00.829）出現了最高的同時成交和報價更新，這符合市場開盤時的高活躍度特徵。

---

### 方法 2: Tick Interval 分析

分析每個 tick 到下一個 tick 之間（tick interval）包含多少 bidask 更新。

#### 關鍵發現

**Tick Interval 中的 BidAsk 分佈** (前 20 項):

| BidAsk 筆數 | Interval 數量 |
|------------|--------------|
| 1-5 筆 | 18,339 (64.1%) |
| 6-10 筆 | 3,140 (11.0%) |
| 11-20 筆 | 2,017 (7.0%) |
| 21+ 筆 | 5,124 (17.9%) |

**包含最多 BidAsk 的 Tick Intervals**:

| 時間範圍 | 間隔 (ms) | BidAsk 筆數 |
|---------|----------|------------|
| 11:32:14.764 → 11:32:29.399 | 14,635 | **79 筆** |
| 11:42:44.211 → 11:43:00.566 | 16,355 | 69 筆 |
| 10:33:15.859 → 10:33:32.295 | 16,436 | 63 筆 |

> **觀察**: 當兩個 tick 之間的時間間隔較長（>10秒）時，bidask 更新數量會顯著增加。這表明在無成交的時段，訂單簿仍在持續更新。

---

### 方法 3: BidAsk 時間密度分析

分析 bidask 更新的時間分佈特性。

#### 統計數據

| 指標 | 數值 |
|------|------|
| 最小間隔 | 0 ms |
| 中位數 | 125 ms |
| 平均值 | 152.80 ms |
| 最大間隔 | 3,625 ms |

**間隔範圍分佈**:

| 範圍 | 筆數 | 百分比 |
|------|------|--------|
| 0-1 ms | 4,065 | 3.45% |
| 1-10 ms | 7,057 | 5.99% |
| 10-50 ms | 13,195 | 11.20% |
| 50-100 ms | 12,059 | 10.24% |
| **100-500 ms** | **75,320** | **63.94%** |
| 500-1000 ms | 5,162 | 4.38% |
| ≥1000 ms | 943 | 0.80% |

> **觀察**: 
> - 63.94% 的 bidask 更新間隔在 100-500ms 之間
> - 有 3.45% 的 bidask 在同一毫秒內連續更新（間隔 0ms）
> - 這說明報價更新頻率很高，但仍有一定的時間分散性

---

## 核心問題回答

### ❓ 多筆 bidask 對應到同一個 tick 是否會導致無法判定真正的訂單簿狀態？

**答案**: **是的，存在根本性的時序不確定問題**。

### 詳細說明

#### 1. **問題場景**

假設在 `T=1000ms` 時的事件序列（真實順序）：

```
T=1000ms, offset=100: bidask #1 (Ask: 100@28200) ← 成交前狀態
T=1000ms, offset=101: tick (成交 50@28200)       ← 成交事件
T=1000ms, offset=102: bidask #2 (Ask: 50@28200)  ← 成交後狀態（Ask 被部分消耗）
```

**關鍵問題**: 從 Kafka 的兩個 topic (`txf-tick`, `txf-bidask`) 重建這個序列時：

- `txf-tick` 只能告訴我們 tick 發生在 `T=1000ms`
- `txf-bidask` 只能告訴我們 bidask #1 和 #2 都在 `T=1000ms`
- **無法確定** tick 相對於 bidask #1 和 #2 的真實順序

#### 2. **為什麼「使用最後一筆」是錯誤的**

如果我們採用「同一時間戳的最後一筆 bidask」策略：

```python
# ❌ 錯誤做法
for tick in ticks:
    bidask_state = get_latest_bidask_before_or_equal(tick.timestamp)
    # 這可能會拿到成交「後」的狀態！
```

**問題**:
- bidask #2 (Ask: 50@28200) 是成交**後**的結果
- 用它來分析成交時的市場微結構是**因果倒置**
- OBI/OFI 指標會被污染

#### 3. **實際影響**

根據分析結果：

- **14.6%** 的 tick 在其時間戳上有 2 筆以上 bidask
- 最極端的情況是 **12 筆 bidask** 在同一毫秒內
- 在這些情況下，**我們無法確定哪些 bidask 是成交前，哪些是成交後**

---

## 根本原因分析

### 為何會發生時序不確定性？

| 原因 | 說明 |
|------|------|
| **時間戳精度限制** | 毫秒級無法區分微秒或納秒級的真實順序 |
| **獨立數據流** | tick 和 bidask 來自不同的 Kafka topics (partition) |
| **Kafka 順序保證** | Kafka 只保證「同一 partition 內」的順序，不保證跨 topic 的順序 |
| **時鐘漂移** | 數據採集端可能有輕微的時鐘不同步 |
| **市場微結構** | 成交和報價更新在交易所內部是原子操作，但傳輸到 Kafka 時被拆分 |

### 當前系統的「Watermark」機制的局限性

當前 `IngestServer` 的設計：

```python
max_quote_ts = self.lob_engine.max_seen_ts
is_safe = (max_quote_ts >= tick_ts)

if is_safe:
    obi, ofi, lag = self.lob_engine.get_metrics(tick_ts)
```

**這個機制的假設**:
- ✅ 如果 `max_quote_ts >= tick_ts`，那麼所有 `timestamp <= tick_ts` 的 bidask 都已處理
- ✅ 這保證了「不會遺漏」成交前的 bidask

**但無法解決的問題**:
- ❌ 當 `bidask.timestamp == tick_ts` 時，無法判斷 bidask 是在 tick **之前**還是**之後**
- ❌ 如果 bidask 是成交後的狀態，OBI/OFI 會被污染

---

## 可能的解決方案

### 方案 1: 保守策略 - 只使用「嚴格早於」的 bidask

```python
# 修改 LOBEngine.get_metrics()
def get_metrics(self, tick_ts):
    # ❌ 舊版: 使用 <= tick_ts 的所有 bidask
    # ✅ 新版: 只使用 < tick_ts 的 bidask (嚴格早於)
    
    # 忽略與 tick 同一時間戳的 bidask
    # 這樣可以避免「成交後狀態」的污染
    pass
```

**優點**:
- ✅ 完全避免因果倒置問題
- ✅ 保證 OBI/OFI 反映的是成交前狀態

**缺點**:
- ⚠️ 如果 tick 和 bidask 的時間戳精確相同且 bidask 確實在成交前，會被忽略
- ⚠️ 可能導致 LAG 偏高

**評估**: 
- 85.4% 的情況下沒有同時間戳的 bidask，不受影響
- 14.6% 的情況下會更保守，但更安全

---

### 方案 2: 使用 Kafka Offset 作為次要排序依據

```python
# 數據結構增強
class BidAskWithOffset:
    timestamp_ms: int
    kafka_offset: int  # 新增
    data: BidAsk
    
class TickWithOffset:
    timestamp_ms: int
    kafka_offset: int  # 新增
    data: Tick

# 處理邏輯
def compare_events(tick, bidask):
    if tick.timestamp_ms > bidask.timestamp_ms:
        return "bidask_before_tick"
    elif tick.timestamp_ms < bidask.timestamp_ms:
        return "bidask_after_tick"
    else:
        # 同一時間戳：無法比較跨 topic 的 offset
        # 因為它們來自不同的 partition
        return "uncertain"
```

**問題**:
- ❌ tick 和 bidask 來自**不同的 Kafka topics**
- ❌ 不同 topic 的 offset 沒有可比性
- ❌ 即使在同一個 broker，partition offset 也是獨立的

**結論**: 此方案**不可行**

---

### 方案 3: 要求數據源提供 Sequence Number

**理想做法**: 在數據採集端（Shioaji collector）加入全局序號

```protobuf
message Tick {
    int64 timestamp_ms = 1;
    int64 global_seq = 2;  // 新增：全局遞增序號
    // ... other fields
}

message BidAsk {
    int64 timestamp_ms = 1;
    int64 global_seq = 2;  // 新增：全局遞增序號
    // ... other fields
}
```

**採集端邏輯**:
```python
global_seq_counter = 0

# 當收到任何事件（tick 或 bidask）
def on_event(event):
    global global_seq_counter
    event.global_seq = global_seq_counter
    global_seq_counter += 1
    send_to_kafka(event)
```

**優點**:
- ✅ 完美解決時序問題
- ✅ 可以精確重建事件順序
- ✅ 即使時間戳相同，也能用 seq 排序

**缺點**:
- ⚠️ 需要修改數據採集端
- ⚠️ 需要重新採集歷史數據（或只對新數據生效）

---

### 方案 4: 統計學方法 - 接受不確定性

在 14.6% 有爭議的情況下，採用**機率性判斷**:

```python
# 當同一時間戳有多筆 bidask 時
# 假設：成交「前」的機率 > 成交「後」的機率
# 理由：通常是先有掛單，才會成交

def get_probable_pre_trade_state(tick_ts):
    same_ts_bidasks = get_bidasks_at(tick_ts)
    
    if len(same_ts_bidasks) == 1:
        return same_ts_bidasks[0]
    
    # 策略 A: 使用「第一筆」（更保守）
    return same_ts_bidasks[0]
    
    # 策略 B: 使用「中位數」
    # return same_ts_bidasks[len(same_ts_bidasks) // 2]
```

**優點**:
- ✅ 不需要修改數據源
- ✅ 對大部分情況（85.4%）沒有影響

**缺點**:
- ❌ 仍然是「猜測」，沒有確定性
- ❌ 可能在極端情況下失效

---

## 建議方案

### 短期方案（立即可行）

**採用方案 1：保守策略**

```python
# 修改 gale/alpha/orderbook.py - LOBEngine.get_metrics()

def get_metrics(self, tick_ts):
    """
    獲取指定時間戳的 LOB 指標
    
    ⚠️ 重要：只使用「嚴格早於」tick 的 bidask 狀態
    理由：同一時間戳的 bidask 可能是成交「後」的結果
    """
    # 找出最後一個 timestamp < tick_ts 的 snapshot
    # (不是 <= ，而是 <)
    
    target_snapshot = None
    for snapshot in self.snapshots:
        if snapshot.timestamp < tick_ts:  # 注意：嚴格小於
            target_snapshot = snapshot
        else:
            break
    
    if target_snapshot is None:
        # 沒有成交前的資料
        return (0.0, 0.0, -1.0)  # 表示無效
    
    lag = tick_ts - target_snapshot.timestamp
    return (target_snapshot.obi, target_snapshot.ofi, lag)
```

**影響評估**:
- 85.4% 的情況：無影響（本來就沒有同時間戳的 bidask）
- 14.6% 的情況：LAG 會略微增加（因為不使用同時間戳的 bidask）
- 好處：完全避免因果倒置

---

### 長期方案（建議實施）

**採用方案 3：增加全局序號**

1. **修改 Protobuf Schema**
   - 在 `Tick` 和 `BidAsk` 中加入 `global_seq` 欄位

2. **修改 Shioaji Collector**
   - 維護全局計數器
   - 每個事件都分配唯一遞增序號

3. **修改 LOBEngine**
   - 使用 `(timestamp, global_seq)` 作為排序依據
   - 完美解決時序問題

4. **向後兼容**
   - 舊數據（沒有 seq）: 回退到保守策略（方案 1）
   - 新數據（有 seq）: 使用精確排序

---

## 當前系統設計評估（修正版）

### ✅ 已做對的部分

1. ✅ **Watermark 機制**: 確保不會「遺漏」成交前的 bidask
2. ✅ **Pending Buffer**: 避免過早處理 tick
3. ✅ **順序處理**: 在單個 topic 內保持順序

### ⚠️ 存在的問題

1. ❌ **無法處理同時間戳的因果性**: 當 `bidask.ts == tick.ts` 時，無法判斷先後
2. ❌ **可能使用成交後狀態**: OBI/OFI 可能被污染
3. ❌ **缺乏精確的時序信息**: 依賴時間戳，但精度不足

### 📊 問題影響範圍

| 場景 | 佔比 | 影響 |
|------|------|------|
| 無同時間戳 bidask | 85.4% | ✅ 無問題 |
| 有同時間戳 bidask | 14.6% | ⚠️ 可能有問題 |
| 最極端情況 (12 筆) | \<0.01% | ❌ 嚴重不確定 |

---

## 最終結論

> **問題**: 是否可以把同一時間的最後一筆 bidask data 當作 tick 成交的當下訂單簿資料？

**答案**: **不可以**。原因：

1. **因果倒置風險**: 同一毫秒內的 bidask 可能是成交**後**的結果
2. **無法判斷順序**: Kafka 跨 topic 無法提供微秒級的時序保證
3. **數據污染**: 會導致 OBI/OFI 等微結構指標不準確

> **建議**:

**立即採取** (短期):
- 實施「保守策略」：只使用 `timestamp < tick_ts` 的 bidask
- 接受略微更高的 LAG，但保證數據純淨度

**計劃實施** (長期):
- 在數據採集端添加全局序號（`global_seq`）
- 徹底解決時序問題

> **影響範圍**:

- 85.4% 的情況不受影響
- 14.6% 的情況需要更謹慎處理
- 當前系統的 watermark 機制**大方向正確**，但需要微調

---

## 附錄：執行資訊

- **分析工具**: `tools/analyze_tick_bidask_kafka.py`
- **執行時間**: 2025-12-23
- **數據時間**: 2025-12-22 Day Session (08:30:00 - 13:45:05)
- **Kafka Broker**: 192.168.1.50:9092
- **Topics**: txf-tick, txf-bidask
- **報告作者**: Antigravity AI
- **重要發現**: 時序不確定性問題（由用戶洞察發現）
