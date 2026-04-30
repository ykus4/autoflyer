"""Unit tests for btcfx.fees."""

import pandas as pd

from autoflyer.fees import FeeTierModel, rate_for_volume


class TestRateForVolume:
    def test_zero_volume(self):
        assert rate_for_volume(0) == 0.0015

    def test_tier_boundary(self):
        assert rate_for_volume(99_999) == 0.0015
        assert rate_for_volume(100_000) == 0.0014

    def test_max_tier(self):
        assert rate_for_volume(999_999_999) == 0.0001


class TestFeeTierModel:
    def _ts(self, day: str) -> pd.Timestamp:
        return pd.Timestamp(day, tz="UTC")

    def test_initial_rate(self):
        m = FeeTierModel()
        assert m.rate == 0.0015

    def test_rate_drops_after_large_fill(self):
        m = FeeTierModel()
        m.record_fill(self._ts("2024-01-01"), 200_000_000)
        m.step(self._ts("2024-01-02"))
        assert m.rate < 0.0015

    def test_prune_after_30_days(self):
        m = FeeTierModel()
        m.record_fill(self._ts("2024-01-01"), 200_000_000)
        m.step(self._ts("2024-02-02"))  # 32 日後
        # 30 日以上前のデータは除去され、レートはリセットされる
        assert m.rate == 0.0015

    def test_no_lookahead(self):
        # step() を呼ぶと「前日末時点の量」でレートを確定する（当日 fill の影響を受けない）
        m = FeeTierModel()
        m.step(self._ts("2024-01-01"))
        rate_before = m.rate
        m.record_fill(self._ts("2024-01-01"), 200_000_000)
        # 同日内に step を再度呼んでもレートは変わらない
        m.step(self._ts("2024-01-01"))
        assert m.rate == rate_before

    def test_cumulative_volume(self):
        m = FeeTierModel()
        for i in range(5):
            m.record_fill(self._ts(f"2024-01-{i + 1:02d}"), 50_000)
        m.step(self._ts("2024-01-06"))
        # 累積 250,000 JPY → 0.0013
        assert m.rate == 0.0013
