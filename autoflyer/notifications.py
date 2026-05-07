"""Email notification for critical bot events."""

from __future__ import annotations

import logging
import smtplib
import threading
from email.mime.text import MIMEText

log = logging.getLogger("autoflyer.notify")


class EmailNotifier:
    """SMTP-based email alerter. Sends asynchronously to avoid blocking the bot loop."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_pass: str,
        to_addr: str,
        from_addr: str | None = None,
    ) -> None:
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_user
        self._pass = smtp_pass
        self._to = to_addr
        self._from = from_addr or smtp_user
        self._enabled = all([smtp_host, smtp_user, smtp_pass, to_addr])
        if not self._enabled:
            log.warning("Email notification disabled — SMTP settings incomplete")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, subject: str, body: str) -> None:
        """Send an email alert in a background thread (fire-and-forget)."""
        if not self._enabled:
            return
        t = threading.Thread(target=self._send_sync, args=(subject, body), daemon=True)
        t.start()

    def _send_sync(self, subject: str, body: str) -> None:
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = f"[autoflyer] {subject}"
            msg["From"] = self._from
            msg["To"] = self._to

            with smtplib.SMTP(self._host, self._port, timeout=15) as srv:
                srv.starttls()
                srv.login(self._user, self._pass)
                srv.sendmail(self._from, [self._to], msg.as_string())
            log.info("Email sent: %s", subject)
        except smtplib.SMTPException as e:
            log.error("Failed to send email (%s): %s", subject, e)
        except OSError as e:
            log.error("Email connection error (%s): %s", subject, e)


def create_notifier() -> EmailNotifier:
    """Create an EmailNotifier from environment variables."""
    import os

    return EmailNotifier(
        smtp_host=os.environ.get("SMTP_HOST", ""),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=os.environ.get("SMTP_USER", ""),
        smtp_pass=os.environ.get("SMTP_PASS", ""),
        to_addr=os.environ.get("NOTIFY_TO", ""),
        from_addr=os.environ.get("SMTP_FROM", ""),
    )
