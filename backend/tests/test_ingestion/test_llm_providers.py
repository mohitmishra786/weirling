"""Claude first, then the OpenAI-compatible endpoint, then extractive."""

from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.config import get_settings
from app.services.llm.anthropic_client import AnthropicClient
from app.services.llm.client import LLMClient
from app.services.llm.providers import Link, ProviderChain, instance_chain
from app.services.summarization.groq_client import LLMSummarizer

SUMMARY = {
    "headline": "Claude wrote this",
    "brief": "Two sentences. Here.",
    "detailed": "A paragraph.",
    "depth_score": 0.6,
    "is_original_reporting": False,
    "has_citations": True,
    "topic_clusters": ["databases"],
    "reading_time_minutes": 4,
}


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _message(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


class _FakeSDK:
    """Stands in for AsyncAnthropic: `replies` are messages or exceptions."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.messages = self

    async def create(self, **params):
        self.calls.append(params)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _status_error(cls, status: int, message: str = "error"):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    return cls(message, response=response, body=None)


def _claude(sdk: _FakeSDK, clock: _Clock | None = None) -> AnthropicClient:
    return AnthropicClient(
        api_key="sk-test", sdk=sdk, clock=clock or _Clock(), tpm=1_000_000, rpm=1_000
    )


def _groq(handler, **kwargs) -> LLMClient:
    async def no_sleep(_: float) -> None:
        return None

    return LLMClient(
        api_key="groq-key",
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
        tpm=100_000,
        rpm=1_000,
        **kwargs,
    )


def _groq_ok(seen: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        reply = json.dumps({**SUMMARY, "headline": "Groq wrote this"})
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    return handler


@pytest.mark.asyncio
async def test_claude_answers_and_groq_is_not_called():
    sdk = _FakeSDK(_message(json.dumps(SUMMARY)))
    groq_models: list[str] = []
    chain = ProviderChain(
        [
            Link("claude", _claude(sdk), model="claude-haiku-5-5"),
            Link("llm", _groq(_groq_ok(groq_models))),
        ]
    )
    result = await LLMSummarizer(llm=chain).summarize("T", "Body")
    assert result is not None and result.headline == "Claude wrote this"
    assert groq_models == []
    sent = sdk.calls[0]
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["output_config"] == {"effort": get_settings().anthropic_effort}
    # Claude 5.5 models reject a non-default temperature; no prefill either.
    assert "temperature" not in sent
    assert sent["messages"][-1]["role"] == "user"
    assert "JSON" in sent["system"]
    assert sent["max_tokens"] > 1024  # room for thinking


@pytest.mark.parametrize(
    "error",
    [
        _status_error(anthropic.RateLimitError, 429),
        _status_error(anthropic.BadRequestError, 400, "Your credit balance is too low"),
        _status_error(anthropic.AuthenticationError, 401),
        _status_error(anthropic.InternalServerError, 529, "Overloaded"),
    ],
)
@pytest.mark.asyncio
async def test_claude_failure_falls_back_to_groq_and_benches_claude(error):
    clock = _Clock()
    sdk = _FakeSDK(error)
    groq_models: list[str] = []
    claude = _claude(sdk, clock)
    chain = ProviderChain(
        [
            Link("claude", claude, model="claude-haiku-5-5"),
            Link("llm", _groq(_groq_ok(groq_models))),
        ]
    )
    first = await LLMSummarizer(llm=chain).summarize("T", "Body")
    assert first is not None and first.headline == "Groq wrote this"
    # Groq gets its own model id, not Claude's.
    assert groq_models == [get_settings().llm_model_fast]
    assert claude.benched

    # While benched, Claude isn't asked again.
    await LLMSummarizer(llm=chain).summarize("T", "Body")
    assert len(sdk.calls) == 1 and len(groq_models) == 2

    clock.t += 3601  # every bench is over by now
    sdk.replies.append(_message(json.dumps(SUMMARY)))
    again = await LLMSummarizer(llm=chain).summarize("T", "Body")
    assert again is not None and again.headline == "Claude wrote this"


@pytest.mark.asyncio
async def test_refusal_and_cut_off_answers_fall_back_without_benching():
    sdk = _FakeSDK(_message("", "refusal"), _message('{"headline": "Cut', "max_tokens"))
    groq_models: list[str] = []
    claude = _claude(sdk)
    chain = ProviderChain([Link("claude", claude), Link("llm", _groq(_groq_ok(groq_models)))])
    for _ in range(2):
        result = await LLMSummarizer(llm=chain).summarize("T", "Body")
        assert result is not None and result.headline == "Groq wrote this"
    assert not claude.benched and len(sdk.calls) == 2


@pytest.mark.asyncio
async def test_whole_chain_failing_returns_none_for_the_extractive_fallback():
    def groq_down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    chain = ProviderChain(
        [
            Link("claude", _claude(_FakeSDK(_status_error(anthropic.RateLimitError, 429)))),
            Link("llm", _groq(groq_down)),
        ]
    )
    assert await LLMSummarizer(llm=chain, use_fallback=False).summarize("T", "Body") is None


@pytest.mark.asyncio
async def test_groq_daily_cap_is_skipped_until_its_retry_after():
    calls = {"n": 0}
    clock = _Clock()

    def capped(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "900"}, json={})

    groq = _groq(capped, clock=clock)
    msg = [{"role": "user", "content": "hi"}]
    assert await groq.complete(model="m", messages=msg) is None
    assert await groq.complete(model="m", messages=msg) is None
    assert calls["n"] == 1 and groq.cooling_down
    clock.t += 901
    await groq.complete(model="m", messages=msg)
    assert calls["n"] == 2


def test_instance_chain_puts_claude_first_only_when_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    assert [link.name for link in instance_chain().links] == ["llm"]
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    chain = instance_chain()
    assert [link.name for link in chain.links] == ["claude", "llm"]
    assert chain.links[0].model == settings.anthropic_model
    assert settings.llm_configured
