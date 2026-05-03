"""BitFlyer fee tier model based on 30-day rolling trading volume."""

from __future__ import annotations

import bisect
from collections import deque

import pandas as pd

# 上限ボリューム一覧（bisect で O(log N) 検索するために分離）
_TIER_UPPER: list[float] = [
    100_000,
    200_000,
    500_000,
    1_000_000,
    2_000_000,
    5_000_000,
    10_000_000,
    20_000_000,
    50_000_000,
    100_000_000,
    500_000_000,
    float("inf"),
]
_TIER_RATES: list[float] = [
    0.0015,
    0.0014,
    0.0013,
    0.0012,
    0.0011,
    0.0010,
    0.0009,
    0.0007,
    0.0005,
    0.0003,
    0.0002,
    0.0001,
]


def rate_for_volume(vol_30d_jpy: float) -> float:
    # 元ロジック: vol < upper のとき適用 → bisect_left で「upper > vol」の最初のインデックスを取得
    idx = bisect.bisect_left(_TIER_UPPER, vol_30d_jpy + 1e-9)
    return _TIER_RATES[min(idx, len(_TIER_RATES) - 1)]


class FeeTierModel:
    """
    直近30日の約定代金(JPY)でティアを決める。
    日次更新: 当日の手数料は前日末時点の30日集計で確定（先読み回避）。
    """

    def __init__(self) -> None:
        self._queue: deque[tuple[pd.Timestamp, float]] = deque()
        self._vol_30d: float = 0.0
        self._last_day: pd.Timestamp | None = None
        self.rate: float = rate_for_volume(0.0)

    def step(self, dt: pd.Timestamp) -> None:
        day = pd.Timestamp(dt).floor("D")
        if self._last_day is None or day != self._last_day:
            self._prune(day)
            self.rate = rate_for_volume(self._vol_30d)
            self._last_day = day

    def record_fill(self, dt: pd.Timestamp, notional_jpy: float) -> None:
        self._queue.append((dt, notional_jpy))
        self._vol_30d += notional_jpy
        self._prune(dt)

    def _prune(self, now: pd.Timestamp) -> None:
        cutoff = now - pd.Timedelta(days=30)
        while self._queue and self._queue[0][0] < cutoff:
            _, v = self._queue.popleft()
            self._vol_30d -= v
