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

## Production Deployment (GCP)

### 1. Provision a VM

GCP Compute Engine (e2-micro, Debian, `us-central1-f`).

```bash
sudo apt-get update && sudo apt-get install -y git
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone and configure

```bash
git clone https://github.com/ykus4/autoflyer.git
cd autoflyer
cp .env.example .env
nano .env
```

Issue a bitFlyer API key with **spot trading**, **FX trading**, and **balance** permissions.

```env
BITFLYER_API_KEY=your_key
BITFLYER_API_SECRET=your_secret
DRY_RUN=1
```

### 3. Fetch historical data (first time only)

```bash
uv sync
python -m autoflyer fetch-binance --end 2026-04-30 --output var/btc_usdt_1d.csv
```

### 4. Start in dry-run mode

```bash
./run.sh start
tail -f var/run.log
```

Expected log output:
```
2026-04-30 09:00:01 [INFO] Bot start — variant=MA200_STOP1.5ATR_GARCH40  dry_run=True
2026-04-30 09:00:02 [INFO] cross_up=False  cross_down=False  in_pos=False  dd=0.0%
```

### 5. Switch to live mode

Set `DRY_RUN=0` in `.env`, then:

```bash
./run.sh restart
```

### 6. Monitor with dashboard

```bash
# SSH tunnel from local machine
ssh -L 8080:localhost:8080 <user>@<server-ip>
```

Open `http://localhost:8080` — shows position, P&L, equity chart, and recent logs.

---

## Common Commands

```bash
# Start / stop / restart
./run.sh start
./run.sh stop
./run.sh restart

# Check status
./run.sh status

# Stream logs
tail -f var/run.log
tail -f var/bot.log

# Check current position
cat var/state.json

# Update price data manually
python -m autoflyer update --output var/btc_usdt_1d.csv
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

Appends bars from the last CSV date to today. Runs automatically every day at 09:05 JST.

```bash
python -m autoflyer update --output var/btc_usdt_1d.csv
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
python -m autoflyer dashboard --log-file var/bot.log
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
├── var/                  Runtime data (gitignored)
│   ├── bot.log           Bot log
│   ├── run.log           All process output
│   ├── state.json        Position state
│   └── btc_usdt_1d.csv   Price data
├── run.sh                Start/stop script
├── .env.example          Env var template
└── pyproject.toml
```

---

## Risk Management

- Use `--amount` to cap the trade size to an amount you can afford to lose
- Use `--max-dd-pct 30` to auto-stop at 30% drawdown
- Expected worst case at max DD 24.3%: ¥3M invested → -¥730K

> **Disclaimer**: Educational project. Use at your own risk. Past backtest results do not guarantee future performance.
