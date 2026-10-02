"""bitFlyer Lightning REST client.

Ticker, account and order endpoints are bitFlyer's; OHLCV history comes from
Binance (see `market_data`) because bitFlyer exposes no candle endpoint.
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
from urllib.parse import urlencode

import pandas as pd
import requests

from .market_data import recent_ohlcv

log = logging.getLogger("autoflyer.client")

_BF_BASE = "https://api.bitflyer.com"
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0  # seconds; doubles each attempt
_OHLCV_CACHE_TTL = 300.0  # seconds
_STOP_ORDER_EXPIRE_MIN = (
    525_600  # 逆指値の有効期限の上限（1 年）。既定の 30 日では長期保有中に失効する
)

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


def free_amount(balance: dict[str, dict[str, float]], currency: str) -> float:
    """`fetch_balance()` の結果から利用可能額を取り出す。通貨がなければ 0。"""
    return float(balance.get(currency, {}).get("free", 0))


def total_amount(balance: dict[str, dict[str, float]], currency: str) -> float:
    """`fetch_balance()` の結果から総額（注文で拘束中の分も含む）を取り出す。"""
    return float(balance.get(currency, {}).get("total", 0))


def net_position(positions: list[dict[str, Any]]) -> tuple[str | None, float, float]:
    """建玉一覧を (side, 合計数量, 平均建値) に集約する。side は "long"/"short"/None。

    bitFlyer は建玉を約定ごとに分けて返すため、同方向の建玉を数量加重で平均する。
    """
    signed = sum(float(p["size"]) * (1 if p["side"] == "BUY" else -1) for p in positions)
    if abs(signed) < 1e-9:
        return None, 0.0, 0.0
    side = "BUY" if signed > 0 else "SELL"
    same = [p for p in positions if p["side"] == side]
    total = sum(float(p["size"]) for p in same)
    avg = sum(float(p["price"]) * float(p["size"]) for p in same) / total
    return ("long" if signed > 0 else "short"), abs(signed), avg


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

    def fetch_ohlcv(self, product_code: str, tf: str, limit: int = 1000) -> pd.DataFrame:
        """直近 `limit` 本の OHLCV（Binance BTCJPY、キャッシュ付き）。

        bitFlyer にはローソク足 API がないため、バックテストと同じ Binance の足を使う。
        `product_code` はキャッシュの区別にだけ使う。最後の 1 本は形成中の足。
        """
        cache_key = f"{product_code}:{tf}:{limit}"
        cached = self._ohlcv_cache.get(cache_key)
        if cached and (time.time() - cached[0]) < _OHLCV_CACHE_TTL:
            return cached[1].copy()

        result = retry_request(lambda: recent_ohlcv(tf, limit, session=self._session))
        self._ohlcv_cache[cache_key] = (time.time(), result)
        return result.copy()

    # ---- Private endpoints ----

    def _auth_headers(self, method: str, path: str, body: str = "") -> dict:
        """`path` はクエリ文字列込みで署名する（bitFlyer の仕様）。"""
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

    def _private_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        full_path = f"{path}?{urlencode(params)}" if params else path

        def _do() -> Any:
            resp = self._session.get(
                f"{_BF_BASE}{full_path}",
                headers=self._auth_headers("GET", full_path),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()

        return retry_request(_do)

    def _private_post(self, path: str, payload: dict[str, Any]) -> Any:
        """POST は冪等でない（注文が二重に出うる）ため自動リトライしない。"""
        body = json.dumps(payload)
        resp = self._session.post(
            f"{_BF_BASE}{path}",
            headers=self._auth_headers("POST", path, body),
            data=body,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def fetch_balance(self) -> dict[str, dict[str, float]]:
        """現物口座の残高。通貨コード -> {"free": 利用可能額, "total": 総額}。"""
        return {
            item["currency_code"]: {"free": item["available"], "total": item["amount"]}
            for item in self._private_get("/v1/me/getbalance")
        }

    def fetch_collateral(self) -> dict[str, float]:
        """証拠金口座（Crypto CFD / FX）の状態。

        主なキー: collateral（預入証拠金）, open_position_pnl（評価損益）,
        require_collateral（必要証拠金）, keep_rate（証拠金維持率）。
        """
        return self._private_get("/v1/me/getcollateral")

    def fetch_positions(self, product_code: str) -> list[dict[str, Any]]:
        """建玉一覧（Crypto CFD / FX のみ）。各要素は side/price/size などを持つ。"""
        return self._private_get("/v1/me/getpositions", {"product_code": product_code})

    def fetch_child_order(self, product_code: str, acceptance_id: str) -> dict[str, Any] | None:
        """受付 ID から注文の約定状況を取得する。まだ反映されていなければ None。"""
        orders = self._private_get(
            "/v1/me/getchildorders",
            {"product_code": product_code, "child_order_acceptance_id": acceptance_id},
        )
        return orders[0] if orders else None

    def fetch_active_parent_orders(self, product_code: str) -> list[dict[str, Any]]:
        """有効な特殊注文（逆指値など）の一覧。"""
        return self._private_get(
            "/v1/me/getparentorders",
            {"product_code": product_code, "parent_order_state": "ACTIVE"},
        )

    def fetch_parent_order_status(
        self, product_code: str, acceptance_id: str
    ) -> dict[str, Any] | None:
        """特殊注文の状態（parent_order_state / executed_size など）。見つからなければ None。

        getparentorders は受付 ID で絞り込めないため、直近の注文から探す。
        """
        orders = self._private_get(
            "/v1/me/getparentorders", {"product_code": product_code, "count": 100}
        )
        return next(
            (o for o in orders if o.get("parent_order_acceptance_id") == acceptance_id), None
        )

    def create_order(self, product_code: str, side: str, size: float) -> dict:
        """成行注文を出す。戻り値は {"child_order_acceptance_id": ...}。"""
        return self._private_post(
            "/v1/me/sendchildorder",
            {
                "product_code": product_code,
                "child_order_type": "MARKET",
                "side": side.upper(),
                "size": size,
            },
        )

    def create_stop_order(
        self, product_code: str, side: str, size: float, trigger_price: float
    ) -> str:
        """逆指値（STOP）の特殊注文を出し、parent_order_acceptance_id を返す。"""
        resp = self._private_post(
            "/v1/me/sendparentorder",
            {
                "order_method": "SIMPLE",
                "minute_to_expire": _STOP_ORDER_EXPIRE_MIN,
                "parameters": [
                    {
                        "product_code": product_code,
                        "condition_type": "STOP",
                        "side": side.upper(),
                        "size": size,
                        "trigger_price": int(round(trigger_price)),
                    }
                ],
            },
        )
        return str(resp["parent_order_acceptance_id"])

    def cancel_parent_order(self, product_code: str, acceptance_id: str) -> None:
        self._private_post(
            "/v1/me/cancelparentorder",
            {"product_code": product_code, "parent_order_acceptance_id": acceptance_id},
        )
