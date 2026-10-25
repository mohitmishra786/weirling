from __future__ import annotations

import asyncio
import json
from typing import Any

import redis.asyncio as aioredis

from app.config import get_settings
from app.utils.logging import get_logger, sanitize_log

logger = get_logger(__name__)
settings = get_settings()

_redis_client: aioredis.Redis | None = None
_redis_loop: asyncio.AbstractEventLoop | None = None


def get_redis() -> aioredis.Redis:
    """The async client for the running event loop.

    Celery tasks each run in their own `asyncio.run()` loop. A client (and its
    pooled connections) made in an earlier, closed loop fails with "Event loop
    is closed", so a new loop gets a new client.
    """
    global _redis_client, _redis_loop
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _redis_client is None or (loop is not None and loop is not _redis_loop):
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            # Idle connections can be dropped silently by the network between
            # services; keepalives, timeouts and a periodic check turn a hang
            # into a quick reconnect.
            socket_keepalive=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
            retry_on_timeout=True,
        )
        _redis_loop = loop
    return _redis_client


async def cache_get(key: str) -> Any | None:
    client = get_redis()
    try:
        value = await client.get(key)
        if value is not None:
            return json.loads(value)
        return None
    except Exception as e:
        logger.warning(f"Redis GET failed for key {sanitize_log(key)}: {e}")
        return None


async def cache_set(key: str, value: Any, ttl_seconds: int = 3600) -> bool:
    client = get_redis()
    try:
        serialized = json.dumps(value, default=str)
        await client.setex(key, ttl_seconds, serialized)
        return True
    except Exception as e:
        logger.warning(f"Redis SET failed for key {sanitize_log(key)}: {e}")
        return False


async def cache_incr(key: str, amount: int = 1, ttl_seconds: int = 3600) -> int | None:
    """INCRBY with a TTL set on first use. None when Redis is unreachable."""
    client = get_redis()
    try:
        value = int(await client.incrby(key, amount))
        if value == amount:  # first increment in this window
            await client.expire(key, ttl_seconds)
        return value
    except Exception as e:
        logger.warning(f"Redis INCR failed for key {sanitize_log(key)}: {e}")
        return None


class CacheUnavailable(RuntimeError):
    """Raised by strict cache operations when Redis cannot be reached."""


async def cache_set_nx_strict(key: str, value: Any, ttl_seconds: int) -> bool:
    """SET NX EX that raises CacheUnavailable instead of failing open (#156).

    For security dedupe (webhook replay protection): an outage must reject
    the request (providers retry) rather than accept a possible replay.
    """
    client = get_redis()
    try:
        result = await client.set(key, json.dumps(value, default=str), ex=ttl_seconds, nx=True)
        return bool(result)
    except Exception as e:
        logger.error(f"Redis SET NX failed for key {sanitize_log(key)}: {e}")
        raise CacheUnavailable(str(e)) from e


async def cache_set_nx(key: str, value: Any, ttl_seconds: int) -> bool:
    """Atomically set `key` only if absent (SET NX EX). Returns True if the key
    was newly set, False if it already existed. Used for replay-attack dedupe.

    On a Redis error we return True (fail-open on the dedupe layer) so that a
    cache outage never silently drops legitimate, already-signature-verified
    webhooks; the HMAC signature remains the authoritative gate.
    """
    client = get_redis()
    try:
        serialized = json.dumps(value, default=str)
        result = await client.set(key, serialized, ex=ttl_seconds, nx=True)
        return bool(result)
    except Exception as e:
        logger.warning(f"Redis SET NX failed for key {sanitize_log(key)}: {e}")
        return True


async def cache_delete(key: str) -> bool:
    client = get_redis()
    try:
        await client.delete(key)
        return True
    except Exception as e:
        logger.warning(f"Redis DELETE failed for key {sanitize_log(key)}: {e}")
        return False


async def cache_exists(key: str) -> bool:
    client = get_redis()
    try:
        return bool(await client.exists(key))
    except Exception as e:
        logger.warning(f"Redis EXISTS failed for key {sanitize_log(key)}: {e}")
        return False


async def ping_redis() -> bool:
    client = get_redis()
    try:
        await client.ping()
        return True
    except Exception:
        return False
