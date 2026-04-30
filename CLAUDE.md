# autoflyer

BitFlyer FX_BTC_JPY automated trading bot using a MA-cross strategy. Single CLI for data fetching, backtesting, live trading, and dashboard.

## Project Structure

```
autoflyer/
├── autoflyer/
│   ├── __main__.py       CLI entry point
│   ├── bot.py            Live trading bot
│   ├── backtest.py       Backtesting engine
│   ├── strategy.py       Strategy variant definitions
│   ├── indicators.py     Technical indicators (MA, ATR, ADX, RSI, MACD)
│   ├── garch_sizing.py   GARCH volatility-based position sizing
│   ├── dashboard.py      Monitoring dashboard (FastAPI, port 8080)
│   ├── data.py           CSV loading and OHLCV resampling
│   ├── fees.py           bitFlyer fee tier model
│   ├── config.py         Constants and configuration
│   └── report.py         Aggregation and display
├── tests/
├── data/                 Price data (gitignored)
├── logs/                 Log files (gitignored)
├── state.json            Live bot position state (gitignored)
├── .env                  Credentials (gitignored)
├── .env.example          Env var template
├── docker-compose.yml    Production Docker setup
├── Dockerfile
└── pyproject.toml
```

## Commands

```bash
# Install dependencies
uv sync

# Run CLI
python -m autoflyer <command>
```

| Command | Description |
|---|---|
| `fetch` | Fetch 1-minute OHLCV from GMO Coin |
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

## Environment Variables (`.env`)

| Variable | Description |
|---|---|
| `BITFLYER_API_KEY` | bitFlyer API key |
| `BITFLYER_API_SECRET` | bitFlyer API secret |
| `DRY_RUN` | `1` = dry run (no real orders), `0` = live |
| `SYMBOL` | Trading pair (default: `FX_BTC_JPY`) |
| `TIMEFRAME` | Candle timeframe (`1D`, `12H`, etc.) |
| `VARIANT` | Strategy variant name |
| `TRADE_AMOUNT_JPY` | Max trade size in JPY (`0` = use full balance) |
| `POLL_INTERVAL_SEC` | Bot polling interval in seconds |

## Bot Options

| Flag | Default | Description |
|---|---|---|
| `--live` | false | Enable real order submission |
| `--variant` | `STOP_3ATR` | Strategy variant |
| `--timeframe` | `1D` | Candle timeframe |
| `--amount` | `0` | Trade size cap in JPY |
| `--max-dd-pct` | `20.0` | Circuit breaker drawdown threshold (%) |
| `--interval` | `60` | Polling interval (seconds) |
| `--state` | `state.json` | Position state file path |

## Deployment (Production VPS)

```bash
# Transfer code to VPS (excludes .env, data/, state.json)
./deploy.sh <server-ip>

# On VPS: start with Docker Compose
docker compose up -d

# SSH tunnel to access dashboard locally
ssh -L 8080:localhost:8080 root@<server-ip>
# then open http://localhost:8080
```

## Git Commit Rules

- **No AI attribution** — never add `Co-Authored-By` or any Claude/AI trailer to commit messages
- **Commit as the repo owner** — always commit under the configured git user (`yotti`)
- **Atomic commits** — one commit per feature or fix; do not bundle unrelated changes

## Key Files

- [autoflyer/strategy.py](autoflyer/strategy.py) — add/modify strategy variants (`Variant` dataclass, `VARIANTS` list)
- [autoflyer/bot.py](autoflyer/bot.py) — live order logic, circuit breaker, state persistence
- [autoflyer/backtest.py](autoflyer/backtest.py) — vectorized backtest engine
- [autoflyer/config.py](autoflyer/config.py) — constants (`START_CASH_JPY`, `TIMEFRAMES`, etc.)
