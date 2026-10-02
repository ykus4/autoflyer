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

from .client import BitFlyerClient, free_amount, net_position, total_amount

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


class OrderOutcomeUnknown(Exception):
    """注文を送ったが受け付けられたか分からない（タイムアウト・5xx など）。"""


# 逆指値の状態
STOP_ACTIVE = "active"
STOP_FILLED = "filled"  # 約定済み（一部約定を含む）
STOP_GONE = "gone"  # 取消・失効済みで約定なし
STOP_UNKNOWN = "unknown"  # 取得失敗・未反映


@dataclass(frozen=True)
class StopInfo:
    status: str  # STOP_*
    executed: float = 0.0  # 約定済み数量（一部約定なら建玉より小さい）


class Broker(Protocol):
    supports_exchange_stop: bool
    tracks_positions: bool  # position() で取引所の建玉を照会できるか

    def equity(
        self, cur_price: float, side: str | None, btc: float, entry: float
    ) -> float | None: ...
    def available_jpy(self) -> float: ...
    def market(self, order_side: str, size: float, ref_price: float) -> Fill | None: ...
    def position(self) -> ExchangePosition | None: ...
    def stop_status(self, order_id: str) -> StopInfo: ...
    def active_stop_ids(self) -> list[str] | None: ...
    def place_stop(self, order_side: str, size: float, trigger: float) -> str: ...
    def cancel_stop(self, order_id: str) -> StopInfo: ...


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

    def stop_status(self, order_id: str) -> StopInfo:
        return StopInfo(STOP_UNKNOWN)

    def active_stop_ids(self) -> list[str] | None:
        return None

    def place_stop(self, order_side: str, size: float, trigger: float) -> str:
        raise NotImplementedError

    def cancel_stop(self, order_id: str) -> StopInfo:
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

    def equity(self, cur_price: float, side: str | None, btc: float, entry: float) -> float | None:
        """口座の時価評価額。取得に失敗したら None（推定値はドローダウン判定を誤らせる）。"""
        try:
            if self.is_cfd:
                c = self.client.fetch_collateral()
                equity = float(c["collateral"]) + float(c.get("open_position_pnl", 0))
                log.info(
                    "証拠金: %.0f  評価損益: %.0f", c["collateral"], c.get("open_position_pnl", 0)
                )
                return equity
            bal = self.client.fetch_balance()
            # 逆指値で拘束中の BTC も資産なので total を使う
            jpy, btc_bal = total_amount(bal, "JPY"), total_amount(bal, "BTC")
            equity = jpy + btc_bal * cur_price
            log.info("残高: JPY=%.0f  BTC=%.6f  資産合計=%.0f", jpy, btc_bal, equity)
            return equity
        except (requests.RequestException, KeyError, ValueError, TypeError) as e:
            log.warning("残高取得失敗 (%s) — このサイクルの資産評価をスキップ", e)
            return None

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
        """成行注文を出し、約定を確認して返す。

        拒否が確定（4xx・REJECTED 等）なら None。受け付けられたか分からなければ
        `OrderOutcomeUnknown` を送出する（呼び出し側は建玉を確かめるまで再発注しない）。
        """
        try:
            resp = self.client.create_order(self.product_code, order_side, size)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if code is not None and 400 <= code < 500:
                log.error("注文が拒否された (%s): %s", code, e)
                return None
            raise OrderOutcomeUnknown(str(e)) from e
        except requests.RequestException as e:
            raise OrderOutcomeUnknown(str(e)) from e
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

    def stop_status(self, order_id: str) -> StopInfo:
        """逆指値の状態と約定済み数量。"""
        try:
            order = self.client.fetch_parent_order_status(self.product_code, order_id)
        except requests.RequestException as e:
            log.warning("逆指値の状態取得に失敗 (%s)", e)
            return StopInfo(STOP_UNKNOWN)
        if order is None:
            return StopInfo(STOP_UNKNOWN)
        state = order.get("parent_order_state")
        executed = float(order.get("executed_size") or 0)
        if executed > 0 or state == "COMPLETED":
            return StopInfo(STOP_FILLED, executed or float(order.get("size") or 0))
        if state == "ACTIVE":
            return StopInfo(STOP_ACTIVE)
        return StopInfo(STOP_GONE)

    def active_stop_ids(self) -> list[str] | None:
        """有効な逆指値（STOP 型の特殊注文）の受付 ID。取得に失敗したら None。

        IFD/OCO など他の特殊注文は対象外（手動の注文を巻き込まないため）。
        """
        try:
            active = self.client.fetch_active_parent_orders(self.product_code)
        except requests.RequestException as e:
            log.warning("有効な逆指値の取得に失敗 (%s)", e)
            return None
        return [
            str(o["parent_order_acceptance_id"])
            for o in active
            if o.get("parent_order_type", "STOP") == "STOP"
        ]

    def place_stop(self, order_side: str, size: float, trigger: float) -> str:
        order_id = self.client.create_stop_order(self.product_code, order_side, size, trigger)
        log.info(
            "Exchange stop placed: %s %.8f @ %.0f (%s)", order_side.upper(), size, trigger, order_id
        )
        return order_id

    def cancel_stop(self, order_id: str) -> StopInfo:
        """逆指値を取り消し、取消が反映されるまで待って最終状態を返す。

        STOP_GONE なら約定なしで消えたことが確定。STOP_FILLED なら取消前に約定していた。
        STOP_ACTIVE / STOP_UNKNOWN は確定できなかったことを意味し、呼び出し側は
        成行決済を見送る必要がある（逆指値と二重に決済しないため）。
        """
        try:
            self.client.cancel_parent_order(self.product_code, order_id)
        except requests.RequestException as e:
            # 約定済み・取消済みの注文は取り消せない。状態を照会して確かめる
            log.warning("逆指値の取消に失敗 (%s): %s", order_id, e)
        info = StopInfo(STOP_UNKNOWN)
        for _ in range(_FILL_POLL_ATTEMPTS):
            info = self.stop_status(order_id)
            if info.status in (STOP_GONE, STOP_FILLED):
                break
            self._sleep(_FILL_POLL_INTERVAL_SEC)
        log.info("Exchange stop %s after cancel: %s", order_id, info)
        return info
