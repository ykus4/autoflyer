# autoflyer

> BitFlyer FX_BTC_JPY 自動売買ボット — MA クロス戦略

[![CI](https://github.com/ykus4/autoflyer/actions/workflows/ci.yml/badge.svg)](https://github.com/ykus4/autoflyer/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10+-blue)
![License](https://img.shields.io/badge/license-MIT-blue)

> [!WARNING]
> **本ソフトウェアは教育・研究目的で公開しています。**
> 実際の資産運用に使用した場合の損失について、作者は一切の責任を負いません。
> 仮想通貨取引は元本割れのリスクがあります。自己責任のもとでご利用ください。

---

## 推奨戦略

8年間グリッドサーチ（2017–2026）で最良のパフォーマンスを記録した戦略：

```
MA200_STOP1.5ATR_GARCH40
```

| 指標 | 値 |
|---|---|
| CAGR | **29.1%** |
| 最大DD | 24.3% |
| Calmar | **1.20** |

- **MA200フィルター** — MA200より上の時のみロング（2018/2022クラッシュを完全回避）
- **1.5× ATRストップ** — 機械的なロスカット
- **GARCH 40% sizing** — ボラティリティが高い時は自動的にポジションサイズを縮小

---

## アーキテクチャ

```
┌─────────────────────────────────────────────────────┐
│                      run.sh                         │
│  ┌──────────────┐  ┌─────────────┐  ┌───────────┐  │
│  │     bot      │  │   updater   │  │ dashboard │  │
│  │  (live bot)  │  │ (daily 9AM) │  │ :8080     │  │
│  └──────┬───────┘  └──────┬──────┘  └─────┬─────┘  │
│         │                 │               │         │
│         └─────────────────┴───────────────┘         │
│                           │                         │
│                        var/                         │
│          bot.log  state.json  btc_usdt_1d.csv        │
└─────────────────────────────────────────────────────┘
```

---

## クイックスタート（GCP）

**1. VM セットアップ**

```bash
sudo apt-get update && sudo apt-get install -y git
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

**2. クローン & 設定**

```bash
git clone https://github.com/ykus4/autoflyer.git && cd autoflyer
cp .env.example .env && nano .env
```

```env
BITFLYER_API_KEY=your_key
BITFLYER_API_SECRET=your_secret
DRY_RUN=1                          # まずはドライランで確認
VARIANT=MA200_STOP1.5ATR_GARCH40
TIMEFRAME=1D
TRADE_AMOUNT_JPY=50000
```

**3. 初回データ取得**

```bash
uv sync
python -m autoflyer fetch-binance --end 2026-04-30 --output var/btc_usdt_1d.csv
```

**4. 起動**

```bash
./run.sh start
tail -f var/run.log
```

```
2026-04-30 09:00:01 [INFO] Bot start — variant=MA200_STOP1.5ATR_GARCH40  dry_run=True
2026-04-30 09:00:02 [INFO] cross_up=False  cross_down=False  in_pos=False  dd=0.0%
```

**5. ライブ移行**

`.env` で `DRY_RUN=0` に変更して:

```bash
./run.sh restart
```

---

## 操作コマンド

```bash
./run.sh start     # 起動（バックグラウンド）
./run.sh stop      # 停止
./run.sh restart   # 再起動
./run.sh status    # 稼働確認

tail -f var/run.log   # ログ監視
tail -f var/bot.log   # ボット詳細ログ
cat  var/state.json   # 現在のポジション確認
```

**ダッシュボード（SSHトンネル経由）:**

```bash
ssh -L 8080:localhost:8080 <user>@<server-ip>
# → http://localhost:8080 を開く
```

---

## CLI リファレンス

<details>
<summary><b>fetch-binance</b> — Binanceから日足データ取得</summary>

```bash
python -m autoflyer fetch-binance --end 2026-04-30 --output var/btc_usdt_1d.csv
```

| オプション | デフォルト | 説明 |
|---|---|---|
| `--start` | `2017-08-17` | 開始日 |
| `--end` | 必須 | 終了日 |
| `--output` | `data/btc_usdt_1d.csv` | 出力先 |

</details>

<details>
<summary><b>update</b> — 差分更新（毎日自動実行）</summary>

```bash
python -m autoflyer update --output var/btc_usdt_1d.csv
```

CSVの最終日から今日まで自動追記。`run.sh` 起動中は毎日 09:05 JST に自動実行。

</details>

<details>
<summary><b>backtest</b> — バックテスト</summary>

```bash
# 単一バリアント
python -m autoflyer backtest --csv data/btc_usdt_1d.csv --timeframe 1D --variant MA200_STOP1.5ATR_GARCH40

# ウォークフォワード（2025年以降をテスト期間に）
python -m autoflyer backtest --csv data/btc_usdt_1d.csv --timeframe 1D --train-end 2025-01-01

# トレード結果をCSV保存
python -m autoflyer backtest --csv data/btc_usdt_1d.csv --timeframe 1D --out-trades results/trades.csv
```

</details>

<details>
<summary><b>bot</b> — ライブ取引ボット</summary>

```bash
# ドライラン
python -m autoflyer bot --timeframe 1D --variant MA200_STOP1.5ATR_GARCH40 --amount 300000

# 本番
python -m autoflyer bot --live --timeframe 1D --variant MA200_STOP1.5ATR_GARCH40 --amount 300000
```

| オプション | デフォルト | 説明 |
|---|---|---|
| `--live` | false | 実注文を有効化 |
| `--timeframe` | `1D` | 時間足 |
| `--variant` | `STOP_3ATR` | 戦略バリアント |
| `--amount` | `0` | 取引上限（JPY、0=残高全額） |
| `--interval` | `60` | ポーリング間隔（秒） |
| `--max-dd-pct` | `20.0` | サーキットブレーカー閾値（%） |
| `--state` | `state.json` | ポジション状態ファイル |

</details>

<details>
<summary><b>variants</b> — 戦略バリアント一覧</summary>

```bash
python -m autoflyer variants
```

</details>

---

## ファイル構成

```
autoflyer/
├── autoflyer/
│   ├── __main__.py       CLI エントリポイント
│   ├── bot.py            ライブ取引ロジック
│   ├── backtest.py       バックテストエンジン
│   ├── strategy.py       戦略バリアント定義
│   ├── indicators.py     テクニカル指標（MA, ATR, ADX, RSI, MACD）
│   ├── garch_sizing.py   GARCHボラティリティ ポジションサイジング
│   ├── dashboard.py      監視ダッシュボード（FastAPI）
│   ├── data.py           データ読み込み・リサンプリング
│   ├── fees.py           bitFlyer 手数料モデル
│   ├── config.py         定数・設定
│   └── report.py         集計・表示
├── var/                  実行時データ（gitignored）
│   ├── bot.log
│   ├── run.log
│   ├── run.pid
│   ├── state.json
│   └── btc_usdt_1d.csv
├── tests/
├── run.sh                起動・停止スクリプト
├── .env.example
└── pyproject.toml
```

---

## リスク管理

| 設定 | 推奨値 | 効果 |
|---|---|---|
| `--amount` | 許容損失額 / 0.243 | 最大DDに基づいたポジション上限 |
| `--max-dd-pct` | `30` | 30%DD到達で自動停止 |
| `DRY_RUN=1` | 本番移行前 | 実注文なしで動作確認 |

> **免責事項**: 本ソフトウェアは教育・研究目的で提供されており、いかなる投資成果も保証しません。
> 本ソフトウェアを使用した取引による損失・損害について、作者および貢献者は一切の責任を負いません。
> 過去のバックテスト結果は将来のパフォーマンスを保証するものではありません。
> 余剰資金の範囲内で、リスクを十分に理解した上でご利用ください。
