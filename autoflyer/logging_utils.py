"""Logging setup shared by the CLI commands."""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime, timedelta, timezone

_JST = timezone(timedelta(hours=9))
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUPS = 3


class JSTFormatter(logging.Formatter):
    """タイムスタンプを JST で出力するフォーマッタ。"""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=_JST)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def setup_logging(log_file: str | None = None, level: int = logging.INFO) -> None:
    """標準出力（と任意でローテート付きファイル）へ INFO 以上を出力する。"""
    formatter = JSTFormatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUPS, encoding="utf-8"
            )
        )
    for h in handlers:
        h.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=handlers)
