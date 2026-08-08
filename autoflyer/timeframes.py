"""Timeframe label parsing.

Timeframes are written as `<n><unit>` with unit `H` (hours) or `D` (days) —
e.g. `1H`, `3H`, `12H`, `1D`, `3D`. This module is the single place that knows
how to turn such a label into a pandas resample rule or a duration.
"""

from __future__ import annotations

import re

_TF_RE = re.compile(r"^(\d*)([HD])$")
_UNIT_MINUTES = {"H": 60, "D": 1440}


def parse(tf: str) -> tuple[int, str]:
    """Split a timeframe label into (count, unit). A bare unit means 1 (`D` -> 1 day)."""
    m = _TF_RE.match(tf.upper().strip())
    if m is None:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return int(m.group(1) or 1), m.group(2)


def to_pandas_rule(tf: str) -> str:
    """Return the pandas `resample()` rule for a timeframe (e.g. `3H` -> `3h`)."""
    count, unit = parse(tf)
    return f"{count}h" if unit == "H" else f"{count}D"


def to_minutes(tf: str) -> int:
    """Return the timeframe duration in minutes."""
    count, unit = parse(tf)
    return count * _UNIT_MINUTES[unit]
