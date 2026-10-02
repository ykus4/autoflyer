"""Unit tests for the live bot loop, driven by a fake exchange."""

import numpy as np
import pandas as pd
import pytest
import requests

from autoflyer.notifications import EmailNotifier
from autoflyer.trading.bot import BotConfig, LiveBot, resolve_dry_run
from autoflyer.trading.broker import LiveBroker
from autoflyer.trading.state import load_state, reset_halt
from autoflyer.trading.strategy import Variant


class FakeExchange:
    """BitFlyerClient のうち LiveBot / LiveBroker が使う部分を再現する。

    Crypto CFD 口座として建玉・証拠金・逆指値を持ち、成行は ltp で即約定する。
    """

    def __init__(self, bars: pd.DataFrame, ltp: float | None = None) -> None:
        self.bars = bars
        self.ltp = ltp if ltp is not None else float(bars["close"].iloc[-1])
        self.orders: list[tuple[str, float]] = []
        self.collateral = 1_000_000.0
        self.pos_size = 0.0  # 符号付き（ロング正 / ショート負）
        self.pos_price = 0.0
        self.child_orders: dict[str, dict] = {}
        self.parents: dict[str, dict] = {}  # 特殊注文（逆指値）
        self.stop_seq = 0
        self.canceled: list[str] = []
        self.fill_stop_on_cancel = False  # 取消が届く直前に約定する競合を再現する
        self.stop_timeouts = 0  # 逆指値の発注応答をタイムアウトさせる回数
        self.collateral_fails = False
        self.reject_orders = False
        self.order_timeouts = 0
        self.cancel_noop = False
        self.balance = {
            "JPY": {"free": 1_000_000.0, "total": 1_000_000.0},
            "BTC": {"free": 0.0, "total": 0.0},
        }

    # ---- public ----
    def fetch_ohlcv(self, product_code, tf, limit=300):
        return self.bars.copy()

    def fetch_ticker(self, product_code):
        return {"ltp": self.ltp}

    # ---- account ----
    def fetch_balance(self):
        return self.balance

    def fetch_collateral(self):
        if self.collateral_fails:
            raise requests.ConnectionError("maintenance")
        pnl = (self.ltp - self.pos_price) * self.pos_size if self.pos_size else 0.0
        return {"collateral": self.collateral, "open_position_pnl": pnl, "require_collateral": 0}

    def fetch_positions(self, product_code):
        if abs(self.pos_size) < 1e-12:
            return []
        side = "BUY" if self.pos_size > 0 else "SELL"
        return [{"side": side, "size": abs(self.pos_size), "price": self.pos_price}]

    # ---- orders ----
    def _execute(self, side: str, size: float, price: float) -> None:
        signed = size if side.upper() == "BUY" else -size
        new_size = self.pos_size + signed
        if self.pos_size == 0 or (self.pos_size > 0) == (signed > 0):
            total = abs(self.pos_size) + size
            self.pos_price = (self.pos_price * abs(self.pos_size) + price * size) / total
        else:
            self.collateral += (price - self.pos_price) * (
                min(size, abs(self.pos_size)) * (1 if self.pos_size > 0 else -1)
            )
            if abs(new_size) > 1e-12 and (new_size > 0) != (self.pos_size > 0):
                self.pos_price = price
        self.pos_size = 0.0 if abs(new_size) < 1e-12 else new_size

    def create_order(self, product_code, side, size):
        if self.reject_orders:
            resp = requests.Response()
            resp.status_code = 400
            raise requests.HTTPError("400 rejected", response=resp)
        self.orders.append((side, size))
        self._execute(side, size, self.ltp)
        if self.order_timeouts > 0:  # 約定したのに応答がタイムアウトする
            self.order_timeouts -= 1
            raise requests.Timeout("order timed out")
        acc = f"child-{len(self.orders)}"
        self.child_orders[acc] = {
            "child_order_state": "COMPLETED",
            "executed_size": size,
            "average_price": self.ltp,
        }
        return {"child_order_acceptance_id": acc}

    def fetch_child_order(self, product_code, acceptance_id):
        return self.child_orders.get(acceptance_id)

    def create_stop_order(self, product_code, side, size, trigger_price):
        self.stop_seq += 1
        acc = f"parent-{self.stop_seq}"
        self.parents[acc] = {
            "parent_order_acceptance_id": acc,
            "parent_order_state": "ACTIVE",
            "parent_order_type": "STOP",
            "executed_size": 0.0,
            "side": side,
            "size": size,
            "trigger": trigger_price,
        }
        if self.stop_timeouts > 0:  # 受け付けたのに応答がタイムアウトする
            self.stop_timeouts -= 1
            raise requests.Timeout("stop order timed out")
        return acc

    def cancel_parent_order(self, product_code, acceptance_id):
        if self.cancel_noop:  # 取消が反映されない
            return
        o = self.parents.get(acceptance_id)
        if o is None or o["parent_order_state"] != "ACTIVE":
            return
        if self.fill_stop_on_cancel:
            self._fill_stop(o)
            return
        o["parent_order_state"] = "CANCELED"
        self.canceled.append(acceptance_id)

    def fetch_active_parent_orders(self, product_code):
        return [o for o in self.parents.values() if o["parent_order_state"] == "ACTIVE"]

    def fetch_parent_order_status(self, product_code, acceptance_id):
        return self.parents.get(acceptance_id)

    @property
    def stops(self) -> dict[str, dict]:
        """有効な逆指値。"""
        return {k: o for k, o in self.parents.items() if o["parent_order_state"] == "ACTIVE"}

    def _fill_stop(self, o: dict) -> None:
        self._execute(o["side"], o["size"], self.ltp)
        o["parent_order_state"] = "COMPLETED"
        o["executed_size"] = o["size"]
        if not self.pos_size:
            self.balance["BTC"]["total"] = 0.0

    def trigger_stops(self) -> None:
        """ltp が逆指値を越えていれば取引所側で約定させる。"""
        for o in list(self.stops.values()):
            sell = o["side"].upper() == "SELL"
            if (sell and self.ltp <= o["trigger"]) or (not sell and self.ltp >= o["trigger"]):
                self._fill_stop(o)


