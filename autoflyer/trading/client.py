"""bitFlyer Lightning REST client.

Public market data for FX_BTC_JPY is read from bitFlyer; OHLCV history comes
from CoinGecko because bitFlyer exposes no candle endpoint.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

import pandas as pd
import requests

from ..timeframes import to_minutes, to_pandas_rule

log = logging.getLogger("autoflyer.bot")

_BF_BASE = "https://api.bitflyer.com"
_COINGECKO_OHLC = "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc"
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0  # seconds; doubles each attempt
_OHLCV_CACHE_TTL = 300.0  # seconds

T = TypeVar("T")


def retry_request(func: Callable[[], T]) -> T:
    """Call `func`, retrying network failures with exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return func()
        except requests.RequestException as e:
            last_exc = e
            if attempt < _MAX_RETRIES - 1:
                wait = _RETRY_BACKOFF * (2**attempt)
                log.warning(
                    "Request failed (attempt %d/%d): %s — retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    e,
                    wait,
                )
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def coingecko_days(tf: str, limit: int) -> int:
    """CoinGecko が受け付ける `days` 値のうち、limit 本を賄える最小のものを返す。"""
    days = max(1, (to_minutes(tf) * limit) // 1440 + 1)
    for d in (1, 7, 14, 30, 90, 180, 365):
        if d >= days:
            return d
    return 365


class BitFlyerClient:
    """BitFlyer Lightning REST APIの薄いラッパー。"""

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._key = api_key
        self._secret = api_secret
        self._session = requests.Session()
        self._ohlcv_cache: dict[str, tuple[float, pd.DataFrame]] = {}

    @property
    def has_credentials(self) -> bool:
        """API キーが設定されているか（プライベート API を呼べるか）。"""
        return bool(self._key and self._secret)

    # ---- Public endpoints ----

    def fetch_ticker(self, product_code: str) -> dict:
        def _do() -> dict:
            resp = self._session.get(
                f"{_BF_BASE}/v1/ticker",
                params={"product_code": product_code},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()

        return retry_request(_do)

    def fetch_ohlcv(self, product_code: str, tf: str, limit: int = 300) -> pd.DataFrame:
        """CoinGecko APIで日足/時間足OHLCVを取得する（キャッシュ付き）。"""
        cache_key = f"{product_code}:{tf}:{limit}"
        cached = self._ohlcv_cache.get(cache_key)
        if cached and (time.time() - cached[0]) < _OHLCV_CACHE_TTL:
            return cached[1].copy()

        def _do() -> list:
            params: dict[str, str | int] = {
                "vs_currency": "jpy",
                "days": coingecko_days(tf, limit),
            }
            resp = self._session.get(_COINGECKO_OHLC, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()

        df = pd.DataFrame(retry_request(_do), columns=["ts", "open", "high", "low", "close"])
        df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df["volume"] = 0.0
        df = df[["dt", "open", "high", "low", "close", "volume"]]

        # CoinGecko は日足以外を返さないため、必要な時間足へリサンプリングする
        if tf.upper().strip() not in ("1D", "D"):
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

        result = df.tail(limit).reset_index(drop=True)
        self._ohlcv_cache[cache_key] = (time.time(), result)
        return result.copy()

    # ---- Private endpoints ----

    def _auth_headers(self, method: str, path: str, body: str = "") -> dict:
        ts = str(int(datetime.now(timezone.utc).timestamp()))
        sign = hmac.new(
            self._secret.encode(), (ts + method + path + body).encode(), hashlib.sha256
        ).hexdigest()
        return {
            "ACCESS-KEY": self._key,
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-SIGN": sign,
            "Content-Type": "application/json",
        }

    def fetch_balance(self) -> dict[str, dict[str, float]]:
        """通貨コード -> {"free": 利用可能額, "total": 総額}。"""

        def _do() -> list[dict[str, Any]]:
            path = "/v1/me/getbalance"
            resp = self._session.get(
                f"{_BF_BASE}{path}", headers=self._auth_headers("GET", path), timeout=10
            )
            resp.raise_for_status()
            return resp.json()

        return {
            item["currency_code"]: {"free": item["available"], "total": item["amount"]}
            for item in retry_request(_do)
        }

    def create_order(self, product_code: str, side: str, size: float) -> dict:
        def _do() -> dict:
            path = "/v1/me/sendchildorder"
            body = json.dumps(
                {
                    "product_code": product_code,
                    "child_order_type": "MARKET",
                    "side": side.upper(),
                    "size": size,
                }
            )
            resp = self._session.post(
                f"{_BF_BASE}{path}",
                headers=self._auth_headers("POST", path, body),
                data=body,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()

        return retry_request(_do)
