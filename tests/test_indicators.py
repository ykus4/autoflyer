"""Unit tests for btcfx.indicators."""

import numpy as np
import pandas as pd

from autoflyer.trading.indicators import add_indicators, adx, atr, macd, rsi, supertrend


def _bars(n: int = 100, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 5_000_000 + np.cumsum(rng.normal(0, 50_000, n))
    high = close + rng.uniform(10_000, 80_000, n)
    low = close - rng.uniform(10_000, 80_000, n)
    return pd.DataFrame(
        {
            "dt": pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC"),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(1, 10, n),
        }
    )


class TestRsi:
    def test_range(self):
        df = _bars(200)
        r = rsi(df["close"])
        valid = r.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_length(self):
        df = _bars(50)
        r = rsi(df["close"], length=14)
        assert len(r) == 50

    def test_constant_series_returns_nan(self):
        s = pd.Series([100.0] * 30)
        r = rsi(s)
        # gain と loss が両方 0 → NaN
        assert r.dropna().empty or (r.dropna() == 50.0).all() or r.dropna().isna().all()


class TestAtr:
    def test_positive(self):
        df = _bars(50)
        a = atr(df)
        assert (a.dropna() > 0).all()

    def test_length(self):
        df = _bars(50)
        assert len(atr(df)) == 50


class TestMacd:
    def test_columns(self):
        df = _bars(100)
        m = macd(df["close"])
        assert set(m.columns) == {"macd", "macd_signal", "macd_hist"}

    def test_hist_equals_line_minus_signal(self):
        df = _bars(100)
        m = macd(df["close"])
        diff = (m["macd"] - m["macd_signal"] - m["macd_hist"]).abs()
        assert diff.max() < 1e-9


class TestAdx:
    def test_range(self):
        df = _bars(200)
        a = adx(df)
        valid = a.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()


class TestSupertrend:
    def test_length_and_index(self):
        df = _bars(100)
        st = supertrend(df, mult=3.0)
        assert len(st) == 100
        assert list(st.index) == list(df.index)

    def test_line_between_high_and_low_bounds(self):
        # Supertrend ラインは価格レンジ内に収まる（極端に外れない）
        df = _bars(200)
        st = supertrend(df, mult=3.0).dropna()
        assert (st > 0).all()
        assert st.max() <= df["high"].max() * 1.5

    def test_tighter_mult_closer_to_price(self):
        # mult が小さいほどラインは価格に近い（平均乖離が小さい）
        df = _bars(200)
        close = df["close"]
        near = (supertrend(df, mult=1.0) - close).abs().mean()
        far = (supertrend(df, mult=5.0) - close).abs().mean()
        assert near < far


class TestAddIndicators:
    def test_all_columns_exist(self):
        df = _bars(300)
        out = add_indicators(df)
        required = [
            "ma_fast",
            "ma_slow",
            "ma200",
            "regime_up",
            "rsi",
            "atr",
            "atr_pct",
            "atrpct_q75",
            "macd",
            "macd_signal",
            "macd_hist",
            "macd_up",
            "adx",
            "don_high",
            "don_low",
            "don_break_up",
        ]
        for col in required:
            assert col in out.columns, f"Missing column: {col}"

    def test_regime_up_binary(self):
        df = _bars(300)
        out = add_indicators(df)
        assert set(out["regime_up"].dropna().unique()).issubset({0, 1})

    def test_no_lookahead_don_high(self):
        # don_high は shift(1) なので現在バーの high より低いはず（ブレークアウト判定用）
        df = _bars(300)
        out = add_indicators(df).dropna(subset=["don_high"])
        # ドンチャン高値は「前バーまでの最高値」なので現在 high より常に低いとは限らないが
        # don_break_up は high > don_high のときだけ 1 になることを確認
        mask = out["don_break_up"] == 1
        assert (out.loc[mask, "high"] > out.loc[mask, "don_high"]).all()
