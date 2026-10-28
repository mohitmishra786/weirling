"""Demo account reset task (#229).

Re-seeds the demo persona nightly so a public zero-login demo instance stays
fresh and clean.
"""

from __future__ import annotations

import asyncio
import secrets

from app.database import AsyncSessionLocal
from app.utils.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _reset_demo_account_async() -> dict:
    from app.config import get_settings
    from scripts.seed_demo import seed

    if not get_settings().demo_mode:
        # A queued or manual run on a normal instance must not create the
        # demo persona.
        return {"skipped": "DEMO_MODE is off"}

    password = secrets.token_urlsafe(12)
    async with AsyncSessionLocal() as session:
        summary = await seed(session, password=password)
        await session.commit()
    logger.info(f"Demo persona reset complete: {summary}")
    return summary


@celery_app.task(name="app.workers.tasks.demo.reset_demo_account")
def reset_demo_account() -> dict:
    return asyncio.run(_reset_demo_account_async())
