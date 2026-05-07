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
VARIANT=MA200_STOP1.5ATR_GARCH40   # 使用するバリアント
TIMEFRAME=1D                       # 時間足
TRADE_AMOUNT_JPY=50000             # 1取引あたりの上限（JPY）
DASHBOARD_USER=admin               # ダッシュボード認証（設定すると外部公開）
DASHBOARD_PASS=changeme
```

> **`.env` と `config.py` の違い**
> `.env` はライブbot専用の実行時設定（環境ごとに変わる値・秘密情報）。
> `config.py` はMAの期間やATRの長さなどアルゴリズムの定数（全環境共通）。

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
python -m autoflyer fetch-binance --end 2026-05-01 --output var/btc_usdt_1d.csv
```

| オプション | デフォルト | 説明 |
|---|---|---|
| `--start` | `2017-08-17` | 開始日 |
| `--end` | 必須 | 終了日（今日の日付を指定） |
| `--symbol` | `BTCUSDT` | 取引ペア |
| `--output` | `data/btc_usdt_1d.csv` | 出力先 |

</details>

<details>
<summary><b>update</b> — 差分更新（毎日自動実行）</summary>

```bash
python -m autoflyer update --output var/btc_usdt_1d.csv
```

CSVの最終日から今日まで差分のみ取得して追記する。`run.sh` 起動中は毎日 09:05 JST に自動実行。

| オプション | デフォルト | 説明 |
|---|---|---|
| `--output` | `data/btc_usdt_1d.csv` | 更新対象CSV |
| `--symbol` | `BTCUSDT` | 取引ペア |

</details>

<details>
<summary><b>backtest</b> — バックテスト</summary>

```bash
# 推奨バリアントを1D足で検証
python -m autoflyer backtest --csv var/btc_usdt_1d.csv --timeframe 1D --variant MA200_STOP1.5ATR_GARCH40

# 複数時間足 × 全バリアントをグリッドサーチ
python -m autoflyer backtest --csv var/btc_usdt_1d.csv

# ウォークフォワード（2025年以降をOOSテスト期間に）
python -m autoflyer backtest --csv var/btc_usdt_1d.csv --timeframe 1D --train-end 2025-01-01

# トレード結果をCSV保存
python -m autoflyer backtest --csv var/btc_usdt_1d.csv --timeframe 1D --out-trades var/trades.csv
```

| オプション | デフォルト | 説明 |
|---|---|---|
| `--csv` | `data/btc_usdt_1d.csv` | 入力データ |
| `--timeframe` | 全時間足 | 検証する時間足（複数指定可: `1D 12H`） |
| `--variant` | 全バリアント | 検証するバリアント名（複数指定可） |
| `--train-end` | なし | ウォークフォワード分割日（これ以降がテスト期間） |
| `--out-trades` | なし | トレード結果のCSV出力先 |

</details>

<details>
<summary><b>bot</b> — ライブ取引ボット</summary>

```bash
# ドライラン（注文なし・動作確認）
python -m autoflyer bot \
  --timeframe 1D \
  --variant MA200_STOP1.5ATR_GARCH40 \
  --amount 300000 \
  --state var/state.json \
  --log-file var/bot.log

# 本番（--live を付けると実注文）
python -m autoflyer bot --live \
  --timeframe 1D \
  --variant MA200_STOP1.5ATR_GARCH40 \
  --amount 300000 \
  --max-dd-pct 30 \
  --state var/state.json \
  --log-file var/bot.log
```

| オプション | デフォルト | 説明 |
|---|---|---|
| `--live` | false | 実注文を有効化（未指定はドライラン） |
| `--timeframe` | `1D` | 時間足（`1H` `3H` `6H` `12H` `1D` `3D`） |
| `--variant` | `STOP_3ATR` | 戦略バリアント名 |
| `--amount` | `0` | 1取引あたりの上限（JPY、`0`=残高全額） |
| `--interval` | `60` | ポーリング間隔（秒） |
| `--max-dd-pct` | `20.0` | サーキットブレーカー発動閾値（%） |
| `--state` | `state.json` | ポジション状態ファイルのパス |
| `--log-file` | なし | ログファイルのパス |

</details>

<details>
<summary><b>dashboard</b> — 監視ダッシュボード</summary>

```bash
python -m autoflyer dashboard \
  --state var/state.json \
  --log-file var/bot.log \
  --port 8080
```

`DASHBOARD_USER` / `DASHBOARD_PASS` を `.env` に設定すると Basic 認証付きで外部公開される。未設定時は `localhost` のみ。

| オプション | デフォルト | 説明 |
|---|---|---|
| `--state` | `state.json` | ポジション状態ファイルのパス |
| `--log-file` | なし | ログファイルのパス |
| `--port` | `8080` | リッスンするポート番号 |

</details>

<details>
<summary><b>variants</b> — 戦略バリアント一覧</summary>

```bash
python -m autoflyer variants
```

