"""Tests for notification channels (no network)."""

import pytest
import requests

from autoflyer import notifications
from autoflyer.notifications import (
    EmailNotifier,
    LineNotifier,
    MultiNotifier,
    SlackNotifier,
    create_notifier,
)


class FakeResp:
    def raise_for_status(self):
        pass


@pytest.fixture
def posts(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        return FakeResp()

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    return calls


def test_slack_payload(posts):
    SlackNotifier("https://hooks.slack.test/x")._send_sync("件名", "本文")
    url, kw = posts[0]
    assert url == "https://hooks.slack.test/x"
    assert kw["json"]["text"] == "*[autoflyer] 件名*\n本文"


def test_line_payload(posts):
    LineNotifier("TOKEN", "U123")._send_sync("件名", "x" * 6000)
    url, kw = posts[0]
    assert url.endswith("/v2/bot/message/push")
    assert kw["headers"]["Authorization"] == "Bearer TOKEN"
    assert kw["json"]["to"] == "U123"
    assert len(kw["json"]["messages"][0]["text"]) == 5000  # 上限で切り詰める


def test_disabled_channels_are_skipped():
    multi = MultiNotifier(
        [SlackNotifier(""), LineNotifier("t", ""), EmailNotifier("", 0, "", "", "")]
    )
    assert multi.enabled is False
    multi.send("s", "b")  # 何も起きない


def test_send_failure_is_logged_not_raised(monkeypatch, caplog):
    def boom(url, **kw):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(notifications.requests, "post", boom)
    SlackNotifier("https://x")._safe_send("s", "b")
    assert "Failed to send slack" in caplog.text


def test_create_notifier_from_env(monkeypatch):
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "NOTIFY_TO", "LINE_CHANNEL_TOKEN", "LINE_TO"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://x")
    assert create_notifier().enabled is True
    monkeypatch.delenv("SLACK_WEBHOOK_URL")
    assert create_notifier().enabled is False
