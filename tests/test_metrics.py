"""Tests for performance metrics and parameter search."""

import numpy as np
import pandas as pd
import pytest

from autoflyer.analysis import optimize
from autoflyer.analysis.metrics import equity_metrics, max_drawdown_pct, performance_table
from autoflyer.trading.indicators import add_indicators
from autoflyer.trading.strategy import Variant


def test_max_drawdown():
    assert max_drawdown_pct(pd.Series([100, 120, 90, 130])) == pytest.approx(25.0)
    assert max_drawdown_pct(pd.Series([1, 2, 3])) == 0.0


def test_cagr_of_steady_growth():
    # 1 年（365 本の日足）で 2 倍
    eq = pd.Series(100 * 2 ** (np.arange(366) / 365))
    m = equity_metrics(eq, "1D")
    assert m["total_return_pct"] == pytest.approx(100.0)
    assert m["cagr_pct"] == pytest.approx(100.0)
    assert m["max_dd_pct"] == 0.0
    assert np.isnan(m["calmar"])  # DD 0 では定義しない


def test_sharpe_sign_and_calmar():
    rng = np.random.default_rng(0)
    eq = pd.Series(100 * np.cumprod(1 + rng.normal(0.002, 0.01, 730)))
    m = equity_metrics(eq, "1D")
    assert m["sharpe"] > 0
    # 対称な分布なら下方偏差は標準偏差の約 1/√2 → ソルティノはシャープより大きい
    assert m["sharpe"] < m["sortino"] < m["sharpe"] * 2
    assert m["calmar"] == pytest.approx(m["cagr_pct"] / m["max_dd_pct"])


def test_short_series_is_nan():
    assert np.isnan(equity_metrics(pd.Series([100.0]), "1D")["cagr_pct"])


def test_performance_table_groups_and_filters():
    dt = pd.date_range("2024-01-01", periods=4, freq="1D", tz="UTC")
    eq = pd.DataFrame(
        {
            "strategy": ["A"] * 4 + ["B"] * 4,
            "timeframe": ["1D"] * 8,
            "dt": list(dt) * 2,
            "equity": [100, 110, 121, 133.1, 100, 90, 81, 72.9],
        }
    )
    t = performance_table(eq).set_index("strategy")
    assert t.loc["A", "total_return_pct"] == pytest.approx(33.1)
    assert t.loc["B", "max_dd_pct"] == pytest.approx(27.1)
    since = performance_table(eq, since=dt[1]).set_index("strategy")
    assert since.loc["A", "total_return_pct"] == pytest.approx(10.0)


class TestGrid:
    def test_parse_and_expand(self):
        base = Variant("B", atr_stop_mult=1.0, use_ma200_filter=True)
        grid = optimize.parse_grid(base, ["atr_stop_mult=1.0,2.0", "use_ma200_filter=true,false"])
        assert grid == {"atr_stop_mult": [1.0, 2.0], "use_ma200_filter": [True, False]}
        variants = optimize.expand(base, grid)
        assert len(variants) == 4
        assert variants[-1].atr_stop_mult == 2.0 and variants[-1].use_ma200_filter is False
        assert variants[0].name == "B[atr_stop_mult=1.0,use_ma200_filter=True]"

    def test_int_and_optional_fields(self):
        base = Variant("B")
        grid = optimize.parse_grid(base, ["cooldown_bars=0,3", "adx_min=none,20"])
        assert grid == {"cooldown_bars": [0, 3], "adx_min": [None, 20.0]}

    def test_unknown_field_rejected(self):
        with pytest.raises(SystemExit):
            optimize.parse_grid(Variant("B"), ["nope=1"])
        with pytest.raises(SystemExit):
            optimize.parse_grid(Variant("B"), ["atr_stop_mult"])


def _dataset(n: int = 1500) -> optimize.Dataset:
    rng = np.random.default_rng(1)
    close = 5_000_000 * np.cumprod(1 + rng.normal(0.0008, 0.03, n))
    bars = pd.DataFrame(
        {
            "dt": pd.date_range("2018-01-01", periods=n, freq="1D", tz="UTC"),
            "open": close,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": 1.0,
        }
    )
    return optimize.Dataset("1D", bars, add_indicators(bars))


def test_grid_ranks_by_metric():
    ds = _dataset()
    base = Variant("BRK", breakout_entry=True, atr_stop_mult=1.0)
    variants = optimize.expand(base, {"atr_stop_mult": [1.0, 2.0, 3.0]})
    ranked = optimize.grid(ds, variants, metric="sharpe")
    scores = ranked["sharpe"].dropna().tolist()
    assert scores == sorted(scores, reverse=True)
    assert set(ranked["variant"]) == {v.name for v in variants}


def test_walk_forward_windows_are_out_of_sample():
    ds = _dataset()
    base = Variant("BRK", breakout_entry=True, atr_stop_mult=1.0)
    variants = optimize.expand(base, {"atr_stop_mult": [1.0, 2.0]})
    wf = optimize.walk_forward(ds, variants, train_days=365, test_days=180)
    assert len(wf) >= 3
    # テスト区間は学習区間の直後に、重ならずに並ぶ
    assert (pd.to_datetime(wf["test_start"]) > pd.to_datetime(wf["train_start"])).all()
    starts = pd.to_datetime(wf["test_start"])
    assert (starts.diff().dropna() == pd.Timedelta(days=180)).all()
    summary = optimize.summarize_walk_forward(wf)
    assert summary["windows"] == len(wf)


def test_save_report_writes_json_without_nan(tmp_path):
    import json

    from autoflyer.analysis.runner import save_report

    perf = pd.DataFrame(
        [
            {"strategy": "A/1D", "timeframe": "1D", "calmar": float("nan"), "cagr_pct": 1.0},
            {"strategy": "B/1D", "timeframe": "1D", "calmar": 2.0, "cagr_pct": 5.0},
        ]
    )
    out = tmp_path / "r" / "latest.json"
    save_report(out, perf, csv="x.csv")
    data = json.loads(out.read_text())
    assert data["csv"] == "x.csv"
    assert [r["strategy"] for r in data["rows"]] == ["B/1D", "A/1D"]
    assert data["rows"][1]["calmar"] is None
