from __future__ import annotations

import json

from app.config import get_settings
from app.services.llm.client import LLMClient
from app.services.llm.providers import Provider, instance_chain
from app.utils.logging import get_logger

logger = get_logger(__name__)


class SummarizationResult:
    def __init__(
        self,
        headline: str,
        brief: str,
        detailed: str,
        depth_score: float,
        is_original_reporting: bool,
        has_citations: bool,
        topic_clusters: list[str],
        reading_time_minutes: int,
        summary_source: str = "llm",
    ) -> None:
        self.headline = headline
        self.brief = brief
        self.detailed = detailed
        self.depth_score = depth_score
        self.is_original_reporting = is_original_reporting
        self.has_citations = has_citations
        self.topic_clusters = topic_clusters
        self.reading_time_minutes = reading_time_minutes
        self.summary_source = summary_source


_SYSTEM_PROMPT = (
    "You are a precise content analyst. Return only valid JSON. No markdown, no explanation."
)

_USER_PROMPT_TEMPLATE = """Analyze this article and return a JSON object with exactly these keys:
- "headline": one sentence, max 15 words, captures the core claim
- "brief": 2-3 sentences summarizing the main argument and key facts
- "detailed": one paragraph (4-6 sentences) with key takeaways for a sophisticated reader
- "depth_score": float 0.0-1.0 where 1.0 is deeply original research and 0.0 is shallow aggregation
- "is_original_reporting": boolean
- "has_citations": boolean
- "topic_clusters": list of 1-5 specific topic labels (e.g. "distributed consensus", "rust programming language", "urban housing policy")
- "reading_time_minutes": estimated integer

Title: {title}
Content: {content}"""


# Summaries are written in the article's own language (#248); topic labels
# stay English so the interest graph matches across languages.
LANGUAGE_NAMES = {
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "nb": "Norwegian",
    "fi": "Finnish",
    "pl": "Polish",
    "cs": "Czech",
    "tr": "Turkish",
    "ru": "Russian",
    "uk": "Ukrainian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "hi": "Hindi",
    "ar": "Arabic",
    "he": "Hebrew",
    "id": "Indonesian",
    "vi": "Vietnamese",
}


def language_instruction(language: str | None) -> str:
    """Prompt line for a non-English article; empty for English or unknown."""
    code = (language or "").split("-")[0].split("_")[0].lower()
    name = LANGUAGE_NAMES.get(code)
    if not name:
        return ""
    return (
        f"\nThe article is in {name}. Write headline, brief and detailed in {name}. "
        "Keep topic_clusters in English."
    )


TRANSCRIPT_INSTRUCTION = (
    "\nThe content is the published transcript of a podcast episode or video. "
    "Summarize what is said; refer to it as an episode, not an article."
)


def _truncate_text(text: str, max_chars: int = 16000) -> str:
    """Approx 4000 tokens at ~4 chars/token."""
    return text[:max_chars]


def _parse_result(raw: str) -> SummarizationResult | None:
    try:
        # Strip any markdown fences if present
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        data = json.loads(clean)
        return SummarizationResult(
            headline=data.get("headline", ""),
            brief=data.get("brief", ""),
            detailed=data.get("detailed", ""),
            depth_score=float(data.get("depth_score", 0.5)),
            is_original_reporting=bool(data.get("is_original_reporting", False)),
            has_citations=bool(data.get("has_citations", False)),
            topic_clusters=list(data.get("topic_clusters", [])),
            reading_time_minutes=int(data.get("reading_time_minutes", 5)),
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"Failed to parse summarization JSON: {e}")
        return None


