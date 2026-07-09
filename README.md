# autoflyer

> BitFlyer FX_BTC_JPY 自動売買ボット — ドンチャンブレイクアウト + GARCH戦略

[![CI](https://github.com/ykus4/autoflyer/actions/workflows/ci.yml/badge.svg)](https://github.com/ykus4/autoflyer/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10+-blue)
![License](https://img.shields.io/badge/license-MIT-blue)

> [!WARNING]
> 本ソフトウェアは教育・研究目的で公開しています。実際の資産運用に使用した場合の
> 損失について、作者は一切の責任を負いません。自己責任のもとでご利用ください。

## ドキュメント

📖 **[ドキュメントサイト](https://ykus4.github.io/autoflyer/)** — セットアップ・CLI・戦略・運用の完全ガイド

- [使い方ガイド](docs/index.md)
- [バックテスト結果](docs/backtest-results.md)

## クイックスタート

```bash
git clone https://github.com/ykus4/autoflyer.git && cd autoflyer
uv sync
cp .env.example .env          # APIキー・戦略設定を編集

# 初回データ取得 → バックテスト
python -m autoflyer fetch-binance --end 2026-05-07 --output var/btc_usdt_1d.csv
python -m autoflyer backtest --csv var/btc_usdt_1d.csv --timeframe 1D

# 起動（ドライラン／ライブは .env の DRY_RUN で切替）
./deploy/run.sh start
```

推奨戦略は `BREAKOUT_STOP1.0_GARCH40`（1D）。詳細・全バリアントの成績は
[バックテスト結果](docs/backtest-results.md) を参照してください。

## 開発

```bash
uv run pytest tests/ -q
uv run ruff check autoflyer/ tests/
uv run mypy autoflyer/
```

## ライセンス

[MIT](LICENSE)
