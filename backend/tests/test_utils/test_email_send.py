"""The SMTP sender: headers spam filters expect, and both TLS modes."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config import get_settings
from app.utils import email


@pytest.fixture
def smtp(monkeypatch):
    s = get_settings()
    for k, v in {
        "smtp_host": "mail.example.com",
        "smtp_port": 587,
        "smtp_user": "hello@example.com",
        "smtp_password": "pw",
        "from_email": "",
        "from_name": "Weirling",
        "reply_to_email": "support@example.com",
    }.items():
        monkeypatch.setattr(s, k, v)
    servers = {"plain": MagicMock(), "ssl": MagicMock()}
    for server in servers.values():
        server.__enter__.return_value = server
    monkeypatch.setattr(email.smtplib, "SMTP", MagicMock(return_value=servers["plain"]))
    monkeypatch.setattr(email.smtplib, "SMTP_SSL", MagicMock(return_value=servers["ssl"]))
    return servers


def test_sends_with_starttls_and_full_headers(smtp):
    assert email.send_email_sync("reader@example.org", "Hi", "<p>hi</p>", "hi")
    server = smtp["plain"]
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("hello@example.com", "pw")
    msg = server.send_message.call_args.args[0]
    assert server.send_message.call_args.kwargs["from_addr"] == "hello@example.com"
    assert msg["From"] == "Weirling <hello@example.com>"
    assert msg["Reply-To"] == "support@example.com"
    assert msg["Date"]
    assert msg["Message-ID"].endswith("@example.com>")
    smtp["ssl"].send_message.assert_not_called()


def test_port_465_uses_implicit_tls(smtp, monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_port", 465)
    assert email.send_email_sync("reader@example.org", "Hi", "<p>hi</p>")
    smtp["ssl"].send_message.assert_called_once()
    smtp["ssl"].starttls.assert_not_called()


def test_from_email_and_explicit_reply_to(smtp, monkeypatch):
    monkeypatch.setattr(get_settings(), "from_email", "news@example.com")
    assert email.send_email_sync("r@example.org", "Hi", "<p>hi</p>", reply_to="me@example.com")
    msg = smtp["plain"].send_message.call_args.args[0]
    assert msg["From"] == "Weirling <news@example.com>"
    assert msg["Reply-To"] == "me@example.com"


def test_not_configured_sends_nothing(smtp, monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_password", "")
    assert not email.send_email_sync("r@example.org", "Hi", "<p>hi</p>")
    smtp["plain"].send_message.assert_not_called()
