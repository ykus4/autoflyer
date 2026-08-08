"""Persistent bot state and the equity history log.

The bot must survive a restart mid-position, so the open position lives in a
small JSON file that is rewritten atomically after every change.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("autoflyer.bot")

STATE_DEFAULT: dict[str, Any] = {
    "in_pos": False,
    "entry_price": None,
    "btc": 0.0,
    "entry_dt": None,
    "stop_px": None,
    "peak_cash": None,
}

FLAT_STATE: dict[str, Any] = {
    "in_pos": False,
    "entry_price": None,
    "btc": 0.0,
    "entry_dt": None,
    "stop_px": None,
}

_EQUITY_MAX_BYTES = 5 * 1024 * 1024  # 5MB per file


def load_state(state_file: Path) -> dict[str, Any]:
    """state.json を読み込む。壊れていればバックアップして初期状態に戻す。"""
    if not state_file.exists():
        return dict(STATE_DEFAULT)
    try:
        data = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        backup = state_file.with_suffix(".json.bak")
        shutil.copy2(state_file, backup)
        log.error("state.json corrupt (%s) — reset to default, backup: %s", e, backup)
        return dict(STATE_DEFAULT)

    for k, v in STATE_DEFAULT.items():
        data.setdefault(k, v)

    # 論理的な矛盾を検出して安全側（ノーポジション）に倒す
    if data["in_pos"] and (not data.get("entry_price") or float(data.get("btc", 0)) <= 0):
        log.warning(
            "State inconsistency: in_pos=True but entry_price=%s, btc=%s — resetting",
            data.get("entry_price"),
            data.get("btc"),
        )
        data.update(FLAT_STATE)
        save_state(state_file, data)
    elif not data["in_pos"] and float(data.get("btc", 0)) > 0:
        log.warning(
            "State inconsistency: in_pos=False but btc=%.8f — resetting btc to 0",
            float(data["btc"]),
        )
        data["btc"] = 0.0
        save_state(state_file, data)
    return data


def save_state(state_file: Path, state: dict[str, Any]) -> None:
    """一時ファイル経由で書き込み、途中で落ちても壊れないようにする。"""
    tmp = state_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(state_file)


def append_equity(equity_file: Path, equity: float) -> None:
    """資産推移を JSONL に追記し、肥大化したらローテートする。"""
    row = json.dumps({"dt": datetime.now(timezone.utc).isoformat(), "equity": equity})
    with equity_file.open("a", encoding="utf-8") as f:
        f.write(row + "\n")
    if equity_file.stat().st_size > _EQUITY_MAX_BYTES:
        rotated = equity_file.with_suffix(".jsonl.1")
        rotated.unlink(missing_ok=True)
        equity_file.rename(rotated)
