"""Tests for Binance kline helpers (no network)."""

import pandas as pd
import pytest

from autoflyer.trading.market_data import binance_interval, recent_ohlcv, rescale

HOUR_MS = 3_600_000


class FakeSession:
    """1h 足を返す偽の Binance。startTime 以降を最大 limit 本返す。"""

    def __init__(self, n_hours: int) -> None:
        self.klines = [
            [i * HOUR_MS, 100 + i, 101 + i, 99 + i, 100.5 + i, 1.0, 0] for i in range(n_hours)
        ]
        self.calls = 0

    def get(self, url, params, timeout):
        self.calls += 1
        start = params["startTime"]
        batch = [k for k in self.klines if k[0] >= start][: params["limit"]]

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return batch

        return R()


@pytest.mark.parametrize(
    ("tf", "expected"),
    [
        ("1H", "1h"),
        ("4H", "4h"),
        ("12H", "12h"),
        ("1D", "1d"),
        ("D", "1d"),
        ("3D", "3d"),
        ("3H", None),
    ],
)
def test_binance_interval(tf, expected):
    assert binance_interval(tf) == expected


def test_recent_ohlcv_resamples_non_native_timeframe():
    session = FakeSession(n_hours=3000)
    now_ms = 2999 * HOUR_MS
    df = recent_ohlcv("3H", 500, session=session, now_ms=now_ms)
    assert len(df) == 500
    assert session.calls >= 2  # 1000 本を超えるのでページングする
    assert (df["dt"].diff().dropna() == pd.Timedelta(hours=3)).all()
    first = df.iloc[0]
    assert first["high"] >= first["open"] and first["low"] <= first["close"]


def test_rescale_matches_reference_price():
    bars = pd.DataFrame({"open": [10.0], "high": [12.0], "low": [9.0], "close": [10.0]})
    out, ratio = rescale(bars, 20.0)
    assert ratio == 2.0
    assert out["close"].iloc[-1] == 20.0 and out["high"].iloc[-1] == 24.0
    assert bars["close"].iloc[-1] == 10.0  # 元データは変更しない
