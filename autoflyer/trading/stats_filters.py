"""Statistical filters for entry signal validation.

Implements:
1. Hurst exponent — trend vs mean-reversion detection
2. HMM regime — bull/bear/sideways classification
3. Kelly criterion — optimal bet sizing
4. Breakout z-score — statistical significance of breakout
5. MAE-based stop — stop width from historical adverse excursion
"""

from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd

from .indicators import log_returns

log = logging.getLogger("autoflyer.stats")


# ============================================================
# 1. Hurst Exponent (R/S method)
# ============================================================


def hurst_exponent(close: pd.Series, lookback: int = 100) -> float:
    """Estimate Hurst exponent using R/S analysis.

    H > 0.5: trending (persistent)
    H = 0.5: random walk
    H < 0.5: mean-reverting (anti-persistent)

    Returns 0.5 if insufficient data.
    """
    series = close.iloc[-lookback:] if len(close) >= lookback else close
    if len(series) < 20:
        return 0.5

    # 差分ベース（log_returns() の比率ベースとは丸め誤差が異なるためここでは使わない）
    log_ret = np.diff(np.log(series.to_numpy(dtype=float)))
    if len(log_ret) < 20:
        return 0.5

    # R/S analysis over multiple sub-periods
    max_k = min(len(log_ret) // 2, 50)
    if max_k < 8:
        return 0.5

    ns = []
    rs_vals = []
    for n in range(8, max_k + 1):
        num_blocks = len(log_ret) // n
        if num_blocks < 1:
            continue
        rs_block = []
        for i in range(num_blocks):
            block = log_ret[i * n : (i + 1) * n]
            mean_b = block.mean()
            deviate = np.cumsum(block - mean_b)
            r = deviate.max() - deviate.min()
            s = block.std(ddof=1)
            if s > 0:
                rs_block.append(r / s)
        if rs_block:
            ns.append(n)
            rs_vals.append(np.mean(rs_block))

    if len(ns) < 3:
        return 0.5

    log_n = np.log(ns)
    log_rs = np.log(rs_vals)
    # Linear regression: log(R/S) = H * log(n) + c
    coeffs = np.polyfit(log_n, log_rs, 1)
    h = float(coeffs[0])
    return max(0.0, min(1.0, h))


# ============================================================
# 2. Hidden Markov Model regime detection
# ============================================================


def hmm_regime(close: pd.Series, lookback: int = 252) -> int:
    """Classify current regime using 3-state Gaussian HMM on returns.

    Returns:
        0: bear (lowest mean return state)
        1: sideways (middle)
        2: bull (highest mean return state)
    """
    series = close.iloc[-lookback:] if len(close) >= lookback else close
    log_ret = log_returns(series).reshape(-1, 1)

    if len(log_ret) < 30:
        return 1  # default: sideways

    try:
        from hmmlearn.hmm import GaussianHMM

        # hmmlearn は収束しないたびに ConvergenceMonitor 経由でログを出す。
        # バックテストでは何千回も呼ぶため出力を抑制する。
        logging.getLogger("hmmlearn").setLevel(logging.ERROR)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = GaussianHMM(n_components=3, covariance_type="full", n_iter=100, random_state=42)
            model.fit(log_ret)
            states = model.predict(log_ret)

        # Map states by mean return: lowest=bear, highest=bull
        means = model.means_.flatten()
        order = np.argsort(means)  # [bear_idx, sideways_idx, bull_idx]
        state_map = {order[0]: 0, order[1]: 1, order[2]: 2}
        current_state = states[-1]
        return state_map[current_state]
    except Exception as e:
        log.warning("HMM fitting failed (%s) — defaulting to sideways", e)
        return 1


# ============================================================
# 3. Kelly Criterion
# ============================================================


def kelly_fraction(
    close: pd.Series,
    win_rate: float | None = None,
    avg_win: float | None = None,
    avg_loss: float | None = None,
    lookback: int = 50,
    half_kelly: bool = True,
) -> float:
    """Calculate Kelly fraction from recent trade-like returns.

    If win_rate/avg_win/avg_loss not provided, estimates from recent bar returns.
    Returns fraction [0, 1] of capital to bet.
    """
    if win_rate is not None and avg_win is not None and avg_loss is not None:
        if avg_loss == 0:
            return 1.0
        b = avg_win / avg_loss  # win/loss ratio
        f = (win_rate * b - (1 - win_rate)) / b
    else:
        # Estimate from bar returns
        series = close.iloc[-lookback:] if len(close) >= lookback else close
        returns = series.pct_change().dropna().to_numpy()
        if len(returns) < 10:
            return 0.5

        wins = returns[returns > 0]
        losses = returns[returns < 0]
        if len(wins) == 0 or len(losses) == 0:
            return 0.5

        wr = len(wins) / len(returns)
        avg_w = wins.mean()
        avg_l = abs(losses.mean())
        if avg_l == 0:
            return 1.0
        b = avg_w / avg_l
        f = (wr * b - (1 - wr)) / b

    f = max(0.0, min(1.0, f))
    return f * 0.5 if half_kelly else f


# ============================================================
# 4. Breakout z-score
# ============================================================


def breakout_zscore(close: pd.Series, lookback: int = 100) -> float:
    """Calculate z-score of current price relative to recent distribution.

    Measures how many standard deviations above the mean the current close is.
    Higher z → more statistically significant breakout.
    """
    series = close.iloc[-lookback:] if len(close) >= lookback else close
    if len(series) < 20:
        return 0.0

    # Exclude current bar for unbiased estimate
    hist = series.iloc[:-1].to_numpy()
    current = float(series.iloc[-1])
    mean = hist.mean()
    std = hist.std(ddof=1)
    if std == 0:
        return 0.0
    return float((current - mean) / std)


# ============================================================
# 5. Maximum Adverse Excursion (MAE) optimal stop
# ============================================================


def mae_optimal_stop(
    close: pd.Series,
    atr_series: pd.Series,
    lookback: int = 200,
    percentile: float = 95.0,
) -> float:
    """Calculate optimal stop width as ATR multiplier from MAE analysis.

    Simulates hypothetical long entries at each bar, measures max adverse excursion
    (worst drawdown before eventual recovery), and returns the ATR multiplier at
    the given percentile. Trades that never recover are excluded (they would have
    been stopped regardless).

    Returns ATR multiplier (e.g., 1.8 means stop at entry - 1.8*ATR).
    """
    c = close.iloc[-lookback:].to_numpy() if len(close) >= lookback else close.to_numpy()
    a = (
        atr_series.iloc[-lookback:].to_numpy()
        if len(atr_series) >= lookback
        else atr_series.to_numpy()
    )

    if len(c) < 30 or len(a) < 30:
        return 1.5  # default fallback

    min_len = min(len(c), len(a))
    c = c[-min_len:]
    a = a[-min_len:]

    mae_atrs = []
    window = min(20, min_len // 3)  # look ahead window

    for i in range(min_len - window):
        entry = c[i]
        entry_atr = a[i]
        if entry_atr <= 0 or np.isnan(entry_atr):
            continue

        # Max adverse excursion in the window
        future = c[i + 1 : i + 1 + window]
        min_price = future.min()
        mae = entry - min_price

        # Only include trades that eventually recovered (profitable entries)
        if future[-1] >= entry:
            mae_atrs.append(mae / entry_atr)

    if len(mae_atrs) < 10:
        return 1.5

    return float(np.percentile(mae_atrs, percentile))
