"""Position exit rules (stop-loss, take-profit, trailing) shared by the backtester and the live bot.

`manage_position()` is called once per *confirmed* bar while a position is open.
It moves the stop (chandelier / Supertrend / fixed ATR / post-TP trailing) and
reports whether the bar's range hit the stop or the take-profit target.

Like `signals`, nothing here touches an exchange or a clock.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .stats_filters import mae_optimal_stop
from .strategy import Variant

LONG = "long"
SHORT = "short"


@dataclass(kw_only=True)
class ExitState:
    """決済判定に必要なポジションの状態。"""

    side: str
    entry_price: float
    stop_px: float | None = None
    trail_best: float | None = None
    tp_px: float | None = None  # 利確ターゲット
    tp_hit: bool = False  # 利確到達フラグ（トレーリング移行用）

    @property
    def is_long(self) -> bool:
        return self.side == LONG

    def favorable(self, bar: pd.Series) -> float:
        """含み益方向の極値（ロング=高値 / ショート=安値）。"""
        return float(bar["high"] if self.is_long else bar["low"])

    def adverse(self, bar: pd.Series) -> float:
        """含み損方向の極値（ロング=安値 / ショート=高値）。"""
        return float(bar["low"] if self.is_long else bar["high"])


def profit_side(side: str, ref: float, dist: float) -> float:
    """`ref` から含み益方向へ `dist` 離れた価格。"""
    return ref + dist if side == LONG else ref - dist


def loss_side(side: str, ref: float, dist: float) -> float:
    """`ref` から含み損方向へ `dist` 離れた価格。"""
    return ref - dist if side == LONG else ref + dist


def stop_hit(side: str, price: float, stop_px: float | None) -> bool:
    """価格がストップに到達したか（ロングは下抜け、ショートは上抜け）。"""
    if stop_px is None:
        return False
    return price <= stop_px if side == LONG else price >= stop_px


def tp_reached(side: str, price: float, tp_px: float | None) -> bool:
    """価格が利確ターゲットに到達したか。"""
    if tp_px is None:
        return False
    return price >= tp_px if side == LONG else price <= tp_px


def initial_stop(
    side: str,
    px: float,
    v: Variant,
    cur_atr: float,
    st_val: float,
    close_hist: pd.Series,
    atr_hist: pd.Series,
) -> float | None:
    """エントリー時のストップ。優先順位は Supertrend > MAE（ロングのみ）> 固定 ATR。"""
    if not np.isnan(st_val):
        return st_val
    if side == LONG and v.use_mae_stop and cur_atr > 0:
        return px - mae_optimal_stop(close_hist, atr_hist) * cur_atr
    if v.atr_stop_mult > 0 and cur_atr > 0:
        return loss_side(side, px, v.atr_stop_mult * cur_atr)
    return None


def initial_tp(side: str, px: float, v: Variant, cur_atr: float) -> float | None:
    """エントリー時の利確ターゲット（無効なら None）。"""
    if v.tp_atr_mult > 0 and cur_atr > 0:
        return profit_side(side, px, v.tp_atr_mult * cur_atr)
    return None


def _trail(pos: ExitState, bar: pd.Series, dist: float) -> None:
    """ピーク（ロング=最高値 / ショート=最安値）を更新し、そこから `dist` 離してストップを置く。"""
    extreme = pos.favorable(bar)
    best = pos.trail_best or extreme
    pos.trail_best = max(best, extreme) if pos.is_long else min(best, extreme)
    pos.stop_px = loss_side(pos.side, pos.trail_best, dist)


def manage_position(
    pos: ExitState, bar: pd.Series, v: Variant, cur_atr: float, st_val: float
) -> str | None:
    """確定バーでストップ/利確を更新し、決済すべきなら理由を返す。

    利確到達前は チャンデリア → Supertrend → 固定 ATR の順でストップを引き直す
    （後に書いたものが優先）。利確到達後は `tp_trail_mult` のトレーリングに切り替える。
    戻り値は "tp" / "stop" / "trail_stop" / None。
    """
    if not pos.tp_hit:
        if v.chandelier_mult > 0 and cur_atr > 0:
            _trail(pos, bar, v.chandelier_mult * cur_atr)
        if not np.isnan(st_val):
            pos.stop_px = st_val
        # 固定 ATR ストップは毎バー最新 ATR で更新（エントリー価格は固定）
        if v.atr_stop_mult > 0 and v.chandelier_mult == 0 and cur_atr > 0:
            pos.stop_px = loss_side(pos.side, pos.entry_price, v.atr_stop_mult * cur_atr)

        fav = pos.favorable(bar)
        if tp_reached(pos.side, fav, pos.tp_px):
            if not trails_after_tp(v, cur_atr):
                return "tp"  # トレーリングなし → 即利確決済
            pos.tp_hit = True  # 利確到達 → ここからトレーリング
            pos.trail_best = fav

    if pos.tp_hit and trails_after_tp(v, cur_atr):
        _trail(pos, bar, v.tp_trail_mult * cur_atr)

    if stop_hit(pos.side, pos.adverse(bar), pos.stop_px):
        return "trail_stop" if pos.tp_hit else "stop"
    return None


def trails_after_tp(v: Variant, cur_atr: float) -> bool:
    """利確到達後にトレーリングへ移行するか（False なら利確で即決済）。"""
    return v.tp_trail_mult > 0 and cur_atr > 0
