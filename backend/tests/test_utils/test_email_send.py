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


class _Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self.text = str(self._body)

    def json(self):
        return self._body


@pytest.fixture
def resend(monkeypatch):
    s = get_settings()
    for k, v in {
        "email_provider": "auto",
        "resend_api_key": "re_test",
        "from_email": "hello@mail.example.com",
        "from_name": "Weirling",
        "reply_to_email": "support@example.com",
    }.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(email.time, "sleep", lambda _s: None)
    calls = []

    def post(url, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers})
        return responses.pop(0)

    responses: list = []
    import httpx

    monkeypatch.setattr(httpx, "post", post)
    return calls, responses


def test_resend_is_used_when_a_key_is_set(smtp, resend):
    calls, responses = resend
    responses.append(_Resp(200, {"id": "abc"}))
    assert email.send_email_sync(
        "r@example.org",
        "Hi",
        "<p>hi</p>",
        "hi",
        extra_headers={"List-Unsubscribe": "<https://x/u>"},
        idempotency_key="verify/1",
    )
    sent = calls[0]
    assert sent["url"] == "https://api.resend.com/emails"
    assert sent["json"]["from"] == "Weirling <hello@mail.example.com>"
    assert sent["json"]["to"] == ["r@example.org"]
    assert sent["json"]["reply_to"] == "support@example.com"
    assert sent["json"]["headers"] == {"List-Unsubscribe": "<https://x/u>"}
    assert sent["headers"]["Idempotency-Key"] == "verify/1"
    smtp["plain"].send_message.assert_not_called()


def test_resend_retries_429_and_5xx_but_not_other_4xx(resend):
    calls, responses = resend
    responses.extend(
        [_Resp(429, headers={"retry-after": "1"}), _Resp(502), _Resp(200, {"id": "x"})]
    )
    assert email.send_email_sync("r@example.org", "Hi", "<p>hi</p>")
    assert len(calls) == 3

    calls.clear()
    responses.extend([_Resp(403, {"message": "domain not verified"})])
    assert not email.send_email_sync("r@example.org", "Hi", "<p>hi</p>")
    assert len(calls) == 1


def test_email_provider_setting():
    from pydantic import ValidationError

    from app.config import Settings

    assert Settings(_env_file=None, resend_api_key="re_x").email_transport == "resend"
    assert Settings(_env_file=None, resend_api_key="").email_transport == "smtp"
    assert (
        Settings(_env_file=None, resend_api_key="re_x", email_provider="SMTP").email_transport
        == "smtp"
    )
    with pytest.raises(ValidationError, match="EMAIL_PROVIDER"):
        Settings(_env_file=None, email_provider="sendgrid")
