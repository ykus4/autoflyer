"""OHLCV データ取得（CSV 保存）。

- fetch_gmo:     GMO Coin から 1 分足を取得
- fetch_binance: Binance から日足を取得（長期バックテスト用）
- update:        既存 CSV を今日まで差分更新
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

_GMO_BASE = "https://api.coin.z.com/public"
_BINANCE_BASE = "https://api.binance.com/api/v3/klines"


def _to_ms(day: str) -> int:
    """ISO 日付を UTC のエポックミリ秒に変換する。"""
    return int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)


def _finalize(df: pd.DataFrame, out: Path) -> pd.DataFrame:
    """timestamp/dt 列を付与し、重複排除・ソートして CSV に保存する。"""
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
    return result


def fetch_gmo(start: str, end: str, output: str, overwrite: bool, sleep: float) -> None:
    """GMO Coin から 1 分足 OHLCV を取得して CSV に保存する。"""
    out = Path(output)
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)

    def fetch_day(day: date) -> list[dict]:
        r = requests.get(
            f"{_GMO_BASE}/v1/klines",
            params={"symbol": "BTC", "interval": "1min", "date": day.strftime("%Y%m%d")},
            timeout=30,
        )
        r.raise_for_status()
        js = r.json()
        if js.get("status") != 0:
            raise RuntimeError(f"API error: {js}")
        return js.get("data", [])

    rows: list[dict] = []
    d, n = start_d, 0
    while d <= end_d:
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
        time.sleep(sleep)

    new_df = pd.DataFrame(rows)
    if out.exists() and not overwrite:
        new_df = pd.concat([pd.read_csv(out), new_df], ignore_index=True)
    _finalize(new_df, out)


def _fetch_binance_klines(
    symbol: str,
    start_ms: int,
    end_ms: int,
    sleep: float,
    progress: bool = False,
) -> list[dict]:
    """[start_ms, end_ms) の日足を 1000 本ずつページングして取得する。"""
    rows: list[dict] = []
    cur_ms = start_ms
    while cur_ms < end_ms:
        params: dict[str, str | int] = {
            "symbol": symbol,
            "interval": "1d",
            "startTime": cur_ms,
            "limit": 1000,
        }
        r = requests.get(_BINANCE_BASE, params=params, timeout=30)
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
        if progress:
            print(f"  fetched {len(rows)} bars ...")
        time.sleep(sleep)
    return rows


def fetch_binance(start: str, end: str, symbol: str, output: str, sleep: float) -> None:
    """Binance から日足 OHLCV を取得して CSV に保存する。"""
    rows = _fetch_binance_klines(
        symbol,
        _to_ms(start),
        _to_ms(end),
        sleep,
        progress=True,
    )
    _finalize(pd.DataFrame(rows), Path(output))


def update(output: str, symbol: str, sleep: float = 0.3) -> None:
    """CSV の最終日から今日まで Binance 日足を差分取得して追記する。"""
    out = Path(output)
    if not out.exists():
        print(f"{out} が見つかりません。先に fetch-binance を実行してください。")
        return

    existing = pd.read_csv(out)
    last_ts_ms = int(existing["timestamp_ms"].max())
    last_dt = pd.to_datetime(last_ts_ms, unit="ms", utc=True)
    today_ms = _to_ms(date.today().isoformat())

    if last_ts_ms >= today_ms:
        print(f"すでに最新です ({last_dt.date()})")
        return

    print(f"差分取得: {last_dt.date()} の翌日 → 今日")
    rows = _fetch_binance_klines(symbol, last_ts_ms + 1, today_ms, sleep)
    if not rows:
        print("新しいデータはありませんでした。")
        return

    combined = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True)
    result = _finalize(combined, out)
    print(f"+{len(rows)} 行追加  合計 {len(result):,} 行")