def _bars(close: np.ndarray, start: str = "2022-01-01") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "dt": pd.date_range(start, periods=len(close), freq="1D", tz="UTC"),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.ones(len(close)),
        }
    )


def _trending_bars(n: int = 300, breakout: bool = False) -> pd.DataFrame:
    """MA200 の上を綺麗に上昇し続ける系列。`breakout` で最終確定バーを高値更新させる。"""
    df = _bars(np.linspace(1_000_000, 5_000_000, n))
    if breakout:
        # 直近確定バー (-2) を大きく吹き上げてドンチャン上限を突破させる
        df.loc[df.index[-2], "high"] = float(df["high"].iloc[-2]) * 1.5
    return df


def _falling_bars(n: int = 300) -> pd.DataFrame:
    """MA200 の下で下落し、最終確定バーで安値を割り込む系列。"""
    df = _bars(np.linspace(5_000_000, 1_000_000, n))
    df.loc[df.index[-2], "low"] = float(df["low"].iloc[-2]) * 0.5
    return df


def _flat_bars(n: int = 300) -> pd.DataFrame:
    """横ばい系列。ドンチャン突破も MA クロスも起きない。"""
    df = _bars(np.full(n, 3_000_000.0))
    df["high"], df["low"] = df["close"] * 1.001, df["close"] * 0.999
    return df


def _advance(ex: FakeExchange, close: float | None = None) -> None:
    """新しいバーを 1 本追加する（＝ひとつ前の未確定バーが確定する）。"""
    last = ex.bars.iloc[-1]
    c = float(close if close is not None else last["close"])
    row = {
        "dt": last["dt"] + pd.Timedelta(days=1),
        "open": c,
        "high": c * 1.001,
        "low": c * 0.999,
        "close": c,
        "volume": 1.0,
    }
    ex.bars = pd.concat([ex.bars, pd.DataFrame([row])], ignore_index=True)
    ex.ltp = c


def _config(tmp_path, variant: Variant, **overrides) -> BotConfig:
    defaults = {
        "symbol": "FX_BTC_JPY",
        "timeframe": "1D",
        "variant": variant,
        "dry_run": True,
        "amount_jpy": 1_000_000.0,
        "interval": 1,
        "state_file": tmp_path / "state.json",
        "max_dd_pct": 20.0,
        "use_mtf": False,
    }
    return BotConfig(**{**defaults, **overrides})


