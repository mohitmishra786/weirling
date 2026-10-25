"""First-boot setup screen (#220): owner account, LLM key, SMTP, sign-ups.

The first account is the instance owner (services/registration.py); only it
can read or change these settings. Secrets are write-only: responses carry
`is_set`, never a value, and request bodies are never logged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.instance_setting import InstanceSetting
from app.models.user import User
from app.services import instance_config
from app.services.registration import effective_mode, has_users, is_instance_owner
from app.utils import secretbox

router = APIRouter(prefix="/setup", tags=["setup"])


async def require_owner(
    current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)
) -> User:
    if not await is_instance_owner(session, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the instance owner can change instance settings.",
        )
    return current_user


@router.get("/status")
async def setup_status(session: AsyncSession = Depends(get_db)) -> dict:
    """Public: does this instance still need its owner account?"""
    return {
        "needs_owner": not await has_users(session),
        "can_store_secrets": secretbox.configured(),
    }


async def _config(session: AsyncSession) -> dict:
    stored = set((await session.execute(select(InstanceSetting.key))).scalars())
    s = get_settings()
    return {
        "can_store_secrets": secretbox.configured(),
        "effective_registration_mode": effective_mode(),
        "llm_configured": s.llm_configured,
        "email_configured": bool(s.smtp_host and s.smtp_password),
        "fields": instance_config.describe(stored),
    }


@router.get("/config")
async def get_config(
    _owner: User = Depends(require_owner), session: AsyncSession = Depends(get_db)
) -> dict:
    return await _config(session)


class ConfigUpdate(BaseModel):
    # Setting key -> new value; null or "" clears it (falls back to the default).
    values: dict[str, str | None]


@router.put("/config")
async def put_config(
    body: ConfigUpdate,
    _owner: User = Depends(require_owner),
    session: AsyncSession = Depends(get_db),
) -> dict:
    try:
        await instance_config.save(session, body.values)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    await session.commit()
    return await _config(session)


class LLMTest(BaseModel):
    # Omitted fields use what is configured now, so a saved key can be tested
    # without sending it again.
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


@router.post("/test-llm")
async def test_llm(body: LLMTest, _owner: User = Depends(require_owner)) -> dict:
    from app.services.setup_checks import check_llm

    s = get_settings()
    ok, message = await check_llm(
        base_url=body.base_url or s.llm_base_url,
        api_key=body.api_key or s.llm_api_key,
        model=body.model or s.llm_model_fast,
    )
    return {"ok": ok, "message": message}


class SMTPTest(BaseModel):
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None


@router.post("/test-smtp")
async def test_smtp(body: SMTPTest, owner: User = Depends(require_owner)) -> dict:
    """Send a test email to the owner's own address."""
    from app.services.setup_checks import check_smtp

    s = get_settings()
    ok, message = await check_smtp(
        host=body.host or s.smtp_host,
        port=body.port or s.smtp_port,
        username=body.username or s.smtp_user,
        password=body.password or s.smtp_password,
        to=owner.email,
    )
    return {"ok": ok, "message": message}