class LLMSummarizer:
    """Provider-agnostic summarizer: Claude, then the OpenAI-compatible endpoint (see `providers`)."""

    def __init__(
        self,
        llm: Provider | None = None,
        *,
        summary_model: str | None = None,
        use_fallback: bool = True,
    ) -> None:
        self._llm_client = llm
        self._fallback_client: LLMClient | None = None
        # A reader's own key (#255) brings its own model and never falls back
        # to the instance's provider.
        self._summary_model = summary_model
        self._use_fallback = use_fallback

    def _llm(self) -> Provider:
        if self._llm_client is None:
            self._llm_client = instance_chain()
        return self._llm_client

    async def summarize(
        self,
        title: str,
        full_text: str,
        language: str | None = None,
        *,
        is_transcript: bool = False,
    ) -> SummarizationResult | None:
        content = _truncate_text(full_text)
        prompt = (
            _USER_PROMPT_TEMPLATE.format(title=title, content=content)
            + language_instruction(language)
            + (TRANSCRIPT_INSTRUCTION if is_transcript else "")
        )
        current = get_settings()
        data = await self._llm().complete_json(
            model=self._summary_model or current.model_for("summary"),
            system=_SYSTEM_PROMPT,
            user=prompt,
            max_tokens=1024,
        )
        if (
            data is None
            and self._use_fallback
            and current.openai_fallback_enabled
            and current.openai_api_key
            and current.openai_model
        ):
            if self._fallback_client is None:
                self._fallback_client = LLMClient(
                    base_url=current.openai_base_url,
                    api_key=current.openai_api_key,
                )
            fallback = self._fallback_client
            data = await fallback.complete_json(
                model=current.openai_model,
                system=_SYSTEM_PROMPT,
                user=prompt,
                max_tokens=1024,
            )
        if not data:
            return None
        result = _parse_result_data(data)
        if result is not None:
            result.summary_source = "llm"
        return result

    async def synthesize_topic(self, items: list[SummarizationResult], topic: str) -> str:
        briefs = "\n\n".join([f"- {item.brief}" for item in items[:5]])
        prompt = (
            f"Given these summaries of articles about {topic}, write a single 3-4 sentence briefing "
            f"that captures what happened, the key disagreements or different angles between sources, "
            f"and what a reader should know. Return only the briefing text.\n\n{briefs}"
        )
        text = await self._llm().complete(
            model=get_settings().model_for("synthesis"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=256,
        )
        return text or ""

    async def extract_topics(self, text: str) -> list[str]:
        prompt = (
            "Extract 5-10 specific topic labels from this text. "
            'Return a JSON object {"topics": ["..."]}. Be specific, not generic.\n\n'
            f"{text[:2000]}"
        )
        data = await self._llm().complete_json(
            model=get_settings().model_for("topics"),
            system="Return only a JSON object with a topics array of strings.",
            user=prompt,
            max_tokens=256,
        )
        if not data:
            return []
        topics = data.get("topics", [])
        if not isinstance(topics, list):
            return []
        return [str(topic) for topic in topics if isinstance(topic, str) and topic.strip()]

    async def complete_json(
        self, *, task: str, system: str, user: str, max_tokens: int = 512
    ) -> dict | None:
        """One JSON call on the model for `task` (summary, topics, synthesis, interests)."""
        return await self._llm().complete_json(
            model=get_settings().model_for(task), system=system, user=user, max_tokens=max_tokens
        )

    async def expand_interests(self, interest_text: str) -> list[str]:
        """CS-01: expand a free-text interest description into 8-15 specific
        subtopics the user can confirm or edit before onboarding continues.

        Labels are short (they become chips and interest-map nodes), and the
        taxonomy's names are offered first so starter packs and the graph
        line up with what the reader typed."""
        from app.services.cold_start.taxonomy import known_labels

        prompt = (
            "A reader described their interests as:\n\n"
            f"{interest_text[:2000]}\n\n"
            "Expand this into 8 to 15 specific subtopics they would want in a "
            "personalized daily digest: concrete areas, technologies or themes. "
            "Each label is 1 to 4 words, with no parentheses, examples or "
            "trailing qualifiers (\"Consensus algorithms\", not \"Consensus "
            "algorithms (Raft, Paxos) in production\"). Use a name from this list "
            "when one fits; write your own when none does:\n"
            f"{', '.join(known_labels())}\n\n"
            'Return a JSON object {"topics": ["..."]}.'
        )
        data = await self._llm().complete_json(
            model=get_settings().model_for("interests"),
            system="Return only a JSON object with a topics array of strings.",
            user=prompt,
            max_tokens=400,
        )
        if not data:
            return []
        topics = data.get("topics", [])
        if not isinstance(topics, list):
            return []
        cleaned = [str(t).strip() for t in topics if isinstance(t, str) and t.strip()]
        return cleaned[:15]


def _parse_result_data(data: dict) -> SummarizationResult | None:
    try:
        return SummarizationResult(
            headline=str(data.get("headline", "")),
            brief=str(data.get("brief", "")),
            detailed=str(data.get("detailed", "")),
            depth_score=float(data.get("depth_score", 0.5)),
            is_original_reporting=bool(data.get("is_original_reporting", False)),
            has_citations=bool(data.get("has_citations", False)),
            topic_clusters=[str(t) for t in data.get("topic_clusters", [])],
            reading_time_minutes=int(data.get("reading_time_minutes", 5)),
            summary_source="llm",
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"Failed to parse summarization JSON: {e}")
        return None


# Deprecated alias (issue #265): the historical Groq name. External code and
# tests patch ``app.services.summarization.groq_client.GroqSummarizer``; that
# path keeps working until the alias is removed in v0.3.
GroqSummarizer = LLMSummarizer
