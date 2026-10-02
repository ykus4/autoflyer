"""Notifications for bot events: email (SMTP), Slack webhook and LINE Messaging API.

Every channel sends in a background thread (fire-and-forget) so a slow or failing
endpoint never blocks the trading loop. `create_notifier()` enables each channel
whose environment variables are set and fans messages out to all of them.
"""

from __future__ import annotations

import logging
import os
import smtplib
import threading
from email.mime.text import MIMEText
from typing import Protocol

import requests

log = logging.getLogger("autoflyer.notify")

_SUBJECT_PREFIX = "[autoflyer]"
_LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
_HTTP_TIMEOUT = 15
_LINE_MAX_CHARS = 5000  # LINE テキストメッセージの上限


class Notifier(Protocol):
    @property
    def enabled(self) -> bool: ...

    def send(self, subject: str, body: str) -> None: ...


class _AsyncNotifier:
    """`_send_sync` をバックグラウンドスレッドで実行する共通部分。"""

    name = "notifier"

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, subject: str, body: str) -> None:
        if not self._enabled:
            return
        threading.Thread(target=self._safe_send, args=(subject, body), daemon=True).start()

    def _safe_send(self, subject: str, body: str) -> None:
        try:
            self._send_sync(subject, body)
            log.info("%s sent: %s", self.name, subject)
        except (smtplib.SMTPException, OSError, requests.RequestException) as e:
            log.error("Failed to send %s (%s): %s", self.name, subject, e)

    def _send_sync(self, subject: str, body: str) -> None:
        raise NotImplementedError


class EmailNotifier(_AsyncNotifier):
    """SMTP (STARTTLS) でメールを送る。"""

    name = "email"

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_pass: str,
        to_addr: str,
        from_addr: str | None = None,
    ) -> None:
        super().__init__(all([smtp_host, smtp_user, smtp_pass, to_addr]))
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_user
        self._pass = smtp_pass
        self._to = to_addr
        self._from = from_addr or smtp_user

    def _send_sync(self, subject: str, body: str) -> None:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = f"{_SUBJECT_PREFIX} {subject}"
        msg["From"] = self._from
        msg["To"] = self._to
        with smtplib.SMTP(self._host, self._port, timeout=_HTTP_TIMEOUT) as srv:
            srv.starttls()
            srv.login(self._user, self._pass)
            srv.sendmail(self._from, [self._to], msg.as_string())


class SlackNotifier(_AsyncNotifier):
    """Slack の Incoming Webhook に投稿する。"""

    name = "slack"

    def __init__(self, webhook_url: str) -> None:
        super().__init__(bool(webhook_url))
        self._url = webhook_url

    def _send_sync(self, subject: str, body: str) -> None:
        resp = requests.post(
            self._url,
            json={"text": f"*{_SUBJECT_PREFIX} {subject}*\n{body}"},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()


class LineNotifier(_AsyncNotifier):
    """LINE Messaging API のプッシュメッセージで送る（LINE Notify は 2025 年に終了）。"""

    name = "line"

    def __init__(self, channel_token: str, to: str) -> None:
        super().__init__(bool(channel_token and to))
        self._token = channel_token
        self._to = to

    def _send_sync(self, subject: str, body: str) -> None:
        text = f"{_SUBJECT_PREFIX} {subject}\n{body}"[:_LINE_MAX_CHARS]
        resp = requests.post(
            _LINE_PUSH_URL,
            headers={"Authorization": f"Bearer {self._token}"},
            json={"to": self._to, "messages": [{"type": "text", "text": text}]},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()


class MultiNotifier:
    """有効なチャネルすべてに同じ通知を送る。"""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self._notifiers = [n for n in notifiers if n.enabled]

    @property
    def enabled(self) -> bool:
        return bool(self._notifiers)

    def send(self, subject: str, body: str) -> None:
        for n in self._notifiers:
            n.send(subject, body)


def create_notifier() -> MultiNotifier:
    """環境変数から通知チャネルを組み立てる。設定のないチャネルは無効。"""
    env = os.environ.get
    channels: list[Notifier] = [
        EmailNotifier(
            smtp_host=env("SMTP_HOST", ""),
            smtp_port=int(env("SMTP_PORT", "587")),
            smtp_user=env("SMTP_USER", ""),
            smtp_pass=env("SMTP_PASS", ""),
            to_addr=env("NOTIFY_TO", ""),
            from_addr=env("SMTP_FROM", ""),
        ),
        SlackNotifier(env("SLACK_WEBHOOK_URL", "")),
        LineNotifier(env("LINE_CHANNEL_TOKEN", ""), env("LINE_TO", "")),
    ]
    notifier = MultiNotifier(channels)
    enabled = [type(c).__name__ for c in channels if c.enabled]
    if enabled:
        log.info("Notifications enabled: %s", ", ".join(enabled))
    else:
        log.warning("Notifications disabled — no channel configured")
    return notifier
