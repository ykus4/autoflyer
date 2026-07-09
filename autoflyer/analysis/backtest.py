"""MA-cross long/short backtester with stop-loss and trailing stop support."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import (
    ADX_LEN,
    ATR_LEN,
    ATR_Q_LOOKBACK,
    DON_TERM,
    MA_FAST,
    MA_SLOW,
    MACD_SLOW,
    REGIME_MA_LEN,
)
from ..trading.fees import FeeTierModel
from ..trading.indicators import add_indicators, supertrend
from ..trading.stats_filters import (
    breakout_zscore,
    hmm_regime,
    hurst_exponent,
    kelly_fraction,
    mae_optimal_stop,
)
from ..trading.strategy import Variant

_WARMUP = max(MA_SLOW, REGIME_MA_LEN, MACD_SLOW, ADX_LEN, ATR_LEN, DON_TERM, ATR_Q_LOOKBACK) + 3


@dataclass
class _Position:
    side: str
    btc: float
    entry_price: float
    entry_dt: object
    entry_fee_rate: float
    stop_px: float | None = None
    trail_best: float | None = None
    tp_px: float | None = None  # 利確ターゲット
    tp_hit: bool = False  # 利確到達フラグ（トレーリング移行用）


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


def compute_live_stop(
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
        from ..trading.indicators import atr as calc_atr

        x["atr"] = calc_atr(x)
    cur_atr = float(x["atr"].iloc[-1]) if pd.notna(x["atr"].iloc[-1]) else 0.0
    if cur_atr <= 0:
        return None
    return (
        entry_price - variant.atr_stop_mult * cur_atr
        if side == "long"
        else entry_price + variant.atr_stop_mult * cur_atr
    )


def run(
    bars: pd.DataFrame,
    *,
    start_cash: float,
    tf_label: str,
    variant: Variant,
    train_end: pd.Timestamp | None = None,
    slippage_pct: float = 0.0,
    bars_with_ind: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (trades_df, equity_df).

    train_end を指定するとウォークフォワード分割ができる。
    slippage_pct: 成行約定価格に対するスリッページ率（例: 0.001 = 0.1%）。
    bars_with_ind: 事前計算済みの add_indicators() 結果。複数バリアントで共有することで
                   同じ時間足内での重複計算を避けられる。None の場合は内部で計算する。
    """
    # add_indicators() は ma_fast/ma_slow を含む全指標を持つため、複数バリアントで
    # 共有すれば時間足ごとの重複計算（ローリング + merge_asof）を丸ごと省ける。
    ind = bars_with_ind if bars_with_ind is not None else add_indicators(bars)
    if not ind["dt"].is_monotonic_increasing:
        ind = ind.sort_values("dt")
    x = ind.reset_index(drop=True)

    # Supertrend トレーリングストップ用のラインを系列全体で 1 度だけ計算
    st_line = (
        supertrend(x, variant.supertrend_mult).to_numpy() if variant.supertrend_mult > 0 else None
    )

    # ウォークフォワード: train_end より前は取引しない
    trade_start_idx = int((x["dt"] > train_end).argmax()) if train_end is not None else 0

    fees = FeeTierModel()
    cash = float(start_cash)
    pos: _Position | None = None
    cooldown_remaining: int = 0
    garch_cache: dict[tuple[int, float], float] = {}

    trades: list[dict] = []
    equity_rows: list[dict] = []
    v = variant
    strat_name = f"{v.name}/{tf_label}"

    for i in range(_WARMUP, len(x) - 1):
        cur = x.iloc[i]
        prev = x.iloc[i - 1]
        nxt = x.iloc[i + 1]

        if pd.isna(cur["close"]) or pd.isna(nxt["open"]):
            continue

        fees.step(pd.Timestamp(cur["dt"]))
        cur_atr = float(cur["atr"]) if pd.notna(cur.get("atr")) else 0.0
        cur_close = float(cur["close"])
        if cooldown_remaining > 0:
            cooldown_remaining -= 1

        equity_rows.append(
            {
                "strategy": strat_name,
                "timeframe": tf_label,
                "dt": cur["dt"],
                "equity": _equity(cash, pos, cur_close),
                "in_pos": int(pos is not None),
                "fee_rate": fees.rate,
            }
        )

        # チャンデリア追跡ストップ更新（バーごとにピークを更新）
        if pos is not None and v.chandelier_mult > 0 and cur_atr > 0 and not pos.tp_hit:
            if pos.side == "long":
                pos.trail_best = max(pos.trail_best or float(cur["high"]), float(cur["high"]))
                pos.stop_px = pos.trail_best - v.chandelier_mult * cur_atr
            else:
                pos.trail_best = min(pos.trail_best or float(cur["low"]), float(cur["low"]))
                pos.stop_px = pos.trail_best + v.chandelier_mult * cur_atr

        # Supertrend トレーリングストップ更新（フリップ済みラインをそのまま採用）
        if pos is not None and st_line is not None and not pos.tp_hit:
            st_val = st_line[i]
            if not np.isnan(st_val):
                pos.stop_px = float(st_val)

        # 固定 ATR ストップも毎バー最新 ATR で更新（エントリー価格は固定）
        if (
            pos is not None
            and v.atr_stop_mult > 0
            and v.chandelier_mult == 0
            and cur_atr > 0
            and not pos.tp_hit
        ):
            if pos.side == "long":
                pos.stop_px = pos.entry_price - v.atr_stop_mult * cur_atr
            else:
                pos.stop_px = pos.entry_price + v.atr_stop_mult * cur_atr

        # 利確到達判定 & トレーリング移行
        if pos is not None and pos.tp_px is not None and not pos.tp_hit:
            tp_triggered = (pos.side == "long" and float(cur["high"]) >= pos.tp_px) or (
                pos.side == "short" and float(cur["low"]) <= pos.tp_px
            )
            if tp_triggered:
                if v.tp_trail_mult > 0 and cur_atr > 0:
                    # 利確到達 → トレーリングストップに移行
                    pos.tp_hit = True
                    if pos.side == "long":
                        pos.trail_best = float(cur["high"])
                        pos.stop_px = pos.trail_best - v.tp_trail_mult * cur_atr
                    else:
                        pos.trail_best = float(cur["low"])
                        pos.stop_px = pos.trail_best + v.tp_trail_mult * cur_atr
                else:
                    # トレーリングなし → 即利確決済
                    exit_px = _apply_slippage(float(nxt["open"]), pos.side, "exit", slippage_pct)
                    cash = _close(
                        trades, pos, exit_px, nxt["dt"], fees, cash, strat_name, tf_label, "tp"
                    )
                    pos = None
                    continue

        # 利確後トレーリング更新
        if pos is not None and pos.tp_hit and v.tp_trail_mult > 0 and cur_atr > 0:
            if pos.side == "long":
                pos.trail_best = max(pos.trail_best or float(cur["high"]), float(cur["high"]))
                pos.stop_px = pos.trail_best - v.tp_trail_mult * cur_atr
            else:
                pos.trail_best = min(pos.trail_best or float(cur["low"]), float(cur["low"]))
                pos.stop_px = pos.trail_best + v.tp_trail_mult * cur_atr

        # ストップ発動
        if pos is not None and pos.stop_px is not None:
            hit = (pos.side == "long" and float(cur["low"]) <= pos.stop_px) or (
                pos.side == "short" and float(cur["high"]) >= pos.stop_px
            )
            if hit:
                reason = "trail_stop" if pos.tp_hit else "stop"
                exit_px = _apply_slippage(float(nxt["open"]), pos.side, "exit", slippage_pct)
                cash = _close(
                    trades, pos, exit_px, nxt["dt"], fees, cash, strat_name, tf_label, reason
                )
                pos = None
                cooldown_remaining = v.cooldown_bars
                continue

        cross_up = _cross_up(cur, prev)
        cross_down = _cross_down(cur, prev)

        # ブレイクアウトエントリーモードのシグナル
        if v.breakout_entry:
            breakout_up = bool(
                pd.notna(cur.get("don_high")) and float(cur["high"]) > float(cur["don_high"])
            )
            breakout_down = bool(
                pd.notna(cur.get("don_low")) and float(cur["low"]) < float(cur["don_low"])
            )
        else:
            breakout_up = False
            breakout_down = False

        # クロスによるエグジット（train 期間でも決済はする）— 利確トレーリング中は除外
        if (
            pos is not None
            and not pos.tp_hit
            and ((pos.side == "long" and cross_down) or (pos.side == "short" and cross_up))
        ):
            exit_px = _apply_slippage(float(nxt["open"]), pos.side, "exit", slippage_pct)
            cash = _close(
                trades, pos, exit_px, nxt["dt"], fees, cash, strat_name, tf_label, "ma_cross"
            )
            pos = None

        # エントリー（test 期間のみ、クールダウン中はスキップ）
        if pos is None and i >= trade_start_idx and cooldown_remaining == 0:
            # エントリーシグナル: ブレイクアウトモードならブレイクアウト、通常はMAクロス
            entry_long = breakout_up if v.breakout_entry else cross_up
            entry_short = breakout_down if v.breakout_entry else cross_down

            # 統計フィルター用の close 履歴
            close_hist = x["close"].iloc[: i + 1]

            if entry_long and _long_ok(cur, v, close_hist):
                raw_px = float(nxt["open"])
                px = _apply_slippage(raw_px, "long", "entry", slippage_pct)

                # Supertrend or MAEベース or 固定ATRストップ（Supertrend が最優先）
                if st_line is not None and not np.isnan(st_line[i]):
                    stop_px = float(st_line[i])
                elif v.use_mae_stop and cur_atr > 0:
                    atr_hist = x["atr"].iloc[: i + 1] if "atr" in x.columns else None
                    if atr_hist is not None:
                        mae_mult = mae_optimal_stop(close_hist, atr_hist)
                        stop_px = px - mae_mult * cur_atr
                    else:
                        stop_px = px - 1.5 * cur_atr
                elif v.atr_stop_mult > 0 and cur_atr > 0:
                    stop_px = px - v.atr_stop_mult * cur_atr
                else:
                    stop_px = None

                tp_px = (
                    (px + v.tp_atr_mult * cur_atr) if v.tp_atr_mult > 0 and cur_atr > 0 else None
                )

                # サイジング: Kelly or GARCH
                if v.use_kelly_sizing:
                    sizing_frac = kelly_fraction(close_hist)
                else:
                    sizing_frac = _garch_fraction(close_hist, v, garch_cache)

                btc = _size(cash, px, stop_px, fees.rate, v, sizing_frac)
                cash -= btc * px * (1.0 + fees.rate)
                fees.record_fill(pd.Timestamp(nxt["dt"]), btc * px)
                pos = _Position(
                    side="long",
                    btc=btc,
                    entry_price=px,
                    entry_dt=cur["dt"],
                    entry_fee_rate=fees.rate,
                    stop_px=stop_px,
                    trail_best=float(cur["high"]) if v.chandelier_mult > 0 else None,
                    tp_px=tp_px,
                )

            elif entry_short and v.enable_short and _short_ok(cur, v):
                raw_px = float(nxt["open"])
                px = _apply_slippage(raw_px, "short", "entry", slippage_pct)
                if st_line is not None and not np.isnan(st_line[i]):
                    stop_px = float(st_line[i])
                elif v.atr_stop_mult > 0 and cur_atr > 0:
                    stop_px = px + v.atr_stop_mult * cur_atr
                else:
                    stop_px = None
                tp_px = (
                    (px - v.tp_atr_mult * cur_atr) if v.tp_atr_mult > 0 and cur_atr > 0 else None
                )
                garch_frac = _garch_fraction(x["close"].iloc[: i + 1], v, garch_cache)
                btc = _size(cash, px, stop_px, fees.rate, v, garch_frac)
                cash -= btc * px * fees.rate  # ショート: 証拠金は別途管理、手数料のみ控除
                fees.record_fill(pd.Timestamp(nxt["dt"]), btc * px)
                pos = _Position(
                    side="short",
                    btc=btc,
                    entry_price=px,
                    entry_dt=cur["dt"],
                    entry_fee_rate=fees.rate,
                    stop_px=stop_px,
                    trail_best=float(cur["low"]) if v.chandelier_mult > 0 else None,
                    tp_px=tp_px,
                )

    return pd.DataFrame(trades), pd.DataFrame(equity_rows)