def _bot(tmp_path, variant, bars=None, exchange: FakeExchange | None = None, **overrides):
    notifier = EmailNotifier("", 0, "", "", "")  # 設定不足 → 送信は無効
    assert not notifier.enabled
    cfg = _config(tmp_path, variant, **overrides)
    ex = exchange or FakeExchange(bars)
    broker = None
    if not cfg.dry_run:
        broker = LiveBroker(ex, cfg.product_code, cfg.fallback_jpy, sleep=lambda _s: None)
    return LiveBot(cfg, ex, notifier, broker=broker)


BREAKOUT = Variant("V", breakout_entry=True, use_ma200_filter=True, atr_stop_mult=1.0)


class TestBotConfig:
    def test_product_code_strips_slash(self, tmp_path):
        cfg = _config(tmp_path, Variant("V"), symbol="BTC/JPY")
        assert cfg.product_code == "BTC_JPY"

    def test_equity_file_sits_next_to_state(self, tmp_path):
        cfg = _config(tmp_path, Variant("V"))
        assert cfg.equity_file == tmp_path / "equity.jsonl"


class TestDryRunInterlock:
    @pytest.mark.parametrize(
        ("live", "env", "dry"),
        [
            (False, "0", True),
            (True, None, True),
            (True, "1", True),
            (True, " 0 ", False),
            (True, "0", False),
        ],
    )
    def test_live_requires_flag_and_env(self, live, env, dry):
        assert resolve_dry_run(live, env) is dry


class TestStep:
    def test_records_equity_each_cycle(self, tmp_path):
        bot = _bot(tmp_path, Variant("V"), _trending_bars())
        bot.step()
        bot.step()
        assert len(bot.cfg.equity_file.read_text().splitlines()) == 2

    def test_no_signal_leaves_position_flat(self, tmp_path):
        bot = _bot(tmp_path, Variant("V", breakout_entry=True), _flat_bars())
        assert bot.step() is True
        assert bot.state["in_pos"] is False
        assert bot.client.orders == []

    def test_breakout_opens_a_position(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True))
        bot.step()
        assert bot.state["in_pos"] is True
        assert bot.state["side"] == "long"
        assert bot.state["btc"] > 0
        assert bot.state["stop_px"] is not None
        # ドライランでは実発注しない
        assert bot.client.orders == []

    def test_position_survives_restart(self, tmp_path):
        bars = _trending_bars(breakout=True)
        bot = _bot(tmp_path, BREAKOUT, bars)
        bot.step()

        reopened = _bot(tmp_path, BREAKOUT, bars)
        assert reopened.state["in_pos"] is True
        assert reopened.state["btc"] == bot.state["btc"]

    def test_short_entry_when_enabled(self, tmp_path):
        v = Variant("S", breakout_entry=True, enable_short=True, atr_stop_mult=1.0)
        bot = _bot(tmp_path, v, _falling_bars(), dry_run=False)
        bot.step()
        assert bot.state["side"] == "short"
        assert bot.client.orders[-1][0] == "sell"
        assert bot.state["stop_px"] > bot.state["entry_price"]

    def test_supertrend_variant_gets_a_stop(self, tmp_path):
        v = Variant("ST", breakout_entry=True, use_ma200_filter=True, supertrend_mult=3.0)
        bot = _bot(tmp_path, v, _trending_bars(breakout=True))
        bot.step()
        assert bot.state["in_pos"] is True
        assert bot.state["stop_px"] is not None


