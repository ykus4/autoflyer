"""Lightweight web dashboard for monitoring the live bot.

Reads the same state/equity files the bot writes, plus live ticker and balance
from bitFlyer. Basic auth is enabled only when `DASHBOARD_USER` is set.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from .trading.broker import unrealized_pnl
from .trading.client import BitFlyerClient
from .trading.market_data import recent_ohlcv
from .trading.state import equity_path, read_jsonl_tail, trades_path

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
    timeframe: str = "1D"
    backtest_report: Path = field(default_factory=lambda: Path("var/backtest/latest.json"))

    @property
    def equity_file(self) -> Path:
        return equity_path(self.state_file)

    @property
    def trades_file(self) -> Path:
        return trades_path(self.state_file)

    @property
    def is_cfd(self) -> bool:
        return self.symbol.replace("/", "_").startswith("FX_")


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
        "collateral": None,
        "open_position_pnl": None,
        "keep_rate": None,
        "is_cfd": _settings.is_cfd,
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
            side = state.get("side") or "long"
            pnl = unrealized_pnl(side, float(state["btc"]), entry, last)
            direction = 1 if side == "long" else -1
            result["unrealized_pnl"] = round(pnl)
            result["unrealized_pnl_pct"] = round((last / entry - 1) * 100 * direction, 2)
    except (requests.RequestException, KeyError, ValueError) as e:
        result["error"] = f"ticker: {e}"

    if _client.has_credentials:
        try:
            if _settings.is_cfd:
                c = _client.fetch_collateral()
                result["collateral"] = c.get("collateral")
                result["open_position_pnl"] = c.get("open_position_pnl")
                result["keep_rate"] = c.get("keep_rate")
            else:
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


@app.get("/api/trades")
def api_trades(n: int = 200, _: str = Depends(_auth)) -> dict:
    """直近 n 件の約定（エントリー/決済）。"""
    return {"trades": read_jsonl_tail(_settings.trades_file, n)}


_candle_cache: dict[tuple[str, int], tuple[float, dict]] = {}
_CANDLE_TTL_SEC = 300.0


@app.get("/api/candles")
def api_candles(n: int = 200, _: str = Depends(_auth)) -> dict:
    """ボットと同じ時間足の終値（Binance BTCJPY）。チャートに売買点を重ねる下地。"""
    key = (_settings.timeframe, n)
    cached = _candle_cache.get(key)
    if cached and time.time() - cached[0] < _CANDLE_TTL_SEC:
        return cached[1]
    try:
        df = recent_ohlcv(_settings.timeframe, n)
    except requests.RequestException as e:
        return {"labels": [], "close": [], "error": str(e)}
    result = {
        "labels": [d.isoformat() for d in df["dt"]],
        "close": [round(float(c)) for c in df["close"]],
        "timeframe": _settings.timeframe,
        "error": None,
    }
    _candle_cache[key] = (time.time(), result)
    return result


@app.get("/api/backtest")
def api_backtest(_: str = Depends(_auth)) -> dict:
    """`backtest` コマンドが保存した最新の成績表。"""
    path = _settings.backtest_report
    if not path.exists():
        return {"rows": [], "generated_at": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"rows": [], "generated_at": None}


@app.get("/api/logs")
def api_logs(n: int = 80, _: str = Depends(_auth)) -> dict:
    return {"lines": _read_logs(n)}


@app.get("/", response_class=HTMLResponse)
def index(_: str = Depends(_auth)) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return _TEMPLATES.get_template("dashboard.html").render({"now": now})
