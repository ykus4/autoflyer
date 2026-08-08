"""Unit tests for the entry rules shared by the backtester and the live bot."""

import pandas as pd
import pytest

from autoflyer.trading.signals import (
    breakout_signals,
    cross_down,
    cross_up,
    long_ok,
    position_size,
    short_ok,
)
from autoflyer.trading.strategy import Variant


def _bar(**overrides) -> pd.Series:
    base = {
        "high": 110.0,
        "low": 90.0,
        "close": 100.0,
        "ma_fast": 100.0,
        "ma_slow": 100.0,
        "regime_up": 1,
        "rsi": 60.0,
        "adx": 30.0,
        "atr_pct": 0.02,
        "atrpct_q75": 0.05,
        "don_high": 105.0,
        "don_low": 95.0,
        "don_break_up": 1,
    }
    return pd.Series({**base, **overrides})


class TestCrosses:
    def test_cross_up(self):
        prev = _bar(ma_fast=99.0, ma_slow=100.0)
        cur = _bar(ma_fast=101.0, ma_slow=100.0)
        assert cross_up(cur, prev) is True
        assert cross_down(cur, prev) is False

    def test_cross_down(self):
        prev = _bar(ma_fast=101.0, ma_slow=100.0)
        cur = _bar(ma_fast=99.0, ma_slow=100.0)
        assert cross_down(cur, prev) is True

    def test_nan_ma_is_not_a_cross(self):
        prev = _bar(ma_fast=float("nan"))
        cur = _bar(ma_fast=101.0)
        assert cross_up(cur, prev) is False
        assert cross_down(cur, prev) is False


class TestBreakoutSignals:
    def test_breaks_above_channel(self):
        assert breakout_signals(_bar(high=106.0, low=100.0)) == (True, False)

    def test_breaks_below_channel(self):
        assert breakout_signals(_bar(high=100.0, low=94.0)) == (False, True)

    def test_inside_channel(self):
        assert breakout_signals(_bar(high=104.0, low=96.0)) == (False, False)

    def test_missing_channel_is_no_signal(self):
        assert breakout_signals(_bar(don_high=float("nan"), don_low=float("nan"))) == (False, False)


class TestLongOk:
    def test_passes_with_no_filters(self):
        assert long_ok(_bar(), Variant("V")) is True

    def test_ma200_filter_blocks_below_regime(self):
        assert long_ok(_bar(regime_up=0), Variant("V", use_ma200_filter=True)) is False

    def test_rsi_below_min_blocks(self):
        assert long_ok(_bar(rsi=40.0), Variant("V", rsi_min=50.0)) is False

    def test_adx_below_min_blocks(self):
        assert long_ok(_bar(adx=10.0), Variant("V", adx_min=20.0)) is False

    def test_high_volatility_blocks_when_avoiding(self):
        v = Variant("V", atr_high_avoid=True)
        assert long_ok(_bar(atr_pct=0.09), v) is False
        assert long_ok(_bar(atr_pct=0.01), v) is True

    def test_donchian_confirmation_required(self):
        assert long_ok(_bar(don_break_up=0), Variant("V", require_don_break=True)) is False

    def test_rejection_reason_is_reported(self):
        reasons: list[str] = []
        long_ok(_bar(regime_up=0), Variant("V", use_ma200_filter=True), on_reject=reasons.append)
        assert reasons and "MA200" in reasons[0]

    def test_statistical_filters_skipped_without_history(self):
        # close_hist=None のときは Hurst/z-score を評価せず通過させる
        assert long_ok(_bar(), Variant("V", hurst_min=0.9, zscore_min=9.0)) is True

    def test_zscore_filter_blocks_weak_breakout(self):
        flat = pd.Series([100.0] * 50 + [100.5])
        assert long_ok(_bar(), Variant("V", zscore_min=2.0), flat) is False


class TestShortOk:
    def test_blocked_while_regime_is_up(self):
        assert short_ok(_bar(regime_up=1), Variant("V")) is False

    def test_allowed_in_downtrend(self):
        assert short_ok(_bar(regime_up=0), Variant("V")) is True

    def test_adx_min_applies(self):
        assert short_ok(_bar(regime_up=0, adx=5.0), Variant("V", adx_min=20.0)) is False


class TestPositionSize:
    def test_full_allocation_without_risk_pct(self):
        btc = position_size(1_000_000, 100.0, None, 0.0, Variant("V"))
        assert btc == pytest.approx(10_000.0)

    def test_fee_reduces_size(self):
        btc = position_size(1_000_000, 100.0, None, 0.001, Variant("V"))
        assert btc == pytest.approx(10_000 / 1.001)

    def test_risk_pct_caps_by_stop_width(self):
        # 1% of 1,000,000 = 10,000 risk / stop width 10 = 1,000 BTC
        btc = position_size(1_000_000, 100.0, 90.0, 0.0, Variant("V", risk_pct=1.0))
        assert btc == pytest.approx(1_000.0)

    def test_risk_sizing_never_exceeds_cash(self):
        # ストップが極端に近いとリスク計算上は巨大になるが、資金上限で頭打ちになる
        btc = position_size(1_000_000, 100.0, 99.999, 0.0, Variant("V", risk_pct=50.0))
        assert btc == pytest.approx(10_000.0)

    def test_sizing_fraction_scales_down(self):
        btc = position_size(1_000_000, 100.0, None, 0.0, Variant("V"), sizing_frac=0.4)
        assert btc == pytest.approx(4_000.0)
