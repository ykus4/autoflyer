"""Performance metrics computed from a backtest equity curve."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..timeframes import to_minutes

_MINUTES_PER_YEAR = 365 * 1440  # 暗号資産は 24 時間 365 日取引される

METRIC_COLUMNS = ["total_return_pct", "cagr_pct", "max_dd_pct", "sharpe", "sortino", "calmar"]


def bars_per_year(tf: str) -> float:
    return _MINUTES_PER_YEAR / to_minutes(tf)


def max_drawdown_pct(equity: pd.Series) -> float:
    """ピークからの最大下落率（%）。"""
    peak = equity.cummax()
    dd = (peak - equity) / peak.replace(0, np.nan)
    value = float(dd.max() * 100)
    return 0.0 if math.isnan(value) else value


def equity_metrics(equity: pd.Series, tf: str) -> dict[str, float]:
    """1 本の資産曲線（バー順）から主要指標を計算する。

    - total_return_pct / cagr_pct: 期間リターンと年率換算
    - max_dd_pct: 最大ドローダウン
    - sharpe / sortino: バーごとのリターンから年率換算（無リスク金利 0）
    - calmar: CAGR / 最大ドローダウン
    """
    eq = equity.astype(float).reset_index(drop=True)
    nan_result = dict.fromkeys(METRIC_COLUMNS, float("nan"))
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return nan_result

    per_year = bars_per_year(tf)
    total = eq.iloc[-1] / eq.iloc[0] - 1.0
    years = (len(eq) - 1) / per_year
    cagr = (1.0 + total) ** (1.0 / years) - 1.0 if years > 0 and total > -1 else float("nan")

    rets = eq.pct_change().dropna()
    std = float(rets.std(ddof=1))
    sharpe = float(rets.mean() / std * math.sqrt(per_year)) if std > 0 else float("nan")
    # 下方偏差は全リターンで平均する（利益側は 0 とみなす）
    dstd = float(np.sqrt((rets.clip(upper=0) ** 2).mean()))
    sortino = float(rets.mean() / dstd * math.sqrt(per_year)) if dstd > 0 else float("nan")

    mdd = max_drawdown_pct(eq)
    calmar = cagr * 100 / mdd if mdd > 0 and not math.isnan(cagr) else float("nan")
    return {
        "total_return_pct": total * 100,
        "cagr_pct": cagr * 100,
        "max_dd_pct": mdd,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
    }


def performance_table(equity: pd.DataFrame, since: pd.Timestamp | None = None) -> pd.DataFrame:
    """backtest の equity_df（複数戦略可）を戦略 × 時間足ごとの指標表にする。

    `since` を渡すとその時刻より後の区間だけで評価する（ウォークフォワードのテスト期間用）。
    """
    rows = []
    for (strategy, tf), g in equity.groupby(["strategy", "timeframe"], observed=True, sort=False):
        g = g.sort_values("dt")
        if since is not None:
            g = g[g["dt"] > since]
        rows.append({"strategy": strategy, "timeframe": tf, **equity_metrics(g["equity"], str(tf))})
    return pd.DataFrame(rows, columns=["strategy", "timeframe", *METRIC_COLUMNS])
