#!/bin/bash
# autoflyer systemd セットアップスクリプト
# 使い方: sudo ./install-systemd.sh
set -e

if [ "$(id -u)" -ne 0 ]; then
  echo "sudo で実行してください: sudo ./install-systemd.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# リポジトリのルート（このスクリプトは deploy/ 配下にある想定）
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "→ 既存の run.sh プロセスを停止..."
"$SCRIPT_DIR/run.sh" stop 2>/dev/null || true

echo "→ サービスファイルをコピー..."
cp "$SCRIPT_DIR/autoflyer-bot.service" /etc/systemd/system/
cp "$SCRIPT_DIR/autoflyer-dashboard.service" /etc/systemd/system/
cp "$SCRIPT_DIR/autoflyer-updater.service" /etc/systemd/system/
cp "$SCRIPT_DIR/autoflyer-updater.timer" /etc/systemd/system/

# WorkingDirectory 等をこのリポジトリのルートに書き換え
sed -i "s|WorkingDirectory=.*|WorkingDirectory=$REPO_DIR|g" /etc/systemd/system/autoflyer-*.service
sed -i "s|ExecStart=.*/.venv/bin/python|ExecStart=$REPO_DIR/.venv/bin/python|g" /etc/systemd/system/autoflyer-*.service
sed -i "s|EnvironmentFile=.*/.env|EnvironmentFile=$REPO_DIR/.env|g" /etc/systemd/system/autoflyer-*.service

echo "→ systemd リロード..."
systemctl daemon-reload

echo "→ サービス有効化 & 起動..."
systemctl enable --now autoflyer-bot
systemctl enable --now autoflyer-dashboard
systemctl enable --now autoflyer-updater.timer

echo ""
echo "✓ セットアップ完了"
echo ""
echo "  状態確認: systemctl status autoflyer-bot"
echo "  ログ:     journalctl -u autoflyer-bot -f"
echo "  再起動:   systemctl restart autoflyer-bot"
echo "  停止:     systemctl stop autoflyer-bot"
