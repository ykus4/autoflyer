# autoflyer

> BitFlyer FX_BTC_JPY 自動売買ボット — ドンチャンブレイクアウト + GARCH戦略

[![CI](https://github.com/ykus4/autoflyer/actions/workflows/ci.yml/badge.svg)](https://github.com/ykus4/autoflyer/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10+-blue)
![License](https://img.shields.io/badge/license-MIT-blue)

> [!WARNING]
> **本ソフトウェアは教育・研究目的で公開しています。**
> 実際の資産運用に使用した場合の損失について、作者は一切の責任を負いません。
> 仮想通貨取引は元本割れのリスクがあります。自己責任のもとでご利用ください。

---

## 推奨戦略

4.3年間バックテスト（2022–2026）で全バリアント・全時間足中 最高リターン:

```
BREAKOUT_STOP1.5_GARCH40 (1D)
```

| 指標 | 値 |
|---|---|
| リターン | **+306%** (100万→406万) |
| PF | **6.03** |
| 最大DD | 18.5% |
| トレード数 | 14 |
| 勝率 | 50% |

- **ドンチャン20日ブレイクアウト** — 20日高値更新でロングエントリー（トレンド初動を捕捉）
- **MA200フィルター** — MA200より上の時のみエントリー（ベア相場を完全回避）
- **1.5× ATRストップ** — 毎日最新ATRで更新される動的ストップロス
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
VARIANT=BREAKOUT_STOP1.5_GARCH40   # 推奨バリアント
TIMEFRAME=1D                       # 時間足
TRADE_AMOUNT_JPY=100000            # 1取引あたりの上限（JPY）
DASHBOARD_USER=admin               # ダッシュボード認証（設定すると外部公開）
DASHBOARD_PASS=changeme
```

> **`.env` と `config.py` の違い**
> `.env` はライブbot専用の実行時設定（環境ごとに変わる値・秘密情報）。
> `config.py` はMAの期間やATRの長さなどアルゴリズムの定数（全環境共通）。

**3. 初回データ取得**

```bash
uv sync
python -m autoflyer fetch-binance --end 2026-05-07 --output var/btc_usdt_1d.csv
```

**4. 起動**

```bash
./run.sh start
tail -f var/run.log
```

```
2026-05-07 09:00:01 [INFO] Bot start — variant=BREAKOUT_STOP1.5_GARCH40  dry_run=True
2026-05-07 09:00:02 [INFO] signal_up=False  signal_down=False  in_pos=False  dd=0.0%
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
python -m autoflyer fetch-binance --end 2026-05-07 --output var/btc_usdt_1d.csv
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
python -m autoflyer backtest --csv var/btc_usdt_1d.csv --timeframe 1D --variant BREAKOUT_STOP1.5_GARCH40

# 複数時間足 × 全バリアントをグリッドサーチ
python -m autoflyer backtest --csv var/btc_usdt_1d.csv

# ウォークフォワード（2025年以降をOOSテスト期間に）
python -m autoflyer backtest --csv var/btc_usdt_1d.csv --timeframe 1D --train-end 2025-01-01

# トレード結果をCSV保存
python -m autoflyer backtest --csv var/btc_usdt_1d.csv --timeframe 1D --out-trades var/trades.csv
```

| オプション | デフォルト | 説明 |
|---|---|---|
| `--csv` | `data/btc_jpy_1m.csv` | 入力データ |
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
  --variant BREAKOUT_STOP1.5_GARCH40 \
  --amount 100000 \
  --state var/state.json \
  --log-file var/bot.log

# 本番（--live を付けると実注文）
python -m autoflyer bot --live \
  --timeframe 1D \
  --variant BREAKOUT_STOP1.5_GARCH40 \
  --amount 100000 \
  --max-dd-pct 25 \
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

主要バリアント:

| バリアント | エントリー | フィルター | ストップ | サイジング |
|---|---|---|---|---|
| `BREAKOUT_STOP1.5_GARCH40` | ブレイクアウト | MA200 | 1.5ATR | GARCH 40% |
| `BREAKOUT_STOP2_GARCH40` | ブレイクアウト | MA200 | 2ATR | GARCH 40% |
| `MA200_STOP1.5ATR_GARCH40` | MAクロス | MA200 | 1.5ATR | GARCH 40% |
| `BREAKOUT_HURST55_Z1.5_STOP1.5_GARCH40` | ブレイクアウト | MA200+Hurst+z-score | 1.5ATR | GARCH 40% |
| `BREAKOUT_MAE_MA200_GARCH40` | ブレイクアウト | MA200 | MAE統計 | GARCH 40% |
| `BREAKOUT_MA200_STOP1.5_KELLY` | ブレイクアウト | MA200 | 1.5ATR | Kelly基準 |

</details>

---

## 戦略一覧

### エントリー方式

| 方式 | 説明 | 代表バリアント |
|---|---|---|
| **ドンチャンブレイクアウト** | 20日高値更新でエントリー | `BREAKOUT_*` |
| MAクロス (20/50) | MA20がMA50を上抜けでエントリー | `BASE`, `MA200_*` |
| ハイブリッド | MAクロス + ドンチャン確認 | `HYBRID_*` |

### フィルター

| フィルター | 説明 |
|---|---|
| MA200 | 価格 > MA200 でのみロング |
| Hurst指数 | H > 閾値（トレンド状態確認） |
| z-score | ブレイクアウトの統計的有意性 |
| HMM | 隠れマルコフモデルによるレジーム判定 |
| ADX | トレンド強度フィルター |
| ATR回避 | 高ボラ時のエントリー回避 |

### サイジング

| 方式 | 説明 |
|---|---|
| GARCH | ボラティリティ予測に基づく動的サイジング |
| Kelly基準 | 統計的に最適なベットサイズ（DD極小） |
| Risk% | 残高の N% をリスクとして固定 |

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
│   │   ├── stats_filters.py 統計フィルター（Hurst, HMM, Kelly, z-score, MAE）
│   │   └── fees.py          bitFlyer 手数料ティアモデル
│   ├── analysis/            バックテスト・データ分析
│   │   ├── backtest.py      バックテストエンジン
│   │   ├── data.py          CSV読み込み・OHLCVリサンプリング
│   │   └── report.py        集計・表示
│   └── templates/
│       └── dashboard.html   ダッシュボード UI
├── var/                     実行時データ（gitignored）
├── tests/
├── BACKTEST_RESULTS.md      全バリアントのバックテスト結果
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
| `--amount` | 許容損失額 / 0.185 | 最大DDに基づいたポジション上限 |
| `--max-dd-pct` | `25` | 25%DD到達で自動停止 |
| `DRY_RUN=1` | 本番移行前 | 実注文なしで動作確認 |
| GARCH sizing | 自動 | 高ボラ時にポジション自動縮小 |

> **免責事項**: 本ソフトウェアは教育・研究目的で提供されており、いかなる投資成果も保証しません。
> 本ソフトウェアを使用した取引による損失・損害について、作者および貢献者は一切の責任を負いません。
> 過去のバックテスト結果は将来のパフォーマンスを保証するものではありません。
> 余剰資金の範囲内で、リスクを十分に理解した上でご利用ください。
