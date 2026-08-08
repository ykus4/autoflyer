"""Unit tests for the live bot loop, driven by a fake exchange client."""

import numpy as np
import pandas as pd
import pytest

from autoflyer.notifications import EmailNotifier
from autoflyer.trading.bot import BotConfig, LiveBot
from autoflyer.trading.strategy import Variant


class FakeClient:
    """BitFlyerClient のうち LiveBot が使う部分だけを再現する。"""

    def __init__(self, bars: pd.DataFrame, ltp: float | None = None) -> None:
        self.bars = bars
        self.ltp = ltp if ltp is not None else float(bars["close"].iloc[-1])
        self.orders: list[tuple[str, float]] = []
        self.balance = {"JPY": {"free": 1_000_000.0}, "BTC": {"free": 0.0}}

    def fetch_ohlcv(self, product_code, tf, limit=300):
        return self.bars.copy()

    def fetch_ticker(self, product_code):
        return {"ltp": self.ltp}

    def fetch_balance(self):
        return self.balance

    def create_order(self, product_code, side, size):
        self.orders.append((side, size))
        return {"id": f"fake-{len(self.orders)}"}


def _trending_bars(n: int = 300, breakout: bool = False) -> pd.DataFrame:
    """MA200 の上を綺麗に上昇し続ける系列。`breakout` で最終確定バーを高値更新させる。"""
    close = np.linspace(1_000_000, 5_000_000, n)
    high = close * 1.01
    low = close * 0.99
    df = pd.DataFrame(
        {
            "dt": pd.date_range("2022-01-01", periods=n, freq="1D", tz="UTC"),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.ones(n),
        }
    )
    if breakout:
        # 直近確定バー (-2) を大きく吹き上げてドンチャン上限を突破させる
        df.loc[df.index[-2], "high"] = float(df["high"].iloc[-2]) * 1.5
    return df


def _flat_bars(n: int = 300) -> pd.DataFrame:
    """横ばい系列。ドンチャン突破も MA クロスも起きない。"""
    close = np.full(n, 3_000_000.0)
    return pd.DataFrame(
        {
            "dt": pd.date_range("2022-01-01", periods=n, freq="1D", tz="UTC"),
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": np.ones(n),
        }
    )


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


def _bot(tmp_path, variant, bars, **overrides) -> LiveBot:
    notifier = EmailNotifier("", 0, "", "", "")  # 設定不足 → 送信は無効
    assert not notifier.enabled
    return LiveBot(_config(tmp_path, variant, **overrides), FakeClient(bars), notifier)


class TestBotConfig:
    def test_product_code_strips_slash(self, tmp_path):
        cfg = _config(tmp_path, Variant("V"), symbol="BTC/JPY")
        assert cfg.product_code == "BTC_JPY"

    def test_equity_file_sits_next_to_state(self, tmp_path):
        cfg = _config(tmp_path, Variant("V"))
        assert cfg.equity_file == tmp_path / "equity.jsonl"


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
        variant = Variant("V", breakout_entry=True, use_ma200_filter=True, atr_stop_mult=1.0)
        bot = _bot(tmp_path, variant, _trending_bars(breakout=True))
        bot.step()
        assert bot.state["in_pos"] is True
        assert bot.state["btc"] > 0
        assert bot.state["stop_px"] is not None
        # ドライランでは実発注しない
        assert bot.client.orders == []

    def test_position_survives_restart(self, tmp_path):
        variant = Variant("V", breakout_entry=True, use_ma200_filter=True, atr_stop_mult=1.0)
        bars = _trending_bars(breakout=True)
        bot = _bot(tmp_path, variant, bars)
        bot.step()

        reopened = _bot(tmp_path, variant, bars)
        assert reopened.state["in_pos"] is True
        assert reopened.state["btc"] == bot.state["btc"]

    def test_stop_hit_closes_position_and_starts_cooldown(self, tmp_path):
        variant = Variant(
            "V", breakout_entry=True, use_ma200_filter=True, atr_stop_mult=1.0, cooldown_bars=3
        )
        # ライブ相当（dry_run=False）にすると現在値が ticker から来る
        bot = _bot(tmp_path, variant, _trending_bars(breakout=True), dry_run=False)
        bot.step()
        assert bot.state["in_pos"] is True
        assert bot.client.orders[-1][0] == "buy"

        # 価格がストップを大きく割り込んだ状態で次のサイクルを回す
        bot.client.ltp = float(bot.state["stop_px"]) * 0.5
        bot.step()
        assert bot.state["in_pos"] is False
        assert bot.state["btc"] == 0.0
        assert bot.client.orders[-1][0] == "sell"
        assert bot.cooldown_remaining == 3


class TestCircuitBreaker:
    def test_stops_when_drawdown_exceeds_limit(self, tmp_path):
        bot = _bot(tmp_path, Variant("V"), _trending_bars(), dry_run=False, max_dd_pct=20.0)
        bot.client.balance = {"JPY": {"free": 1_000_000.0}, "BTC": {"free": 0.0}}
        assert bot.step() is True  # ピークを記録
        assert bot.state["peak_cash"] == pytest.approx(1_000_000.0)

        bot.client.balance = {"JPY": {"free": 700_000.0}, "BTC": {"free": 0.0}}
        assert bot.step() is False  # 30% ドローダウン → 停止
        assert bot.last_dd_pct == pytest.approx(30.0)

    def test_keeps_running_below_limit(self, tmp_path):
        bot = _bot(tmp_path, Variant("V"), _trending_bars(), dry_run=False, max_dd_pct=20.0)
        assert bot.step() is True
        bot.client.balance = {"JPY": {"free": 950_000.0}, "BTC": {"free": 0.0}}
        assert bot.step() is True
