"""Live trading bot loop.

Polls OHLCV, applies the same entry/exit rules as the backtester (see
`trading.signals`), and places market orders on bitFlyer. All position state is
persisted after every change so a restart resumes where it left off.
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
from .client import BitFlyerClient
from .fees import FeeTierModel
from .indicators import add_indicators
from .signals import breakout_signals, compute_signal, live_stop, long_ok, position_size
from .signals import sizing_fraction as compute_sizing_fraction
from .state import FLAT_STATE, append_equity, load_state, save_state
from .strategy import VARIANTS, Variant

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

    @property
    def product_code(self) -> str:
        """bitFlyer の product_code はスラッシュなし (BTC_JPY / FX_BTC_JPY)。"""
        return self.symbol.replace("/", "_")

    @property
    def equity_file(self) -> Path:
        return self.state_file.with_name("equity.jsonl")


class LiveBot:
    """ポーリングループ本体。1 サイクルが `step()` に対応する。"""

    def __init__(
        self,
        cfg: BotConfig,
        client: BitFlyerClient,
        notifier: EmailNotifier,
    ) -> None:
        self.cfg = cfg
        self.client = client
        self.notifier = notifier
        self.fees = FeeTierModel()
        cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state = load_state(cfg.state_file)
        self.cooldown_remaining = 0
        self.last_dd_pct = 0.0
        log.info("Loaded state: %s", self.state)

    # ---- 小さなヘルパー ----

    def _save(self) -> None:
        save_state(self.cfg.state_file, self.state)

    def _go_flat(self) -> None:
        self.state.update(FLAT_STATE)
        self._save()

    def _market_order(self, side: str, btc: float) -> dict | None:
        if self.cfg.dry_run:
            log.info("[DRY_RUN] %s %s %.8f BTC", side.upper(), self.cfg.product_code, btc)
            return {"id": "dry_run"}
        order = self.client.create_order(self.cfg.product_code, side, btc)
        log.info("Order placed: %s", order)
        return order

    def _estimated_equity(self, cur_price: float, btc_held: float, entry_price: float) -> float:
        """残高が取れないときの推定資産。"""
        base = self.cfg.amount_jpy or _FALLBACK_EQUITY_JPY
        if not self.state["in_pos"]:
            return base
        return (cur_price - entry_price) * btc_held + base

    def _current_price(self, bars: pd.DataFrame) -> float:
        if self.cfg.dry_run:
            return float(bars["close"].iloc[-1])
        return float(self.client.fetch_ticker(self.cfg.product_code)["ltp"])

    def _current_equity(self, cur_price: float, btc_held: float, entry_price: float) -> float:
        if self.cfg.dry_run:
            return self._estimated_equity(cur_price, btc_held, entry_price)
        try:
            bal = self.client.fetch_balance()
            jpy_balance = float(bal.get("JPY", {}).get("free", 0))
            btc_balance = float(bal.get("BTC", {}).get("free", 0))
            equity = jpy_balance + btc_balance * cur_price
            log.info("残高: JPY=%.0f  BTC=%.6f  資産合計=%.0f", jpy_balance, btc_balance, equity)
            return equity
        except (requests.RequestException, KeyError, ValueError) as e:
            equity = self._estimated_equity(cur_price, btc_held, entry_price)
            log.warning("残高取得失敗 (%s) — 推定値で資産計算: %.0f JPY", e, equity)
            return equity

    def _available_jpy(self) -> float:
        """エントリーに使える資金。`--amount` があれば上限として適用する。"""
        if self.cfg.dry_run:
            jpy = self.cfg.amount_jpy or _FALLBACK_EQUITY_JPY
        else:
            try:
                jpy = float(self.client.fetch_balance().get("JPY", {}).get("free", 0))
            except (requests.RequestException, KeyError, ValueError) as e:
                jpy = self.cfg.amount_jpy or _FALLBACK_EQUITY_JPY
                log.warning("残高取得失敗 (%s) — フォールバック %.0f JPY を使用", e, jpy)
        return min(jpy, self.cfg.amount_jpy) if self.cfg.amount_jpy > 0 else jpy

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

    # ---- サイクルの各段階 ----

    def _entry_signals(self, bars: pd.DataFrame, bars_ind: pd.DataFrame) -> tuple[bool, bool]:
        """(signal_up, signal_down)。ブレイクアウトモードは最新確定バーで判定する。"""
        if self.cfg.variant.breakout_entry:
            return breakout_signals(bars_ind.iloc[-2])
        return compute_signal(bars)

    def _check_circuit_breaker(self, cur_equity: float, cur_price: float) -> bool:
        """ドローダウンが閾値に達したらポジションを閉じて True を返す（＝停止）。"""
        peak = self.state["peak_cash"]
        dd_pct = (1.0 - cur_equity / peak) * 100 if peak else 0.0
        self.last_dd_pct = dd_pct
        if dd_pct < self.cfg.max_dd_pct:
            return False

        log.critical(
            "CIRCUIT BREAKER: drawdown %.1f%% >= %.1f%% — closing position and stopping.",
            dd_pct,
            self.cfg.max_dd_pct,
        )
        self.notifier.send(
            "CIRCUIT BREAKER 発動",
            f"ドローダウン {dd_pct:.1f}% が閾値 {self.cfg.max_dd_pct:.1f}% に到達。\n"
            f"Bot を停止しポジションをクローズします。\n"
            f"資産: {cur_equity:,.0f} JPY / ピーク: {peak:,.0f} JPY",
        )
        btc = float(self.state.get("btc", 0.0))
        if self.state["in_pos"] and btc > 0 and self._market_order("sell", btc):
            log.critical("CIRCUIT BREAKER: sold %.8f BTC @ ~%.0f JPY", btc, cur_price)
            self._go_flat()
        return True

    def _refresh_stop(self, bars_ind: pd.DataFrame, entry_price: float) -> None:
        """毎サイクル最新 ATR でストップ価格を引き直す。"""
        if not self.state["in_pos"] or self.cfg.variant.atr_stop_mult <= 0:
            return
        new_stop = live_stop(bars_ind, entry_price, "long", self.cfg.variant)
        if new_stop is not None and new_stop != self.state.get("stop_px"):
            log.info("Stop updated: %.0f → %.0f", self.state.get("stop_px") or 0, new_stop)
            self.state["stop_px"] = new_stop
            self._save()

    def _close_position(
        self,
        bars: pd.DataFrame,
        cur_price: float,
        btc_held: float,
        entry_price: float,
        *,
        subject: str,
        headline: str,
        log_label: str,
    ) -> bool:
        if not self._market_order("sell", btc_held):
            return False
        self.fees.record_fill(pd.Timestamp(bars["dt"].iloc[-1]), btc_held * cur_price)
        pnl = (cur_price - entry_price) * btc_held
        log.info("%s  %.8f BTC @ %.0f JPY  pnl≈%.0f", log_label, btc_held, cur_price, pnl)
        self.notifier.send(
            subject,
            f"{headline}\n売却: {btc_held:.8f} BTC @ {cur_price:,.0f} JPY\n損益: {pnl:+,.0f} JPY",
        )
        self._go_flat()
        return True

    def _try_enter(self, bars: pd.DataFrame, bars_ind: pd.DataFrame, cur_price: float) -> None:
        confirmed = bars_ind.iloc[-2]
        close_hist = bars_ind["close"].iloc[:-1]  # 確定バーのみ
        v = self.cfg.variant

        if not self._mtf_trend_ok():
            log.info("MTF filter blocked long entry")
            return
        if not long_ok(
            confirmed, v, close_hist, on_reject=lambda r: log.info("Filter blocked: %s", r)
        ):
            return

        jpy = self._available_jpy()
        log.info("使用資金: %.0f JPY  fee_rate=%.4f%%", jpy, self.fees.rate * 100)
        stop_px = live_stop(bars_ind, cur_price, "long", v)
        frac = compute_sizing_fraction(close_hist, v)
        if frac != 1.0:
            log.info("Sizing fraction: %.3f", frac)
        btc = round(position_size(jpy, cur_price, stop_px, self.fees.rate, v, frac), 8)
        if btc <= 0 or not self._market_order("buy", btc):
            return

        self.fees.record_fill(pd.Timestamp(bars["dt"].iloc[-1]), btc * cur_price)
        self.state.update(
            in_pos=True,
            entry_price=cur_price,
            btc=btc,
            entry_dt=bars.iloc[-2]["dt"].isoformat(),
            stop_px=stop_px,
        )
        self._save()
        log.info("ENTRY  %.8f BTC @ %.0f JPY  stop_px=%s", btc, cur_price, stop_px)
        self.notifier.send(
            "ENTRY — ポジション取得",
            f"買いエントリーしました。\n"
            f"購入: {btc:.8f} BTC @ {cur_price:,.0f} JPY\n"
            f"ストップ: {stop_px}",
        )

    # ---- 1 サイクル ----

    def step(self) -> bool:
        """1 サイクル実行する。停止すべきときだけ False を返す。"""
        bars = self.client.fetch_ohlcv(self.cfg.product_code, self.cfg.timeframe)
        bars_ind = add_indicators(bars)
        self.fees.step(pd.Timestamp(bars["dt"].iloc[-2]))
        signal_up, signal_down = self._entry_signals(bars, bars_ind)

        cur_price = self._current_price(bars)
        entry_price = float(self.state["entry_price"] or cur_price)
        btc_held = float(self.state.get("btc", 0.0))

        cur_equity = self._current_equity(cur_price, btc_held, entry_price)
        if self.state["peak_cash"] is None or cur_equity > self.state["peak_cash"]:
            self.state["peak_cash"] = cur_equity
            self._save()
        append_equity(self.cfg.equity_file, cur_equity)

        if self._check_circuit_breaker(cur_equity, cur_price):
            return False

        self._refresh_stop(bars_ind, entry_price)

        stop_px = self.state.get("stop_px")
        if self.state["in_pos"] and stop_px is not None and cur_price <= float(stop_px):
            if self._close_position(
                bars,
                cur_price,
                btc_held,
                entry_price,
                subject="STOP HIT — ポジション決済",
                headline="ストップロスに到達しました。",
                log_label="STOP HIT",
            ):
                self.cooldown_remaining = self.cfg.variant.cooldown_bars
            return True

        if self.cooldown_remaining > 0:
            log.info("Cooldown: %d bars remaining", self.cooldown_remaining)
            self.cooldown_remaining -= 1

        log.info(
            "signal_up=%s  signal_down=%s  in_pos=%s  dd=%.1f%%  cooldown=%d",
            signal_up,
            signal_down,
            self.state["in_pos"],
            self.last_dd_pct,
            self.cooldown_remaining,
        )

        if signal_up and not self.state["in_pos"] and self.cooldown_remaining <= 0:
            self._try_enter(bars, bars_ind, cur_price)
        elif signal_down and self.state["in_pos"]:
            self._close_position(
                bars,
                cur_price,
                btc_held,
                entry_price,
                subject="EXIT — ポジション決済",
                headline="シグナルによりポジションを決済しました。",
                log_label="EXIT",
            )
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


def resolve_variant(name: str) -> Variant:
    v = next((v for v in VARIANTS if v.name == name), None)
    if v is None:
        raise SystemExit(
            f"Unknown variant: {name}. Run `python -m autoflyer variants` to list them."
        )
    return v


def _config_from_args(args: argparse.Namespace) -> BotConfig:
    return BotConfig(
        symbol=args.symbol,
        timeframe=args.timeframe[0] if args.timeframe else os.environ.get("TIMEFRAME", "1D"),
        variant=resolve_variant(args.variant or os.environ.get("VARIANT", "STOP_3ATR")),
        dry_run=not args.live,
        amount_jpy=args.amount,
        interval=args.interval,
        state_file=Path(args.state),
        max_dd_pct=args.max_dd_pct,
        use_mtf=args.use_mtf,
    )


def run(args: argparse.Namespace) -> None:
    load_dotenv()
    setup_logging(args.log_file)
    cfg = _config_from_args(args)
    log.info(
        "Bot start — symbol=%s  tf=%s  variant=%s  dry_run=%s  max_dd=%.1f%%",
        cfg.symbol,
        cfg.timeframe,
        cfg.variant.name,
        cfg.dry_run,
        cfg.max_dd_pct,
    )
    client = BitFlyerClient(
        os.environ.get("BITFLYER_API_KEY", ""),
        os.environ.get("BITFLYER_API_SECRET", ""),
    )
    LiveBot(cfg, client, create_notifier()).run_forever()


__all__ = ["BotConfig", "LiveBot", "resolve_variant", "run"]
