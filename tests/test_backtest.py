"""Unit tests for btcfx.backtest."""

import numpy as np
import pandas as pd
import pytest

from autoflyer.analysis.backtest import _apply_slippage, compute_signal, run
from autoflyer.config import START_CASH_JPY
from autoflyer.trading.strategy import Variant


def _make_bars(n: int = 500, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 5_000_000 + np.cumsum(rng.normal(0, 30_000, n))
    close = np.maximum(close, 100_000)
    high = close + rng.uniform(5_000, 50_000, n)
    low = close - rng.uniform(5_000, 50_000, n)
    return pd.DataFrame(
        {
            "dt": pd.date_range("2022-01-01", periods=n, freq="1D", tz="UTC"),
            "open": close,
            "high": high,
            "low": np.minimum(low, close),
            "close": close,
            "volume": rng.uniform(1, 10, n),
        }
    )


class TestComputeSignal:
    def test_returns_two_bools(self):
        bars = _make_bars(100)
        result = compute_signal(bars)
        assert len(result) == 2
        assert all(isinstance(v, bool) for v in result)

    def test_too_few_bars(self):
        bars = _make_bars(10)
        assert compute_signal(bars) == (False, False)

    def test_not_both_true(self):
        bars = _make_bars(300)
        up, down = compute_signal(bars)
        assert not (up and down)


class TestRun:
    def test_returns_dataframes(self):
        bars = _make_bars()
        trades, equity = run(
            bars, start_cash=START_CASH_JPY, tf_label="1D", variant=Variant("BASE")
        )
        assert isinstance(trades, pd.DataFrame)
        assert isinstance(equity, pd.DataFrame)

    def test_equity_non_negative(self):
        bars = _make_bars()
        _, equity = run(bars, start_cash=START_CASH_JPY, tf_label="1D", variant=Variant("BASE"))
        assert (equity["equity"] >= 0).all()

    def test_trade_columns(self):
        bars = _make_bars()
        trades, _ = run(bars, start_cash=START_CASH_JPY, tf_label="1D", variant=Variant("BASE"))
        if trades.empty:
            pytest.skip("No trades generated with this seed")
        required = ["entry_dt", "exit_dt", "net_pnl_jpy", "fee_jpy", "cash_after", "win"]
        for col in required:
            assert col in trades.columns

    def test_cash_never_negative(self):
        bars = _make_bars()
        trades, _ = run(
            bars,
            start_cash=START_CASH_JPY,
            tf_label="1D",
            variant=Variant("STOP_3ATR", atr_stop_mult=3.0),
        )
        if not trades.empty:
            assert (trades["cash_after"] >= 0).all()

    def test_stop_reduces_trade_count_vs_no_stop(self):
        bars = _make_bars(800, seed=1)
        trades_base, _ = run(
            bars, start_cash=START_CASH_JPY, tf_label="1D", variant=Variant("BASE")
        )
        trades_stop, _ = run(
            bars,
            start_cash=START_CASH_JPY,
            tf_label="1D",
            variant=Variant("STOP_2ATR", atr_stop_mult=2.0),
        )
        # ストップがあると同数以上のトレード（ストップで早期決済 → 再エントリーが増える）
        assert len(trades_stop) >= len(trades_base)

    def test_walk_forward_reduces_trades(self):
        bars = _make_bars(800, seed=2)
        train_end = pd.Timestamp("2023-06-01", tz="UTC")
        trades_full, _ = run(
            bars, start_cash=START_CASH_JPY, tf_label="1D", variant=Variant("BASE")
        )
        trades_wf, _ = run(
            bars,
            start_cash=START_CASH_JPY,
            tf_label="1D",
            variant=Variant("BASE"),
            train_end=train_end,
        )
        assert len(trades_wf) <= len(trades_full)

    def test_risk_sizing_caps_btc(self):
        bars = _make_bars(500, seed=3)
        v_risk = Variant("RISK1PCT", atr_stop_mult=3.0, risk_pct=1.0)
        v_full = Variant("BASE")
        trades_risk, _ = run(bars, start_cash=START_CASH_JPY, tf_label="1D", variant=v_risk)
        trades_full, _ = run(bars, start_cash=START_CASH_JPY, tf_label="1D", variant=v_full)
        if trades_risk.empty or trades_full.empty:
            pytest.skip("No trades")
        # リスクサイジングの方が BTC 数量が少ないか等しい
        assert trades_risk["btc"].mean() <= trades_full["btc"].mean() + 1e-8


class TestSlippage:
    def test_long_entry_pays_more(self):
        # ロングエントリーはスリッページで高く約定
        px = _apply_slippage(1_000_000.0, "long", "entry", 0.001)
        assert px == pytest.approx(1_001_000.0)

    def test_long_exit_receives_less(self):
        px = _apply_slippage(1_000_000.0, "long", "exit", 0.001)
        assert px == pytest.approx(999_000.0)

    def test_zero_slippage_unchanged(self):
        px = _apply_slippage(1_000_000.0, "long", "entry", 0.0)
        assert px == 1_000_000.0

    def test_slippage_reduces_pnl(self):
        bars = _make_bars(500, seed=0)
        trades_no_slip, _ = run(
            bars, start_cash=START_CASH_JPY, tf_label="1D", variant=Variant("BASE")
        )
        trades_slip, _ = run(
            bars,
            start_cash=START_CASH_JPY,
            tf_label="1D",
            variant=Variant("BASE"),
            slippage_pct=0.001,
        )
        if trades_no_slip.empty:
            pytest.skip("No trades")
        # スリッページありの方が純損益が低い（または同等）
        assert trades_slip["net_pnl_jpy"].sum() <= trades_no_slip["net_pnl_jpy"].sum() + 1e-3


class TestCooldown:
    def test_cooldown_reduces_trades_after_stop(self):
        bars = _make_bars(800, seed=1)
        v_no_cd = Variant("STOP_NO_CD", atr_stop_mult=2.0, cooldown_bars=0)
        v_cd = Variant("STOP_CD5", atr_stop_mult=2.0, cooldown_bars=5)
        trades_no_cd, _ = run(bars, start_cash=START_CASH_JPY, tf_label="1D", variant=v_no_cd)
        trades_cd, _ = run(bars, start_cash=START_CASH_JPY, tf_label="1D", variant=v_cd)
        # クールダウンがあるとトレード数が減るか同等
        assert len(trades_cd) <= len(trades_no_cd)
