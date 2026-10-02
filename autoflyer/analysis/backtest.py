"""Bar-by-bar long/short backtester with stop-loss, take-profit and trailing stops.

Entry rules come from `trading.signals` and exit rules from `trading.exits`, so results
match the live bot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import (
    ADX_LEN,
    ATR_LEN,
    ATR_Q_LOOKBACK,
    DON_TERM,
    MA_SLOW,
    MACD_SLOW,
    REGIME_MA_LEN,
)
from ..timeframes import to_minutes
from ..trading.exits import ExitState, initial_stop, initial_tp, manage_position
from ..trading.fees import CostModel, make_cost_model
from ..trading.indicators import add_indicators, supertrend
from ..trading.signals import (
    entry_signals,
    exit_reason,
    exit_signals,
    long_ok,
    position_size,
    short_ok,
    sizing_fraction,
)
from ..trading.strategy import Variant

_WARMUP = max(MA_SLOW, REGIME_MA_LEN, MACD_SLOW, ADX_LEN, ATR_LEN, DON_TERM, ATR_Q_LOOKBACK) + 3


@dataclass(kw_only=True)
class _Position(ExitState):
    btc: float
    entry_dt: pd.Timestamp
    entry_fee_rate: float
    holding_cost: float = 0.0  # 保有中に支払った CFD の日次コスト累計

    def equity(self, cash: float, price: float) -> float:
        if self.is_long:
            return cash + self.btc * price
        return cash + (self.entry_price - price) * self.btc


@dataclass
class _Book:
    """現金・手数料ティア・約定履歴をまとめて管理する口座。"""

    cash: float
    strategy: str
    timeframe: str
    slippage_pct: float
    fees: CostModel = field(default_factory=lambda: make_cost_model("spot"))
    trades: list[dict] = field(default_factory=list)

    def open(
        self,
        side: str,
        px: float,
        stop_px: float | None,
        fill_dt: pd.Timestamp,
        signal_bar: pd.Series,
        v: Variant,
        cur_atr: float,
        sizing_frac: float,
    ) -> _Position:
        """約定価格 `px`（スリッページ適用済み）で成行エントリーする。"""
        tp_px = initial_tp(side, px, v, cur_atr)
        btc = position_size(self.cash, px, stop_px, self.fees.rate, v, sizing_frac)
        if side == "long":
            self.cash -= btc * px * (1.0 + self.fees.rate)
        else:
            self.cash -= btc * px * self.fees.rate  # ショート: 証拠金は別途管理、手数料のみ控除
        self.fees.record_fill(pd.Timestamp(fill_dt), btc * px)
        pos = _Position(
            side=side,
            btc=btc,
            entry_price=px,
            entry_dt=signal_bar["dt"],
            entry_fee_rate=self.fees.rate,
            stop_px=stop_px,
            tp_px=tp_px,
        )
        if v.chandelier_mult > 0:
            pos.trail_best = pos.favorable(signal_bar)
        return pos

    def accrue_holding(self, pos: _Position, price: float, bar_days: float) -> None:
        """1 バー分の保有コストを現金から差し引く（スポットでは 0）。"""
        cost = pos.btc * price * self.fees.holding_rate(pos.side) * bar_days
        pos.holding_cost += cost
        self.cash -= cost

    def close(self, pos: _Position, raw_px: float, exit_dt: pd.Timestamp, reason: str) -> None:
        """次足始値で成行決済し、約定履歴に 1 行追加する。"""
        exit_px = _apply_slippage(raw_px, pos.side, "exit", self.slippage_pct)
        notional_exit = pos.btc * exit_px
        fee_exit = notional_exit * self.fees.rate
        self.fees.record_fill(pd.Timestamp(exit_dt), notional_exit)

        total_fee = pos.btc * pos.entry_price * pos.entry_fee_rate + fee_exit + pos.holding_cost
        if pos.is_long:
            gross = (exit_px - pos.entry_price) * pos.btc
            self.cash += notional_exit - fee_exit
        else:
            gross = (pos.entry_price - exit_px) * pos.btc
            self.cash += gross - fee_exit

        net = gross - total_fee
        self.trades.append(
            {
                "strategy": self.strategy,
                "timeframe": self.timeframe,
                "side": pos.side,
                "exit_reason": reason,
                "entry_dt": pos.entry_dt,
                "exit_dt": exit_dt,
                "entry_price": pos.entry_price,
                "exit_price": exit_px,
                "btc": pos.btc,
                "gross_pnl_jpy": gross,
                "fee_jpy": total_fee,
                "holding_jpy": pos.holding_cost,
                "net_pnl_jpy": net,
                "cash_after": self.cash,
                "win": int(net > 0),
            }
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
    costs: str = "spot",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (trades_df, equity_df).

    train_end を指定するとウォークフォワード分割ができる。
    slippage_pct: 成行約定価格に対するスリッページ率（例: 0.001 = 0.1%）。
    bars_with_ind: 事前計算済みの add_indicators() 結果。複数バリアントで共有することで
                   同じ時間足内での重複計算を避けられる。None の場合は内部で計算する。
    costs: "spot"（現物の手数料ティア）または "cfd"（手数料 0 + 建玉の日次コスト）。

    バー i の終値でシグナルを判定し、約定はすべてバー i+1 の始値で行う。
    """
    ind = bars_with_ind if bars_with_ind is not None else add_indicators(bars)
    if not ind["dt"].is_monotonic_increasing:
        ind = ind.sort_values("dt")
    x = ind.reset_index(drop=True)
    v = variant

    # Supertrend トレーリングストップ用のラインを系列全体で 1 度だけ計算
    st_line = (
        supertrend(x, v.supertrend_mult).to_numpy()
        if v.supertrend_mult > 0
        else np.full(len(x), np.nan)
    )

    # ウォークフォワード: train_end より前は取引しない
    trade_start_idx = int((x["dt"] > train_end).argmax()) if train_end is not None else 0

    book = _Book(
        cash=float(start_cash),
        strategy=f"{v.name}/{tf_label}",
        timeframe=tf_label,
        slippage_pct=slippage_pct,
        fees=make_cost_model(costs),
    )
    bar_days = to_minutes(tf_label) / 1440
    pos: _Position | None = None
    cooldown_remaining = 0
    garch_cache: dict[tuple[int, float], float] = {}
    equity_rows: list[dict] = []

    for i in range(_WARMUP, len(x) - 1):
        cur = x.iloc[i]
        prev = x.iloc[i - 1]
        nxt = x.iloc[i + 1]

        if pd.isna(cur["close"]) or pd.isna(nxt["open"]):
            continue

        book.fees.step(pd.Timestamp(cur["dt"]))
        cur_atr = float(cur["atr"]) if pd.notna(cur.get("atr")) else 0.0
        st_val = float(st_line[i])
        next_open = float(nxt["open"])
        if cooldown_remaining > 0:
            cooldown_remaining -= 1

        cur_close = float(cur["close"])
        equity_rows.append(
            {
                "strategy": book.strategy,
                "timeframe": tf_label,
                "dt": cur["dt"],
                "equity": book.cash if pos is None else pos.equity(book.cash, cur_close),
                "in_pos": int(pos is not None),
                "fee_rate": book.fees.rate,
            }
        )

        # ストップ / 利確の管理（このバーを持ち越した分の保有コストを先に計上）
        if pos is not None:
            book.accrue_holding(pos, cur_close, bar_days)
            reason = manage_position(pos, cur, v, cur_atr, st_val)
            if reason is not None:
                book.close(pos, next_open, nxt["dt"], reason)
                pos = None
                if reason != "tp":
                    cooldown_remaining = v.cooldown_bars
                continue

        # エントリー / エグジットともバリアントのモードに従う
        entry_long_sig, entry_short_sig = entry_signals(cur, prev, v)
        exit_long_sig, exit_short_sig = exit_signals(cur, prev, v)

        # シグナルによるエグジット（train 期間でも決済はする）— 利確トレーリング中は除外
        if (
            pos is not None
            and not pos.tp_hit
            and (exit_long_sig if pos.is_long else exit_short_sig)
        ):
            book.close(pos, next_open, nxt["dt"], exit_reason(v))
            pos = None

        # エントリー（test 期間のみ、クールダウン中はスキップ）
        if pos is not None or i < trade_start_idx or cooldown_remaining != 0:
            continue

        close_hist = x["close"].iloc[: i + 1]  # 統計フィルター用の close 履歴
        if entry_long_sig and long_ok(cur, v, close_hist):
            side = "long"
        elif entry_short_sig and v.enable_short and short_ok(cur, v):
            side = "short"
        else:
            continue

        px = _apply_slippage(next_open, side, "entry", slippage_pct)
        stop_px = initial_stop(side, px, v, cur_atr, st_val, close_hist, x["atr"].iloc[: i + 1])
        sizing_frac = sizing_fraction(close_hist, v, garch_cache)
        pos = book.open(side, px, stop_px, nxt["dt"], cur, v, cur_atr, sizing_frac)

    return pd.DataFrame(book.trades), pd.DataFrame(equity_rows)


# =========================
# 内部ヘルパー
# =========================


def _apply_slippage(px: float, side: str, action: str, slippage_pct: float) -> float:
    """成行約定価格にスリッページを適用する。
    不利方向: ロングエントリー/ショートエグジット → 高め、逆 → 安め。
    """
    if slippage_pct <= 0:
        return px
    unfavorable = (side == "long" and action == "entry") or (side == "short" and action == "exit")
    return px * (1.0 + slippage_pct) if unfavorable else px * (1.0 - slippage_pct)
