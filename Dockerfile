FROM python:3.12-slim

WORKDIR /app

# uv をインストール
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 依存関係を先にコピーしてキャッシュ効率を高める
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

# ソースコードをコピー
COPY autoflyer/ ./autoflyer/

# 実行ユーザーを非 root に
RUN useradd -m botuser && mkdir -p /app/logs /app/data && chown -R botuser /app
USER botuser

ENTRYPOINT ["uv", "run", "python", "-m", "autoflyer"]
CMD ["bot"]
