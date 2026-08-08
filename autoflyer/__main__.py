"""
CLI entry point.

Commands
--------
  fetch          GMO Coin から 1 分足 OHLCV を取得して CSV に保存
  fetch-binance  Binance から日足 OHLCV を取得（長期バックテスト用）
  update         既存 CSV を今日まで差分更新
  backtest       CSV データを使ってバックテストを実行
  bot            BitFlyer FX ライブ取引ボットを起動
  dashboard      ボット監視ダッシュボードを起動
  variants       利用可能なバリアント一覧を表示

各サブコマンドは重い依存（pandas / uvicorn など）を関数内で import して
起動時間を短く保つ。
"""

from __future__ import annotations

import argparse

DEFAULT_STATE = "var/state.json"


def _cmd_fetch(args: argparse.Namespace) -> None:
    from .analysis.fetch import fetch_gmo

    fetch_gmo(args.start, args.end, args.output, args.overwrite, args.sleep)


def _cmd_fetch_binance(args: argparse.Namespace) -> None:
    from .analysis.fetch import fetch_binance

    fetch_binance(args.start, args.end, args.symbol, args.output, args.sleep)


def _cmd_update(args: argparse.Namespace) -> None:
    from .analysis.fetch import update

    update(args.output, args.symbol)


def _cmd_backtest(args: argparse.Namespace) -> None:
    from .analysis.runner import run_backtest

    run_backtest(
        csv=args.csv,
        timeframes=args.timeframe,
        variant_names=args.variant,
        train_end=args.train_end,
        out_trades=args.out_trades,
    )


def _cmd_bot(args: argparse.Namespace) -> None:
    from .trading.bot import run

    run(args)


def _cmd_dashboard(args: argparse.Namespace) -> None:
    import os
    from pathlib import Path

    import uvicorn
    from dotenv import load_dotenv

    from .dashboard import DashboardSettings, app, configure

    load_dotenv()
    user = os.environ.get("DASHBOARD_USER", "")
    configure(
        DashboardSettings(
            state_file=Path(args.state),
            log_file=Path(args.log_file) if args.log_file else None,
            symbol=args.symbol,
            api_key=os.environ.get("BITFLYER_API_KEY", ""),
            api_secret=os.environ.get("BITFLYER_API_SECRET", ""),
            user=user,
            password=os.environ.get("DASHBOARD_PASS", ""),
        )
    )
    # 認証情報があるときだけ外部公開する
    host = "0.0.0.0" if user else "127.0.0.1"
    print(f"Dashboard: http://{host}:{args.port}")
    if not user:
        print(f"SSHトンネル: ssh -L {args.port}:localhost:{args.port} <user>@<server>")
    uvicorn.run(app, host=host, port=args.port, log_level="warning")


def _cmd_variants(_args: argparse.Namespace) -> None:
    from .trading.strategy import format_variants_table

    print(format_variants_table())


_COMMANDS = {
    "fetch": _cmd_fetch,
    "fetch-binance": _cmd_fetch_binance,
    "update": _cmd_update,
    "backtest": _cmd_backtest,
    "bot": _cmd_bot,
    "variants": _cmd_variants,
    "dashboard": _cmd_dashboard,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autoflyer", description="BTC FX trading toolkit")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

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

    p = sub.add_parser("update", help="データCSVを今日まで差分更新")
    p.add_argument("--output", default="data/btc_usdt_1d.csv")
    p.add_argument("--symbol", default="BTCUSDT")

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
    p.add_argument("--state", default=DEFAULT_STATE)
    p.add_argument("--log-file", metavar="PATH")
    p.add_argument("--max-dd-pct", type=float, default=20.0, metavar="PCT")
    p.add_argument("--use-mtf", action="store_true")

    p = sub.add_parser("dashboard", help="ボット監視ダッシュボードを起動")
    p.add_argument("--state", default=DEFAULT_STATE)
    p.add_argument("--log-file", metavar="PATH")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--symbol", default="FX_BTC_JPY")

    sub.add_parser("variants", help="利用可能なバリアント一覧を表示")

    return parser


def main() -> None:
    args = _build_parser().parse_args()
    _COMMANDS[args.command](args)


if __name__ == "__main__":
    main()
