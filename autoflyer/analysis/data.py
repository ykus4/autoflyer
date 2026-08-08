"""OHLCV data loading and resampling."""

from __future__ import annotations

from collections.abc import Hashable, Mapping

import pandas as pd

from ..timeframes import to_pandas_rule

_OHLCV_DTYPE: Mapping[Hashable, type[complex]] = {
    "open": float,
    "high": float,
    "low": float,
    "close": float,
    "volume": float,
}


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=_OHLCV_DTYPE)
    df["dt"] = pd.to_datetime(df["dt"], utc=True, errors="coerce")
    return (
        df.dropna(subset=["dt", "open", "high", "low", "close"])
        .sort_values("dt")
        .reset_index(drop=True)
    )


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    return (
        df.set_index("dt")
        .resample(to_pandas_rule(timeframe))
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
