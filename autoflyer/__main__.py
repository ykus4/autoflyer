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
    import time
    from datetime import date, timedelta
    from pathlib import Path

    import pandas as pd
    import requests

    BASE = "https://api.coin.z.com/public"
    out = Path(args.output)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    def fetch_day(day: date) -> list[dict]:
        r = requests.get(
            f"{BASE}/v1/klines",
            params={"symbol": "BTC", "interval": "1min", "date": day.strftime("%Y%m%d")},
            timeout=30,
        )
        r.raise_for_status()
        js = r.json()
        if js.get("status") != 0:
            raise RuntimeError(f"API error: {js}")
        return js.get("data", [])

    rows: list[dict] = []
    d, n = start, 0
    while d <= end:
        for k in fetch_day(d):
            rows.append(
                {
                    "timestamp_ms": int(k["openTime"]),
                    "open": float(k["open"]),
                    "high": float(k["high"]),
                    "low": float(k["low"]),
                    "close": float(k["close"]),
                    "volume": float(k["volume"]),
                }
            )
        n += 1
        if n % 10 == 0:
            print(f"  {n} days  rows={len(rows):,}  ({d})")
        d += timedelta(days=1)
        time.sleep(args.sleep)

    new_df = pd.DataFrame(rows)
    new_df["timestamp"] = (new_df["timestamp_ms"] // 1000).astype("int64")
    new_df["dt"] = pd.to_datetime(new_df["timestamp"], unit="s", utc=True)

    if out.exists() and not args.overwrite:
        new_df = pd.concat([pd.read_csv(out), new_df], ignore_index=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    result = (
        new_df.drop_duplicates(subset=["timestamp_ms"])
        .sort_values("timestamp_ms")
        .reset_index(drop=True)
    )
    result.to_csv(out, index=False)
    print(f"Saved {out}  rows={len(result):,}")
    print(f"range: {result['dt'].iloc[0]}  ->  {result['dt'].iloc[-1]}")


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
    """Binance BTC/USDT 日足を CSV に保存（バックテスト用）。"""
    import time
    from pathlib import Path

    import pandas as pd
    import requests

    BASE = "https://api.binance.com/api/v3/klines"
    out = Path(args.output)
    start_ms = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(args.end, tz="UTC").timestamp() * 1000)

    rows: list[dict] = []
    cur_ms = start_ms
    while cur_ms < end_ms:
        r = requests.get(
            BASE,
            params={"symbol": args.symbol, "interval": "1d", "startTime": cur_ms, "limit": 1000},
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for k in batch:
            if int(k[0]) >= end_ms:
                break
            rows.append(
                {
                    "timestamp_ms": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                }
            )
        cur_ms = int(batch[-1][0]) + 1
        print(f"  fetched {len(rows)} bars ...")
        time.sleep(args.sleep)

    df = pd.DataFrame(rows)
    df["timestamp"] = (df["timestamp_ms"] // 1000).astype("int64")
    df["dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    result = (
        df.drop_duplicates(subset=["timestamp_ms"])
        .sort_values("timestamp_ms")
        .reset_index(drop=True)
    )
    result.to_csv(out, index=False)
    print(f"Saved {out}  rows={len(result):,}")
    print(f"range: {result['dt'].iloc[0]}  ->  {result['dt'].iloc[-1]}")


def _cmd_update(args: argparse.Namespace) -> None:
    """CSVの最終日から今日まで差分取得して追記する。"""
    import time
    from datetime import date
    from pathlib import Path

    import pandas as pd
    import requests

    BASE = "https://api.binance.com/api/v3/klines"
    out = Path(args.output)

    if not out.exists():
        print(f"{out} が見つかりません。先に fetch-binance を実行してください。")
        return

    existing = pd.read_csv(out)
    last_ts_ms = int(existing["timestamp_ms"].max())
    last_dt = pd.to_datetime(last_ts_ms, unit="ms", utc=True)
    today_ms = int(pd.Timestamp(date.today().isoformat(), tz="UTC").timestamp() * 1000)

    if last_ts_ms >= today_ms:
        print(f"すでに最新です ({last_dt.date()})")
        return

    start_ms = last_ts_ms + 1
    print(f"差分取得: {last_dt.date()} の翌日 → 今日")

    rows: list[dict] = []
    cur_ms = start_ms
    while cur_ms < today_ms:
        r = requests.get(
            BASE,
            params={"symbol": args.symbol, "interval": "1d", "startTime": cur_ms, "limit": 1000},
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for k in batch:
            if int(k[0]) >= today_ms:
                break
            rows.append(
                {
                    "timestamp_ms": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                }
            )
        cur_ms = int(batch[-1][0]) + 1
        time.sleep(0.3)

    if not rows:
        print("新しいデータはありませんでした。")
        return

    new_df = pd.DataFrame(rows)
    new_df["timestamp"] = (new_df["timestamp_ms"] // 1000).astype("int64")
    new_df["dt"] = pd.to_datetime(new_df["timestamp"], unit="s", utc=True)

    combined = (
        pd.concat([existing, new_df], ignore_index=True)
        .drop_duplicates(subset=["timestamp_ms"])
        .sort_values("timestamp_ms")
        .reset_index(drop=True)
    )
    combined.to_csv(out, index=False)
    print(f"+{len(rows)} 行追加  合計 {len(combined):,} 行")
    print(f"range: {combined['dt'].iloc[0]}  ->  {combined['dt'].iloc[-1]}")


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
        f"{'Name':<26} {'ma200':^5} {'adx_min':^7} {'stop':^5} {'risk%':^6} {'short':^5} {'chan':^5}"
    )
    print("-" * 62)
    for v in VARIANTS:
        print(
            f"{v.name:<26}"
            f" {'✓' if v.use_ma200_filter else ' ':^5}"
            f" {str(v.adx_min) if v.adx_min else '-':^7}"
            f" {str(v.atr_stop_mult) if v.atr_stop_mult else '-':^5}"
            f" {str(v.risk_pct) if v.risk_pct else '-':^6}"
            f" {'✓' if v.enable_short else ' ':^5}"
            f" {str(v.chandelier_mult) if v.chandelier_mult else '-':^5}"
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
