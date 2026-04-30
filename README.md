# autoflyer

BitFlyer FX_BTC_JPY automated trading bot using a MA-cross strategy.

Data fetching, backtesting, live trading, and monitoring dashboard — all via a single `python -m autoflyer <command>` CLI.

---

## Recommended Strategy

Best strategy from an 8-year grid search (2017–2026):

| Strategy | CAGR | Max DD | Calmar |
|---|---|---|---|
| **MA200_STOP1.5ATR_GARCH40** | 29.1% | 24.3% | **1.20** |

- **MA200 filter** — long entries only when price > MA200 (avoided the 2018/2022 crashes entirely)
- **1.5× ATR stop-loss** — mechanical stop execution
- **GARCH 40% sizing** — automatically reduces position size during high volatility

---

## Local Development

```bash
# Install dependencies
uv sync

# Run backtest
python -m autoflyer backtest --csv data/btc_usdt_1d.csv --timeframe 1D --variant MA200_STOP1.5ATR_GARCH40

# Tests
uv run pytest tests/ -q

# Lint
uv run ruff check autoflyer/ tests/
```

---

## Production Deployment (VPS)

### 1. Provision a VPS

Recommended: [さくらのVPS](https://vps.sakura.ad.jp/), 512MB plan (~¥520/month), Ubuntu 24.04.

```bash
ssh root@<server-ip>
curl -fsSL https://get.docker.com | sh
```

### 2. Transfer code

```bash
# Run locally — excludes .env, data/, state.json
./deploy.sh <server-ip>
```

### 3. Configure API keys

Issue a bitFlyer API key with **spot trading**, **FX trading**, and **balance** permissions.

```bash
# On VPS
cd ~/autoflyer
cp .env.example .env
nano .env
```

```env
BITFLYER_API_KEY=your_key
BITFLYER_API_SECRET=your_secret
```

### 4. Fetch historical data (first time only)

```bash
docker compose run --rm bot fetch-binance --end 2026-04-30
```

### 5. Start in dry-run mode

```bash
docker compose up -d
docker compose logs -f bot
```

Expected log output:
```
2026-04-30 09:00:01 [INFO] Bot start — variant=MA200_STOP1.5ATR_GARCH40  dry_run=True
2026-04-30 09:00:02 [INFO] cross_up=False  cross_down=False  in_pos=False  dd=0.0%
```

### 6. Switch to live mode

Uncomment `--live` in `docker-compose.yml`, then:

```bash
docker compose down && docker compose up -d
```

### 7. Monitor with dashboard

```bash
# SSH tunnel from local machine
ssh -L 8080:localhost:8080 root@<server-ip>
```

Open `http://localhost:8080` — shows position, P&L, equity chart, and recent logs.

---

## Common Commands

```bash
# Stream logs
docker compose logs -f bot

# Check current position
cat ~/autoflyer/state.json

# Stop bot
docker compose down

# Restart bot
docker compose restart bot

# Update price data
docker compose run --rm bot update
```

---

## Command Reference

### `fetch-binance` — Fetch daily data from Binance

```bash
python -m autoflyer fetch-binance --end 2026-04-30
```

| Option | Default | Description |
|---|---|---|
| `--start` | `2017-08-17` | Start date |
| `--end` | required | End date |
| `--output` | `data/btc_usdt_1d.csv` | Output path |

### `update` — Incremental data update

Appends bars from the last CSV date to today. Runs automatically every day at 09:05.

```bash
python -m autoflyer update
```

### `backtest` — Run backtest

```bash
# Single variant
python -m autoflyer backtest --csv data/btc_usdt_1d.csv --timeframe 1D --variant MA200_STOP1.5ATR_GARCH40

# Walk-forward (test period: 2025+)
python -m autoflyer backtest --csv data/btc_usdt_1d.csv --timeframe 1D --train-end 2025-01-01

# Save trades to CSV
python -m autoflyer backtest --csv data/btc_usdt_1d.csv --timeframe 1D --out-trades results/trades.csv
```

### `bot` — Live trading bot

```bash
# Dry run
python -m autoflyer bot --timeframe 1D --variant MA200_STOP1.5ATR_GARCH40 --amount 300000

# Live
python -m autoflyer bot --live --timeframe 1D --variant MA200_STOP1.5ATR_GARCH40 --amount 300000
```

| Option | Default | Description |
|---|---|---|
| `--live` | false | Enable real order submission |
| `--symbol` | `FX_BTC_JPY` | Trading pair |
| `--timeframe` | `1D` | Candle timeframe |
| `--variant` | `STOP_3ATR` | Strategy variant |
| `--amount` | `0` | Trade size cap in JPY (0 = full balance) |
| `--interval` | `60` | Polling interval (seconds) |
| `--max-dd-pct` | `20.0` | Circuit breaker drawdown threshold (%) |
| `--state` | `state.json` | Position state file |

### `dashboard` — Monitoring dashboard

```bash
python -m autoflyer dashboard --log-file logs/bot.log
# → http://localhost:8080
```

### `variants` — List strategy variants

```bash
python -m autoflyer variants
```

---

## File Structure

```
autoflyer/
├── autoflyer/
│   ├── __main__.py       CLI entry point
│   ├── bot.py            Live trading bot
│   ├── backtest.py       Backtesting engine
│   ├── strategy.py       Strategy variant definitions
│   ├── indicators.py     Technical indicators
│   ├── garch_sizing.py   GARCH position sizing
│   ├── dashboard.py      Monitoring dashboard (FastAPI)
│   ├── data.py           Data loading and resampling
│   ├── fees.py           bitFlyer fee model
│   ├── config.py         Constants
│   └── report.py         Aggregation and display
├── data/                 Price data (gitignored)
├── logs/                 Log files (gitignored)
├── .env.example          Env var template
├── docker-compose.yml    Production Docker config
├── Dockerfile
└── pyproject.toml
```

---

## Risk Management

- Use `--amount` to cap the trade size to an amount you can afford to lose
- Use `--max-dd-pct 30` to auto-stop at 30% drawdown
- Expected worst case at max DD 24.3%: ¥3M invested → -¥730K

> **Disclaimer**: Educational project. Use at your own risk. Past backtest results do not guarantee future performance.
