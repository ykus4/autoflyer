"""Entry/exit decision rules shared by the backtester and the live bot.

Both engines must agree bar-for-bar, so every rule that decides *whether* to
trade and *how much* lives here rather than being written twice.

Callers pass a single indicator-annotated bar (`cur`) plus the history needed by
the statistical filters; nothing in this module touches an exchange or a clock.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from ..config import DON_EXIT_TERM, DON_TERM, MA_FAST, MA_SLOW
from .garch_sizing import garch_position_fraction
from .indicators import atr as compute_atr
from .stats_filters import breakout_zscore, hmm_regime, hurst_exponent, kelly_fraction
from .strategy import Variant

# Called with a human-readable reason when a filter rejects an entry.
RejectLogger = Callable[[str], None]


def _noop(_reason: str) -> None:
    pass


# =========================
# シグナル
# =========================


def compute_signal(bars: pd.DataFrame) -> tuple[bool, bool]:
    """
    終値確定済み直近 2 バーで MA クロスを判定する。
    最新バー（未確定）は除外する。
    Returns (cross_up, cross_down).
    """
    x = bars.iloc[:-1].copy().reset_index(drop=True)
    if len(x) < MA_SLOW + 2:
        return False, False

    fast = x["close"].rolling(MA_FAST).mean()
    slow = x["close"].rolling(MA_SLOW).mean()

    if pd.isna(fast.iloc[-1]) or pd.isna(fast.iloc[-2]):
        return False, False

    cross_up = bool(fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1])
    cross_down = bool(fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1])
    return cross_up, cross_down


def cross_up(cur: pd.Series, prev: pd.Series) -> bool:
    """MA クロスアップ（事前計算済みの ma_fast/ma_slow を使う）。"""
    if pd.isna(cur["ma_fast"]) or pd.isna(prev["ma_fast"]):
        return False
    return bool(prev["ma_fast"] <= prev["ma_slow"] and cur["ma_fast"] > cur["ma_slow"])


def cross_down(cur: pd.Series, prev: pd.Series) -> bool:
    """MA クロスダウン（事前計算済みの ma_fast/ma_slow を使う）。"""
    if pd.isna(cur["ma_fast"]) or pd.isna(prev["ma_fast"]):
        return False
    return bool(prev["ma_fast"] >= prev["ma_slow"] and cur["ma_fast"] < cur["ma_slow"])


def breakout_signals(cur: pd.Series) -> tuple[bool, bool]:
    """ドンチャンチャネルの上抜け/下抜けを判定する。Returns (up, down)."""
    up = bool(pd.notna(cur.get("don_high")) and float(cur["high"]) > float(cur["don_high"]))
    down = bool(pd.notna(cur.get("don_low")) and float(cur["low"]) < float(cur["don_low"]))
    return up, down


def entry_signals(cur: pd.Series, prev: pd.Series, v: Variant) -> tuple[bool, bool]:
    """バリアントのエントリーモードに応じた (long, short) シグナル。"""
    if v.breakout_entry:
        return breakout_signals(cur)
    return cross_up(cur, prev), cross_down(cur, prev)


# エグジット用チャネルの列名（term -> (安値列, 高値列)）
_EXIT_CHANNEL: dict[int, tuple[str, str]] = {
    DON_EXIT_TERM: ("don_exit_low", "don_exit_high"),
    DON_TERM: ("don_low", "don_high"),
}


def exit_signals(cur: pd.Series, prev: pd.Series, v: Variant) -> tuple[bool, bool]:
    """(exit_long, exit_short)。

    タートル式（`donchian_exit_term` 指定時）は逆方向チャネル割れで決済し、
    既定では MA クロスで決済する。
    """
    if v.donchian_exit_term > 0:
        low_col, high_col = _EXIT_CHANNEL[v.donchian_exit_term]
        exit_long = bool(pd.notna(cur.get(low_col)) and float(cur["low"]) < float(cur[low_col]))
        exit_short = bool(pd.notna(cur.get(high_col)) and float(cur["high"]) > float(cur[high_col]))
        return exit_long, exit_short
    return cross_down(cur, prev), cross_up(cur, prev)


def exit_reason(v: Variant) -> str:
    """シグナル決済の記録用ラベル。"""
    return "don_exit" if v.donchian_exit_term > 0 else "ma_cross"


def momentum(close_hist: pd.Series, lookback: int) -> float | None:
    """N バー前からの騰落率。履歴が足りなければ None。"""
    if len(close_hist) <= lookback:
        return None
    past = float(close_hist.iloc[-1 - lookback])
    if past <= 0:
        return None
    return float(close_hist.iloc[-1]) / past - 1.0


# =========================
# エントリーフィルター
# =========================


def long_ok(
    cur: pd.Series,
    v: Variant,
    close_hist: pd.Series | None = None,
    on_reject: RejectLogger = _noop,
) -> bool:
    """ロングエントリーの全フィルターを適用する。

    `close_hist` が None の統計フィルター（Hurst/HMM/z-score）はスキップされる。
    """
    if v.use_ma200_filter and int(cur.get("regime_up", 0)) != 1:
        on_reject("MA200 regime_up=0")
        return False
    if v.mom_lookback > 0 and close_hist is not None:
        mom = momentum(close_hist, v.mom_lookback)
        if mom is None or mom <= 0:
            on_reject(f"TSMOM({v.mom_lookback}) = {mom} <= 0")
            return False
    if v.bb_squeeze:
        width, q25 = cur.get("bb_width"), cur.get("bb_width_q25")
        if pd.isna(width) or pd.isna(q25) or float(width) > float(q25):
            on_reject("no Bollinger squeeze (band width above 25th pct)")
            return False
    if v.use_hmm_regime and close_hist is not None and hmm_regime(close_hist) != 2:
        on_reject("HMM regime is not bull")
        return False
    if v.rsi_min is not None and (pd.isna(cur.get("rsi")) or float(cur["rsi"]) < v.rsi_min):
        on_reject(f"RSI={cur.get('rsi')} < min={v.rsi_min}")
        return False
    if (
        v.atr_high_avoid
        and pd.notna(cur.get("atr_pct"))
        and pd.notna(cur.get("atrpct_q75"))
        and float(cur["atr_pct"]) > float(cur["atrpct_q75"])
    ):
        on_reject("ATR volatility too high")
        return False
    if v.require_don_break and int(cur.get("don_break_up", 0)) != 1:
        on_reject("no Donchian breakout")
        return False
    if v.adx_min is not None and (pd.isna(cur.get("adx")) or float(cur["adx"]) < v.adx_min):
        on_reject(f"ADX={cur.get('adx')} < min={v.adx_min}")
        return False
    if v.hurst_min > 0 and close_hist is not None:
        h = hurst_exponent(close_hist)
        if h < v.hurst_min:
            on_reject(f"Hurst={h:.3f} < min={v.hurst_min}")
            return False
    if v.zscore_min > 0 and close_hist is not None:
        z = breakout_zscore(close_hist)
        if z < v.zscore_min:
            on_reject(f"z-score={z:.2f} < min={v.zscore_min}")
            return False
    return True


def short_ok(cur: pd.Series, v: Variant, on_reject: RejectLogger = _noop) -> bool:
    """ショートエントリーのフィルターを適用する。"""
    if int(cur.get("regime_up", 1)) != 0:
        on_reject("regime_up=1, short not allowed")
        return False
    if v.adx_min is not None and (pd.isna(cur.get("adx")) or float(cur["adx"]) < v.adx_min):
        on_reject(f"ADX={cur.get('adx')} < min={v.adx_min}")
        return False
    return True


# =========================
# ストップ / サイジング
# =========================


def live_stop(
    bars: pd.DataFrame,
    entry_price: float,
    side: str,
    variant: Variant,
) -> float | None:
    """
    ライブボット用: 現在の ATR からストップ価格を計算して返す。
    atr_stop_mult が 0 なら None を返す。
    """
    if variant.atr_stop_mult <= 0:
        return None
    x = bars.iloc[:-1].copy().reset_index(drop=True)
    if x.empty or "atr" not in x.columns:
        x["atr"] = compute_atr(x)
    cur_atr = float(x["atr"].iloc[-1]) if pd.notna(x["atr"].iloc[-1]) else 0.0
    if cur_atr <= 0:
        return None
    return (
        entry_price - variant.atr_stop_mult * cur_atr
        if side == "long"
        else entry_price + variant.atr_stop_mult * cur_atr
    )


def sizing_fraction(
    close_hist: pd.Series,
    v: Variant,
    cache: dict[tuple[int, float], float] | None = None,
) -> float:
    """Kelly / GARCH による資金投入比率。どちらも無効なら 1.0（全額）。

    `cache` を渡すとバックテスト中の GARCH 再フィットを避けられる。
    """
    if v.use_kelly_sizing:
        return kelly_fraction(close_hist)
    if v.garch_target_vol <= 0:
        return 1.0
    key = (len(close_hist), v.garch_target_vol)
    if cache is None:
        return garch_position_fraction(close_hist, target_vol=v.garch_target_vol)
    if key not in cache:
        cache[key] = garch_position_fraction(close_hist, target_vol=v.garch_target_vol)
    return cache[key]


def position_size(
    cash: float,
    px: float,
    stop_px: float | None,
    fee_rate: float,
    v: Variant,
    sizing_frac: float = 1.0,
) -> float:
    """投入可能 BTC 数量。risk_pct が設定されていればストップ幅でリスクを制限する。"""
    max_btc = cash * sizing_frac / (px * (1.0 + fee_rate))
    if v.risk_pct > 0 and stop_px is not None and px > 0:
        stop_width = abs(px - stop_px)
        if stop_width > 0:
            return min((cash * sizing_frac * v.risk_pct / 100.0) / stop_width, max_btc)
    return max_btc