class TestStops:
    def test_stop_hit_closes_position_and_starts_cooldown(self, tmp_path):
        v = Variant(
            "V", breakout_entry=True, use_ma200_filter=True, atr_stop_mult=1.0, cooldown_bars=3
        )
        bot = _bot(tmp_path, v, _trending_bars(breakout=True), dry_run=False, exchange_stop=False)
        bot.step()
        assert bot.state["in_pos"] is True
        assert bot.client.orders[-1][0] == "buy"

        # 価格がストップを大きく割り込んだ状態で次のサイクルを回す
        bot.client.ltp = float(bot.state["stop_px"]) * 0.99
        bot.step()
        assert bot.state["in_pos"] is False
        assert bot.state["btc"] == 0.0
        assert bot.client.orders[-1][0] == "sell"
        assert bot.state["cooldown_remaining"] == 3
        assert bot.client.pos_size == 0

    def test_no_reentry_on_the_same_bar_after_stop(self, tmp_path):
        bot = _bot(
            tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False, exchange_stop=False
        )
        bot.step()
        bot.client.ltp = float(bot.state["stop_px"]) * 0.99
        bot.step()
        assert bot.state["in_pos"] is False
        bot.client.ltp = float(bot.client.bars["close"].iloc[-1])
        bot.step()  # 同じ確定バーのブレイクアウトが残っていても再エントリーしない
        assert bot.state["in_pos"] is False
        assert [o[0] for o in bot.client.orders] == ["buy", "sell"]

    def test_cooldown_counts_bars_not_polls(self, tmp_path):
        v = Variant(
            "V", breakout_entry=True, use_ma200_filter=True, atr_stop_mult=1.0, cooldown_bars=2
        )
        bot = _bot(tmp_path, v, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        bot.client.ltp = float(bot.state["stop_px"]) * 0.99
        bot.step()
        for _ in range(5):  # 同じバーで何度ポーリングしても減らない
            bot.step()
        assert bot.state["cooldown_remaining"] == 2
        _advance(bot.client)
        bot.step()
        assert bot.state["cooldown_remaining"] == 1

    def test_take_profit_closes_intrabar(self, tmp_path):
        v = Variant(
            "TP", breakout_entry=True, use_ma200_filter=True, atr_stop_mult=1.0, tp_atr_mult=1.0
        )
        bot = _bot(tmp_path, v, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        bot.client.ltp = float(bot.state["tp_px"]) + 1
        bot.step()
        assert bot.state["in_pos"] is False
        assert bot.client.orders[-1][0] == "sell"


class TestCloseFailures:
    def test_failed_close_is_retried_next_poll(self, tmp_path):
        bot = _bot(
            tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False, exchange_stop=False
        )
        bot.step()
        ex = bot.client
        ex.reject_orders = True
        assert bot._close_position("tp", ex.ltp, bot.state["last_bar_dt"]) is False
        assert bot.state["pending_exit"] == "tp" and bot.state["in_pos"] is True
        ex.reject_orders = False
        bot.step()
        assert bot.state["in_pos"] is False
        assert [o[0] for o in ex.orders] == ["buy", "sell"]

    def test_repeated_close_failures_halt(self, tmp_path):
        bot = _bot(
            tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False, exchange_stop=False
        )
        bot.step()
        bot.client.reject_orders = True
        bot.client.ltp = float(bot.state["stop_px"]) * 0.99
        results = [bot.step() for _ in range(3)]
        assert results == [True, True, False]
        assert bot.state["halted"] is True


class TestExchangeStop:
    def test_stop_order_placed_and_moved(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        assert len(ex.stops) == 1
        (order,) = ex.stops.values()
        assert order["side"] == "sell"
        assert order["trigger"] == round(bot.state["stop_px"])
        assert order["size"] == bot.state["btc"]

        # 新しいバーで ATR が変わればストップを置き直す
        _advance(ex, close=float(ex.bars["close"].iloc[-1]) * 1.2)
        bot.step()
        assert len(ex.stops) == 1
        assert ex.canceled  # 古い逆指値は取り消された
        assert next(iter(ex.stops.values()))["trigger"] == round(bot.state["stop_px"])

    def test_exchange_fill_is_detected_without_double_selling(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        ex.ltp = float(bot.state["stop_px"]) - 1
        ex.trigger_stops()  # Bot が見る前に取引所で約定
        assert ex.pos_size == 0

        bot.step()
        assert bot.state["in_pos"] is False
        assert [o[0] for o in ex.orders] == ["buy"]  # Bot は売り注文を出していない
        assert ex.pos_size == 0  # 逆方向の建玉もできていない

    def test_exchange_fill_starts_cooldown(self, tmp_path):
        v = Variant(
            "V", breakout_entry=True, use_ma200_filter=True, atr_stop_mult=1.0, cooldown_bars=2
        )
        bot = _bot(tmp_path, v, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        bot.client.ltp = float(bot.state["stop_px"]) - 1
        bot.client.trigger_stops()
        bot.step()
        assert bot.state["cooldown_remaining"] == 2

    def test_fill_racing_with_cancel_is_not_doubled(self, tmp_path):
        v = Variant(
            "V", breakout_entry=True, use_ma200_filter=True, atr_stop_mult=1.0, cooldown_bars=2
        )
        bot = _bot(tmp_path, v, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        ex.fill_stop_on_cancel = True
        ex.ltp = float(bot.state["stop_px"]) - 1  # Bot が気づいて取消→その瞬間に約定
        for _ in range(3):  # 逆指値の約定待ちの猶予を過ぎると Bot が取消を試みる
            bot.step()
        assert [o[0] for o in ex.orders] == ["buy"]
        assert ex.pos_size == 0
        assert bot.state["in_pos"] is False
        assert bot.state["cooldown_remaining"] == 2  # 損切りとして扱う

    def test_bot_waits_for_exchange_stop_then_falls_back(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        ex.ltp = float(bot.state["stop_px"]) - 1  # 取引所の逆指値が（なぜか）約定しない
        bot.step()
        bot.step()
        assert [o[0] for o in ex.orders] == ["buy"]  # 取引所に任せて待つ
        bot.step()  # 猶予を超えたら取り消して Bot が決済
        assert [o[0] for o in ex.orders] == ["buy", "sell"]
        assert ex.stops == {} and ex.pos_size == 0

    def test_signal_exit_cancels_exchange_stop(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        bot._close_position("ma_cross", ex.ltp, bot.state["last_bar_dt"])
        assert ex.stops == {} and ex.pos_size == 0
        assert [o[0] for o in ex.orders] == ["buy", "sell"]

    def test_breached_stop_is_not_placed(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        # ストップを現在値より上に引き上げた状態（バー確定後のトレーリングなど）
        bot.state["stop_px"] = ex.ltp + 10_000
        bot._sync_exchange_stop(ex.ltp, bot.state["last_bar_dt"])
        assert all(o["trigger"] != round(ex.ltp + 10_000) for o in ex.parents.values())
        assert bot.state["in_pos"] is False  # 成行で決済した
        assert ex.pos_size == 0

    def test_stop_timeout_does_not_leave_duplicates(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.client.stop_timeouts = 2
        bot.step()  # エントリー → 逆指値タイムアウト（受付済みの残骸は取り消す）
        bot.step()  # もう一度タイムアウト
        bot.step()  # 成功
        assert len(bot.client.stops) == 1
        assert next(iter(bot.client.stops)) == bot.state["stop_order_id"]

    def test_expired_stop_is_replaced(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        old = bot.state["stop_order_id"]
        ex.parents[old]["parent_order_state"] = "EXPIRED"
        bot.step()
        assert bot.state["in_pos"] is True  # 失効は約定扱いしない
        assert bot.state["stop_order_id"] not in (None, old)
        assert len(ex.stops) == 1

    def test_orphan_stops_canceled_on_startup(self, tmp_path):
        ex = FakeExchange(_flat_bars())
        ex.create_stop_order("FX_BTC_JPY", "sell", 0.1, 1)
        bot = _bot(tmp_path, BREAKOUT, exchange=ex, dry_run=False)
        bot.step()
        assert ex.stops == {}

    def test_disabled_flag_places_no_stop(self, tmp_path):
        bot = _bot(
            tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False, exchange_stop=False
        )
        bot.step()
        assert bot.client.stops == {}


class TestReconcile:
    def test_untracked_position_halts(self, tmp_path):
        ex = FakeExchange(_flat_bars())
        ex._execute("BUY", 0.05, 3_000_000)
        bot = _bot(tmp_path, BREAKOUT, exchange=ex, dry_run=False)
        assert bot.step() is False
        assert bot.state["halted"] is True
        assert bot.state["in_pos"] is False
        assert ex.orders == []  # 自動では触らない

    def test_restores_entry_lost_by_crash(self, tmp_path):
        ex = FakeExchange(_flat_bars())
        ex._execute("BUY", 0.05, 3_000_000)
        bot = _bot(tmp_path, BREAKOUT, exchange=ex, dry_run=False)
        bot.state["pending_entry"] = "long"  # 発注後、記録前に落ちた
        bot.step()
        assert bot.state["in_pos"] is True
        assert bot.state["btc"] == pytest.approx(0.05)
        assert bot.state["entry_price"] == pytest.approx(3_000_000)
        assert bot.state["stop_px"] is not None
        assert len(ex.stops) == 1

    def test_opposite_side_halts(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        ex._execute("SELL", ex.pos_size * 2, ex.ltp)  # ドテンしている
        assert bot.step() is False
        assert bot.state["halted"] is True

    def test_external_close_cancels_leftover_stop(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        ex._execute("SELL", ex.pos_size, ex.ltp)  # 手動決済（逆指値は残っている）
        bot.step()
        assert bot.state["in_pos"] is False
        assert ex.stops == {}
        ex.ltp = 1  # 価格が暴落しても逆方向の建玉はできない
        ex.trigger_stops()
        assert ex.pos_size == 0

    def test_clears_position_missing_on_exchange(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        ex._execute("SELL", ex.pos_size, ex.ltp)  # 手動決済
        bot.step()
        assert bot.state["in_pos"] is False
        assert bot.state["cooldown_remaining"] == 0  # 損切り扱いではない

    def test_size_mismatch_follows_exchange(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        ex._execute("SELL", ex.pos_size / 2, ex.ltp)  # 半分だけ手動決済
        bot.step()
        assert bot.state["btc"] == pytest.approx(ex.pos_size)
        assert next(iter(ex.stops.values()))["size"] == pytest.approx(ex.pos_size)


class TestCircuitBreaker:
    def test_balance_outage_does_not_trip(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        bot.client.collateral_fails = True  # メンテナンス中
        assert bot.step() is True
        assert bot.state["halted"] is False
        assert bot.state["in_pos"] is True
        assert bot.state["peak_cash"] == pytest.approx(1_000_000.0)
        assert len(bot.cfg.equity_file.read_text().splitlines()) == 1

    def test_stops_when_drawdown_exceeds_limit(self, tmp_path):
        bot = _bot(tmp_path, Variant("V"), _trending_bars(), dry_run=False, max_dd_pct=20.0)
        assert bot.step() is True  # ピークを記録
        assert bot.state["peak_cash"] == pytest.approx(1_000_000.0)

        bot.client.collateral = 700_000.0
        assert bot.step() is False  # 30% ドローダウン → 停止
        assert bot.last_dd_pct == pytest.approx(30.0)
        assert bot.state["halted"] is True

    def test_halt_survives_restart_until_reset(self, tmp_path):
        bot = _bot(tmp_path, Variant("V"), _trending_bars(), dry_run=False, max_dd_pct=20.0)
        bot.step()
        bot.client.collateral = 700_000.0
        bot.step()

        restarted = _bot(tmp_path, Variant("V"), exchange=bot.client, dry_run=False)
        assert restarted.step() is False
        assert reset_halt(tmp_path / "state.json") is True
        assert load_state(tmp_path / "state.json")["halted"] is False
        assert reset_halt(tmp_path / "state.json") is False

    def test_keeps_running_below_limit(self, tmp_path):
        bot = _bot(tmp_path, Variant("V"), _trending_bars(), dry_run=False, max_dd_pct=20.0)
        assert bot.step() is True
        bot.client.collateral = 950_000.0
        assert bot.step() is True


class TestSpotAccount:
    def test_spot_exchange_stop_fill_detected(self, tmp_path):
        ex = FakeExchange(_trending_bars(breakout=True))
        bot = _bot(tmp_path, BREAKOUT, exchange=ex, dry_run=False, symbol="BTC_JPY")
        bot.step()
        assert bot.state["in_pos"] and len(ex.stops) == 1
        ex.ltp = float(bot.state["stop_px"]) - 1
        ex.trigger_stops()
        bot.step()
        assert bot.state["in_pos"] is False
        assert [o[0] for o in ex.orders] == ["buy"]

    def test_spot_bot_close_cancels_active_stop(self, tmp_path):
        ex = FakeExchange(_trending_bars(breakout=True))
        bot = _bot(tmp_path, BREAKOUT, exchange=ex, dry_run=False, symbol="BTC_JPY")
        bot.step()
        bot._close_position("ma_cross", ex.ltp, bot.state["last_bar_dt"])
        assert [o[0] for o in ex.orders] == ["buy", "sell"]
        assert ex.stops == {}

    def test_spot_rejects_short_variants(self, tmp_path):
        with pytest.raises(ValueError, match="CFD"):
            _config(tmp_path, Variant("S", enable_short=True), symbol="BTC_JPY")

    def test_spot_equity_uses_balance(self, tmp_path):
        bot = _bot(tmp_path, Variant("V"), _trending_bars(), dry_run=False, symbol="BTC_JPY")
        # 逆指値で拘束中の BTC も資産に含める（free=0 でも total で評価）
        bot.client.balance = {
            "JPY": {"free": 400_000.0, "total": 400_000.0},
            "BTC": {"free": 0.0, "total": 0.1},
        }
        bot.client.ltp = 1_000_000.0
        bot.step()
        assert bot.state["peak_cash"] == pytest.approx(500_000.0)


class TestReviewRound2:
    def test_close_timeout_does_not_double_sell(self, tmp_path):
        bot = _bot(
            tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False, exchange_stop=False
        )
        bot.step()
        ex = bot.client
        ex.order_timeouts = 1  # 売りは約定するが応答はタイムアウト
        ex.ltp = float(bot.state["stop_px"]) * 0.99
        bot.step()
        assert [o[0] for o in ex.orders] == ["buy", "sell"]  # 同じサイクルで重ねて売らない
        assert bot.state["exit_unconfirmed"] is True
        bot.step()  # 照合で約定を確認して閉じる
        assert [o[0] for o in ex.orders] == ["buy", "sell"]
        assert bot.state["in_pos"] is False and bot.state["halted"] is False
        assert ex.pos_size == 0

    def test_close_failure_counts_once_per_poll(self, tmp_path):
        bot = _bot(
            tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False, exchange_stop=False
        )
        bot.step()
        bot.client.reject_orders = True
        bot.client.ltp = float(bot.state["stop_px"]) * 0.99
        bot.step()
        assert bot._close_failures == 1

    def test_halt_keeps_a_protective_stop(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        ex.reject_orders = True
        for _ in range(3):  # シグナル決済が毎回拒否される（価格はストップより上）
            bot._close_blocked = False
            bot._close_position("ma_cross", ex.ltp, bot.state["last_bar_dt"])
        assert bot.state["halted"] is True
        assert ex.pos_size > 0
        assert len(ex.stops) == 1  # 停止中も逆指値で守られている
        (stop,) = ex.stops.values()
        assert stop["size"] == pytest.approx(ex.pos_size)

    def test_orphan_swept_on_new_bar(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        orphan = ex.create_stop_order("FX_BTC_JPY", "sell", 0.1, 1)  # 後から現れた残骸
        _advance(ex)
        bot.step()
        assert orphan not in ex.stops
        assert bot.state["stop_order_id"] in ex.stops

    def test_manual_parent_orders_are_left_alone(self, tmp_path):
        ex = FakeExchange(_flat_bars())
        acc = ex.create_stop_order("FX_BTC_JPY", "sell", 0.1, 1)
        ex.parents[acc]["parent_order_type"] = "IFDOCO"
        bot = _bot(tmp_path, BREAKOUT, exchange=ex, dry_run=False)
        bot.step()
        assert ex.parents[acc]["parent_order_state"] == "ACTIVE"

    def test_uncancellable_leftover_stop_halts(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        ex._execute("SELL", ex.pos_size, ex.ltp)  # 手動決済
        ex.cancel_noop = True
        assert bot.step() is False
        assert bot.state["halted"] is True

    def test_partial_stop_fill_on_spot_keeps_remainder(self, tmp_path):
        ex = FakeExchange(_trending_bars(breakout=True))
        bot = _bot(tmp_path, BREAKOUT, exchange=ex, dry_run=False, symbol="BTC_JPY")
        bot.step()
        btc = bot.state["btc"]
        order = ex.parents[bot.state["stop_order_id"]]
        order.update(parent_order_state="COMPLETED", executed_size=btc / 2)
        bot.step()
        assert bot.state["in_pos"] is True
        assert bot.state["btc"] == pytest.approx(btc / 2)
        assert len(ex.stops) == 1
        assert next(iter(ex.stops.values()))["size"] == pytest.approx(btc / 2)

    def test_stale_pending_entry_cleared(self, tmp_path):
        ex = FakeExchange(_flat_bars())
        bot = _bot(tmp_path, BREAKOUT, exchange=ex, dry_run=False)
        bot.state["pending_entry"] = "long"
        bot.step()
        assert bot.state["pending_entry"] is None
        ex._execute("BUY", 0.05, 3_000_000)  # 後から現れた手動の建玉は復元しない
        assert bot.step() is False
        assert bot.state["halted"] is True


class TestReviewRound3:
    def test_lagging_positions_after_stop_fill_do_not_double_close(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        order = ex.parents[bot.state["stop_order_id"]]
        # 逆指値は約定済みだが建玉照会にはまだ反映されていない
        order.update(parent_order_state="COMPLETED", executed_size=order["size"])
        bot._close_position("ma_cross", ex.ltp, bot.state["last_bar_dt"])
        assert [o[0] for o in ex.orders] == ["buy"]  # 成行を重ねない
        assert bot.state["in_pos"] is False

    def test_stop_mid_execution_is_not_final(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        ex.cancel_noop = True
        ex.parents[bot.state["stop_order_id"]]["executed_size"] = bot.state["btc"] / 2  # 約定途中
        assert bot._close_position("ma_cross", ex.ltp, bot.state["last_bar_dt"]) is False
        assert [o[0] for o in ex.orders] == ["buy"]
        assert bot.state["in_pos"] is True

    def test_unconfirmed_close_waits_before_retry(self, tmp_path):
        bot = _bot(
            tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False, exchange_stop=False
        )
        bot.step()
        ex = bot.client
        ex.reject_orders = False
        bot.state.update(exit_unconfirmed=True, pending_exit="stop")  # 結果不明・未約定
        bot.step()
        assert [o[0] for o in ex.orders] == ["buy"]  # 1 回目は待つ
        bot.step()
        assert [o[0] for o in ex.orders] == ["buy", "sell"]  # 猶予後に再試行


class TestTradeLog:
    def test_entry_and_exit_are_logged(self, tmp_path):
        from autoflyer.trading.state import read_jsonl_tail

        bot = _bot(
            tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False, exchange_stop=False
        )
        bot.step()
        bot.client.ltp = float(bot.state["stop_px"]) * 0.99
        bot.step()
        rows = read_jsonl_tail(bot.cfg.trades_file, 10)
        assert [(r["action"], r["reason"]) for r in rows] == [("entry", "signal"), ("exit", "stop")]
        assert rows[1]["pnl"] < 0 and rows[0]["side"] == "long"


class CapturingNotifier:
    enabled = True

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, subject, body):
        self.sent.append((subject, body))


class TestDailySummary:
    def _bot(self, tmp_path, now):
        cfg = _config(tmp_path, BREAKOUT, summary_hour_jst=9)
        notifier = CapturingNotifier()
        bot = LiveBot(cfg, FakeExchange(_trending_bars(breakout=True)), notifier)
        bot._now = lambda: now[0]
        return bot, notifier

    def _summaries(self, notifier):
        return [s for s in notifier.sent if s[0].startswith("日次サマリー")]

    def test_sent_once_per_day_after_hour(self, tmp_path):
        from datetime import datetime, timezone

        now = [datetime(2026, 10, 1, 23, 0, tzinfo=timezone.utc)]  # JST 10/2 08:00
        bot, notifier = self._bot(tmp_path, now)
        bot.step()
        assert self._summaries(notifier) == []
        now[0] = datetime(2026, 10, 2, 0, 30, tzinfo=timezone.utc)  # JST 09:30
        bot.step()
        bot.step()
        (summary,) = self._summaries(notifier)
        assert summary[0] == "日次サマリー 2026-10-02"
        assert "ポジション: long" in summary[1]

    def test_lists_recent_trades(self, tmp_path):
        from datetime import datetime, timezone

        cfg = _config(tmp_path, BREAKOUT, summary_hour_jst=0)
        notifier = CapturingNotifier()
        bot = LiveBot(cfg, FakeExchange(_trending_bars(breakout=True)), notifier)
        bot._now = lambda: datetime.now(timezone.utc)  # 約定の記録時刻と揃える
        bot.step()  # エントリー（サマリーはエントリー前に送られる）
        bot.state["last_summary_date"] = None
        bot.step()
        body = self._summaries(notifier)[-1][1]
        assert "直近 24 時間の約定: 1 件" in body and "entry long" in body

    def test_reports_change_since_previous_summary(self, tmp_path):
        from datetime import datetime, timezone

        now = [datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc)]
        bot, notifier = self._bot(tmp_path, now)
        bot.state["last_summary_equity"] = 900_000.0
        bot.state["last_summary_date"] = "2026-10-01"
        bot.step()
        body = self._summaries(notifier)[0][1]
        assert "前回比" in body

    def test_disabled_with_negative_hour(self, tmp_path):
        from datetime import datetime, timezone

        cfg = _config(tmp_path, BREAKOUT, summary_hour_jst=-1)
        notifier = CapturingNotifier()
        bot = LiveBot(cfg, FakeExchange(_flat_bars()), notifier)
        bot._now = lambda: datetime(2026, 10, 2, 5, 0, tzinfo=timezone.utc)
        bot.step()
        assert self._summaries(notifier) == []
