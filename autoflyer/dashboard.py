"""Lightweight web dashboard for monitoring the live bot.

Reads the same state/equity files the bot writes, plus live ticker and balance
from bitFlyer. Basic auth is enabled only when `DASHBOARD_USER` is set.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from .trading.client import BitFlyerClient

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="autoflyer dashboard")
_security = HTTPBasic()
_security_dep = Depends(_security)


@dataclass
class DashboardSettings:
    """ダッシュボードの実行時設定。`configure()` で差し込む。"""

    state_file: Path = field(default_factory=lambda: Path("var/state.json"))
    log_file: Path | None = None
    symbol: str = "FX_BTC_JPY"
    api_key: str = ""
    api_secret: str = ""
    user: str = ""
    password: str = ""

    @property
    def equity_file(self) -> Path:
        return self.state_file.with_name("equity.jsonl")


_settings = DashboardSettings()
_client = BitFlyerClient("", "")


def configure(settings: DashboardSettings) -> None:
    global _settings, _client
    _settings = settings
    _client = BitFlyerClient(settings.api_key, settings.api_secret)


def _auth(credentials: HTTPBasicCredentials = _security_dep) -> str:
    if not _settings.user:
        return credentials.username
    ok = secrets.compare_digest(credentials.username, _settings.user) and secrets.compare_digest(
        credentials.password, _settings.password
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _read_state() -> dict:
    if not _settings.state_file.exists():
        return {}
    try:
        return json.loads(_settings.state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _read_logs(n: int = 80) -> list[str]:
    log_file = _settings.log_file
    if log_file is None or not log_file.exists():
        return []
    try:
        return log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


@app.get("/api/state")
def api_state(_: str = Depends(_auth)) -> dict:
    return _read_state()


@app.get("/api/ticker")
def api_ticker(_: str = Depends(_auth)) -> dict:
    """現在価格・含み損益・残高を返す。"""
    state = _read_state()
    result: dict[str, Any] = {
        "last_price": None,
        "bid": None,
        "ask": None,
        "unrealized_pnl": None,
        "unrealized_pnl_pct": None,
        "jpy_balance": None,
        "btc_balance": None,
        "error": None,
    }

    try:
        ticker = _client.fetch_ticker(_settings.symbol)
        last = float(ticker["ltp"])
        result["last_price"] = last
        result["bid"] = ticker.get("best_bid")
        result["ask"] = ticker.get("best_ask")

        # 含み損益（ポジション保有中のみ）
        if state.get("in_pos") and state.get("entry_price") and state.get("btc"):
            entry = float(state["entry_price"])
            btc = float(state["btc"])
            result["unrealized_pnl"] = round((last - entry) * btc)
            result["unrealized_pnl_pct"] = round((last / entry - 1) * 100, 2)
    except (requests.RequestException, KeyError, ValueError) as e:
        result["error"] = f"ticker: {e}"

    if _client.has_credentials:
        try:
            bal = _client.fetch_balance()
            result["jpy_balance"] = bal.get("JPY", {}).get("free")
            result["btc_balance"] = bal.get("BTC", {}).get("free")
        except (requests.RequestException, KeyError, ValueError) as e:
            result["error"] = (result["error"] or "") + f" balance: {e}"

    return result


@app.get("/api/equity")
def api_equity(n: int = 500, _: str = Depends(_auth)) -> dict:
    """直近n件の資産推移を返す。"""
    equity_file = _settings.equity_file
    if not equity_file.exists():
        return {"labels": [], "values": []}
    try:
        lines = equity_file.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines[-n:] if line.strip()]
        return {
            "labels": [r["dt"][:16].replace("T", " ") for r in rows],
            "values": [round(r["equity"]) for r in rows],
        }
    except (json.JSONDecodeError, OSError, KeyError):
        return {"labels": [], "values": []}


@app.get("/api/logs")
def api_logs(n: int = 80, _: str = Depends(_auth)) -> dict:
    return {"lines": _read_logs(n)}


@app.get("/", response_class=HTMLResponse)
def index(_: str = Depends(_auth)) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return _TEMPLATES.get_template("dashboard.html").render({"now": now})
