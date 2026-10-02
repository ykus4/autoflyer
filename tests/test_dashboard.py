"""Tests for dashboard API handlers (called directly, no HTTP server)."""

import json

from autoflyer import dashboard
from autoflyer.dashboard import DashboardSettings, configure
from autoflyer.trading.state import append_trade


def _setup(tmp_path, **kw):
    settings = DashboardSettings(
        state_file=tmp_path / "state.json",
        backtest_report=tmp_path / "latest.json",
        **kw,
    )
    configure(settings)
    return settings


def test_trades_returns_recent_rows(tmp_path):
    s = _setup(tmp_path)
    for i in range(5):
        append_trade(s.trades_file, {"action": "entry", "price": i})
    out = dashboard.api_trades(n=3, _="u")
    assert [t["price"] for t in out["trades"]] == [2, 3, 4]


def test_trades_empty_when_no_file(tmp_path):
    _setup(tmp_path)
    assert dashboard.api_trades(n=10, _="u") == {"trades": []}


def test_backtest_report(tmp_path):
    s = _setup(tmp_path)
    assert dashboard.api_backtest(_="u")["rows"] == []
    s.backtest_report.write_text(json.dumps({"rows": [{"strategy": "A"}], "generated_at": "x"}))
    assert dashboard.api_backtest(_="u")["rows"] == [{"strategy": "A"}]
    s.backtest_report.write_text("{broken")
    assert dashboard.api_backtest(_="u")["rows"] == []


def test_ticker_short_pnl_and_cfd_collateral(tmp_path, monkeypatch):
    s = _setup(tmp_path, symbol="FX_BTC_JPY", api_key="k", api_secret="s")
    s.state_file.write_text(
        json.dumps({"in_pos": True, "side": "short", "entry_price": 100.0, "btc": 2.0})
    )
    monkeypatch.setattr(dashboard._client, "fetch_ticker", lambda _p: {"ltp": 90.0})
    monkeypatch.setattr(
        dashboard._client,
        "fetch_collateral",
        lambda: {"collateral": 5000, "open_position_pnl": 20, "keep_rate": 3.5},
    )
    out = dashboard.api_ticker(_="u")
    assert out["unrealized_pnl"] == 20
    assert out["unrealized_pnl_pct"] == 10.0
    assert out["is_cfd"] is True and out["collateral"] == 5000 and out["jpy_balance"] is None


def test_index_renders(tmp_path):
    _setup(tmp_path)
    html = dashboard.index(_="u")
    assert "priceChart" in html and "halt-banner" in html
