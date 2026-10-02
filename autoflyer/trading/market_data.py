"""OHLCV candles from Binance's public kline API.

bitFlyer has no official candle endpoint, so both the live bot and the backtest
data (`fetch-binance` / `update`) use Binance klines. Using the *same* source in
both places keeps live signals consistent with backtest results; `BTCJPY` gives
a JPY-denominated series whose scale matches bitFlyer prices.
"""

from __future__ import annotations

import time

import pandas as pd
import requests

from ..timeframes import parse, to_minutes, to_pandas_rule

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
LIVE_SYMBOL = "BTCJPY"
_MAX_PER_REQUEST = 1000

# Binance がネイティブに提供する足（これ以外は 1h 足からリサンプリングする）
_NATIVE_INTERVALS = {("H", n): f"{n}h" for n in (1, 2, 4, 6, 8, 12)} | {
    ("D", 1): "1d",
    ("D", 3): "3d",
}

OHLCV_COLUMNS = ["dt", "open", "high", "low", "close", "volume"]


def binance_interval(tf: str) -> str | None:
    """時間足ラベルを Binance の interval に変換する。ネイティブにない足は None。"""
    count, unit = parse(tf)
    return _NATIVE_INTERVALS.get((unit, count))


def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    sleep: float = 0.0,
    session: requests.Session | None = None,
    progress: bool = False,
) -> list[dict]:
    """[start_ms, end_ms) のローソク足を 1000 本ずつページングして取得する。"""
    http = session or requests
    rows: list[dict] = []
    cur_ms = start_ms
    while cur_ms < end_ms:
        params: dict[str, str | int] = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cur_ms,
            "limit": _MAX_PER_REQUEST,
        }
        r = http.get(BINANCE_KLINES, params=params, timeout=30)
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
        if len(batch) < _MAX_PER_REQUEST:
            break
        time.sleep(sleep)
    return rows


def recent_ohlcv(
    tf: str,
    limit: int,
    *,
    symbol: str = LIVE_SYMBOL,
    session: requests.Session | None = None,
    now_ms: int | None = None,
) -> pd.DataFrame:
    """直近 `limit` 本の OHLCV（最後の 1 本は形成中の足）。"""
    native = binance_interval(tf)
    interval = native or "1h"
    tf_ms = to_minutes(tf) * 60_000
    end_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    # 足の境界に揃えたうえで 1 本余分に取る
    start_ms = (end_ms // tf_ms - limit) * tf_ms
    rows = fetch_klines(symbol, interval, start_ms, end_ms + 1, session=session)

    df = pd.DataFrame(rows, columns=["timestamp_ms", "open", "high", "low", "close", "volume"])
    df["dt"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    df = df[OHLCV_COLUMNS]
    if native is None:
        df = (
            df.set_index("dt")
            .resample(to_pandas_rule(tf))
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            )
            .dropna()
            .reset_index()
        )
    return df.tail(limit).reset_index(drop=True)


def rescale(bars: pd.DataFrame, ref_price: float) -> tuple[pd.DataFrame, float]:
    """最新値が `ref_price` になるよう OHLC を一律に拡大縮小する。

    Binance BTCJPY と bitFlyer の価格差（数 % 以内）を吸収し、Supertrend などの
    絶対価格のストップを bitFlyer の価格水準に合わせる。比率は指標の比較結果を変えない。
    """
    last = float(bars["close"].iloc[-1])
    if last <= 0 or ref_price <= 0:
        return bars, 1.0
    ratio = ref_price / last
    out = bars.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = out[col] * ratio
    return out, ratio
