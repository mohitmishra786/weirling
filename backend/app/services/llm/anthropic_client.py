"""Claude through the Anthropic SDK, with the same calls as `LLMClient`.

`complete` and `complete_json` return None whenever Claude can't answer, so
the chain in `providers` hands the call to the next provider. A failure that
will repeat on the next call (a rejected key, no credit left, a missing
model, a rate limit) also benches the client for a while, so later calls go
straight to the fallback instead of waiting on Claude first.
"""

from __future__ import annotations

import time
from typing import Any

import anthropic

from app.config import get_settings
from app.services.llm.client import (
    Clock,
    TokenBucket,
    _parse_json_object,
    _record,
)
from app.utils.logging import get_logger, sanitize_log

logger = get_logger(__name__)

# Seconds Claude is skipped after each kind of failure.
_BENCH_KEY = 3600.0  # key rejected, model missing: needs an operator
_BENCH_CREDIT = 1800.0  # credit balance too low: needs a top-up
_BENCH_BUSY = 60.0  # rate limited or overloaded after the SDK's retries
# Room for adaptive thinking on top of the answer the caller asked for.
_THINKING_HEADROOM = 2048


class AnthropicClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        clock: Clock = time.monotonic,
        tpm: int | None = None,
        rpm: int | None = None,
        sdk: Any = None,
    ) -> None:
        settings = get_settings()
        self._api_key_override = api_key
        self._clock = clock
        self._bucket = TokenBucket(
            tpm if tpm is not None else settings.anthropic_tpm_limit,
            rpm if rpm is not None else settings.anthropic_rpm_limit,
            clock=clock,
        )
        # Tests pass a fake; production builds an SDK client per call, since
        # Celery runs each task in a fresh event loop.
        self._sdk = sdk
        self._benched_until = 0.0
        self._bench_reason = ""

    @property
    def api_key(self) -> str:
        if self._api_key_override is not None:
            return self._api_key_override
        return get_settings().anthropic_api_key

    @property
    def benched(self) -> bool:
        return self._clock() < self._benched_until

    def _bench(self, seconds: float, reason: str) -> None:
        self._benched_until = self._clock() + seconds
        self._bench_reason = reason
        logger.warning(
            "Claude skipped for %.0fs: %s", seconds, sanitize_log(self._redact(reason))[:300]
        )

    def _redact(self, text: str) -> str:
        key = self.api_key
        return text.replace(key, "***") if key else text

    async def _create(self, **params: Any) -> Any:
        if self._sdk is not None:
            return await self._sdk.messages.create(**params)
        settings = get_settings()
        async with anthropic.AsyncAnthropic(
            api_key=self.api_key,
            max_retries=settings.anthropic_max_retries,
            timeout=settings.llm_timeout_seconds,
        ) as client:
            return await client.messages.create(**params)

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.1,  # accepted for LLMClient parity; Claude 5.5 rejects it
        json_mode: bool = False,
    ) -> str | None:
        if not self.api_key or self.benched:
            return None
        system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
        turns = [
            {"role": m["role"], "content": m.get("content", "")}
            for m in messages
            if m.get("role") in ("user", "assistant")
        ]
        if json_mode:
            system = (system + "\n\nReply with one JSON object and nothing else.").strip()
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens + _THINKING_HEADROOM,
            "messages": turns,
            "output_config": {"effort": get_settings().anthropic_effort},
        }
        if system:
            params["system"] = system

        estimate = sum(len(m["content"]) for m in turns) // 4 + max_tokens
        await self._bucket.acquire(estimate)
        try:
            message = await self._create(**params)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            self._bench(_BENCH_KEY, f"key rejected: {e}")
            await _record(False, "Claude key rejected")
            return None
        except anthropic.NotFoundError as e:
            self._bench(_BENCH_KEY, f"model {model!r} not found: {e}")
            await _record(False, f"model {model} not available")
            return None
        except anthropic.RateLimitError as e:
            self._bench(_BENCH_BUSY, f"rate limited: {e}")
            await _record(False, "HTTP 429")
            return None
        except anthropic.BadRequestError as e:
            if "credit balance" in str(e).lower():
                self._bench(_BENCH_CREDIT, "credit balance too low")
                await _record(False, "Claude credit balance too low")
            else:
                logger.error("Claude rejected the request: %s", self._redact(str(e))[:300])
                await _record(False, "HTTP 400")
            return None
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                self._bench(_BENCH_BUSY, f"HTTP {e.status_code}")
            else:
                logger.error("Claude HTTP %s: %s", e.status_code, self._redact(str(e))[:300])
            await _record(False, f"HTTP {e.status_code}")
            return None
        except anthropic.APIConnectionError as e:  # includes timeouts
            self._bench(_BENCH_BUSY, f"unreachable: {type(e).__name__}")
            await _record(False, "provider unreachable")
            return None

        usage = getattr(message, "usage", None)
        tokens = int(getattr(usage, "input_tokens", 0) or 0) + int(
            getattr(usage, "output_tokens", 0) or 0
        )
        if message.stop_reason == "refusal":
            # Declines are per request; the fallback provider may still answer.
            logger.info("Claude declined a request")
            await _record(True, tokens=tokens)
            return None
        if message.stop_reason == "max_tokens":
            logger.warning("Claude ran out of tokens (max_tokens=%s)", params["max_tokens"])
            await _record(False, "answer cut off", tokens=tokens)
            return None
        text = "".join(block.text for block in message.content if block.type == "text")
        await _record(True, tokens=tokens)
        return text

    async def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 1024,
    ) -> dict[str, Any] | None:
        """A JSON object, or None. No repair round: the next provider is the retry."""
        raw = await self.complete(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            json_mode=True,
        )
        return _parse_json_object(raw) if raw else None
