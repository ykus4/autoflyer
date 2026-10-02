# autoflyer

BitFlyer FX_BTC_JPY automated trading bot (Donchian breakout / MA-cross variants). Single CLI for data fetching, backtesting, live trading, and dashboard.

## Project Structure

```
autoflyer/
├── autoflyer/
│   ├── __main__.py          CLI entry point (thin parse + dispatch only)
│   ├── config.py            Algorithm constants (MA periods, ATR length, etc.)
│   ├── timeframes.py        Timeframe label parsing (`1D`/`3H` -> pandas rule)
│   ├── logging_utils.py     JST log formatter and handler setup
│   ├── notifications.py     Alerts: email (SMTP), Slack webhook, LINE Messaging API
│   ├── dashboard.py         Monitoring dashboard API (FastAPI, port 8080)
│   ├── trading/             Live trading
│   │   ├── bot.py           BotConfig + LiveBot polling loop (reconcile, exchange stop, halt)
│   │   ├── broker.py        Order execution / fill confirmation / account (Paper vs Live)
│   │   ├── client.py        BitFlyerClient REST wrapper, retry/backoff
│   │   ├── state.py         state.json persistence + equity.jsonl / trades.jsonl logs
│   │   ├── signals.py       Entry rules shared by bot and backtester
│   │   ├── exits.py         Stop / TP / trailing state machine shared by bot and backtester
│   │   ├── strategy.py      Variant definitions + lookup (VARIANTS, get_variant)
│   │   ├── indicators.py    Technical indicators (MA, ATR, ADX, RSI, MACD, Supertrend)
│   │   ├── garch_sizing.py  GARCH volatility-based position sizing
│   │   ├── stats_filters.py Statistical filters (Hurst, HMM, Kelly, z-score, MAE)
│   │   ├── market_data.py   Binance klines (live candles + backtest data)
│   │   └── fees.py          Cost models: spot fee tiers / Crypto CFD holding cost
│   ├── analysis/            Backtesting and data
│   │   ├── backtest.py      Bar-by-bar backtest engine (spot / CFD costs)
│   │   ├── metrics.py       CAGR, max DD, Sharpe, Sortino, Calmar
│   │   ├── optimize.py      Parameter grid search and walk-forward
│   │   ├── runner.py        Backtest orchestration across variants/timeframes
│   │   ├── fetch.py         OHLCV fetching (GMO/Binance, incremental update)
│   │   ├── data.py          CSV loading and OHLCV resampling
│   │   └── report.py        Aggregation and display
│   └── templates/
│       └── dashboard.html   Dashboard UI
├── deploy/                  Deployment
│   ├── run.sh               Start/stop script
│   ├── install-systemd.sh   systemd setup
│   └── autoflyer-*.service  systemd units/timer
├── docs/                    Documentation (mkdocs)
│   ├── index.md             Usage guide
│   └── backtest-results.md  Backtest results for all variants
├── var/                     Runtime data (gitignored)
├── tests/
├── .env                     Runtime config (gitignored)
├── .env.example             Env var template
└── pyproject.toml
```

## Commands

```bash
uv sync
python -m autoflyer <command>
```

| Command | Description |
|---|---|
| `fetch` | Fetch 1-minute OHLCV from GMO Coin |
| `fetch-binance` | Fetch daily OHLCV from Binance (for backtesting) |
| `update` | Append new bars to existing CSV |
| `backtest` | Run backtest across variants and timeframes (`--costs cfd`) |
| `grid` | Grid search over Variant fields (`--param field=v1,v2`) |
| `walk-forward` | Pick best params on train window, evaluate on next window |
| `bot` | Start live trading bot |
| `reset-halt` | Clear the persisted halted flag (circuit breaker / mismatch) |
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

`BREAKOUT_STOP1.0_GARCH40` — best from grid search (see docs/backtest-results.md, 2026-07-09)

