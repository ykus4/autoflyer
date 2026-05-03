"""OHLCV data loading and resampling."""

from __future__ import annotations

import pandas as pd

_OHLCV_DTYPE: dict[str, str] = {
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
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
    rule = _to_pandas_rule(timeframe)
    return (
        df.set_index("dt")
        .resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )


def _to_pandas_rule(tf: str) -> str:
    tf = tf.upper().strip()
    if tf.endswith("H"):
        return f"{tf[:-1]}h"
    if tf in ("1D", "D"):
        return "1D"
    raise ValueError(f"Unsupported timeframe: {tf}")