| バリアント | MA200 | ADX | ストップ | リスク% | GARCH | 説明 |
|---|:---:|:---:|:---:|:---:|:---:|---|
| `BASE` | | | | | | MAクロスのみ（ベースライン） |
| `MA200_FILTER` | ✓ | | | | | MA200フィルターのみ |
| `ADX20` | | 20 | | | | ADX>20でのみエントリー |
| `STOP_3ATR` | | | 3ATR | | | 3ATRストップロス |
| `STOP_4ATR` | | | 4ATR | | | 4ATRストップロス |
| `STOP_5ATR` | | | 5ATR | | | 5ATRストップロス |
| `MA200_STOP3` | ✓ | | 3ATR | | | |
| `MA200_STOP2` | ✓ | | 2ATR | | | |
| `ADX20_STOP3` | | 20 | 3ATR | | | |
| `MA200_ADX20_STOP3` | ✓ | 20 | 3ATR | | | |
| `RISK1PCT_STOP3` | | | 3ATR | 1% | | リスク1%サイジング |
| `RISK2PCT_STOP3` | | | 3ATR | 2% | | リスク2%サイジング |
| `MA200_RISK1PCT_STOP3` | ✓ | | 3ATR | 1% | | |
| `MA200_RISK2PCT_STOP3` | ✓ | | 3ATR | 2% | | |
| `MA200_STOP2_RISK1PCT` | ✓ | | 2ATR | 1% | | |
| `MA200_STOP2_RISK2PCT` | ✓ | | 2ATR | 2% | | |
| `CHAN_3ATR` | | | チャンデリア3ATR | | | トレーリングストップ |
| `CHAN_2ATR` | | | チャンデリア2ATR | | | トレーリングストップ |
| `MA200_CHAN3` | ✓ | | チャンデリア3ATR | | | |
| `MA200_CHAN2` | ✓ | | チャンデリア2ATR | | | |
| `DON_BREAK` | | | | | | ドンチャンブレークアウト確認 |
| `DON_BREAK_STOP3` | | | 3ATR | | | |
| `MA200_DON_STOP3` | ✓ | | 3ATR | | | |
| `MA200_GARCH20` | ✓ | | | | 20% | GARCHサイジング |
| `MA200_GARCH30` | ✓ | | | | 30% | GARCHサイジング |
| `MA200_STOP2_GARCH20` | ✓ | | 2ATR | | 20% | |
| `MA200_STOP2_GARCH30` | ✓ | | 2ATR | | 30% | |
| `MA200_STOP1.5ATR_GARCH40` ⭐ | ✓ | | 1.5ATR | | 40% | **推奨** CAGR 29.1% / Calmar 1.20 |
| `SHORT_STOP3` | | | 3ATR | | | ショート両面 |
| `MA200_SHORT_STOP3` | ✓ | | 3ATR | | | ショート両面 + MA200 |
| `ATR_AVOID` | | | | | | 高ボラ回避 |
| `MA200_ATRAVOID` | ✓ | | | | | 高ボラ回避 + MA200 |

</details>

---

## メール通知

エラーや重要イベント発生時にメールで通知します。`.env` に SMTP 設定を追加するだけで有効化：

```env
SMTP_HOST=smtp.mail.me.com
SMTP_PORT=587
SMTP_USER=your_email@icloud.com
SMTP_PASS=<App用パスワード>
NOTIFY_TO=your_email@icloud.com
```

**通知されるイベント:**
- サーキットブレーカー発動（ドローダウン閾値超過）
- エントリー / エグジット / ストップロス到達
- HTTP / ネットワークエラー（リトライ後も失敗）
- 予期しないエラー

未設定の場合は通知なしで動作します（ログのみ）。

---

## ファイル構成

```
autoflyer/
├── autoflyer/
│   ├── __main__.py          CLI エントリポイント
│   ├── config.py            アルゴリズム定数（MA期間・ATR長など全環境共通）
│   ├── notifications.py     メール通知（SMTP）
│   ├── dashboard.py         監視ダッシュボード API（FastAPI）
│   ├── trading/             ライブ取引関連
│   │   ├── bot.py           ライブ取引ループ・BitFlyerClient
│   │   ├── strategy.py      バリアント定義（Variant dataclass・VARIANTS）
│   │   ├── indicators.py    テクニカル指標（MA, ATR, ADX, RSI, MACD）
│   │   ├── garch_sizing.py  GARCHボラティリティ ポジションサイジング
│   │   └── fees.py          bitFlyer 手数料ティアモデル
│   ├── analysis/            バックテスト・データ分析
│   │   ├── backtest.py      バックテストエンジン
│   │   ├── data.py          CSV読み込み・OHLCVリサンプリング
│   │   └── report.py        集計・表示
│   └── templates/
│       └── dashboard.html   ダッシュボード UI
├── var/                     実行時データ（gitignored）
│   ├── bot.log
│   ├── run.log
│   ├── run.pid
│   ├── state.json
│   └── btc_usdt_1d.csv
├── tests/
├── autoflyer-bot.service    systemd ユニットファイル
├── run.sh                   起動・停止スクリプト
├── .env                     実行時設定（gitignored）
├── .env.example             設定テンプレート
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
