"""Unit tests for the live bot loop, driven by a fake exchange."""

import numpy as np
import pandas as pd
import pytest

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
        self.stops: dict[str, dict] = {}  # 有効な逆指値
        self.canceled: list[str] = []
        self.fill_stop_on_cancel = False  # 取消が届く直前に約定する競合を再現する
        self.balance = {"JPY": {"free": 1_000_000.0}, "BTC": {"free": 0.0}}

    # ---- public ----
    def fetch_ohlcv(self, product_code, tf, limit=300):
        return self.bars.copy()

    def fetch_ticker(self, product_code):
        return {"ltp": self.ltp}

    # ---- account ----
    def fetch_balance(self):
        return self.balance

    def fetch_collateral(self):
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
        self.orders.append((side, size))
        self._execute(side, size, self.ltp)
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
        acc = f"parent-{len(self.stops) + len(self.canceled) + 1}"
        self.stops[acc] = {"side": side, "size": size, "trigger": trigger_price}
        return acc

    def cancel_parent_order(self, product_code, acceptance_id):
        if self.fill_stop_on_cancel and acceptance_id in self.stops:
            o = self.stops[acceptance_id]
            self._execute(o["side"], o["size"], self.ltp)
            if not self.pos_size:
                self.balance["BTC"]["free"] = 0.0
        self.stops.pop(acceptance_id, None)
        self.canceled.append(acceptance_id)

    def fetch_active_parent_orders(self, product_code):
        return [{"parent_order_acceptance_id": k} for k in self.stops]

    def trigger_stops(self) -> None:
        """ltp が逆指値を越えていれば取引所側で約定させる。"""
        for acc, o in list(self.stops.items()):
            sell = o["side"].upper() == "SELL"
            if (sell and self.ltp <= o["trigger"]) or (not sell and self.ltp >= o["trigger"]):
                self._execute(o["side"], o["size"], self.ltp)
                del self.stops[acc]


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
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
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
        bot.step()
        assert [o[0] for o in ex.orders] == ["buy"]
        assert ex.pos_size == 0
        assert bot.state["in_pos"] is False
        assert bot.state["cooldown_remaining"] == 2  # 損切りとして扱う

    def test_exit_cancels_exchange_stop(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        bot.client.ltp = float(bot.state["stop_px"]) - 1  # Bot 側が先に気づくケース
        bot.step()
        assert bot.client.stops == {}
        assert bot.client.pos_size == 0

    def test_disabled_flag_places_no_stop(self, tmp_path):
        bot = _bot(
            tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False, exchange_stop=False
        )
        bot.step()
        assert bot.client.stops == {}


class TestReconcile:
    def test_adopts_untracked_position(self, tmp_path):
        ex = FakeExchange(_flat_bars())
        ex._execute("BUY", 0.05, 3_000_000)
        bot = _bot(tmp_path, BREAKOUT, exchange=ex, dry_run=False)
        bot.step()
        assert bot.state["in_pos"] is True
        assert bot.state["btc"] == pytest.approx(0.05)
        assert bot.state["entry_price"] == pytest.approx(3_000_000)
        assert bot.state["stop_px"] is not None
        assert len(ex.stops) == 1

    def test_clears_position_missing_on_exchange(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        ex.stops.clear()
        ex._execute("SELL", ex.pos_size, ex.ltp)  # 手動決済
        bot.state["stop_order_id"] = None
        bot.step()
        assert bot.state["in_pos"] is False

    def test_size_mismatch_follows_exchange(self, tmp_path):
        bot = _bot(tmp_path, BREAKOUT, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        ex = bot.client
        ex._execute("SELL", ex.pos_size / 2, ex.ltp)  # 半分だけ手動決済
        bot.step()
        assert bot.state["btc"] == pytest.approx(ex.pos_size)
        assert next(iter(ex.stops.values()))["size"] == pytest.approx(ex.pos_size)


class TestCircuitBreaker:
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
        ex.ltp = float(bot.state["stop_px"]) - 1  # 取引所はまだ約定させていない
        bot.step()
        assert [o[0] for o in ex.orders] == ["buy", "sell"]
        assert ex.stops == {}

    def test_spot_equity_uses_balance(self, tmp_path):
        bot = _bot(tmp_path, Variant("V"), _trending_bars(), dry_run=False, symbol="BTC_JPY")
        bot.client.balance = {"JPY": {"free": 400_000.0}, "BTC": {"free": 0.1}}
        bot.client.ltp = 1_000_000.0
        bot.step()
        assert bot.state["peak_cash"] == pytest.approx(500_000.0)
