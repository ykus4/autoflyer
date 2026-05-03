"""Performance reporting utilities."""

from __future__ import annotations

import math

import pandas as pd

from ..config import SHOW_LAST_N_MONTHS, TZ_DISPLAY


def print_overall_summary(trades: pd.DataFrame) -> None:
    print("\n=== Overall Summary ===")
    if trades.empty:
        print("(no trades)")
        return

    def _pf(x: pd.Series) -> float:
        profits = x[x > 0].sum()
        losses = -x[x < 0].sum()
        return float(profits / losses) if losses > 0 else (float("inf") if profits > 0 else 0.0)

    g = trades.groupby(["strategy", "timeframe"], as_index=False, observed=True).agg(
        trades=("net_pnl_jpy", "count"),
        net_jpy=("net_pnl_jpy", "sum"),
        fee_jpy=("fee_jpy", "sum"),
        win_rate=("win", lambda x: round(float(x.mean() * 100), 1)),
        avg_pnl=("net_pnl_jpy", "mean"),
        pf=("net_pnl_jpy", _pf),
        final_cash=("cash_after", "last"),
    )
    g[["net_jpy", "fee_jpy", "avg_pnl", "final_cash"]] = (
        g[["net_jpy", "fee_jpy", "avg_pnl", "final_cash"]].round(0).astype(int)
    )
    g["pf"] = g["pf"].replace(float("inf"), 9999).round(2)
    print(g.sort_values(["pf", "net_jpy"], ascending=[False, False]).to_string(index=False))


def print_max_drawdown(equity: pd.DataFrame) -> None:
    if equity.empty:
        return

    rows = []
    for (strategy, tf), g in equity.groupby(["strategy", "timeframe"], observed=True):
        eq = g.sort_values("dt")["equity"].astype(float)
        peak = eq.cummax()
        dd = peak - eq
        dd_pct = float((dd / peak.replace(0, float("nan"))).max() * 100)
        rows.append(
            {
                "strategy": strategy,
                "timeframe": tf,
                "max_dd_jpy": int(round(dd.max())),
                "max_dd_pct": round(dd_pct, 2) if not math.isnan(dd_pct) else 0.0,
            }
        )

    print("\n=== Max Drawdown ===")
    print(pd.DataFrame(rows).sort_values("max_dd_pct", ascending=False).to_string(index=False))


def print_monthly_pivot(trades: pd.DataFrame, label: str) -> None:
    if trades.empty:
        print(f"\n(no data for {label})")
        return

    t = trades.copy()
    # tz_convert 後に tz_localize(None) してから to_period で警告を回避
    t["month"] = (
        pd.to_datetime(t["exit_dt"], utc=True)
        .dt.tz_convert(TZ_DISPLAY)
        .dt.tz_localize(None)
        .dt.to_period("M")
        .astype(str)
    )

    months = sorted(t["month"].unique())
    if SHOW_LAST_N_MONTHS:
        months = months[-SHOW_LAST_N_MONTHS:]
        t = t[t["month"].isin(months)]

    def pivot(val: str, agg: str = "sum") -> pd.DataFrame:
        return (
            t.pivot_table(index="month", columns="timeframe", values=val, aggfunc=agg)
            .fillna(0)
            .astype(int)
        )

    print(f"\n=== [{label}] Monthly Net PnL (JPY) ===")
    print(pivot("net_pnl_jpy").to_string())
    print(f"\n=== [{label}] Monthly Fees (JPY) ===")
    print(pivot("fee_jpy").to_string())
    print(f"\n=== [{label}] Monthly Trades ===")
    print(pivot("net_pnl_jpy", agg="count").to_string())
