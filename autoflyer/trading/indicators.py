"""Technical indicator calculations."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import (
    ADX_LEN,
    ATR_LEN,
    ATR_Q_LOOKBACK,
    DON_TERM,
    MA_FAST,
    MA_SLOW,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    REGIME_MA_LEN,
    RSI_LEN,
    SUPERTREND_ATR_LEN,
)


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def rsi(series: pd.Series, length: int = RSI_LEN) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1 / length, adjust=False).mean()
    loss = (-delta).clip(lower=0.0).ewm(alpha=1 / length, adjust=False).mean()
    return 100 - (100 / (1 + gain / loss.replace(0, float("nan"))))


def atr(df: pd.DataFrame, length: int = ATR_LEN) -> pd.Series:
    return _true_range(df).ewm(alpha=1 / length, adjust=False).mean()


def macd(series: pd.Series) -> pd.DataFrame:
    line = ema(series, MACD_FAST) - ema(series, MACD_SLOW)
    signal = ema(line, MACD_SIGNAL)
    return pd.DataFrame({"macd": line, "macd_signal": signal, "macd_hist": line - signal})


def adx(df: pd.DataFrame, length: int = ADX_LEN) -> pd.Series:
    up, down = df["high"].diff(), -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)

    # _true_range を一度だけ計算して ATR と DI 両方に再利用
    tr = _true_range(df)
    alpha = 1 / length
    atr_ = tr.ewm(alpha=alpha, adjust=False).mean().replace(0, float("nan"))
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    return dx.ewm(alpha=alpha, adjust=False).mean()


def supertrend(df: pd.DataFrame, mult: float, length: int = SUPERTREND_ATR_LEN) -> pd.Series:
    """Supertrend トレーリングストップ・ラインを系列全体で計算する。

    上昇トレンド中は価格の下、下降トレンド中は価格の上に位置し、終値が
    ラインを突破するとフリップする。トレンド追従型のストップに適する。
    フリップ済みのラインをそのまま `stop_px` に使うと、ロングは下抜け、
    ショートは上抜けで決済される。
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr_arr = atr(df, length).to_numpy(dtype=float)
    n = len(close)
    hl2 = (high + low) / 2.0
    upper = hl2 + mult * atr_arr
    lower = hl2 - mult * atr_arr

    st = np.full(n, np.nan)
    final_upper = np.nan
    final_lower = np.nan
    prev_st = np.nan
    for i in range(n):
        if np.isnan(atr_arr[i]):
            continue
        fu = (
            upper[i]
            if (np.isnan(final_upper) or upper[i] < final_upper or close[i - 1] > final_upper)
            else final_upper
        )
        fl = (
            lower[i]
            if (np.isnan(final_lower) or lower[i] > final_lower or close[i - 1] < final_lower)
            else final_lower
        )
        if np.isnan(prev_st):
            cur_st = fu
        elif prev_st == final_upper:
            cur_st = fl if close[i] > fu else fu
        else:  # 直前はロワーバンド（上昇トレンド）
            cur_st = fu if close[i] < fl else fl
        st[i] = cur_st
        final_upper, final_lower, prev_st = fu, fl, cur_st

    return pd.Series(st, index=df.index)


def add_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    # 入力がすでにソート済みの場合はコピーのみ、未ソートなら sort して返す
    if bars["dt"].is_monotonic_increasing:
        x = bars.reset_index(drop=True).copy()
    else:
        x = bars.sort_values("dt").reset_index(drop=True)

    # MA クロス用の移動平均（バックテストで共有し重複計算を避ける）
    x["ma_fast"] = x["close"].rolling(MA_FAST).mean()
    x["ma_slow"] = x["close"].rolling(MA_SLOW).mean()

    x["ma200"] = x["close"].rolling(REGIME_MA_LEN).mean()
    x["regime_up"] = (x["close"] > x["ma200"]).astype("int64")
    x["rsi"] = rsi(x["close"])

    # ATR と ADX で true_range を共有して計算を節約
    tr = _true_range(x)
    alpha_atr = 1 / ATR_LEN
    atr_series = tr.ewm(alpha=alpha_atr, adjust=False).mean()
    x["atr"] = atr_series
    x["atr_pct"] = x["atr"] / x["close"]
    x["atr_pct"] = x["atr_pct"].replace([float("inf"), -float("inf")], float("nan"))
    x["atrpct_q75"] = x["atr_pct"].rolling(ATR_Q_LOOKBACK).quantile(0.75)

    # MACD を concat せず直接代入（中間 DataFrame を作らない）
    macd_df = macd(x["close"])
    x["macd"] = macd_df["macd"]
    x["macd_signal"] = macd_df["macd_signal"]
    x["macd_hist"] = macd_df["macd_hist"]
    x["macd_up"] = (x["macd_hist"] > 0).astype("int64")

    # ADX: true_range を再計算せず adx() を直接呼ぶ代わりにインライン計算
    up, down = x["high"].diff(), -x["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    alpha_adx = 1 / ADX_LEN
    atr_adx = tr.ewm(alpha=alpha_adx, adjust=False).mean().replace(0, float("nan"))
    plus_di = 100 * plus_dm.ewm(alpha=alpha_adx, adjust=False).mean() / atr_adx
    minus_di = 100 * minus_dm.ewm(alpha=alpha_adx, adjust=False).mean() / atr_adx
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    x["adx"] = dx.ewm(alpha=alpha_adx, adjust=False).mean()

    x["don_high"] = x["high"].rolling(DON_TERM).max().shift(1)
    x["don_low"] = x["low"].rolling(DON_TERM).min().shift(1)
    x["don_break_up"] = (pd.notna(x["don_high"]) & (x["high"] > x["don_high"])).astype("int64")

    return x
