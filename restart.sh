#!/bin/bash
# サーバー上で実行: ./restart.sh
set -e

docker ps -aq | xargs docker rm -f 2>/dev/null || true
docker compose up --build -d
docker compose logs --tail=20 bot
