from __future__ import annotations

import sys
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import get_settings

settings = get_settings()


def pool_options(argv: list[str], mode: str, size: int, overflow: int) -> dict:
    """Connection pooling for this process.

    Celery runs each task in its own asyncio.run() event loop. Pooled asyncpg
    connections belong to the loop that opened them, so a shared pool in a
    worker can neither reuse nor cleanly close them: they leak until Postgres
    refuses everyone ("too many clients"; an embed worker held 49 with a pool
    of 30). Workers and beat therefore use no pool: each task opens and
    closes its own connections. The API keeps a pool.
    """
    mode = (mode or "auto").strip().lower()
    # Started as `celery ...` (argv[0] is the celery script) or as
    # `python -m celery ...` (argv[0] is celery/__main__.py).
    first = Path(argv[0]) if argv else Path()
    in_celery = first.name == "celery" or (
        first.name == "__main__.py" and first.parent.name == "celery"
    )
    if mode == "none" or (mode == "auto" and in_celery):
        return {"poolclass": NullPool}
    # Recycle pooled connections before a platform proxy can drop them while
    # idle: a dropped connection otherwise hangs the next request until TCP
    # gives up (a 15 s "rate this" on Railway).
    return {"pool_size": size, "max_overflow": overflow, "pool_recycle": 300}


engine = create_async_engine(
    settings.database_url,
    echo=settings.app_env == "development",
    pool_pre_ping=True,
    # Fail fast instead of waiting on a database that doesn't answer.
    connect_args={"timeout": 10},
    **pool_options(sys.argv, settings.db_pool, settings.db_pool_size, settings.db_max_overflow),
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
