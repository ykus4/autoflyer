#!/bin/bash
set -e

cd "$(dirname "$0")"

[ -f .env ] && set -a && source .env && set +a

VAR=var
mkdir -p "$VAR"

PIDFILE="$VAR/run.pid"

start() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    echo "すでに起動しています (PID: $(cat $PIDFILE))"
    exit 1
  fi

  echo "→ ボットを起動します..."
  uv run python -m autoflyer bot \
    --live \
    --timeframe "${TIMEFRAME:-1D}" \
    --variant "${VARIANT:-MA200_STOP1.5ATR_GARCH40}" \
    --amount "${TRADE_AMOUNT_JPY:-0}" \
    --interval "${POLL_INTERVAL_SEC:-60}" \
    --state "$VAR/state.json" \
    --log-file "$VAR/bot.log" \
    --max-dd-pct 30 \
    >> "$VAR/run.log" 2>&1 &
  BOT_PID=$!

  echo "→ データ自動更新ループを起動します..."
  (
    while true; do
      now=$(date -u +%s)
      next=$(( ( (now / 86400) + 1 ) * 86400 + 300 ))
      sleep $(( next - now ))
      uv run python -m autoflyer update --output "$VAR/btc_usdt_1d.csv" >> "$VAR/run.log" 2>&1
    done
  ) &
  UPDATER_PID=$!

  echo "→ ダッシュボードを起動します (http://localhost:8080)..."
  uv run python -m autoflyer dashboard \
    --state "$VAR/state.json" \
    --log-file "$VAR/bot.log" \
    --port 8080 \
    >> "$VAR/run.log" 2>&1 &
  DASHBOARD_PID=$!

  echo "$BOT_PID $UPDATER_PID $DASHBOARD_PID" > "$PIDFILE"
  echo "✓ 起動完了 (PIDs: $BOT_PID $UPDATER_PID $DASHBOARD_PID)"
  echo "  ログ: tail -f $VAR/run.log"
  echo "  停止: $0 stop"
}

stop() {
  echo "→ 停止します..."
  # PIDファイルに記録されたプロセスを終了
  if [ -f "$PIDFILE" ]; then
    kill $(cat "$PIDFILE") 2>/dev/null || true
    rm -f "$PIDFILE"
  fi
  # ポート8080を占有しているプロセスも強制終了
  fuser -k 8080/tcp 2>/dev/null || true
  echo "✓ 停止しました"
}

status() {
  if [ ! -f "$PIDFILE" ]; then
    echo "停止中"
    return
  fi
  read -r BOT_PID UPDATER_PID DASHBOARD_PID < "$PIDFILE"
  for pid in $BOT_PID $UPDATER_PID $DASHBOARD_PID; do
    if kill -0 "$pid" 2>/dev/null; then
      echo "稼働中 (PID: $pid)"
    else
      echo "停止済 (PID: $pid)"
    fi
  done
}

case "${1:-start}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; sleep 1; start ;;
  status)  status ;;
  *)
    echo "使い方: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
