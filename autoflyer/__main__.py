"""
CLI entry point.

Commands
--------
  fetch      GMO Coin から 1 分足 OHLCV を取得して CSV に保存
  backtest   CSV データを使ってバックテストを実行
  bot        BitFlyer FX ライブ取引ボットを起動
  variants   利用可能なバリアント一覧を表示
"""

from __future__ import annotations

import argparse


def _cmd_fetch(args: argparse.Namespace) -> None:
    from .analysis.fetch import fetch_gmo

    fetch_gmo(args.start, args.end, args.output, args.overwrite, args.sleep)


def _cmd_backtest(args: argparse.Namespace) -> None:
    import pandas as pd

    from .analysis import backtest, data, report
    from .config import START_CASH_JPY, TIMEFRAMES
    from .trading.indicators import add_indicators
    from .trading.strategy import VARIANTS

    timeframes = args.timeframe or TIMEFRAMES
    variants = [v for v in VARIANTS if v.name in args.variant] if args.variant else VARIANTS
    train_end = pd.Timestamp(args.train_end, tz="UTC") if args.train_end else None

    if train_end is not None:
        print(f"Walk-forward: train <= {train_end.date()}  |  test > {train_end.date()}")

    df_1m = data.load_csv(args.csv)
    print(f"Loaded {len(df_1m):,} rows  {df_1m['dt'].iloc[0]} -> {df_1m['dt'].iloc[-1]}")

    all_trades: list[pd.DataFrame] = []
    all_equity: list[pd.DataFrame] = []

    for tf in timeframes:
        bars = data.resample(df_1m, tf)
        print(f"\n{'=' * 40}\n Timeframe: {tf}  ({len(bars)} bars)\n{'=' * 40}")
        bars_with_ind = add_indicators(bars)

        for v in variants:
            trades, equity = backtest.run(
                bars,
                start_cash=START_CASH_JPY,
                tf_label=tf,
                variant=v,
                train_end=train_end,
                bars_with_ind=bars_with_ind,
            )
            if trades.empty:
                print(f"  [{v.name}/{tf}]  no trades")
                continue

            stops = int((trades["exit_reason"] == "stop").sum())
            print(
                f"  [{v.name}/{tf}]"
                f"  n={len(trades)}"
                f"  wr={trades['win'].mean() * 100:.1f}%"
                f"  net={int(trades['net_pnl_jpy'].sum()):,}"
                f"  fees={int(trades['fee_jpy'].sum()):,}"
                f"  final={int(trades['cash_after'].iloc[-1]):,}"
                f"  stops={stops}"
            )
            all_trades.append(trades)
            all_equity.append(equity)

    if not all_trades:
        print("No trades.")
        return

    trades_all = pd.concat(all_trades, ignore_index=True)
    equity_all = pd.concat(all_equity, ignore_index=True)

    report.print_overall_summary(trades_all)
    report.print_max_drawdown(equity_all)

    base = trades_all[trades_all["strategy"].str.contains("BASE/", regex=False)]
    report.print_monthly_pivot(base, label="BASE (all TF)")

    if args.out_trades:
        trades_all.to_csv(args.out_trades, index=False)
        print(f"Trades saved: {args.out_trades}")


def _cmd_fetch_binance(args: argparse.Namespace) -> None:
    from .analysis.fetch import fetch_binance

    fetch_binance(args.start, args.end, args.symbol, args.output, args.sleep)


def _cmd_update(args: argparse.Namespace) -> None:
    from .analysis.fetch import update

    update(args.output, args.symbol)


def _cmd_bot(args: argparse.Namespace) -> None:
    from .trading.bot import run

    run(args)


