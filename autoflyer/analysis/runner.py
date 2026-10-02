"""Backtest orchestration: run every variant across every timeframe and report."""

from __future__ import annotations

import pandas as pd

from ..config import START_CASH_JPY, TIMEFRAMES
from ..trading.indicators import add_indicators
from ..trading.strategy import get_variant, select_variants
from . import backtest, data, optimize, report


def run_backtest(
    csv: str,
    timeframes: list[str] | None = None,
    variant_names: list[str] | None = None,
    train_end: str | None = None,
    out_trades: str | None = None,
    costs: str = "spot",
    slippage_pct: float = 0.0,
) -> None:
    tfs = timeframes or TIMEFRAMES
    variants = select_variants(variant_names)
    split = pd.Timestamp(train_end, tz="UTC") if train_end else None

    print(f"Costs: {costs}  slippage: {slippage_pct:.4%}")
    if split is not None:
        print(f"Walk-forward: train <= {split.date()}  |  test > {split.date()}")

    df_1m = data.load_csv(csv)
    print(f"Loaded {len(df_1m):,} rows  {df_1m['dt'].iloc[0]} -> {df_1m['dt'].iloc[-1]}")

    all_trades: list[pd.DataFrame] = []
    all_equity: list[pd.DataFrame] = []

    for tf in tfs:
        bars = data.resample(df_1m, tf)
        print(f"\n{'=' * 40}\n Timeframe: {tf}  ({len(bars)} bars)\n{'=' * 40}")
        # 指標は時間足ごとに 1 度だけ計算して全バリアントで共有する
        bars_with_ind = add_indicators(bars)

        for v in variants:
            trades, equity = backtest.run(
                bars,
                start_cash=START_CASH_JPY,
                tf_label=tf,
                variant=v,
                train_end=split,
                bars_with_ind=bars_with_ind,
                costs=costs,
                slippage_pct=slippage_pct,
            )
            if trades.empty:
                print(f"  [{v.name}/{tf}]  no trades")
                continue
            print(f"  {_summary_line(trades, v.name, tf)}")
            all_trades.append(trades)
            all_equity.append(equity)

    if not all_trades:
        print("No trades.")
        return

    trades_all = pd.concat(all_trades, ignore_index=True)
    equity_all = pd.concat(all_equity, ignore_index=True)

    report.print_overall_summary(trades_all)
    report.print_max_drawdown(equity_all)
    report.print_performance(equity_all)

    base = trades_all[trades_all["strategy"].str.contains("BASE/", regex=False)]
    report.print_monthly_pivot(base, label="BASE (all TF)")

    if out_trades:
        trades_all.to_csv(out_trades, index=False)
        print(f"Trades saved: {out_trades}")


def _summary_line(trades: pd.DataFrame, variant_name: str, tf: str) -> str:
    stops = int((trades["exit_reason"] == "stop").sum())
    return (
        f"[{variant_name}/{tf}]"
        f"  n={len(trades)}"
        f"  wr={trades['win'].mean() * 100:.1f}%"
        f"  net={int(trades['net_pnl_jpy'].sum()):,}"
        f"  fees={int(trades['fee_jpy'].sum()):,}"
        f"  final={int(trades['cash_after'].iloc[-1]):,}"
        f"  stops={stops}"
    )


def run_grid(
    csv: str,
    timeframe: str,
    base_name: str,
    params: list[str],
    *,
    metric: str = "calmar",
    costs: str = "spot",
    slippage_pct: float = 0.0,
    top: int = 20,
    out: str | None = None,
) -> None:
    base = get_variant(base_name)
    variants = optimize.expand(base, optimize.parse_grid(base, params))
    print(f"Grid: {base.name} × {len(variants)} combinations on {timeframe}  (rank by {metric})")
    ranked = optimize.grid(
        optimize.Dataset.load(csv, timeframe),
        variants,
        metric=metric,
        costs=costs,
        slippage_pct=slippage_pct,
    )
    print(ranked.head(top).round(2).to_string(index=False))
    if out:
        ranked.to_csv(out, index=False)
        print(f"Saved: {out}")


def run_walk_forward(
    csv: str,
    timeframe: str,
    base_name: str,
    params: list[str],
    *,
    train_days: int,
    test_days: int,
    metric: str = "calmar",
    costs: str = "spot",
    slippage_pct: float = 0.0,
    out: str | None = None,
) -> None:
    base = get_variant(base_name)
    variants = optimize.expand(base, optimize.parse_grid(base, params))
    print(
        f"Walk-forward: {base.name} × {len(variants)} combinations on {timeframe}  "
        f"train={train_days}d test={test_days}d  (select by {metric})"
    )
    wf = optimize.walk_forward(
        optimize.Dataset.load(csv, timeframe),
        variants,
        train_days=train_days,
        test_days=test_days,
        metric=metric,
        costs=costs,
        slippage_pct=slippage_pct,
    )
    if wf.empty:
        print(
            "データが短すぎてウィンドウを作れません。--train-days / --test-days を短くしてください。"
        )
        return
    print(wf.round(2).to_string(index=False))
    print("\n=== Out-of-sample summary ===")
    for k, val in optimize.summarize_walk_forward(wf).items():
        print(f"  {k}: {val:.2f}" if isinstance(val, float) else f"  {k}: {val}")
    if out:
        wf.to_csv(out, index=False)
        print(f"Saved: {out}")
