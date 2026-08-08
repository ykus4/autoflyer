"""GARCH(1,1)-based position sizing.

At each entry signal, fit GARCH(1,1) on the most recent `lookback` log-returns
to forecast next-period conditional volatility.  The position fraction is then:

    fraction = target_vol / forecast_vol

capped at `max_fraction` (default 1.0 = full capital).  When volatility is low
the model bets more; when high it bets less.

A plain std fallback is used if GARCH fails to converge.
"""

from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd

from .indicators import log_returns

log = logging.getLogger("autoflyer.garch")

_GARCH_LOOKBACK = 252  # ~1 year of daily bars for estimation


def garch_vol_forecast(
    close: pd.Series,
    lookback: int = _GARCH_LOOKBACK,
) -> float:
    """Return one-step-ahead annualised volatility forecast (as a fraction, e.g. 0.80 = 80%).

    Falls back to rolling std if GARCH does not converge.
    """
    series = close.iloc[-lookback:] if len(close) >= lookback else close
    log_ret = log_returns(series) * 100  # percent returns

    if len(log_ret) < 30:
        return float(np.std(log_ret) * np.sqrt(252) / 100) or 1.0

    try:
        from arch import arch_model  # local import to keep startup fast

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = arch_model(log_ret, vol="GARCH", p=1, q=1, dist="normal").fit(
                disp="off", show_warning=False
            )
        # one-step forecast variance (in percent^2)
        fc = res.forecast(horizon=1, reindex=False)
        var_pct2 = float(fc.variance.to_numpy()[-1, 0])
        # annualise and convert back to fraction
        return float(np.sqrt(var_pct2 * 252) / 100)
    except Exception as e:
        log.warning("GARCH fitting failed (%s) — falling back to rolling std", e)
        return float(np.std(log_ret) * np.sqrt(252) / 100) or 1.0


def garch_position_fraction(
    close: pd.Series,
    target_vol: float = 0.20,
    max_fraction: float = 1.0,
    lookback: int = _GARCH_LOOKBACK,
) -> float:
    """Return capital fraction [0, max_fraction] to allocate for this trade.

    target_vol: desired annualised portfolio volatility (e.g. 0.20 = 20 %).
    The higher the forecast vol, the smaller the position.
    """
    fvol = garch_vol_forecast(close, lookback)
    if fvol <= 0:
        return max_fraction
    fraction = target_vol / fvol
    return float(min(fraction, max_fraction))