# =========================
# 内部ヘルパー
# =========================


def _equity(cash: float, pos: _Position | None, price: float) -> float:
    if pos is None:
        return cash
    if pos.side == "long":
        return cash + pos.btc * price
    return cash + (pos.entry_price - price) * pos.btc


def _close(
    trades: list[dict],
    pos: _Position,
    exit_px: float,
    exit_dt: object,
    fees: FeeTierModel,
    cash: float,
    strat_name: str,
    tf_label: str,
    exit_reason: str,
) -> float:
    notional_exit = pos.btc * exit_px
    fee_exit = notional_exit * fees.rate
    fees.record_fill(pd.Timestamp(exit_dt), notional_exit)

    if pos.side == "long":
        gross = (exit_px - pos.entry_price) * pos.btc
        total_fee = pos.btc * pos.entry_price * pos.entry_fee_rate + fee_exit
        cash += notional_exit - fee_exit
    else:
        gross = (pos.entry_price - exit_px) * pos.btc
        total_fee = pos.btc * pos.entry_price * pos.entry_fee_rate + fee_exit
        cash += gross - fee_exit

    net = gross - total_fee
    trades.append(
        {
            "strategy": strat_name,
            "timeframe": tf_label,
            "side": pos.side,
            "exit_reason": exit_reason,
            "entry_dt": pos.entry_dt,
            "exit_dt": exit_dt,
            "entry_price": pos.entry_price,
            "exit_price": exit_px,
            "btc": pos.btc,
            "gross_pnl_jpy": gross,
            "fee_jpy": total_fee,
            "net_pnl_jpy": net,
            "cash_after": cash,
            "win": int(net > 0),
        }
    )
    return cash


