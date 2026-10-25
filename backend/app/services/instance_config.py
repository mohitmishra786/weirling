"""Instance settings entered on the first-boot setup screen (#220).

The owner can set the LLM key, SMTP and registration from the browser
instead of editing `.env`. Values are stored encrypted (app.utils.secretbox)
in `instance_settings` and applied on top of the environment at start-up
and after every save. A value set in the environment or `.env` always wins;
the screen shows it as "set in .env" and can't change it.

Secrets are write-only: the API reports only whether one is set, never the
value, and nothing here logs a value.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.instance_setting import InstanceSetting
from app.utils import secretbox
from app.utils.logging import get_logger

logger = get_logger(__name__)

PURPOSE = "instance-settings"


@dataclass(frozen=True)
class Field:
    key: str  # Settings attribute
    group: str
    label: str
    secret: bool = False
    kind: type = str
    choices: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()  # legacy env names that also count as "set in .env"


FIELDS: tuple[Field, ...] = (
    Field("llm_api_key", "llm", "API key", secret=True, aliases=("groq_api_key",)),
    Field("llm_base_url", "llm", "Base URL"),
    Field(
        "llm_model_primary",
        "llm",
        "Large model (story synthesis, interests)",
        aliases=("groq_summarization_model",),
    ),
    Field("llm_model_fast", "llm", "Fast model (summaries)", aliases=("groq_fast_model",)),
    Field("smtp_host", "smtp", "SMTP host"),
    Field("smtp_port", "smtp", "SMTP port", kind=int),
    Field("smtp_user", "smtp", "SMTP username"),
    Field("smtp_password", "smtp", "SMTP password", secret=True),
    Field("from_email", "smtp", "From address (empty: the username)"),
    Field(
        "registration_mode",
        "registration",
        "Sign-up mode",
        choices=("closed", "invite", "open"),
    ),
    Field("registration_invite_codes", "registration", "Invite codes", secret=True),
)
BY_KEY = {f.key: f for f in FIELDS}

# Defaults as loaded from the environment, so clearing a DB value restores them.
_baseline: dict[str, object] = {}
_loaded_at = 0.0


def _dotenv() -> dict[str, str]:
    env_file = get_settings().model_config.get("env_file") or ".env"
    path = Path(str(env_file))
    if not path.is_file():
        return {}
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - pydantic-settings depends on it
        return {}
    return {k.lower(): v or "" for k, v in dotenv_values(path).items()}


def from_env(field: Field) -> bool:
    """True when the environment or `.env` sets this field (non-empty)."""
    names = (field.key, *field.aliases)
    dotenv = _dotenv()
    for name in names:
        if os.environ.get(name.upper(), "").strip() or dotenv.get(name, "").strip():
            return True
    return False


def coerce(field: Field, raw: str) -> object:
    value = raw.strip()
    if field.kind is int:
        number = int(value)
        if not 0 < number < 65536:
            raise ValueError(f"{field.label} must be between 1 and 65535")
        return number
    if field.choices and value not in field.choices:
        raise ValueError(f"{field.label} must be one of: {', '.join(field.choices)}")
    if field.key == "llm_base_url" and not value.startswith(("https://", "http://")):
        raise ValueError("Base URL must start with https:// or http://")
    return value


def apply(stored: dict[str, str]) -> None:
    """Overlay decrypted DB values on the live Settings object."""
    s = get_settings()
    for field in FIELDS:
        _baseline.setdefault(field.key, getattr(s, field.key))
        if from_env(field):
            continue
        if field.key in stored:
            try:
                setattr(s, field.key, coerce(field, stored[field.key]))
            except ValueError:
                logger.warning(f"Ignoring invalid stored setting {field.key}")
        else:
            setattr(s, field.key, _baseline[field.key])


async def read_stored(session: AsyncSession) -> dict[str, str]:
    if not secretbox.configured():
        return {}
    rows = (await session.execute(select(InstanceSetting))).scalars()
    out: dict[str, str] = {}
    for row in rows:
        if row.key not in BY_KEY:
            continue
        try:
            out[row.key] = secretbox.decrypt(row.value_encrypted, purpose=PURPOSE)
        except secretbox.InvalidToken:
            logger.warning(
                f"Stored setting {row.key} can't be decrypted (SECRETS_ENCRYPTION_KEY changed?)"
            )
    return out


async def load(session: AsyncSession) -> None:
    global _loaded_at
    apply(await read_stored(session))
    _loaded_at = time.monotonic()


async def save(session: AsyncSession, values: dict[str, str | None]) -> list[str]:
    """Store `values` (None or "" clears). Returns the keys changed.

    Raises ValueError for unknown keys, invalid values or env-locked fields.
    """
    if not secretbox.configured():
        raise ValueError(
            "SECRETS_ENCRYPTION_KEY is not set. Run ./scripts/init_env.sh, then restart."
        )
    changed: list[str] = []
    for key, raw in values.items():
        field = BY_KEY.get(key)
        if field is None:
            raise ValueError(f"Unknown setting {key}")
        if from_env(field):
            raise ValueError(f"{field.label} is set in .env, which takes precedence")
        if raw is None or not str(raw).strip():
            await session.execute(delete(InstanceSetting).where(InstanceSetting.key == key))
        else:
            coerce(field, str(raw))
            token = secretbox.encrypt(str(raw).strip(), purpose=PURPOSE)
            row = await session.get(InstanceSetting, key)
            if row is None:
                session.add(InstanceSetting(key=key, value_encrypted=token))
            else:
                row.value_encrypted = token
        changed.append(key)
    await session.flush()
    await load(session)
    return changed


def describe(stored_keys: set[str]) -> list[dict]:
    """What the setup screen shows. Secrets: only whether one is set."""
    s = get_settings()
    out = []
    for field in FIELDS:
        current = getattr(s, field.key)
        source = "env" if from_env(field) else "saved" if field.key in stored_keys else "default"
        item: dict = {
            "key": field.key,
            "group": field.group,
            "label": field.label,
            "secret": field.secret,
            "source": source,
            "is_set": bool(current),
        }
        if field.choices:
            item["choices"] = list(field.choices)
        if not field.secret:
            item["value"] = current
        out.append(item)
    return out


def refresh_in_worker(max_age_seconds: float = 60) -> None:
    """Re-read the stored settings in a Celery worker, at most once a minute.

    Uses its own short-lived engine: the app engine's pool belongs to other
    event loops.
    """
    if time.monotonic() - _loaded_at < max_age_seconds:
        return
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    async def _run() -> None:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine)() as session:
                await load(session)
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.warning(f"Could not load instance settings: {type(e).__name__}")
