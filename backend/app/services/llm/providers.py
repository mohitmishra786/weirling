"""The instance's LLM providers, tried in order.

Claude (ANTHROPIC_API_KEY) comes first when it is set, with ANTHROPIC_MODEL
for every task; the OpenAI-compatible LLM_* endpoint (Groq by default) takes
whatever Claude can't answer, with its per-task models. A provider that
returns None, or is benched or cooling down, is passed over; None from the
whole chain means the caller keeps its extractive fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.config import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


class Provider(Protocol):
    @property
    def api_key(self) -> str: ...

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.1,
        json_mode: bool = False,
    ) -> str | None: ...

    async def complete_json(
        self, *, model: str, system: str, user: str, max_tokens: int = 1024
    ) -> dict[str, Any] | None: ...


@dataclass
class Link:
    name: str
    client: Provider
    # Replaces the caller's model id, which names an LLM_* model.
    model: str | None = None


class ProviderChain:
    def __init__(self, links: list[Link]) -> None:
        self.links = links

    @property
    def api_key(self) -> str:
        return next((link.client.api_key for link in self.links if link.client.api_key), "")

    async def complete(self, *, model: str, **kwargs: Any) -> str | None:
        for i, link in enumerate(self.links):
            text = await link.client.complete(model=link.model or model, **kwargs)
            if text is not None:
                if i:
                    logger.info("LLM call answered by fallback %s", link.name)
                return text
        return None

    async def complete_json(self, *, model: str, **kwargs: Any) -> dict[str, Any] | None:
        for i, link in enumerate(self.links):
            data = await link.client.complete_json(model=link.model or model, **kwargs)
            if data is not None:
                if i:
                    logger.info("LLM call answered by fallback %s", link.name)
                return data
        return None


def instance_chain() -> ProviderChain:
    """Claude first when configured, then LLM_*. Build once per process so
    rate buckets, benches and cooldowns persist between calls."""
    from app.services.llm.anthropic_client import AnthropicClient
    from app.services.llm.client import LLMClient

    settings = get_settings()
    links: list[Link] = []
    if settings.anthropic_api_key:
        links.append(Link("claude", AnthropicClient(), model=settings.anthropic_model))
    links.append(Link("llm", LLMClient()))
    return ProviderChain(links)