def _cross_up(cur: pd.Series, prev: pd.Series) -> bool:
    if pd.isna(cur["ma_fast"]) or pd.isna(prev["ma_fast"]):
        return False
    return prev["ma_fast"] <= prev["ma_slow"] and cur["ma_fast"] > cur["ma_slow"]


def _cross_down(cur: pd.Series, prev: pd.Series) -> bool:
    if pd.isna(cur["ma_fast"]) or pd.isna(prev["ma_fast"]):
        return False
    return prev["ma_fast"] >= prev["ma_slow"] and cur["ma_fast"] < cur["ma_slow"]


def _long_ok(cur: pd.Series, v: Variant, close_hist: pd.Series | None = None) -> bool:
    if v.use_ma200_filter and int(cur.get("regime_up", 0)) != 1:
        return False
    if v.use_hmm_regime and close_hist is not None:
        regime = hmm_regime(close_hist)
        if regime != 2:  # bull only
            return False
    if v.rsi_min is not None and (pd.isna(cur.get("rsi")) or float(cur["rsi"]) < v.rsi_min):
        return False
    if (
        v.atr_high_avoid
        and pd.notna(cur.get("atr_pct"))
        and pd.notna(cur.get("atrpct_q75"))
        and float(cur["atr_pct"]) > float(cur["atrpct_q75"])
    ):
        return False
    if v.require_don_break and int(cur.get("don_break_up", 0)) != 1:
        return False
    if v.adx_min is not None and (pd.isna(cur.get("adx")) or float(cur["adx"]) < v.adx_min):
        return False
    # Hurst exponent filter
    if v.hurst_min > 0 and close_hist is not None:
        h = hurst_exponent(close_hist)
        if h < v.hurst_min:
            return False
    # Breakout z-score filter
    if v.zscore_min > 0 and close_hist is not None:
        z = breakout_zscore(close_hist)
        if z < v.zscore_min:
            return False
    return True


