"""Tests for the shared exit rules."""

import numpy as np
import pandas as pd

from autoflyer.trading.exits import (
    ExitState,
    initial_stop,
    initial_tp,
    manage_position,
    stop_hit,
    tp_reached,
)
from autoflyer.trading.strategy import Variant

NAN = float("nan")


def _bar(high: float, low: float) -> pd.Series:
    return pd.Series({"high": high, "low": low, "close": (high + low) / 2})


def test_stop_and_tp_direction():
    assert stop_hit("long", 99, 100) and not stop_hit("long", 101, 100)
    assert stop_hit("short", 101, 100) and not stop_hit("short", 99, 100)
    assert not stop_hit("long", 0, None)
    assert tp_reached("long", 110, 110) and tp_reached("short", 90, 90)
    assert not tp_reached("long", 1e9, None)


def test_initial_stop_priority():
    v = Variant("X", atr_stop_mult=2.0)
    hist = pd.Series(np.ones(10))
    # Supertrend が最優先
    assert initial_stop("long", 100, v, 5, 80.0, hist, hist) == 80.0
    assert initial_stop("long", 100, v, 5, NAN, hist, hist) == 90.0
    assert initial_stop("short", 100, v, 5, NAN, hist, hist) == 110.0
    assert initial_stop("long", 100, Variant("B"), 5, NAN, hist, hist) is None


def test_initial_tp():
    v = Variant("X", tp_atr_mult=3.0)
    assert initial_tp("long", 100, v, 5) == 115
    assert initial_tp("short", 100, v, 5) == 85
    assert initial_tp("long", 100, Variant("B"), 5) is None


def test_fixed_atr_stop_follows_latest_atr():
    v = Variant("X", atr_stop_mult=2.0)
    pos = ExitState(side="long", entry_price=100, stop_px=90)
    assert manage_position(pos, _bar(105, 99), v, 3, NAN) is None
    assert pos.stop_px == 94


def test_stop_hit_on_bar_range():
    v = Variant("X", atr_stop_mult=1.0)
    pos = ExitState(side="long", entry_price=100, stop_px=95)
    assert manage_position(pos, _bar(101, 94), v, 5, NAN) == "stop"


def test_tp_without_trailing_exits():
    v = Variant("X", tp_atr_mult=2.0)
    pos = ExitState(side="long", entry_price=100, tp_px=110)
    assert manage_position(pos, _bar(111, 100), v, 5, NAN) == "tp"


def test_tp_then_trailing():
    v = Variant("X", tp_atr_mult=2.0, tp_trail_mult=1.0)
    pos = ExitState(side="long", entry_price=100, tp_px=110)
    assert manage_position(pos, _bar(112, 108), v, 5, NAN) is None
    assert pos.tp_hit and pos.trail_best == 112 and pos.stop_px == 107
    # 高値更新でストップが切り上がる
    manage_position(pos, _bar(120, 116), v, 5, NAN)
    assert pos.stop_px == 115
    assert manage_position(pos, _bar(118, 114), v, 5, NAN) == "trail_stop"


def test_chandelier_short():
    v = Variant("X", chandelier_mult=2.0)
    pos = ExitState(side="short", entry_price=100, trail_best=100)
    manage_position(pos, _bar(98, 90), v, 2, NAN)
    assert pos.trail_best == 90 and pos.stop_px == 94


def test_supertrend_line_becomes_stop():
    v = Variant("X", supertrend_mult=3.0)
    pos = ExitState(side="long", entry_price=100, stop_px=90)
    manage_position(pos, _bar(110, 105), v, 2, 97.0)
    assert pos.stop_px == 97.0
