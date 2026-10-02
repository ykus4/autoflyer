"""Parameter grid search and walk-forward validation.

- `grid`: run every combination of the given Variant fields on one timeframe and
  rank by a metric (default: Calmar).
- `walk_forward`: slide a train/test window over the data. In each window the
  best combination on the *train* period is chosen and then evaluated on the
  following *test* period, so the reported test results are out-of-sample.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..config import START_CASH_JPY
from ..trading.indicators import add_indicators
from ..trading.strategy import Variant
from . import backtest, data
from .metrics import METRIC_COLUMNS, equity_metrics

_VARIANT_FIELDS = {f.name for f in dataclasses.fields(Variant)} - {"name"}


def _cast(base: Variant, field: str, raw: str) -> object:
    current = getattr(base, field)
    if isinstance(current, bool):
        if raw.lower() in ("1", "true", "yes", "on"):
            return True
        if raw.lower() in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"{field}: bool 値ではありません: {raw}")
    if isinstance(current, int):
        return int(raw)
    if raw.lower() in ("none", "-"):
        return None
    return float(raw)


def parse_grid(base: Variant, specs: list[str]) -> dict[str, list[object]]:
    """`["atr_stop_mult=1.0,1.5", "garch_target_vol=0.3,0.4"]` を {field: [値...]} にする。"""
    grid: dict[str, list[object]] = {}
    for spec in specs:
        field, sep, values = spec.partition("=")
        field = field.strip()
        if not sep or not values:
            raise SystemExit(f"--param は field=v1,v2,... の形式で指定してください: {spec}")
        if field not in _VARIANT_FIELDS:
            raise SystemExit(f"Unknown Variant field: {field}")
        grid[field] = [_cast(base, field, v.strip()) for v in values.split(",")]
    return grid


def expand(base: Variant, grid: dict[str, list[object]]) -> list[Variant]:
    """グリッドの全組み合わせを Variant にする。名前に変えたパラメータを付ける。"""
    if not grid:
        return [base]
    keys = list(grid)
    variants = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        params: dict[str, Any] = dict(zip(keys, combo, strict=True))
        label = ",".join(f"{k}={v}" for k, v in params.items())
        variants.append(dataclasses.replace(base, name=f"{base.name}[{label}]", **params))
    return variants


@dataclass(frozen=True)
class Dataset:
    """1 つの時間足のバーと、それに対する指標（全期間で 1 度だけ計算する）。"""

    timeframe: str
    bars: pd.DataFrame
    ind: pd.DataFrame

    @classmethod
    def load(cls, csv: str, timeframe: str) -> Dataset:
        bars = data.resample(data.load_csv(csv), timeframe)
        return cls(timeframe, bars, add_indicators(bars))

    def evaluate(
        self,
        v: Variant,
        start: pd.Timestamp | None,
        end: pd.Timestamp | None,
        costs: str,
        slippage_pct: float,
    ) -> dict[str, float]:
        """(start, end] の区間で取引させたときの指標。

        指標は因果的なので、全期間で計算したものを end で切っても先読みにならない。
        start より前はウォームアップとして使う（取引はしない）。
        """
        bars, ind = self.bars, self.ind
        if end is not None:
            mask = (bars["dt"] <= end).to_numpy()
            bars, ind = bars[mask], ind[mask]
        trades, equity = backtest.run(
            bars,
            start_cash=START_CASH_JPY,
            tf_label=self.timeframe,
            variant=v,
            train_end=start,
            slippage_pct=slippage_pct,
            bars_with_ind=ind,
            costs=costs,
        )
        if equity.empty:
            return {**dict.fromkeys(METRIC_COLUMNS, float("nan")), "trades": 0}
        eq = equity.sort_values("dt")
        if start is not None:
            eq = eq[eq["dt"] > start]
        return {**equity_metrics(eq["equity"], self.timeframe), "trades": len(trades)}


def _score(row: Mapping[str, object], metric: str) -> float:
    value = row.get(metric)
    if not isinstance(value, int | float) or math.isnan(value):
        return -math.inf
    return float(value)


def grid(
    dataset: Dataset,
    variants: list[Variant],
    *,
    metric: str = "calmar",
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    costs: str = "spot",
    slippage_pct: float = 0.0,
) -> pd.DataFrame:
    """全バリアントを評価し、`metric` の降順で返す。"""
    rows = [
        {"variant": v.name, **dataset.evaluate(v, start, end, costs, slippage_pct)}
        for v in variants
    ]
    df = pd.DataFrame(rows)
    order = sorted(range(len(rows)), key=lambda i: _score(rows[i], metric), reverse=True)
    return df.iloc[order].reset_index(drop=True)


def windows(
    dts: pd.Series, train_days: int, test_days: int, warmup_bars: int
) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """(train_start, test_start, test_end) のリスト。テスト区間を test_days ずつずらす。"""
    first = pd.Timestamp(dts.iloc[min(warmup_bars, len(dts) - 1)])
    last = pd.Timestamp(dts.iloc[-1])
    out = []
    train_start = first
    while True:
        test_start = train_start + pd.Timedelta(days=train_days)
        test_end = test_start + pd.Timedelta(days=test_days)
        if test_end > last:
            break
        out.append((train_start, test_start, test_end))
        train_start += pd.Timedelta(days=test_days)
    return out


def walk_forward(
    dataset: Dataset,
    variants: list[Variant],
    *,
    train_days: int,
    test_days: int,
    metric: str = "calmar",
    costs: str = "spot",
    slippage_pct: float = 0.0,
) -> pd.DataFrame:
    """各ウィンドウで学習期間ベストを選び、直後のテスト期間で評価した結果の表。"""
    rows = []
    for train_start, test_start, test_end in windows(
        dataset.bars["dt"], train_days, test_days, backtest.WARMUP_BARS
    ):
        ranked = grid(
            dataset,
            variants,
            metric=metric,
            start=train_start,
            end=test_start,
            costs=costs,
            slippage_pct=slippage_pct,
        )
        best_name = str(ranked.iloc[0]["variant"])
        best = next(v for v in variants if v.name == best_name)
        test = dataset.evaluate(best, test_start, test_end, costs, slippage_pct)
        rows.append(
            {
                "train_start": train_start.date(),
                "test_start": test_start.date(),
                "test_end": test_end.date(),
                "best": best_name,
                f"train_{metric}": ranked.iloc[0][metric],
                **{f"test_{k}": v for k, v in test.items()},
            }
        )
    return pd.DataFrame(rows)


def summarize_walk_forward(wf: pd.DataFrame) -> dict[str, float]:
    """テスト期間をつなげた（複利の）アウトオブサンプル成績。"""
    if wf.empty:
        return {}
    rets = wf["test_total_return_pct"].fillna(0) / 100
    compounded = (float(np.prod(1 + rets.to_numpy(dtype=float))) - 1) * 100
    return {
        "windows": len(wf),
        "oos_return_pct": compounded,
        "positive_windows": int((rets > 0).sum()),
        "worst_window_dd_pct": float(wf["test_max_dd_pct"].max()),
    }
