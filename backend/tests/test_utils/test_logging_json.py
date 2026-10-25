"""#198: structured JSON logs without personal data."""

from __future__ import annotations

import json
import logging
import uuid
from unittest.mock import MagicMock

import pytest

from app.utils import logging as app_logging
from app.utils.logging import _JSONFormatter, get_logger, mask_email, sanitize_log, setup_logging


@pytest.fixture(autouse=True)
def _restore_root_logging():
    saved_handlers = logging.getLogger().handlers[:]
    saved_level = logging.getLogger().level
    yield
    root = logging.getLogger()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_setup_logging_installs_the_json_formatter_by_default():
    setup_logging()
    (handler,) = logging.getLogger().handlers
    assert isinstance(handler.formatter, _JSONFormatter)


def test_text_format_remains_available():
    setup_logging("INFO", "text")
    (handler,) = logging.getLogger().handlers
    assert isinstance(handler.formatter, app_logging._SanitizingFormatter)


def test_setup_logging_defaults_to_the_configured_format(monkeypatch):
    monkeypatch.setattr(app_logging.get_settings(), "log_format", "text")
    setup_logging()
    (handler,) = logging.getLogger().handlers
    assert isinstance(handler.formatter, app_logging._SanitizingFormatter)


def test_json_log_lines_are_parseable_and_injection_safe():
    record = logging.LogRecord(
        "audit", logging.INFO, __file__, 1, "digest line %s", ("one\nforged line two",), None
    )
    formatted = _JSONFormatter().format(record)
    lines = [line for line in formatted.splitlines() if line.strip()]
    assert len(lines) == 1, f"one record, one line: {lines}"
    parsed = json.loads(lines[0])
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "audit"
    # The message survived, but the newline was neutralized (sanitize_log) —
    # no second line was forged.
    assert "forged line two" in parsed["message"]


def test_json_formatter_uses_sanitize_log():
    record = logging.LogRecord("audit", logging.INFO, __file__, 1, "line %s", ("one\ntwo",), None)
    formatted = _JSONFormatter().format(record)
    assert "\n" not in formatted, "control chars must not break the JSON line"
    assert "one two" in formatted  # sanitize_log's replacement


def test_email_log_lines_do_not_carry_the_recipient(caplog, monkeypatch):
    """The send path must never log the email address or the subject (#198)."""
    with caplog.at_level(logging.INFO, logger="app.utils.email"):
        monkeypatch.setattr(app_logging.get_settings(), "smtp_password", "configured")
        monkeypatch.setattr("app.utils.email.smtplib.SMTP", MagicMock())
        email = f"secret_{uuid.uuid4().hex[:8]}@example.com"
        from app.utils.email import send_email_sync

        assert send_email_sync(
            to=email, subject="Your Weirling Digest: October 18, 2026", html_body="<p>hi</p>"
        )
    text = caplog.text
    assert email not in text
    assert "Your Weirling Digest" not in text
    # The recipient appears only as the one-way hash.
    assert "Email sent to email:" in text


def test_mask_email_is_stable_and_one_way():
    assert mask_email("a@example.com") == mask_email("a@example.com")
    assert mask_email("a@example.com") != mask_email("b@example.com")
    assert "a@example.com" not in mask_email("a@example.com")


def test_sanitize_log_still_shared():
    assert sanitize_log("a\nb") == "a b"
    record = logging.LogRecord("audit", logging.INFO, __file__, 1, "x\ny", (), None)
    assert "x y" in _JSONFormatter().format(record)


def test_json_logs_keep_the_traceback_on_one_line():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    line = _JSONFormatter().format(record)
    assert "\n" not in line.replace("\\n", "")
    parsed = json.loads(line)
    assert "ValueError: boom" in parsed["traceback"] and "Traceback" in parsed["traceback"]


def test_mask_email_is_keyed_and_case_insensitive(monkeypatch):
    import hashlib

    first = mask_email("Reader@Example.com")
    assert first == mask_email("reader@example.com")
    plain = "email:" + hashlib.sha256(b"reader@example.com").hexdigest()[:12]
    assert first != plain  # not reversible by hashing candidate addresses
    monkeypatch.setattr(app_logging.get_settings(), "secret_key", "another-instance-key")
    assert mask_email("reader@example.com") != first
