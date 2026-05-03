"""Live trading bot loop."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import logging.handlers
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

from ..analysis.backtest import compute_live_stop, compute_signal
from .fees import FeeTierModel
from .indicators import add_indicators
from .strategy import VARIANTS, Variant

_MTF_UP: dict[str, str] = {
    "1H": "4H",
    "3H": "1D",
    "6H": "1D",
    "12H": "3D",
    "1D": "3D",
}
_STATE_DEFAULT: dict = {
    "in_pos": False,
    "entry_price": None,
    "btc": 0.0,
    "entry_dt": None,
    "stop_px": None,
    "peak_cash": None,
}

_BF_BASE = "https://api.bitflyer.com"


class BitFlyerClient:
    """BitFlyer Lightning REST APIの薄いラッパー。"""

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._key = api_key
        self._secret = api_secret
        self._session = requests.Session()

    # ---- Public endpoints ----

    def fetch_ticker(self, product_code: str) -> dict:
        resp = self._session.get(
            f"{_BF_BASE}/v1/ticker",
            params={"product_code": product_code},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_executions(
        self, product_code: str, count: int = 500, before: int | None = None
    ) -> list[dict]:
        params: dict = {"product_code": product_code, "count": count}
        if before is not None:
            params["before"] = before
        resp = self._session.get(f"{_BF_BASE}/v1/getexecutions", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def fetch_ohlcv(self, product_code: str, tf: str, limit: int = 300) -> pd.DataFrame:
        """CoinGecko APIで日足/時間足OHLCVを取得する。"""
        days = _tf_to_coingecko_days(tf, limit)
        resp = self._session.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc",
            params={"vs_currency": "jpy", "days": days},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close"])
        df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df["volume"] = 0.0
        df = df[["dt", "open", "high", "low", "close", "volume"]]

        # 時間足へのリサンプリングが必要な場合
        rule = _tf_to_pandas_rule(tf)
        if tf.upper() not in ("1D", "D"):
            df = (
                df.set_index("dt")
                .resample(rule)
                .agg(
                    open=("open", "first"),
                    high=("high", "max"),
                    low=("low", "min"),
                    close=("close", "last"),
                    volume=("volume", "sum"),
                )
                .dropna()
                .reset_index()
            )

        return df.tail(limit).reset_index(drop=True)

    # ---- Private endpoints ----

    def _auth_headers(self, method: str, path: str, body: str = "") -> dict:
        ts = str(int(datetime.now(timezone.utc).timestamp()))
        text = ts + method + path + body
        sign = hmac.new(self._secret.encode(), text.encode(), hashlib.sha256).hexdigest()
        return {
            "ACCESS-KEY": self._key,
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-SIGN": sign,
            "Content-Type": "application/json",
        }

    def fetch_balance(self) -> dict:
        path = "/v1/me/getbalance"
        headers = self._auth_headers("GET", path)
        resp = self._session.get(f"{_BF_BASE}{path}", headers=headers, timeout=10)
        resp.raise_for_status()
        # [{currency_code: "JPY", amount: ..., available: ...}, ...]
        result = {}
        for item in resp.json():
            code = item["currency_code"]
            result[code] = {"free": item["available"], "total": item["amount"]}
        return result

    def create_order(self, product_code: str, side: str, size: float) -> dict:
        path = "/v1/me/sendchildorder"
        body_dict = {
            "product_code": product_code,
            "child_order_type": "MARKET",
            "side": side.upper(),
            "size": size,
        }
        body = json.dumps(body_dict)
        headers = self._auth_headers("POST", path, body)
        resp = self._session.post(f"{_BF_BASE}{path}", headers=headers, data=body, timeout=10)
        resp.raise_for_status()
        return resp.json()


def _append_equity(equity_file: Path, equity: float) -> None:
    row = json.dumps({"dt": datetime.now(timezone.utc).isoformat(), "equity": equity})
    with equity_file.open("a", encoding="utf-8") as f:
        f.write(row + "\n")


def run(args: argparse.Namespace) -> None:
    load_dotenv()

    api_key = os.environ.get("BITFLYER_API_KEY", "")
    api_secret = os.environ.get("BITFLYER_API_SECRET", "")
    dry_run = not args.live
    symbol = args.symbol
    timeframe = args.timeframe[0] if args.timeframe else os.environ.get("TIMEFRAME", "1D")
    amount_jpy: float = args.amount
    interval: int = args.interval
    state_file = Path(args.state)
    equity_file = state_file.with_name("equity.jsonl")
    max_dd_pct: float = args.max_dd_pct

    variant = _resolve_variant(args.variant or os.environ.get("VARIANT", "STOP_3ATR"))
    log = _setup_logging(args.log_file, symbol, timeframe, variant, dry_run, max_dd_pct)

    client = BitFlyerClient(api_key, api_secret)
    state = _load_state(state_file, log)
    log.info("Loaded state: %s", state)
    fee_model = FeeTierModel()

    # product_codeはスラッシュなし (BTC_JPY / FX_BTC_JPY)
    product_code = symbol.replace("/", "_") if "/" in symbol else symbol

    def fetch_bars(tf: str, limit: int = 300) -> pd.DataFrame:
        return client.fetch_ohlcv(product_code, tf, limit)

    def market_order(side: str, btc: float) -> dict | None:
        if dry_run:
            log.info("[DRY_RUN] %s %s %.8f BTC", side.upper(), product_code, btc)
            return {"id": "dry_run"}
        order = client.create_order(product_code, side, btc)
        log.info("Order placed: %s", order)
        return order

    def mtf_trend_ok() -> bool:
        if not args.use_mtf:
            return True
        tf_up = _MTF_UP.get(timeframe.upper())
        if tf_up is None:
            return True
        try:
            bars_up = fetch_bars(tf_up, limit=100)
            ma50 = bars_up["close"].rolling(50).mean().iloc[-1]
            trend_up = float(bars_up["close"].iloc[-1]) > float(ma50)
            log.info(
                "MTF(%s) close=%.0f MA50=%.0f trend_up=%s",
                tf_up,
                bars_up["close"].iloc[-1],
                ma50,
                trend_up,
            )
            return trend_up
        except Exception as e:
            log.warning("MTF fetch failed (%s) — allowing entry", e)
            return True

    while True:
        try:
            bars = fetch_bars(timeframe)
            bars_ind = add_indicators(bars)
            cross_up, cross_down = compute_signal(bars)
            fee_model.step(pd.Timestamp(bars["dt"].iloc[-2]))

            if not dry_run:
                ticker = client.fetch_ticker(product_code)
                cur_price = float(ticker["ltp"])
            else:
                cur_price = float(bars["close"].iloc[-1])

            entry_price_val = float(state["entry_price"]) if state["entry_price"] else cur_price
            btc_held = float(state.get("btc", 0.0))

            if dry_run:
                cur_equity = (
                    (cur_price - entry_price_val) * btc_held + (amount_jpy or 100_000)
                    if state["in_pos"]
                    else (amount_jpy or 100_000)
                )
            else:
                try:
                    bal = client.fetch_balance()
                    jpy_balance = float(bal.get("JPY", {}).get("free", 0))
                    btc_balance = float(bal.get("BTC", {}).get("free", 0))
                    cur_equity = jpy_balance + btc_balance * cur_price
                    log.info(
                        "残高: JPY=%.0f  BTC=%.6f  資産合計=%.0f",
                        jpy_balance,
                        btc_balance,
                        cur_equity,
                    )
                except Exception:
                    cur_equity = (
                        (cur_price - entry_price_val) * btc_held + (amount_jpy or 100_000)
                        if state["in_pos"]
                        else (amount_jpy or 100_000)
                    )
                    log.warning("残高取得失敗 — 推定値で資産計算: %.0f JPY", cur_equity)

            if state["peak_cash"] is None or cur_equity > state["peak_cash"]:
                state["peak_cash"] = cur_equity
                _save_state(state_file, state)
            _append_equity(equity_file, cur_equity)

            dd_pct = (1.0 - cur_equity / state["peak_cash"]) * 100 if state["peak_cash"] else 0.0
            if dd_pct >= max_dd_pct:
                log.critical(
                    "CIRCUIT BREAKER: drawdown %.1f%% >= %.1f%% — closing position and stopping.",
                    dd_pct,
                    max_dd_pct,
                )
                if (
                    state["in_pos"]
                    and float(state.get("btc", 0.0)) > 0
                    and market_order("sell", float(state["btc"]))
                ):
                    log.critical(
                        "CIRCUIT BREAKER: sold %.8f BTC @ ~%.0f JPY",
                        state["btc"],
                        cur_price,
                    )
                    state.update(
                        in_pos=False, entry_price=None, btc=0.0, entry_dt=None, stop_px=None
                    )
                    _save_state(state_file, state)
                break

            if (
                state["in_pos"]
                and state.get("stop_px") is not None
                and cur_price <= float(state["stop_px"])
            ):
                if market_order("sell", btc_held):
                    fee_model.record_fill(pd.Timestamp(bars["dt"].iloc[-1]), btc_held * cur_price)
                    pnl = (cur_price - entry_price_val) * btc_held
                    log.info("STOP HIT  %.8f BTC @ %.0f JPY  pnl≈%.0f", btc_held, cur_price, pnl)
                    state.update(
                        in_pos=False, entry_price=None, btc=0.0, entry_dt=None, stop_px=None
                    )
                    _save_state(state_file, state)
                time.sleep(interval)
                continue

            log.info(
                "cross_up=%s  cross_down=%s  in_pos=%s  dd=%.1f%%",
                cross_up,
                cross_down,
                state["in_pos"],
                dd_pct,
            )

            if cross_up and not state["in_pos"]:
                if not mtf_trend_ok():
                    log.info("MTF filter blocked long entry")
                else:
                    if not dry_run:
                        try:
                            bal = client.fetch_balance()
                            jpy = float(bal.get("JPY", {}).get("free", 0))
                        except Exception:
                            jpy = amount_jpy or 100_000
                            log.warning("残高取得失敗 — フォールバック %.0f JPY を使用", jpy)
                    else:
                        jpy = amount_jpy or 100_000
                    if amount_jpy > 0:
                        jpy = min(jpy, amount_jpy)
                    log.info("使用資金: %.0f JPY  fee_rate=%.4f%%", jpy, fee_model.rate * 100)
                    btc = round(jpy / (cur_price * (1 + fee_model.rate)), 8)
                    stop_px = compute_live_stop(bars_ind, cur_price, "long", variant)
                    if btc > 0 and market_order("buy", btc):
                        fee_model.record_fill(pd.Timestamp(bars["dt"].iloc[-1]), btc * cur_price)
                        state.update(
                            in_pos=True,
                            entry_price=cur_price,
                            btc=btc,
                            entry_dt=bars.iloc[-2]["dt"].isoformat(),
                            stop_px=stop_px,
                        )
                        _save_state(state_file, state)
                        log.info("ENTRY  %.8f BTC @ %.0f JPY  stop_px=%s", btc, cur_price, stop_px)

            elif cross_down and state["in_pos"]:
                pnl = (cur_price - entry_price_val) * btc_held
                if market_order("sell", btc_held):
                    fee_model.record_fill(pd.Timestamp(bars["dt"].iloc[-1]), btc_held * cur_price)
                    log.info("EXIT  %.8f BTC @ %.0f JPY  pnl≈%.0f", btc_held, cur_price, pnl)
                    state.update(
                        in_pos=False, entry_price=None, btc=0.0, entry_dt=None, stop_px=None
                    )
                    _save_state(state_file, state)

        except requests.HTTPError as e:
            log.error("HTTP error: %s", e)
        except requests.RequestException as e:
            log.error("Network error: %s", e)
        except Exception:
            log.exception("Unexpected error")

        log.info("Sleeping %ds...", interval)
        time.sleep(interval)


# =========================
# 内部ヘルパー
# =========================


def _tf_to_coingecko_days(tf: str, limit: int) -> int:
    minutes = {"1H": 60, "3H": 180, "4H": 240, "6H": 360, "12H": 720, "1D": 1440, "3D": 4320}
    m = minutes.get(tf.upper().strip(), 1440)
    days = max(1, (m * limit) // 1440 + 1)
    # CoinGeckoは1/7/14/30/90/180/365/maxのみ有効
    for d in [1, 7, 14, 30, 90, 180, 365]:
        if d >= days:
            return d
    return 365


def _tf_to_seconds(tf: str) -> int:
    mapping = {
        "1H": 3600,
        "3H": 10800,
        "4H": 14400,
        "6H": 21600,
        "12H": 43200,
        "1D": 86400,
        "3D": 259200,
    }
    v = mapping.get(tf.upper().strip())
    if v is None:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return v


def _tf_to_pandas_rule(tf: str) -> str:
    mapping = {"1H": "1h", "3H": "3h", "4H": "4h", "6H": "6h", "12H": "12h", "1D": "1D", "3D": "3D"}
    v = mapping.get(tf.upper().strip())
    if v is None:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return v


def _resolve_variant(name: str) -> Variant:
    v = next((v for v in VARIANTS if v.name == name), None)
    if v is None:
        raise SystemExit(
            f"Unknown variant: {name}. Run `python -m autoflyer variants` to list them."
        )
    return v


def _setup_logging(
    log_file: str | None,
    symbol: str,
    timeframe: str,
    variant: Variant,
    dry_run: bool,
    max_dd_pct: float,
) -> logging.Logger:
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
        )
    logging.basicConfig(
        level=logging.INFO, format=fmt, datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers
    )
    log = logging.getLogger("autoflyer.bot")
    log.info(
        "Bot start — symbol=%s  tf=%s  variant=%s  dry_run=%s  max_dd=%.1f%%",
        symbol,
        timeframe,
        variant.name,
        dry_run,
        max_dd_pct,
    )
    return log


def _load_state(state_file: Path, log: logging.Logger) -> dict:
    if not state_file.exists():
        return dict(_STATE_DEFAULT)
    try:
        data = json.loads(state_file.read_text())
        for k, v in _STATE_DEFAULT.items():
            data.setdefault(k, v)
        return data
    except (json.JSONDecodeError, OSError) as e:
        backup = state_file.with_suffix(".json.bak")
        shutil.copy2(state_file, backup)
        log.error("state.json corrupt (%s) — reset to default, backup: %s", e, backup)
        return dict(_STATE_DEFAULT)


def _save_state(state_file: Path, state: dict) -> None:
    tmp = state_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(state_file)
