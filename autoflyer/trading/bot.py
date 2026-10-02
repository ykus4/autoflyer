"""Live trading bot loop.

Polls OHLCV and applies the same rules as the backtester: entries from
`trading.signals`, stops / take-profit / trailing from `trading.exits`. All
position state is persisted after every change so a restart resumes where it
left off.

Timing model (mirrors the backtester):

- Per *confirmed* bar (once): count down the cooldown, move the stop with
  `exits.manage_position`, and evaluate entry/exit signals.
- Per poll: compare the live price with the stop / take-profit so an intrabar
  breach is acted on immediately, and reconcile with the exchange position.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

from ..logging_utils import setup_logging
from ..notifications import EmailNotifier, create_notifier
from .broker import (
    STOP_FILLED,
    STOP_GONE,
    Broker,
    ExchangePosition,
    LiveBroker,
    OrderOutcomeUnknown,
    PaperBroker,
    StopInfo,
    unrealized_pnl,
)
from .client import BitFlyerClient
from .exits import (
    LONG,
    SHORT,
    ExitState,
    initial_stop,
    initial_tp,
    manage_position,
    stop_hit,
    tp_reached,
)
from .fees import cost_model_for_product
from .indicators import add_indicators, supertrend
from .market_data import rescale
from .signals import entry_signals, exit_reason, exit_signals, long_ok, position_size, short_ok
from .signals import sizing_fraction as compute_sizing_fraction
from .state import (
    FLAT_STATE,
    append_equity,
    append_trade,
    equity_path,
    load_state,
    save_state,
    trades_path,
)
from .strategy import Variant, get_variant

log = logging.getLogger("autoflyer.bot")

# 上位足トレンド確認に使う時間足の対応表
_MTF_UP: dict[str, str] = {
    "1H": "4H",
    "3H": "1D",
    "6H": "1D",
    "12H": "3D",
    "1D": "3D",
}
_MTF_MA_LEN = 50
_FALLBACK_EQUITY_JPY = 100_000.0
_MIN_ORDER_BTC = 0.001  # bitFlyer の最小発注数量
_SIZE_TOLERANCE_BTC = 1e-8
_MAX_CLOSE_FAILURES = 3  # 決済注文が続けて失敗したら停止する
_STOP_BREACH_GRACE_POLLS = 3  # 取引所の逆指値の約定を待つポーリング回数
_UNCONFIRMED_GRACE_POLLS = 2  # 結果不明の決済注文が遅れて約定しないか待つポーリング回数

# 決済理由のうち、損切り扱い（クールダウン開始）のもの
_STOP_REASONS = {"stop", "trail_stop", "exchange_stop"}
# この理由で決済したバーでは再エントリーしない（バックテストと同じ）
_NO_REENTRY_REASONS = _STOP_REASONS | {"tp"}


@dataclass(frozen=True)
class BotConfig:
    """1 回の起動分の設定。CLI 引数と環境変数から組み立てる。"""

    symbol: str
    timeframe: str
    variant: Variant
    dry_run: bool
    amount_jpy: float
    interval: int
    state_file: Path
    max_dd_pct: float
    use_mtf: bool
    exchange_stop: bool = True  # 取引所に逆指値を置く（ライブのみ）

    def __post_init__(self) -> None:
        if self.variant.enable_short and not self.product_code.startswith("FX_"):
            # 現物でのショートは保有 BTC の売却になってしまう
            raise ValueError(f"{self.variant.name} はショートを使うため CFD (FX_*) 専用です")

    @property
    def product_code(self) -> str:
        """bitFlyer の product_code はスラッシュなし (BTC_JPY / FX_BTC_JPY)。"""
        return self.symbol.replace("/", "_")

    @property
    def equity_file(self) -> Path:
        return equity_path(self.state_file)

    @property
    def trades_file(self) -> Path:
        return trades_path(self.state_file)

    @property
    def fallback_jpy(self) -> float:
        """残高を取得できない（DRY_RUN・API 失敗）ときに仮定する資金。"""
        return self.amount_jpy or _FALLBACK_EQUITY_JPY


def _order_side(pos_side: str, action: str) -> str:
    """ポジション方向と open/close から売買方向を決める。"""
    buy = (pos_side == LONG) == (action == "open")
    return "buy" if buy else "sell"


class LiveBot:
    """ポーリングループ本体。1 サイクルが `step()` に対応する。"""

    def __init__(
        self,
        cfg: BotConfig,
        client: BitFlyerClient,
        notifier: EmailNotifier,
        broker: Broker | None = None,
    ) -> None:
        self.cfg = cfg
        self.client = client
        self.notifier = notifier
        self.broker: Broker = broker or (
            PaperBroker(cfg.fallback_jpy)
            if cfg.dry_run
            else LiveBroker(client, cfg.product_code, cfg.fallback_jpy)
        )
        self.fees = cost_model_for_product(cfg.product_code)
        cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state = load_state(cfg.state_file)
        self.last_dd_pct = 0.0
        self._close_failures = 0
        self._stop_breach_polls = 0
        self._started = False  # 起動後最初のサイクルで残骸の逆指値を掃除する
        self._close_blocked = False  # このサイクルで決済に失敗した（同じサイクルで再発注しない）
        self._unconfirmed_polls = 0
        self._last_price = 0.0
        log.info("Loaded state: %s", self.state)

    # ---- 状態ヘルパー ----

    def _save(self) -> None:
        save_state(self.cfg.state_file, self.state)

    @property
    def in_pos(self) -> bool:
        return bool(self.state["in_pos"])

    @property
    def side(self) -> str:
        return str(self.state["side"] or LONG)

    @property
    def btc(self) -> float:
        return float(self.state.get("btc") or 0.0)

    def _exit_state(self) -> ExitState:
        s = self.state
        return ExitState(
            side=self.side,
            entry_price=float(s["entry_price"]),
            stop_px=s["stop_px"],
            trail_best=s["trail_best"],
            tp_px=s["tp_px"],
            tp_hit=bool(s["tp_hit"]),
        )

    def _store_exit_state(self, pos: ExitState) -> None:
        self.state.update(
            stop_px=pos.stop_px, trail_best=pos.trail_best, tp_px=pos.tp_px, tp_hit=pos.tp_hit
        )

    def _current_price(self, bars: pd.DataFrame) -> float:
        if self.cfg.dry_run:
            return float(bars["close"].iloc[-1])
        return float(self.client.fetch_ticker(self.cfg.product_code)["ltp"])

    def _supertrend_value(self, confirmed_bars: pd.DataFrame) -> float:
        if self.cfg.variant.supertrend_mult <= 0:
            return float("nan")
        return float(supertrend(confirmed_bars, self.cfg.variant.supertrend_mult).iloc[-1])

    def _mtf_trend_ok(self) -> bool:
        """上位足が上昇トレンドか。取得に失敗したらエントリーを止めない。"""
        if not self.cfg.use_mtf:
            return True
        tf_up = _MTF_UP.get(self.cfg.timeframe.upper())
        if tf_up is None:
            return True
        try:
            bars_up = self.client.fetch_ohlcv(self.cfg.product_code, tf_up, limit=100)
            ma = bars_up["close"].rolling(_MTF_MA_LEN).mean().iloc[-1]
            trend_up = float(bars_up["close"].iloc[-1]) > float(ma)
            log.info(
                "MTF(%s) close=%.0f MA%d=%.0f trend_up=%s",
                tf_up,
                bars_up["close"].iloc[-1],
                _MTF_MA_LEN,
                ma,
                trend_up,
            )
            return trend_up
        except (requests.RequestException, ValueError, KeyError) as e:
            log.warning("MTF fetch failed (%s) — allowing entry", e)
            return True

    # ---- 取引所の逆指値 ----
    #
    # 逆指値が取引所にある間、ストップ決済は取引所に任せる。Bot が同じ価格で成行決済すると
    # 二重決済（CFD では逆方向の建玉）になるため、Bot が自分で決済するのは
    #   - 逆指値がない（発注失敗・--no-exchange-stop・ドライラン）
    #   - 逆指値を取り消し、約定なしで消えたことを確認できた
    # ときだけにする。

    def _use_exchange_stop(self) -> bool:
        return self.cfg.exchange_stop and self.broker.supports_exchange_stop

    def _stop_on_exchange(self) -> bool:
        """現在の stop_px と同じ価格の逆指値が取引所に置かれているか。"""
        stop_px = self.state["stop_px"]
        return (
            bool(self.state["stop_order_id"])
            and stop_px is not None
            and self.state["stop_order_px"] == int(round(stop_px))
        )

    def _retire_exchange_stop(self, bar_dt: str) -> str:
        """取引所の逆指値を外す。

        戻り値:
          "clear"     逆指値はない／約定なしで消えたことを確認した → 成行決済してよい
          "closed"    取消前に約定して建玉が閉じた → state は閉じた
          "deferred"  取消前に一部約定していた → 残りは次のサイクルで扱う
          "unsure"    取消を確認できない → 二重決済を避けるため今回は何もしない
        """
        order_id = self.state.get("stop_order_id")
        if not order_id or not self.broker.supports_exchange_stop:
            self.state.update(stop_order_id=None, stop_order_px=None)
            return "clear"

        info = self.broker.cancel_stop(order_id)
        if info.status == STOP_FILLED:
            # 逆指値が約定していたら、このサイクルでは成行を出さない（残りは次サイクルで扱う）
            return "closed" if self._on_stop_filled(info, bar_dt) else "deferred"
        if info.status != STOP_GONE:
            log.warning("逆指値 %s の取消を確認できない (%s) — 成行決済を見送る", order_id, info)
            return "unsure"
        self.state.update(stop_order_id=None, stop_order_px=None)
        self._save()
        if self.broker.tracks_positions and self._exchange_is_flat():
            self._record_external_close(bar_dt, via_stop=False)
            return "closed"
        return "clear"

    def _on_stop_filled(self, info: StopInfo, bar_dt: str) -> bool:
        """最終状態になった逆指値の約定を反映する。建玉がすべて閉じたら True。

        残量は逆指値の約定数量から求める（建玉照会は反映が遅れることがあるため使わない。
        食い違いは次サイクルの照合で補正される）。
        """
        remaining = self.btc - info.executed
        if remaining <= _SIZE_TOLERANCE_BTC:
            self._record_external_close(bar_dt, via_stop=True)
            return True
        log.warning("逆指値が一部約定: 残り %.8f BTC — 残りに逆指値を置き直す", remaining)
        self.notifier.send(
            "逆指値の一部約定",
            f"逆指値が一部だけ約定しました。残り {remaining:.8f} BTC を引き続き管理します。",
        )
        self.state.update(btc=remaining, stop_order_id=None, stop_order_px=None)
        self._save()
        return False

    def _exchange_is_flat(self) -> bool:
        ex = self.broker.position()
        return ex is not None and ex.side is None

    def _cancel_orphan_stops(self) -> None:
        """state が知らない有効な逆指値（発注タイムアウトの残骸など）を取り消す。"""
        if not self.broker.supports_exchange_stop:
            return
        active = self.broker.active_stop_ids()
        if active is None:
            return
        orphans = [o for o in active if o != self.state.get("stop_order_id")]
        for order_id in orphans:
            info = self.broker.cancel_stop(order_id)
            log.warning("未管理の逆指値 %s を取り消しました (%s)", order_id, info)
        if orphans:
            self.notifier.send(
                "未管理の逆指値を取消",
                f"Bot が管理していない逆指値 {len(orphans)} 件を取り消しました: {orphans}",
            )

    def _place_stop(self) -> bool:
        """state の stop_px に逆指値を置く。成功したら True。"""
        stop_px = self.state["stop_px"]
        if stop_px is None:
            return False
        target = int(round(stop_px))
        try:
            order_id = self.broker.place_stop(_order_side(self.side, "close"), self.btc, target)
        except requests.RequestException as e:
            log.error("逆指値の発注に失敗 (%s) — Bot 側のストップ監視のみで継続", e)
            self.notifier.send("逆指値の発注失敗", f"取引所への逆指値発注に失敗しました。\n{e}")
            # タイムアウトでも受け付けられている可能性がある。残骸は取り消して次回置き直す
            self._cancel_orphan_stops()
            return False
        self.state.update(stop_order_id=order_id, stop_order_px=target)
        self._save()
        return True

    def _sync_exchange_stop(self, cur_price: float, bar_dt: str) -> None:
        """取引所の逆指値を state の stop_px に合わせる（変わったときだけ置き直す）。"""
        if not self._use_exchange_stop() or not self.in_pos or self._stop_on_exchange():
            return
        stop_px = self.state["stop_px"]
        if stop_px is not None and stop_hit(self.side, cur_price, stop_px):
            # すでに越えている逆指値は即約定するので置かない。Bot が成行で決済する
            self._close_position(self._stop_reason(), cur_price, bar_dt)
            return
        if self._retire_exchange_stop(bar_dt) == "clear" and self.in_pos:
            self._place_stop()

    # ---- 取引所との照合 ----

    def _record_external_close(self, bar_dt: str, *, via_stop: bool) -> None:
        """Bot の外で決済された建玉を state から外す。"""
        if via_stop:
            px = float(self.state["stop_order_px"] or self.state["stop_px"] or 0.0)
            pnl = unrealized_pnl(self.side, self.btc, float(self.state["entry_price"]), px)
            log.warning("EXCHANGE STOP  %.8f BTC @ ~%.0f JPY  pnl≈%.0f", self.btc, px, pnl)
            self._log_trade("exit", px, "exchange_stop", pnl)
            self.notifier.send(
                "STOP HIT（取引所逆指値）— ポジション決済",
                f"取引所の逆指値が約定しました。\n数量: {self.btc:.8f} BTC @ ~{px:,.0f} JPY\n"
                f"損益: {pnl:+,.0f} JPY（概算）",
            )
        else:
            log.critical("建玉が取引所から消えています（手動決済・ロスカット?）— state をクリア")
            self._log_trade("exit", None, "external", None)
            self.notifier.send(
                "建玉の不一致",
                "Bot が管理していた建玉が取引所に見つかりません。"
                "手動決済またはロスカットの可能性があります。state をノーポジションに戻しました。",
            )
            # 残った逆指値が後で約定すると逆方向の建玉になる。取消を確認できなければ止める
            order_id = self.state.get("stop_order_id")
            if order_id and self.broker.supports_exchange_stop:
                info = self.broker.cancel_stop(order_id)
                if info.status != STOP_GONE:
                    self._halt(f"建玉消失後の逆指値 {order_id} を取り消せない ({info.status})")
        self._go_flat("exchange_stop" if via_stop else "external", bar_dt)

    def _reconcile(self, ind: pd.DataFrame, bar_dt: str) -> None:
        """state と取引所を突き合わせる。"""
        if not self.broker.tracks_positions:
            self._reconcile_spot(bar_dt)
            return

        try:
            ex = self.broker.position()
        except requests.RequestException as e:
            log.warning("建玉の照会に失敗 (%s) — 照合をスキップ", e)
            return
        assert ex is not None

        if not self.in_pos:
            if ex.side is not None:
                self._adopt_or_halt(ex, ind, bar_dt)
            elif self.state.get("pending_entry"):
                self.state["pending_entry"] = None  # エントリーは約定していなかった
                self._save()
            return

        if self.state.get("exit_unconfirmed"):
            # 前回の決済注文は結果不明だった。建玉で結果を確かめる
            reason = self.state["pending_exit"] or "exit"
            if ex.side is None:
                log.warning("結果不明だった決済注文 [%s] は約定していた", reason)
                self._log_trade("exit", None, reason, None)
                self.notifier.send(
                    f"EXIT [{reason}] — 決済を確認", "結果不明だった決済注文の約定を確認しました。"
                )
                self._go_flat(reason, bar_dt)
                return
            # 遅れて約定することがあるので、何回か建玉を確かめてから再試行する
            self._unconfirmed_polls += 1
            if self._unconfirmed_polls < _UNCONFIRMED_GRACE_POLLS:
                self._close_blocked = True
                return
            self._unconfirmed_polls = 0
            self.state["exit_unconfirmed"] = False  # 約定していない。通常どおり再試行する
            self._save()

        if ex.side is None:
            order_id = self.state.get("stop_order_id")
            info = self.broker.stop_status(order_id) if order_id else None
            # 取引所がノーポジなら全量閉じている（逆指値の約定か、Bot 外の決済）
            via_stop = info is not None and info.status == STOP_FILLED
            self._record_external_close(bar_dt, via_stop=via_stop)
        elif ex.side != self.side:
            self._halt(f"建玉の方向が不一致: state={self.side} / 取引所={ex.side} {ex.size:.8f}")
        elif abs(ex.size - self.btc) > _SIZE_TOLERANCE_BTC:
            log.critical(
                "建玉数量の不一致: state=%.8f / 取引所=%.8f — 取引所に合わせます", self.btc, ex.size
            )
            self.notifier.send(
                "建玉数量の不一致",
                f"state: {self.btc:.8f} BTC / 取引所: {ex.size:.8f} BTC\n取引所の値に合わせました。",
            )
            # 逆指値の数量も合わせる必要があるので一度外す（次の同期で置き直す）
            retired = self._retire_exchange_stop(bar_dt)
            if retired == "closed":
                return
            self.state["btc"] = ex.size
            self._save()
            if retired == "unsure":
                self._halt("数量の合わない逆指値を取り消せない")
        elif self.state.get("stop_order_id"):
            self._replace_vanished_stop()

    def _reconcile_spot(self, bar_dt: str) -> None:
        """現物は建玉を照会できないため、逆指値の状態と未確定の決済だけで判断する。"""
        if self.in_pos and self.state.get("exit_unconfirmed"):
            self._halt("現物の決済注文の結果が不明（残高を確認してください）")
            return
        order_id = self.state.get("stop_order_id")
        if not (self.in_pos and order_id):
            return
        info = self.broker.stop_status(order_id)
        if info.status == STOP_FILLED:
            self._on_stop_filled(info, bar_dt)
        else:
            self._replace_vanished_stop(info)

    def _replace_vanished_stop(self, info: StopInfo | None = None) -> None:
        """逆指値が約定せずに消えていたら（失効・手動取消）、次の同期で置き直させる。"""
        order_id = self.state["stop_order_id"]
        info = info or self.broker.stop_status(order_id)
        if info.status == STOP_GONE:
            log.warning("逆指値 %s が約定せずに消えました（失効?）— 置き直します", order_id)
            self.state.update(stop_order_id=None, stop_order_px=None)
            self._save()

    def _adopt_or_halt(self, ex: ExchangePosition, ind: pd.DataFrame, bar_dt: str) -> None:
        """Bot が知らない建玉への対応。

        直前のエントリー注文の結果（記録前にクラッシュした等）なら管理下に入れる。
        それ以外（逆方向の建玉・手動の建玉）は自動では触らず停止して人に知らせる。
        """
        assert ex.side is not None
        if self.state.get("pending_entry") != ex.side:
            self._halt(f"未管理の建玉: {ex.side} {ex.size:.8f} BTC @ {ex.avg_price:,.0f}")
            return
        confirmed_bars = ind.iloc[:-1]
        stop_px, tp_px = self._initial_exits(ex.side, ex.avg_price, confirmed_bars)
        self.state.update(
            in_pos=True,
            side=ex.side,
            entry_price=ex.avg_price,
            btc=ex.size,
            entry_dt=bar_dt,
            stop_px=stop_px,
            trail_best=None,
            tp_px=tp_px,
            tp_hit=False,
            pending_entry=None,
        )
        self._save()
        self._log_trade("entry", ex.avg_price, "restored", None)
        log.critical(
            "記録漏れのエントリーを取引所から復元: %s %.8f BTC @ %.0f (stop=%s)",
            ex.side,
            ex.size,
            ex.avg_price,
            stop_px,
        )
        self.notifier.send(
            "エントリーを取引所から復元",
            f"{ex.side} {ex.size:.8f} BTC @ {ex.avg_price:,.0f} JPY\nストップ: {stop_px}",
        )

    def _halt(self, reason: str) -> None:
        """人の確認が必要な状態。取引を止め、`reset-halt` まで再開しない。

        建玉が残るなら、停止中も守れるよう取引所に逆指値を置いておく。
        """
        log.critical("HALT: %s", reason)
        protection = ""
        if self.in_pos:
            protection = self._protect_before_halt()
        self.notifier.send(
            "Bot 停止 — 要確認",
            f"{reason}\n{protection}\n確認後 `python -m autoflyer reset-halt` で再開してください。",
        )
        self.state.update(halted=True, halt_reason=reason)
        self._save()

    def _protect_before_halt(self) -> str:
        """停止前に建玉の逆指値を確保し、その状況を通知用の文にして返す。"""
        if self._stop_on_exchange():
            return f"建玉には逆指値 {self.state['stop_order_px']} が置かれています。"
        if not self.broker.supports_exchange_stop:
            return "⚠ 建玉が残っていますが取引所の逆指値はありません。"
        if self.state.get("exit_unconfirmed"):
            # 決済済みかもしれない数量に逆指値を置くと、手元の別の BTC を売りかねない
            return "⚠ 決済注文の結果が不明なため逆指値は置いていません。残高を確認してください。"
        stop_px = self.state["stop_px"]
        if stop_px is None or stop_hit(self.side, self._last_price, stop_px):
            return "⚠ 建玉が残っていますが、ストップ価格を越えているため逆指値を置けません。至急確認してください。"
        if self.state.get("stop_order_id"):
            return "建玉には逆指値が置かれています（価格は要確認）。"
        if self._place_stop():
            return f"建玉を守るため逆指値 {self.state['stop_order_px']} を置きました。"
        return "⚠ 建玉が残っていますが逆指値を置けませんでした。至急確認してください。"

    # ---- エントリー / 決済 ----

    def _initial_exits(
        self, side: str, px: float, confirmed_bars: pd.DataFrame
    ) -> tuple[float | None, float | None]:
        v = self.cfg.variant
        cur = confirmed_bars.iloc[-1]
        cur_atr = float(cur["atr"]) if pd.notna(cur.get("atr")) else 0.0
        stop_px = initial_stop(
            side,
            px,
            v,
            cur_atr,
            self._supertrend_value(confirmed_bars),
            confirmed_bars["close"],
            confirmed_bars["atr"],
        )
        return stop_px, initial_tp(side, px, v, cur_atr)

    def _stop_reason(self) -> str:
        return "trail_stop" if self.state["tp_hit"] else "stop"

    def _log_trade(self, action: str, price: float | None, reason: str, pnl: float | None) -> None:
        append_trade(
            self.cfg.trades_file,
            {
                "action": action,  # "entry" / "exit"
                "side": self.side,
                "btc": self.btc,
                "price": price,
                "reason": reason,
                "pnl": pnl,
                "dry_run": self.cfg.dry_run,
            },
        )

    def _go_flat(self, reason: str, bar_dt: str) -> None:
        self.state.update(FLAT_STATE)
        if reason in _STOP_REASONS:
            self.state["cooldown_remaining"] = self.cfg.variant.cooldown_bars
        if reason in _NO_REENTRY_REASONS:
            self.state["no_entry_bar_dt"] = bar_dt
        self._save()

    def _close_failed(self, reason: str, detail: str) -> bool:
        """決済できなかった。このサイクルではもう決済を試みず、次のサイクルで再試行する。"""
        self._close_blocked = True
        self._close_failures += 1
        self.state["pending_exit"] = reason
        self._save()
        log.error("決済できず [%s] (%d 回目): %s", reason, self._close_failures, detail)
        if self._close_failures >= _MAX_CLOSE_FAILURES:
            self._halt(f"決済が {self._close_failures} 回続けてできない: {detail}")
        else:
            self.notifier.send(
                "決済注文の失敗", f"{reason}: {detail}（{self._close_failures} 回目、次回再試行）"
            )
        return False

    def _close_position(self, reason: str, cur_price: float, bar_dt: str) -> bool:
        """成行で決済する。決済できなかったら理由を pending_exit に残し、次のサイクルで再試行する。

        1 サイクルで決済を試みるのは 1 回だけ（結果不明の注文に重ねて発注しないため）。
        """
        if self._close_blocked:
            return False
        retired = self._retire_exchange_stop(bar_dt)
        if retired == "closed":
            return True
        if retired == "deferred":
            self._close_blocked = True
            self.state["pending_exit"] = reason
            self._save()
            return False
        if retired == "unsure":
            return self._close_failed(reason, "逆指値の取消を確認できない")

        side, btc = self.side, self.btc
        try:
            fill = self.broker.market(_order_side(side, "close"), btc, cur_price)
        except OrderOutcomeUnknown as e:
            # 受け付けられた可能性がある。建玉で確かめるまで再発注しない
            self.state["exit_unconfirmed"] = True
            return self._close_failed(reason, f"決済注文の結果が不明 ({e})")
        if fill is None:
            return self._close_failed(reason, "決済注文が拒否された")

        self._close_failures = 0
        self.fees.record_fill(pd.Timestamp.now(tz="UTC"), fill.size * fill.price)
        pnl = unrealized_pnl(side, btc, float(self.state["entry_price"]), fill.price)
        log.info("EXIT[%s]  %s %.8f BTC @ %.0f JPY  pnl≈%.0f", reason, side, btc, fill.price, pnl)
        self._log_trade("exit", fill.price, reason, pnl)
        self.notifier.send(
            f"EXIT [{reason}] — ポジション決済",
            f"{side} ポジションを決済しました（理由: {reason}）。\n"
            f"数量: {btc:.8f} BTC @ {fill.price:,.0f} JPY\n損益: {pnl:+,.0f} JPY",
        )
        self._go_flat(reason, bar_dt)
        return True

    def _try_enter(self, side: str, ind: pd.DataFrame, cur_price: float, bar_dt: str) -> None:
        confirmed_bars = ind.iloc[:-1]  # 確定バーのみ
        close_hist = confirmed_bars["close"]
        v = self.cfg.variant

        jpy = self.broker.available_jpy()
        if self.cfg.amount_jpy > 0:
            jpy = min(jpy, self.cfg.amount_jpy)
        log.info("使用資金: %.0f JPY  fee_rate=%.4f%%", jpy, self.fees.rate * 100)
        stop_est, _ = self._initial_exits(side, cur_price, confirmed_bars)
        frac = compute_sizing_fraction(close_hist, v)
        if frac != 1.0:
            log.info("Sizing fraction: %.3f", frac)
        btc = round(position_size(jpy, cur_price, stop_est, self.fees.rate, v, frac), 8)
        if btc < _MIN_ORDER_BTC:
            log.info("発注数量 %.8f BTC が最小数量 %.3f 未満 — 見送り", btc, _MIN_ORDER_BTC)
            return

        # 発注前に記録する: 応答が返らなくても同じバーで再発注せず、
        # 約定していれば次サイクルの照合で建玉を復元できる
        self.state.update(no_entry_bar_dt=bar_dt, pending_entry=side)
        self._save()
        try:
            fill = self.broker.market(_order_side(side, "open"), btc, cur_price)
        except OrderOutcomeUnknown as e:
            # pending_entry を残す: CFD なら次サイクルの照合で建玉を復元する
            log.error("エントリー注文の結果が不明 (%s)", e)
            self.notifier.send("エントリー注文の結果不明", f"{side} {btc:.8f} BTC: {e}")
            if not self.broker.tracks_positions:
                self._halt("現物のエントリー注文の結果が不明（残高を確認してください）")
            return
        if fill is None:
            self.state["pending_entry"] = None
            self._save()
            return
        self.fees.record_fill(pd.Timestamp.now(tz="UTC"), fill.size * fill.price)
        # ストップ・利確は実際の約定価格から引き直す
        stop_px, tp_px = self._initial_exits(side, fill.price, confirmed_bars)
        signal_bar = confirmed_bars.iloc[-1]
        self.state.update(
            in_pos=True,
            side=side,
            entry_price=fill.price,
            btc=fill.size,
            entry_dt=bar_dt,
            stop_px=stop_px,
            trail_best=(
                ExitState(side=side, entry_price=fill.price).favorable(signal_bar)
                if v.chandelier_mult > 0
                else None
            ),
            tp_px=tp_px,
            tp_hit=False,
            pending_entry=None,
        )
        self._save()
        self._log_trade("entry", fill.price, "signal", None)
        log.info(
            "ENTRY  %s %.8f BTC @ %.0f JPY  stop_px=%s  tp_px=%s",
            side,
            fill.size,
            fill.price,
            stop_px,
            tp_px,
        )
        self.notifier.send(
            f"ENTRY — {side} ポジション取得",
            f"{side} エントリーしました。\n"
            f"数量: {fill.size:.8f} BTC @ {fill.price:,.0f} JPY\n"
            f"ストップ: {stop_px}\n利確: {tp_px}",
        )
        self._sync_exchange_stop(cur_price, bar_dt)

    # ---- サイクルの各段階 ----

    def _check_circuit_breaker(self, cur_equity: float, cur_price: float, bar_dt: str) -> bool:
        """ドローダウンが閾値に達したら決済して停止状態を保存し、True を返す。"""
        peak = self.state["peak_cash"]
        dd_pct = (1.0 - cur_equity / peak) * 100 if peak else 0.0
        self.last_dd_pct = dd_pct
        if dd_pct < self.cfg.max_dd_pct:
            return False

        reason = f"drawdown {dd_pct:.1f}% >= {self.cfg.max_dd_pct:.1f}%"
        log.critical("CIRCUIT BREAKER: %s — closing position and stopping.", reason)
        self.notifier.send(
            "CIRCUIT BREAKER 発動",
            f"ドローダウン {dd_pct:.1f}% が閾値 {self.cfg.max_dd_pct:.1f}% に到達。\n"
            f"Bot を停止しポジションをクローズします。\n"
            f"資産: {cur_equity:,.0f} JPY / ピーク: {peak:,.0f} JPY\n"
            f"再開するには `python -m autoflyer reset-halt` を実行してください。",
        )
        if self.in_pos and self.btc > 0:
            self._close_position("circuit_breaker", cur_price, bar_dt)
        self.state.update(halted=True, halt_reason=reason)
        self._save()
        return True

    def _on_new_bar(self, ind: pd.DataFrame, bar_dt: str, cur_price: float) -> None:
        """確定バーが更新されたときに 1 回だけ行う処理。"""
        if self.state["cooldown_remaining"] > 0:
            self.state["cooldown_remaining"] -= 1
        # 発注応答のタイムアウト等で取り残された逆指値を定期的に掃除する
        self._cancel_orphan_stops()
        if not self.in_pos and not self.broker.tracks_positions:
            self.state["pending_entry"] = None

        # エントリーしたバー自体では判定しない（その足の高安はエントリー前の値動き）
        reason = None
        if self.in_pos and self.state["entry_dt"] != bar_dt:
            confirmed_bars = ind.iloc[:-1]
            bar = confirmed_bars.iloc[-1]
            cur_atr = float(bar["atr"]) if pd.notna(bar.get("atr")) else 0.0
            pos = self._exit_state()
            prev_stop = pos.stop_px
            reason = manage_position(
                pos, bar, self.cfg.variant, cur_atr, self._supertrend_value(confirmed_bars)
            )
            self._store_exit_state(pos)
            if pos.stop_px != prev_stop:
                log.info("Stop updated: %s → %s", prev_stop, pos.stop_px)
            if reason is not None:
                self.state["pending_exit"] = reason  # 決済に失敗しても次サイクルで再試行する

        self.state["last_bar_dt"] = bar_dt
        self._save()
        if reason is not None:
            self._close_position(reason, cur_price, bar_dt)
        else:
            self._sync_exchange_stop(cur_price, bar_dt)

    def _check_intrabar(self, cur_price: float, bar_dt: str) -> bool:
        """現在値がストップ/利確を越えていれば決済して True。"""
        if not self.in_pos:
            return False
        if self.state["pending_exit"]:
            return self._close_position(self.state["pending_exit"], cur_price, bar_dt)
        if stop_hit(self.side, cur_price, self.state["stop_px"]):
            if self._stop_on_exchange():
                # 同じ価格の逆指値が取引所で約定するはず。照合で確認する
                self._stop_breach_polls += 1
                if self._stop_breach_polls < _STOP_BREACH_GRACE_POLLS:
                    log.info("ストップ到達 — 取引所の逆指値の約定待ち")
                    return False
                log.warning("逆指値が約定しないため Bot が決済します")
            self._stop_breach_polls = 0
            return self._close_position(self._stop_reason(), cur_price, bar_dt)
        self._stop_breach_polls = 0
        # トレーリング移行型の利確は確定バーで判定する（manage_position が切り替える）
        if (
            self.cfg.variant.tp_trail_mult <= 0
            and not self.state["tp_hit"]
            and tp_reached(self.side, cur_price, self.state["tp_px"])
        ):
            return self._close_position("tp", cur_price, bar_dt)
        return False

    # ---- 1 サイクル ----

    def step(self) -> bool:
        """1 サイクル実行する。停止すべきときだけ False を返す。"""
        if self.state["halted"]:
            log.critical("HALTED (%s) — 取引を停止中。", self.state["halt_reason"])
            return False

        bars = self.client.fetch_ohlcv(self.cfg.product_code, self.cfg.timeframe)
        cur_price = self._current_price(bars)
        if not self.cfg.dry_run:
            # Binance の足を bitFlyer の価格水準に合わせる（ストップ価格を取引所と揃える）
            bars, ratio = rescale(bars, cur_price)
            log.debug("price basis ratio (bitFlyer / Binance): %.4f", ratio)
        ind = add_indicators(bars)
        confirmed, prev = ind.iloc[-2], ind.iloc[-3]
        bar_dt = pd.Timestamp(confirmed["dt"]).isoformat()
        self.fees.step(pd.Timestamp(confirmed["dt"]))

        self._last_price = cur_price
        self._close_blocked = False
        if not self._started:
            self._cancel_orphan_stops()
            self._started = True
        self._reconcile(ind, bar_dt)
        if self.state["halted"]:
            return False

        cur_equity = self.broker.equity(
            cur_price,
            self.side if self.in_pos else None,
            self.btc,
            float(self.state["entry_price"] or cur_price),
        )
        if cur_equity is not None:
            if self.state["peak_cash"] is None or cur_equity > self.state["peak_cash"]:
                self.state["peak_cash"] = cur_equity
                self._save()
            append_equity(self.cfg.equity_file, cur_equity)
            if self._check_circuit_breaker(cur_equity, cur_price, bar_dt):
                return False

        if bar_dt != self.state["last_bar_dt"]:
            self._on_new_bar(ind, bar_dt, cur_price)
        else:
            self._sync_exchange_stop(cur_price, bar_dt)  # 未設置・発注失敗の逆指値を補う
        if self._check_intrabar(cur_price, bar_dt) or self._close_blocked or self.state["halted"]:
            return not self.state["halted"]

        v = self.cfg.variant
        entry_long, entry_short = entry_signals(confirmed, prev, v)
        exit_long, exit_short = exit_signals(confirmed, prev, v)
        log.info(
            "entry=(L:%s S:%s) exit=(L:%s S:%s) pos=%s dd=%.1f%% cooldown=%d",
            entry_long,
            entry_short,
            exit_long,
            exit_short,
            self.side if self.in_pos else "flat",
            self.last_dd_pct,
            self.state["cooldown_remaining"],
        )

        # シグナル決済（利確トレーリング中は除外）
        if (
            self.in_pos
            and not self.state["tp_hit"]
            and (exit_long if self.side == LONG else exit_short)
        ):
            self._close_position(exit_reason(v), cur_price, bar_dt)

        if (
            self.in_pos
            or self.state["halted"]
            or self.state["cooldown_remaining"] > 0
            or self.state["no_entry_bar_dt"] == bar_dt
        ):
            return not self.state["halted"]

        close_hist = ind["close"].iloc[:-1]
        on_reject = lambda r: log.info("Filter blocked: %s", r)  # noqa: E731
        if entry_long:
            if not self._mtf_trend_ok():
                log.info("MTF filter blocked long entry")
            elif long_ok(confirmed, v, close_hist, on_reject=on_reject):
                self._try_enter(LONG, ind, cur_price, bar_dt)
                return True
        if entry_short and v.enable_short and short_ok(confirmed, v, on_reject=on_reject):
            self._try_enter(SHORT, ind, cur_price, bar_dt)
        return True

    def run_forever(self) -> None:
        while True:
            try:
                if not self.step():
                    return
            except requests.HTTPError as e:
                log.error("HTTP error: %s", e)
                self.notifier.send("HTTP エラー", f"API呼び出しでHTTPエラーが発生しました。\n{e}")
            except requests.RequestException as e:
                log.error("Network error: %s", e)
                self.notifier.send("ネットワークエラー", f"API通信に失敗しました。\n{e}")
            except (ValueError, KeyError, TypeError) as e:
                log.exception("Data processing error: %s", e)
                self.notifier.send(
                    "データ処理エラー",
                    f"データの処理中にエラーが発生しました。\n{type(e).__name__}: {e}",
                )
            except Exception as e:  # noqa: BLE001 — ループを絶対に落とさない
                log.exception("Unexpected error: %s", e)
                self.notifier.send(
                    "予期しないエラー",
                    f"Botで予期しないエラーが発生しました。確認してください。\n"
                    f"{type(e).__name__}: {e}",
                )

            log.info("Sleeping %ds...", self.cfg.interval)
            time.sleep(self.cfg.interval)


def resolve_dry_run(live_flag: bool, env_dry_run: str | None) -> bool:
    """実発注するのは `--live` かつ `DRY_RUN=0` のときだけ（どちらかが欠ければドライラン）。"""
    if not live_flag:
        return True
    if (env_dry_run or "1").strip() != "0":
        log.warning(
            "--live が指定されていますが DRY_RUN=%s のためドライランで起動します", env_dry_run
        )
        return True
    return False


def _config_from_args(args: argparse.Namespace) -> BotConfig:
    return BotConfig(
        symbol=args.symbol or os.environ.get("SYMBOL", "FX_BTC_JPY"),
        timeframe=args.timeframe[0] if args.timeframe else os.environ.get("TIMEFRAME", "1D"),
        variant=get_variant(args.variant or os.environ.get("VARIANT", "STOP_3ATR")),
        dry_run=resolve_dry_run(args.live, os.environ.get("DRY_RUN")),
        amount_jpy=args.amount,
        interval=args.interval,
        state_file=Path(args.state),
        max_dd_pct=args.max_dd_pct,
        use_mtf=args.use_mtf,
        exchange_stop=not args.no_exchange_stop,
    )


def run(args: argparse.Namespace) -> None:
    load_dotenv()
    setup_logging(args.log_file)
    cfg = _config_from_args(args)
    log.info(
        "Bot start — symbol=%s  tf=%s  variant=%s  dry_run=%s  max_dd=%.1f%%  exchange_stop=%s",
        cfg.symbol,
        cfg.timeframe,
        cfg.variant.name,
        cfg.dry_run,
        cfg.max_dd_pct,
        cfg.exchange_stop,
    )
    client = BitFlyerClient(
        os.environ.get("BITFLYER_API_KEY", ""),
        os.environ.get("BITFLYER_API_SECRET", ""),
    )
    LiveBot(cfg, client, create_notifier()).run_forever()


__all__ = ["BotConfig", "LiveBot", "resolve_dry_run", "run"]