def _cmd_dashboard(args: argparse.Namespace) -> None:
    import os
    from pathlib import Path

    import uvicorn
    from dotenv import load_dotenv

    from .dashboard import app, set_paths

    load_dotenv()
    user = os.environ.get("DASHBOARD_USER", "")
    passwd = os.environ.get("DASHBOARD_PASS", "")
    set_paths(
        state=Path(args.state),
        log=Path(args.log_file) if args.log_file else None,
        symbol=args.symbol,
        api_key=os.environ.get("BITFLYER_API_KEY", ""),
        api_secret=os.environ.get("BITFLYER_API_SECRET", ""),
        dashboard_user=user,
        dashboard_pass=passwd,
    )
    host = "0.0.0.0" if user else "127.0.0.1"
    print(f"Dashboard: http://{host}:{args.port}")
    if not user:
        print(f"SSHトンネル: ssh -L {args.port}:localhost:{args.port} <user>@<server>")
    uvicorn.run(app, host=host, port=args.port, log_level="warning")


def _cmd_variants(_args: argparse.Namespace) -> None:
    from .trading.strategy import VARIANTS

    print(
        f"{'Name':<40} {'ma200':^5} {'stop':^5} {'tp':^4} {'trail':^5}"
        f" {'brk':^3} {'st':^4} {'garch':^5} {'risk%':^6}"
    )
    print("-" * 87)
    for v in VARIANTS:
        print(
            f"{v.name:<40}"
            f" {'✓' if v.use_ma200_filter else ' ':^5}"
            f" {str(v.atr_stop_mult) if v.atr_stop_mult else '-':^5}"
            f" {str(v.tp_atr_mult) if v.tp_atr_mult else '-':^4}"
            f" {str(v.tp_trail_mult) if v.tp_trail_mult else '-':^5}"
            f" {'✓' if v.breakout_entry else ' ':^3}"
            f" {str(v.supertrend_mult) if v.supertrend_mult else '-':^4}"
            f" {str(v.garch_target_vol) if v.garch_target_vol else '-':^5}"
            f" {str(v.risk_pct) if v.risk_pct else '-':^6}"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autoflyer", description="BTC FX trading toolkit")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    p = sub.add_parser("fetch", help="GMO Coin から 1 分足データを取得")
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--end", required=True)
    p.add_argument("--output", default="data/btc_jpy_1m.csv")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--sleep", type=float, default=0.12)

    p = sub.add_parser("fetch-binance", help="Binance から日足データを取得（長期バックテスト用）")
    p.add_argument("--start", default="2017-08-17")
    p.add_argument("--end", required=True)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--output", default="data/btc_usdt_1d.csv")
    p.add_argument("--sleep", type=float, default=0.2)

    p = sub.add_parser("backtest", help="バックテストを実行")
    p.add_argument("--csv", default="data/btc_jpy_1m.csv")
    p.add_argument("--timeframe", nargs="+", metavar="TF")
    p.add_argument("--variant", nargs="+", metavar="NAME")
    p.add_argument("--train-end", metavar="DATE")
    p.add_argument("--out-trades", metavar="PATH")

    p = sub.add_parser("bot", help="ライブ取引ボットを起動")
    p.add_argument("--live", action="store_true")
    p.add_argument("--symbol", default="FX_BTC_JPY")
    p.add_argument("--timeframe", nargs=1, metavar="TF")
    p.add_argument("--variant", metavar="NAME")
    p.add_argument("--amount", type=float, default=0)
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--state", default="state.json")
    p.add_argument("--log-file", metavar="PATH")
    p.add_argument("--max-dd-pct", type=float, default=20.0, metavar="PCT")
    p.add_argument("--use-mtf", action="store_true")

    p = sub.add_parser("update", help="データCSVを今日まで差分更新")
    p.add_argument("--output", default="data/btc_usdt_1d.csv")
    p.add_argument("--symbol", default="BTCUSDT")

    sub.add_parser("variants", help="利用可能なバリアント一覧を表示")

    p = sub.add_parser("dashboard", help="ボット監視ダッシュボードを起動")
    p.add_argument("--state", default="state.json")
    p.add_argument("--log-file", metavar="PATH")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--symbol", default="FX_BTC_JPY")

    return parser


def main() -> None:
    args = _build_parser().parse_args()
    {
        "fetch": _cmd_fetch,
        "fetch-binance": _cmd_fetch_binance,
        "update": _cmd_update,
        "backtest": _cmd_backtest,
        "bot": _cmd_bot,
        "variants": _cmd_variants,
        "dashboard": _cmd_dashboard,
    }[args.command](args)


if __name__ == "__main__":
    main()
