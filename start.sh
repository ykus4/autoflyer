#!/bin/bash
# サーバー上で実行するだけで全プロセスを起動する
# 使い方: ./start.sh

set -e

cd "$(dirname "$0")"

source "$HOME/.local/bin/env" 2>/dev/null || true

mkdir -p logs data
# zipの展開でstate.jsonがディレクトリになることがあるので削除
[ -d state.json ] && rm -rf state.json

# 既存プロセスを停止
pkill -f "autoflyer bot" 2>/dev/null || true
pkill -f "autoflyer dashboard" 2>/dev/null || true
sleep 1

uv sync --no-dev --frozen

nohup uv run python -m autoflyer bot \
  --timeframe 1D \
  --variant MA200_STOP1.5ATR_GARCH40 \
  --log-file logs/bot.log \
  --max-dd-pct 30 \
  --live \
  >> logs/bot.log 2>&1 &
echo "bot PID: $!"

nohup uv run python -m autoflyer dashboard \
  --log-file logs/bot.log \
  --port 8080 \
  >> logs/dashboard.log 2>&1 &
echo "dashboard PID: $!"

echo "✓ 起動完了 — tail -f logs/bot.log でログ確認"
