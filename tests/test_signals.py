"""Unit tests for the entry rules shared by the backtester and the live bot."""

import pandas as pd
import pytest

from autoflyer.trading.signals import (
    breakout_signals,
    cross_down,
    cross_up,
    exit_reason,
    exit_signals,
    long_ok,
    momentum,
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
        "don_exit_high": 105.0,
        "don_exit_low": 98.0,
        "bb_width": 0.05,
        "bb_width_q25": 0.10,
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


class TestExitSignals:
    def test_defaults_to_ma_cross(self):
        prev = _bar(ma_fast=101.0, ma_slow=100.0)
        cur = _bar(ma_fast=99.0, ma_slow=100.0)
        assert exit_signals(cur, prev, Variant("V")) == (True, False)
        assert exit_reason(Variant("V")) == "ma_cross"

    def test_donchian_exit_uses_short_channel(self):
        v = Variant("V", donchian_exit_term=10)
        # 10バー安値 (98) を割ったら決済、割らなければ継続
        assert exit_signals(_bar(low=97.0), _bar(), v)[0] is True
        assert exit_signals(_bar(low=99.0), _bar(), v)[0] is False
        assert exit_reason(v) == "don_exit"

    def test_donchian_exit_ignores_ma_cross(self):
        v = Variant("V", donchian_exit_term=10)
        prev = _bar(ma_fast=101.0, ma_slow=100.0)
        cur = _bar(ma_fast=99.0, ma_slow=100.0, low=99.0)  # MAデッドクロスだが安値は割っていない
        assert exit_signals(cur, prev, v)[0] is False

    def test_term_20_uses_entry_channel(self):
        v = Variant("V", donchian_exit_term=20)
        assert exit_signals(_bar(low=94.0), _bar(), v)[0] is True  # don_low=95
        assert exit_signals(_bar(low=96.0), _bar(), v)[0] is False

    def test_invalid_term_is_rejected(self):
        with pytest.raises(ValueError, match="donchian_exit_term"):
            Variant("V", donchian_exit_term=7)


class TestMomentum:
    def test_positive_and_negative(self):
        rising = pd.Series([100.0, 110.0, 120.0, 130.0])
        assert momentum(rising, 2) == pytest.approx(130 / 110 - 1)
        falling = pd.Series([130.0, 120.0, 110.0, 100.0])
        assert momentum(falling, 2) < 0

    def test_insufficient_history_returns_none(self):
        assert momentum(pd.Series([100.0, 110.0]), 5) is None


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

    def test_tsmom_blocks_when_price_is_below_lookback(self):
        v = Variant("V", mom_lookback=3)
        falling = pd.Series([130.0, 120.0, 110.0, 100.0])
        rising = pd.Series([100.0, 110.0, 120.0, 130.0])
        assert long_ok(_bar(), v, falling) is False
        assert long_ok(_bar(), v, rising) is True

    def test_bb_squeeze_requires_narrow_bands(self):
        v = Variant("V", bb_squeeze=True)
        assert long_ok(_bar(bb_width=0.05, bb_width_q25=0.10), v) is True
        assert long_ok(_bar(bb_width=0.20, bb_width_q25=0.10), v) is False

    def test_bb_squeeze_blocks_when_width_unknown(self):
        v = Variant("V", bb_squeeze=True)
        assert long_ok(_bar(bb_width=float("nan"), bb_width_q25=0.10), v) is False


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
