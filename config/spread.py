"""價差三條線的**唯一定義**(階段 3b,2026-08-11)。全景見 `wiki/Spread-Semantics.md`。

## 為什麼要有這個檔

2026-08-11 清點時,同一條規則在 repo 裡被寫了**五次**:

    feeds/historical.py  _spread_cols        polars,歷史主線
    feeds/historical.py  _apply_settle_1d    polars,結算口徑覆蓋
    core/data_engine.py  _process_tick       純量,live 次月成交當下(×2:calendar / r2_basis)
    core/feature_engine.py                   純量,live 的 basis + calendar 退路

五份都對,但**它們是各自對的** —— 沒有任何機制保證第六次改動會同時落在五個地方。
2026-08-11 的災情裡有三類問題(D/E/F)都是這個形狀:一份改了、另一份沒有,
而分歧的那天**沒有人會被通知**。

## 為什麼是「兩份實作 + 一條等價測試」而不是一份

歷史路徑要**向量化**(5s 一次兩萬列,逐列呼叫 Python 函式會慢兩個數量級);
live 路徑是**逐 tick 的純量**。硬要共用一份,不是把純量塞進 `map_elements`(慢),
就是把 live 改成建 DataFrame(荒謬)。

⇒ 誠實的做法:**一個規格、兩份實作、一條測試釘住兩者等價**
(`tests/test_spread_definition.py`,含 null / 0 / 負 / 極端值)。
這與「一份實作」的差別在於**分歧會當場變紅**,而不是等使用者看到怪數字。

## 規格

    basis           = R1(近月 TXF)  − 現貨(TAIEX)
    r2_basis        = R2(次月)      − 現貨
    calendar_spread = R2            − R1

⚠ 這是**台灣慣例(期貨 − 現貨)**,與教科書的 basis(現貨 − 期貨)符號相反。
  使用者鎖定,不要「修正」它。

⚠ 任一腿缺(None)或 **≤ 0** ⇒ 該條線是 `None`,**不是 0**。
  0 是一個合法價差值(兩腿等價),用它表示「算不出來」會讓下游分不出來。
  live 那三處本來就擋 `> 0`,歷史那兩處只擋 null —— 這裡統一成前者
  (指數與期貨價永遠 > 0,所以這只會擋掉壞資料,不會改變任何真實值)。

## 這個檔**不管**的事(刻意)

‧ **鮮度 / ffill / 階梯沿用** —— 那是「V46:期貨腿有新成交的那一刻算一次,之間沿用」,
  屬於呼叫端的時序邏輯,不是減法本身。混進來會讓這個檔跟著時序需求一起變。
‧ **口徑選擇(close vs settle)** —— 呼叫端決定拿哪一組價進來減。
‧ **age** —— 見 `feeds/historical.py::_with_age` 與 `core/data_engine._calendar_for`。
"""
from __future__ import annotations

from typing import Dict, Optional

BASIS = "basis"
R2_BASIS = "r2_basis"
CALENDAR = "calendar_spread"
#: 三條線的正式欄名(順序 = 疊圖與 legend 的順序,見 wiki VWAP 家族的同款約定)
NAMES = (BASIS, R2_BASIS, CALENDAR)
#: 價差一律取到小數兩位(TXF 最小跳動 1 點,但現貨指數有小數 ⇒ basis 有小數)
ROUND_DP = 2


def _usable(x) -> bool:
    """腿可用 = 有值且 > 0。見檔頭「≤ 0 ⇒ None」的說明。"""
    return x is not None and x > 0


def spread_from_legs(r1=None, r2=None, spot=None) -> Dict[str, Optional[float]]:
    """三條腿 → 三條價差(純量版)。缺腿的那條是 `None`。

    >>> spread_from_legs(r1=45000, r2=45123, spot=44928.76)
    {'basis': 71.24, 'r2_basis': 194.24, 'calendar_spread': 123.0}
    >>> spread_from_legs(r1=45000, spot=44928.76)          # 沒有次月
    {'basis': 71.24, 'r2_basis': None, 'calendar_spread': None}
    """
    def sub(a, b):
        return round(a - b, ROUND_DP) if (_usable(a) and _usable(b)) else None
    return {BASIS: sub(r1, spot), R2_BASIS: sub(r2, spot), CALENDAR: sub(r2, r1)}


def spread_exprs(r1: str = "R1", r2: str = "R2", spot: str = "TAIEX") -> Dict[str, "object"]:
    """同一條規則的 polars 版,回傳 `{欄名: 運算式}`。

    參數是**欄名**,所以結算口徑那條路可以傳 `("r1_settle", "r2_settle", "TAIEX")`
    —— 換的是輸入,不是規則。

    ⚠ 回傳 dict 而非 list:呼叫端常常只要其中一兩條(`_apply_settle_1d` 要各自套
      不同的覆蓋條件),用位置索引取會在加欄那天靜默錯位。
    """
    import polars as pl

    def sub(a: str, b: str, name: str):
        # null 參與比較 ⇒ null ⇒ when 不成立 ⇒ 結果 null,與純量版的 `_usable` 同義
        return (pl.when((pl.col(a) > 0) & (pl.col(b) > 0))
                  .then((pl.col(a) - pl.col(b)).round(ROUND_DP))
                  .alias(name))

    return {BASIS: sub(r1, spot, BASIS),
            R2_BASIS: sub(r2, spot, R2_BASIS),
            CALENDAR: sub(r2, r1, CALENDAR)}
