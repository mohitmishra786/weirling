"""First-boot setup screen (#220)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models.instance_setting import InstanceSetting
from app.models.user import User
from app.services import instance_config

SECRET = "sk-test-0123456789abcdef"


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "secrets_encryption_key", "test-secrets-key-" + "x" * 32)
    for field in instance_config.FIELDS:
        monkeypatch.setattr(s, field.key, getattr(s, field.key))
        for name in (field.key, *field.aliases):
            monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setattr(instance_config, "_dotenv", lambda: {})
    monkeypatch.setattr(instance_config, "_baseline", {})
    yield


async def _account(client, db_session, *, age_days):
    email = f"s{uuid.uuid4().hex[:6]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "TestPass123!"}
    )
    assert reg.status_code in (200, 201), reg.text
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    user.created_at = datetime.now(UTC) - timedelta(days=age_days)
    await db_session.commit()
    return {"Authorization": f"Bearer {reg.json()['access_token']}"}


@pytest.mark.asyncio
async def test_fresh_instance_needs_an_owner(client, db_session):
    status = (await client.get("/api/v1/setup/status")).json()
    assert status["needs_owner"] is True
    reg = (await client.get("/api/v1/auth/registration")).json()
    assert reg["first_account"] is True
    await _account(client, db_session, age_days=1)
    assert (await client.get("/api/v1/setup/status")).json()["needs_owner"] is False
    assert (await client.get("/api/v1/auth/registration")).json()["first_account"] is False


@pytest.mark.asyncio
async def test_owner_configures_llm_and_the_key_is_never_echoed(client, db_session, caplog):
    owner = await _account(client, db_session, age_days=5)
    caplog.set_level(logging.DEBUG)
    resp = await client.put(
        "/api/v1/setup/config",
        headers=owner,
        json={"values": {"llm_api_key": SECRET, "llm_model_fast": "my/fast-model"}},
    )
    assert resp.status_code == 200, resp.text
    assert SECRET not in resp.text and SECRET[-4:] not in resp.text
    fields = {f["key"]: f for f in resp.json()["fields"]}
    assert fields["llm_api_key"] == {
        "key": "llm_api_key",
        "group": "llm",
        "label": "API key",
        "secret": True,
        "source": "saved",
        "is_set": True,
    }
    assert fields["llm_model_fast"]["value"] == "my/fast-model"
    # Applied live, stored encrypted, never logged.
    assert get_settings().llm_api_key == SECRET
    assert resp.json()["llm_configured"] is True
    row = await db_session.get(InstanceSetting, "llm_api_key")
    assert row is not None and SECRET not in row.value_encrypted
    assert SECRET not in caplog.text
    again = await client.get("/api/v1/setup/config", headers=owner)
    assert SECRET not in again.text

    # Clearing restores the default.
    resp = await client.put(
        "/api/v1/setup/config", headers=owner, json={"values": {"llm_api_key": None}}
    )
    assert get_settings().llm_api_key == ""
    assert {f["key"]: f for f in resp.json()["fields"]}["llm_api_key"]["source"] == "default"


@pytest.mark.asyncio
async def test_env_wins_and_cannot_be_overwritten(client, db_session, monkeypatch):
    owner = await _account(client, db_session, age_days=5)
    monkeypatch.setenv("LLM_MODEL_PRIMARY", "env/model")
    fields = {
        f["key"]: f
        for f in (await client.get("/api/v1/setup/config", headers=owner)).json()["fields"]
    }
    assert fields["llm_model_primary"]["source"] == "env"
    resp = await client.put(
        "/api/v1/setup/config",
        headers=owner,
        json={"values": {"llm_model_primary": "db/model"}},
    )
    assert resp.status_code == 422 and ".env" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_registration_lock_from_the_setup_screen(client, db_session):
    owner = await _account(client, db_session, age_days=5)
    resp = await client.put(
        "/api/v1/setup/config", headers=owner, json={"values": {"registration_mode": "closed"}}
    )
    assert resp.json()["effective_registration_mode"] == "closed"
    stranger = await client.post(
        "/api/v1/auth/register",
        json={"email": f"x{uuid.uuid4().hex[:6]}@example.com", "password": "TestPass123!"},
    )
    assert stranger.status_code == 403
    await client.put(
        "/api/v1/setup/config", headers=owner, json={"values": {"registration_mode": "open"}}
    )
    friend = await client.post(
        "/api/v1/auth/register",
        json={"email": f"y{uuid.uuid4().hex[:6]}@example.com", "password": "TestPass123!"},
    )
    assert friend.status_code in (200, 201)
    bad = await client.put(
        "/api/v1/setup/config", headers=owner, json={"values": {"registration_mode": "maybe"}}
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_only_the_owner_can_read_or_change_settings(client, db_session):
    await _account(client, db_session, age_days=5)
    get_settings().registration_mode = "open"
    other = await _account(client, db_session, age_days=1)
    assert (await client.get("/api/v1/setup/config", headers=other)).status_code == 403
    put = await client.put(
        "/api/v1/setup/config", headers=other, json={"values": {"llm_api_key": SECRET}}
    )
    assert put.status_code == 403
    assert (await client.post("/api/v1/setup/test-llm", headers=other, json={})).status_code == 403


@pytest.mark.asyncio
async def test_without_an_encryption_key_nothing_is_stored(client, db_session, monkeypatch):
    owner = await _account(client, db_session, age_days=5)
    monkeypatch.setattr(get_settings(), "secrets_encryption_key", "")
    resp = await client.put(
        "/api/v1/setup/config", headers=owner, json={"values": {"llm_api_key": SECRET}}
    )
    assert resp.status_code == 422 and "SECRETS_ENCRYPTION_KEY" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_llm_test_button_reports_without_echoing(client, db_session):
    owner = await _account(client, db_session, age_days=5)
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(401, text=f"bad key {SECRET}")

    with patch("app.services.setup_checks._TRANSPORT", httpx.MockTransport(handler)):
        resp = await client.post("/api/v1/setup/test-llm", headers=owner, json={"api_key": SECRET})
    assert resp.json() == {"ok": False, "message": "The provider rejected the API key."}
    assert seen["auth"] == f"Bearer {SECRET}"


@pytest.mark.asyncio
async def test_smtp_test_button_sends_to_the_owner(client, db_session):
    owner = await _account(client, db_session, age_days=5)
    with patch("app.services.setup_checks._send_smtp", return_value=(True, "Sent.")) as send:
        resp = await client.post(
            "/api/v1/setup/test-smtp",
            headers=owner,
            json={"host": "smtp.example.com", "port": 587, "username": "u", "password": "p"},
        )
    assert resp.json()["ok"] is True
    me = (await client.get("/api/v1/auth/me", headers=owner)).json()["email"]
    assert send.call_args.kwargs["to"] == me


def test_smtp_errors_never_include_the_server_text():
    import smtplib

    from app.services.setup_checks import _send_smtp

    class Fail:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, u, p):
            raise smtplib.SMTPAuthenticationError(535, f"bad credentials for {u} {p}")

    with patch("smtplib.SMTP", Fail):
        ok, message = _send_smtp(host="h", port=587, username="me@x", password="pw", to="me@x")
    assert not ok and "me@x" not in message and "pw" not in message


@pytest.mark.asyncio
async def test_stored_settings_load_at_start_up(db_session):
    from app.utils import secretbox

    db_session.add(
        InstanceSetting(
            key="smtp_port",
            value_encrypted=secretbox.encrypt("2525", purpose=instance_config.PURPOSE),
        )
    )
    await db_session.commit()
    await instance_config.load(db_session)
    assert get_settings().smtp_port == 2525
