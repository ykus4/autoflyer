# autoflyer

BitFlyer FX_BTC_JPY automated trading bot using a MA-cross strategy. Single CLI for data fetching, backtesting, live trading, and dashboard.

## Project Structure

```
autoflyer/
├── autoflyer/
│   ├── __main__.py          CLI entry point
│   ├── config.py            Algorithm constants (MA periods, ATR length, etc.)
│   ├── notifications.py     Email alerts (SMTP)
│   ├── dashboard.py         Monitoring dashboard API (FastAPI, port 8080)
│   ├── trading/             Live trading
│   │   ├── bot.py           Live trading loop, BitFlyerClient, retry logic
│   │   ├── strategy.py      Variant definitions (Variant dataclass, VARIANTS)
│   │   ├── indicators.py    Technical indicators (MA, ATR, ADX, RSI, MACD)
│   │   ├── garch_sizing.py  GARCH volatility-based position sizing
│   │   └── fees.py          bitFlyer fee tier model
│   ├── analysis/            Backtesting and data
│   │   ├── backtest.py      Vectorized backtest engine
│   │   ├── data.py          CSV loading and OHLCV resampling
│   │   └── report.py        Aggregation and display
│   └── templates/
│       └── dashboard.html   Dashboard UI
├── var/                     Runtime data (gitignored)
├── tests/
├── autoflyer-bot.service    systemd unit file
├── .env                     Runtime config (gitignored)
├── .env.example             Env var template
├── run.sh                   Start/stop script
└── pyproject.toml
```

## Commands

```bash
uv sync
python -m autoflyer <command>
```

| Command | Description |
|---|---|
| `fetch-binance` | Fetch daily OHLCV from Binance (for backtesting) |
| `update` | Append new bars to existing CSV |
| `backtest` | Run backtest across variants and timeframes |
| `bot` | Start live trading bot |
| `dashboard` | Start monitoring dashboard at `http://localhost:8080` |
| `variants` | List available strategy variants |

## Development

```bash
uv run pytest tests/ -q
uv run ruff check autoflyer/ tests/
uv run ruff format autoflyer/ tests/
uv run mypy autoflyer/
```

## Recommended Strategy

`MA200_STOP1.5ATR_GARCH40` — best from 8-year grid search (2017–2026)

- MA200 filter: long entries only when price > MA200 (avoids 2018/2022 crashes)
- 1.5× ATR stop-loss
- GARCH 40% position sizing: reduces size during high volatility
- Results: CAGR 29.1%, Max DD 24.3%, Calmar 1.20

## Configuration

**`.env`** — runtime config (per-environment, gitignored)

| Variable | Description |
|---|---|
| `BITFLYER_API_KEY` | bitFlyer API key |
| `BITFLYER_API_SECRET` | bitFlyer API secret |
| `DRY_RUN` | `1` = dry run, `0` = live |
| `SYMBOL` | Trading pair (default: `FX_BTC_JPY`) |
| `TIMEFRAME` | Candle timeframe for live bot (`1D`, `12H`, etc.) |
| `VARIANT` | Strategy variant name |
| `TRADE_AMOUNT_JPY` | Max trade size in JPY (`0` = full balance) |
| `POLL_INTERVAL_SEC` | Bot polling interval in seconds |
| `DASHBOARD_USER` | Basic auth username (enables external access when set) |
| `DASHBOARD_PASS` | Basic auth password |
| `SMTP_HOST` | SMTP server hostname (e.g. `smtp.mail.me.com`) |
| `SMTP_PORT` | SMTP port (default: `587`) |
| `SMTP_USER` | SMTP login username |
| `SMTP_PASS` | SMTP password / app password |
| `SMTP_FROM` | Sender email address (defaults to SMTP_USER) |
| `NOTIFY_TO` | Notification recipient email address |

**`config.py`** — algorithm constants (same across all environments): MA periods, ATR length, indicator parameters.

## Git Commit Rules

- **No AI attribution** — never add `Co-Authored-By` or any Claude/AI trailer to commit messages
- **Commit as the repo owner** — always commit under the configured git user (`yotti`)
- **Atomic commits** — one commit per feature or fix; do not bundle unrelated changes

## Key Files

- [autoflyer/trading/strategy.py](autoflyer/trading/strategy.py) — add/modify strategy variants
- [autoflyer/trading/bot.py](autoflyer/trading/bot.py) — live order logic, circuit breaker, retry, state persistence
- [autoflyer/notifications.py](autoflyer/notifications.py) — email notification module
- [autoflyer/analysis/backtest.py](autoflyer/analysis/backtest.py) — vectorized backtest engine
- [autoflyer/config.py](autoflyer/config.py) — constants (`START_CASH_JPY`, `TIMEFRAMES`, etc.)
- [autoflyer/templates/dashboard.html](autoflyer/templates/dashboard.html) — dashboard UI
