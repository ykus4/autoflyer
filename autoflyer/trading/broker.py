"""Order execution and account queries for the live bot.

`LiveBot` talks only to a `Broker`, so the strategy flow is independent of how
orders are filled:

- `PaperBroker`  — dry run. Fills instantly at the reference price, never sends
  anything to the exchange.
- `LiveBroker`   — bitFlyer. Confirms fills via `getchildorders`, reads equity from
  the margin account for Crypto CFD (`FX_BTC_JPY`) or the spot balance otherwise,
  and manages exchange-side STOP orders.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import requests

from .client import BitFlyerClient, free_amount, net_position

log = logging.getLogger("autoflyer.broker")

_FILL_POLL_ATTEMPTS = 5
_FILL_POLL_INTERVAL_SEC = 1.0
_DONE_STATES = {"CANCELED", "EXPIRED", "REJECTED"}


@dataclass(frozen=True)
class Fill:
    size: float
    price: float
    confirmed: bool = True  # False = 約定を確認できず、依頼値で代用した


@dataclass(frozen=True)
class ExchangePosition:
    side: str | None  # "long" / "short" / None（ノーポジ）
    size: float
    avg_price: float


class Broker(Protocol):
    supports_exchange_stop: bool
    tracks_positions: bool  # position() で取引所の建玉を照会できるか

    def equity(self, cur_price: float, side: str | None, btc: float, entry: float) -> float: ...
    def available_jpy(self) -> float: ...
    def market(self, order_side: str, size: float, ref_price: float) -> Fill | None: ...
    def position(self) -> ExchangePosition | None: ...
    def stop_is_active(self, order_id: str) -> bool | None: ...
    def place_stop(self, order_side: str, size: float, trigger: float) -> str: ...
    def cancel_stop(self, order_id: str) -> None: ...


def unrealized_pnl(side: str | None, btc: float, entry: float, price: float) -> float:
    if side is None or btc <= 0:
        return 0.0
    return (price - entry) * btc if side == "long" else (entry - price) * btc


class PaperBroker:
    """ドライラン用。資金は固定額 `base_jpy` と仮定し、含み損益だけを反映する。"""

    supports_exchange_stop = False
    tracks_positions = False

    def __init__(self, base_jpy: float) -> None:
        self.base_jpy = base_jpy

    def equity(self, cur_price: float, side: str | None, btc: float, entry: float) -> float:
        return self.base_jpy + unrealized_pnl(side, btc, entry, cur_price)

    def available_jpy(self) -> float:
        return self.base_jpy

    def market(self, order_side: str, size: float, ref_price: float) -> Fill | None:
        log.info("[DRY_RUN] %s %.8f BTC @ ~%.0f", order_side.upper(), size, ref_price)
        return Fill(size, ref_price)

    def position(self) -> ExchangePosition | None:
        return None

    def stop_is_active(self, order_id: str) -> bool | None:
        return None

    def place_stop(self, order_side: str, size: float, trigger: float) -> str:
        raise NotImplementedError

    def cancel_stop(self, order_id: str) -> None:
        raise NotImplementedError


class LiveBroker:
    """bitFlyer 実取引。`product_code` が `FX_` で始まれば Crypto CFD として扱う。"""

    supports_exchange_stop = True

    def __init__(
        self,
        client: BitFlyerClient,
        product_code: str,
        fallback_jpy: float,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.product_code = product_code
        self.is_cfd = product_code.startswith("FX_")
        self.tracks_positions = self.is_cfd
        self._fallback = PaperBroker(fallback_jpy)
        self._sleep = sleep

    # ---- 口座 ----

    def equity(self, cur_price: float, side: str | None, btc: float, entry: float) -> float:
        try:
            if self.is_cfd:
                c = self.client.fetch_collateral()
                equity = float(c["collateral"]) + float(c.get("open_position_pnl", 0))
                log.info(
                    "証拠金: %.0f  評価損益: %.0f", c["collateral"], c.get("open_position_pnl", 0)
                )
                return equity
            bal = self.client.fetch_balance()
            jpy, btc_bal = free_amount(bal, "JPY"), free_amount(bal, "BTC")
            equity = jpy + btc_bal * cur_price
            log.info("残高: JPY=%.0f  BTC=%.6f  資産合計=%.0f", jpy, btc_bal, equity)
            return equity
        except (requests.RequestException, KeyError, ValueError, TypeError) as e:
            equity = self._fallback.equity(cur_price, side, btc, entry)
            log.warning("残高取得失敗 (%s) — 推定値で資産計算: %.0f JPY", e, equity)
            return equity

    def available_jpy(self) -> float:
        """新規建てに使える資金。CFD は証拠金から使用中の必要証拠金を引いた額。"""
        try:
            if self.is_cfd:
                c = self.client.fetch_collateral()
                return (
                    float(c["collateral"])
                    + float(c.get("open_position_pnl", 0))
                    - float(c.get("require_collateral", 0))
                )
            return free_amount(self.client.fetch_balance(), "JPY")
        except (requests.RequestException, KeyError, ValueError, TypeError) as e:
            jpy = self._fallback.available_jpy()
            log.warning("残高取得失敗 (%s) — フォールバック %.0f JPY を使用", e, jpy)
            return jpy

    def position(self) -> ExchangePosition | None:
        """取引所上の建玉。現物は建玉の概念がないため None。"""
        if not self.is_cfd:
            return None
        side, size, avg = net_position(self.client.fetch_positions(self.product_code))
        return ExchangePosition(side, size, avg)

    # ---- 注文 ----

    def market(self, order_side: str, size: float, ref_price: float) -> Fill | None:
        """成行注文を出し、約定を確認して返す。拒否されたら None。"""
        resp = self.client.create_order(self.product_code, order_side, size)
        acceptance_id = resp.get("child_order_acceptance_id")
        log.info(
            "Order accepted: %s %s %.8f BTC (%s)",
            order_side.upper(),
            self.product_code,
            size,
            acceptance_id,
        )
        if not acceptance_id:
            log.error("注文受付 ID が返らなかった: %s", resp)
            return None

        order = None
        for _ in range(_FILL_POLL_ATTEMPTS):
            self._sleep(_FILL_POLL_INTERVAL_SEC)
            try:
                order = self.client.fetch_child_order(self.product_code, acceptance_id)
            except requests.RequestException as e:
                log.warning("約定確認に失敗 (%s)", e)
                continue
            if order is None:
                continue
            state = order.get("child_order_state")
            executed = float(order.get("executed_size") or 0)
            if state == "COMPLETED" or (state in _DONE_STATES and executed > 0):
                fill = Fill(executed, float(order["average_price"]))
                log.info("Filled: %.8f BTC @ %.0f (%s)", fill.size, fill.price, state)
                return fill
            if state in _DONE_STATES:
                log.error("注文が約定しなかった: %s", order)
                return None

        # 確認できなかった。数量・価格は依頼値で代用し、CFD なら次サイクルの照合で補正される
        log.error("約定を確認できなかった (%s) — 依頼値で記録します: %s", acceptance_id, order)
        return Fill(size, ref_price, confirmed=False)

    def stop_is_active(self, order_id: str) -> bool | None:
        """逆指値がまだ有効か。取得に失敗したら None（不明）。"""
        try:
            active = self.client.fetch_active_parent_orders(self.product_code)
        except requests.RequestException as e:
            log.warning("逆指値の状態取得に失敗 (%s)", e)
            return None
        return any(o.get("parent_order_acceptance_id") == order_id for o in active)

    def place_stop(self, order_side: str, size: float, trigger: float) -> str:
        order_id = self.client.create_stop_order(self.product_code, order_side, size, trigger)
        log.info(
            "Exchange stop placed: %s %.8f @ %.0f (%s)", order_side.upper(), size, trigger, order_id
        )
        return order_id

    def cancel_stop(self, order_id: str) -> None:
        try:
            self.client.cancel_parent_order(self.product_code, order_id)
            log.info("Exchange stop canceled: %s", order_id)
        except requests.HTTPError as e:
            # 約定済み・取消済みの注文は取り消せない。状態は呼び出し側が建玉で確かめる
            log.warning("逆指値の取消に失敗 (%s): %s", order_id, e)
