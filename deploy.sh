#!/bin/bash
# GCP Compute Engine へデプロイするスクリプト
# 使い方: ./deploy.sh
# 事前準備: gcloud auth login

set -e

INSTANCE=instance-20260429-025541
ZONE=us-central1-f
PROJECT=yottinoproject
REMOTE_DIR="~/autoflyer"

echo "→ $INSTANCE ($ZONE) にデプロイします..."

gcloud compute scp --recurse \
  --exclude='.env' \
  --exclude='data' \
  --exclude='logs' \
  --exclude='state.json' \
  --exclude='state.json.bak' \
  --exclude='equity.jsonl' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.venv' \
  --project="$PROJECT" \
  --zone="$ZONE" \
  . "$INSTANCE:$REMOTE_DIR"

echo "→ Docker を再ビルド・再起動します..."
gcloud compute ssh "$INSTANCE" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  -- "cd $REMOTE_DIR && docker compose up --build -d"

echo "✓ デプロイ完了"
echo "  ログ確認: gcloud compute ssh $INSTANCE --project=$PROJECT --zone=$ZONE -- 'docker compose -f $REMOTE_DIR/docker-compose.yml logs -f bot'"