def _short_ok(cur: pd.Series, v: Variant) -> bool:
    if int(cur.get("regime_up", 1)) != 0:
        return False
    return not (
        v.adx_min is not None and (pd.isna(cur.get("adx")) or float(cur["adx"]) < v.adx_min)
    )


def _apply_slippage(px: float, side: str, action: str, slippage_pct: float) -> float:
    """成行約定価格にスリッページを適用する。
    不利方向: ロングエントリー/ショートエグジット → 高め、逆 → 安め。
    """
    if slippage_pct <= 0:
        return px
    unfavorable = (side == "long" and action == "entry") or (side == "short" and action == "exit")
    return px * (1.0 + slippage_pct) if unfavorable else px * (1.0 - slippage_pct)


def _garch_fraction(
    close: pd.Series,
    v: Variant,
    cache: dict[tuple[int, float], float],
) -> float:
    if v.garch_target_vol <= 0:
        return 1.0
    from ..trading.garch_sizing import garch_position_fraction

    key = (len(close), v.garch_target_vol)
    if key not in cache:
        cache[key] = garch_position_fraction(close, target_vol=v.garch_target_vol)
    return cache[key]


def _size(
    cash: float,
    px: float,
    stop_px: float | None,
    fee_rate: float,
    v: Variant,
    garch_frac: float = 1.0,
) -> float:
    max_btc = cash * garch_frac / (px * (1.0 + fee_rate))
    if v.risk_pct > 0 and stop_px is not None and px > 0:
        stop_width = abs(px - stop_px)
        if stop_width > 0:
            btc = (cash * garch_frac * v.risk_pct / 100.0) / stop_width
            return min(btc, max_btc)
    return max_btc