- Donchian 20-bar breakout entry: buy when price breaks above 20-day high
- MA200 filter: long entries only when price > MA200 (avoids bear markets)
- **1.0× ATR stop-loss** (tighter than the old 1.5× — cuts losers faster; improves
  return, profit factor and drawdown simultaneously across all validation windows)
- GARCH 40% position sizing: reduces size during high volatility
- Results (2022–2026, BTC/USDT 1D): +178%, PF 3.79, Max DD 24.7%, 17 trades
- Max-return alternative: `BREAKOUT_STOP1.0_GARCH50` (+194%, PF 3.71, DD 26.9%)

New algorithm — **Supertrend trailing stop** (`supertrend_mult` on a Variant): follows the
trend and exits on flip. Best is `BREAKOUT_SUPERTREND4_GARCH40` (+166%, PF 3.17, WR 52.6%).

Previous best: `BREAKOUT_STOP1.5_GARCH40` (+136%, PF 2.62, DD 30.1% on the same engine/data).

## Configuration

**`.env`** — runtime config (per-environment, gitignored)

| Variable | Description |
|---|---|
| `BITFLYER_API_KEY` | bitFlyer API key |
| `BITFLYER_API_SECRET` | bitFlyer API secret |
| `DRY_RUN` | `1` = dry run, `0` = live. Live orders need **both** `--live` and `DRY_RUN=0` |
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
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL (optional) |
| `LINE_CHANNEL_TOKEN` / `LINE_TO` | LINE Messaging API token and recipient (optional) |
| `DAILY_SUMMARY_HOUR` | JST hour for the daily summary (`-1` disables, default `9`) |

**`config.py`** — algorithm constants (same across all environments): MA periods, ATR length, indicator parameters.

## Git Commit Rules

- **No AI attribution** — never add `Co-Authored-By` or any Claude/AI trailer to commit messages
- **Commit as the repo owner** — always commit under the configured git user (`yotti`)
- **Atomic commits** — one commit per feature or fix; do not bundle unrelated changes

## Architecture Notes

- **`trading/signals.py` is the single source of truth for entry rules** and
  **`trading/exits.py` for stops / take-profit / trailing.** The backtester and the live bot
  both call them, so a rule change applies to both. Never re-implement a rule in one engine only.
- **Live order safety (`trading/bot.py`):** while an exchange STOP order exists, the exchange owns
  the stop-out; the bot only market-closes after `cancel_stop` confirms the stop is gone unfilled.
  At most one close attempt per poll; unknown order outcomes (timeouts) are resolved from
  `getpositions` before retrying. Anything ambiguous halts (`state.halted`) instead of guessing.
- **Dependency direction is `analysis/` → `trading/`.** `trading/` must not import from
  `analysis/`.
- Runtime files (state, equity, logs) all live under `var/`.
- **Candles come from Binance** (`trading/market_data.py`): bitFlyer has no candle API. The live
  bot uses `BTCJPY` rescaled to the bitFlyer price; do not reintroduce CoinGecko (its free OHLC
  endpoint returns 4-day candles for long ranges).

## Key Files

- [autoflyer/trading/strategy.py](autoflyer/trading/strategy.py) — add/modify strategy variants
- [autoflyer/trading/signals.py](autoflyer/trading/signals.py) — entry filters, sizing, stops (shared)
- [autoflyer/trading/bot.py](autoflyer/trading/bot.py) — live order logic, circuit breaker, state persistence
- [autoflyer/notifications.py](autoflyer/notifications.py) — email notification module
- [autoflyer/analysis/backtest.py](autoflyer/analysis/backtest.py) — backtest engine (`_manage_position` = stop/TP state machine)
- [autoflyer/config.py](autoflyer/config.py) — constants (`START_CASH_JPY`, `TIMEFRAMES`, etc.)
- [autoflyer/templates/dashboard.html](autoflyer/templates/dashboard.html) — dashboard UI
