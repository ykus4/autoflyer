"""Lightweight web dashboard for monitoring the live bot."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from .trading.bot import BitFlyerClient

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="autoflyer dashboard")
_security = HTTPBasic()
_security_dep = Depends(_security)

_STATE_FILE = Path("state.json")
_EQUITY_FILE = Path("equity.jsonl")
_LOG_FILE: Path | None = None
_SYMBOL = "FX_BTC_JPY"
_ex: BitFlyerClient | None = None
_DASHBOARD_USER = ""
_DASHBOARD_PASS = ""


def set_paths(
    state: Path,
    log: Path | None = None,
    symbol: str = "FX_BTC_JPY",
    api_key: str = "",
    api_secret: str = "",
    dashboard_user: str = "",
    dashboard_pass: str = "",
) -> None:
    global _STATE_FILE, _EQUITY_FILE, _LOG_FILE, _SYMBOL, _ex, _DASHBOARD_USER, _DASHBOARD_PASS
    _STATE_FILE = state
    _EQUITY_FILE = state.with_name("equity.jsonl")
    _LOG_FILE = log
    _SYMBOL = symbol
    _ex = BitFlyerClient(api_key, api_secret)
    _DASHBOARD_USER = dashboard_user
    _DASHBOARD_PASS = dashboard_pass


def _auth(credentials: HTTPBasicCredentials = _security_dep) -> str:
    if not _DASHBOARD_USER:
        return credentials.username
    ok = secrets.compare_digest(credentials.username, _DASHBOARD_USER) and secrets.compare_digest(
        credentials.password, _DASHBOARD_PASS
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _read_state() -> dict:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text())
    except Exception:
        return {}


def _read_logs(n: int = 80) -> list[str]:
    if _LOG_FILE is None or not _LOG_FILE.exists():
        return []
    try:
        lines = _LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return []


@app.get("/api/state")
def api_state(_: str = Depends(_auth)) -> dict:
    return _read_state()


@app.get("/api/ticker")
def api_ticker(_: str = Depends(_auth)) -> dict:
    """現在価格・含み損益・残高をccxtで取得して返す。"""
    state = _read_state()
    result: dict = {
        "last_price": None,
        "bid": None,
        "ask": None,
        "unrealized_pnl": None,
        "unrealized_pnl_pct": None,
        "jpy_balance": None,
        "btc_balance": None,
        "error": None,
    }

    if _ex is None:
        result["error"] = "exchange not initialized"
        return result

    try:
        ticker = _ex.fetch_ticker(_SYMBOL)
        last = float(ticker["last"])
        result["last_price"] = last

        # 含み損益（ポジション保有中のみ）
        if state.get("in_pos") and state.get("entry_price") and state.get("btc"):
            entry = float(state["entry_price"])
            btc = float(state["btc"])
            pnl = (last - entry) * btc
            pnl_pct = (last / entry - 1) * 100
            result["unrealized_pnl"] = round(pnl)
            result["unrealized_pnl_pct"] = round(pnl_pct, 2)
    except Exception as e:
        result["error"] = f"ticker: {e}"

    # 残高
    if _ex._key:
        try:
            bal = _ex.fetch_balance()
            result["jpy_balance"] = bal.get("JPY", {}).get("free")
            result["btc_balance"] = bal.get("BTC", {}).get("free")
        except Exception as e:
            result["error"] = (result["error"] or "") + f" balance: {e}"

    return result


@app.get("/api/equity")
def api_equity(n: int = 500, _: str = Depends(_auth)) -> dict:
    """直近n件の資産推移を返す。"""
    if not _EQUITY_FILE.exists():
        return {"labels": [], "values": []}
    try:
        lines = _EQUITY_FILE.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines[-n:] if line.strip()]
        labels = [r["dt"][:16].replace("T", " ") for r in rows]
        values = [round(r["equity"]) for r in rows]
        return {"labels": labels, "values": values}
    except Exception:
        return {"labels": [], "values": []}


@app.get("/api/logs")
def api_logs(n: int = 80, _: str = Depends(_auth)) -> dict:
    return {"lines": _read_logs(n)}


@app.get("/", response_class=HTMLResponse)
def index(_: str = Depends(_auth)) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return _TEMPLATES.get_template("dashboard.html").render({"now": now})
