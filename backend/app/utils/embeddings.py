from __future__ import annotations

import asyncio
import hashlib

import numpy as np

from app.config import get_settings
from app.utils.cache import cache_get, cache_set
from app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

_embedding_service: EmbeddingService | None = None


def is_hash_embeddings() -> bool:
    """Return True if running in lite mode without sentence_transformers."""
    try:
        import sentence_transformers  # noqa: F401

        return False
    except ImportError:
        return True


HASH_EMBEDDER_NAME = "lexical-hash"


def active_embedder() -> dict:
    """What actually produces vectors on this instance (#195, #244).

    The configured EMBEDDING_MODEL is only used when sentence-transformers is
    installed; the lite image silently falls back to the hash embedder, so the
    settings page must report the active one, not the configured one.
    """
    from app.services.embeddings.registry import active_spec

    active = active_spec(
        model=settings.embedding_model,
        cutover=settings.embedding_cutover_enabled,
    )
    if is_hash_embeddings():
        return {
            "name": HASH_EMBEDDER_NAME,
            "is_hash": True,
            "quality_note": (
                "Lexical hash fallback: topics match on shared words only, so "
                "related articles that use different words rank lower."
            ),
            "how_to_upgrade": (
                "Run the standard image (not :latest-lite) to use the "
                f"{active.name} model; existing items are re-embedded "
                "as they are next scored."
            ),
        }
    return {
        "name": active.name,
        "is_hash": False,
        "quality_note": "Semantic embeddings: related topics match even with different wording.",
        "how_to_upgrade": None,
    }


def container_cpus() -> int:
    """CPUs this process may actually use: the smaller of the cgroup quota
    (Docker, Railway, Kubernetes limits) and the affinity mask. os.cpu_count()
    reports the host's cores, far more than a 2-vCPU container gets."""
    import contextlib
    import math
    import os

    limits: list[int] = []
    try:
        with open("/sys/fs/cgroup/cpu.max") as f:
            quota, period = f.read().split()[:2]
        if quota != "max":
            limits.append(math.ceil(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    with contextlib.suppress(AttributeError, OSError):  # no affinity API on macOS
        limits.append(len(os.sched_getaffinity(0)))
    if not limits:
        limits.append(os.cpu_count() or 1)
    return max(1, min(limits))


def _positive_int(value: str | None) -> int | None:
    try:
        number = int(value or "")
    except ValueError:
        return None
    return number if number > 0 else None


def limit_torch_threads() -> int | None:
    """Cap PyTorch's thread pool at the container's CPUs. Left at the host core
    count, its threads fight over a 2-vCPU quota and one article took 30-40 s
    to embed on Railway instead of 0.06 s. A positive TORCH_NUM_THREADS or
    OMP_NUM_THREADS still wins (0 or junk is ignored: PyTorch rejects 0)."""
    import os

    try:
        import torch
    except ImportError:
        return None
    threads = (
        _positive_int(os.environ.get("TORCH_NUM_THREADS"))
        or _positive_int(os.environ.get("OMP_NUM_THREADS"))
        or container_cpus()
    )
    torch.set_num_threads(threads)
    return threads


class EmbeddingService:
    def __init__(self, model_name: str, device: str = "cpu") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            # Lite profile (RL-01): no torch/sentence-transformers installed.
            # The deterministic hash embedder keeps the pipeline running with
            # weaker (lexical-bag) vectors — ranking still works, semantic
            # quality is reduced. Documented in docs/DEPLOYMENT.md.
            from app.services.embeddings.registry import hash_embed, spec_for

            logger.warning(
                "sentence-transformers not installed (lite profile): "
                "falling back to the deterministic hash embedder"
            )
            spec = spec_for(model_name)
            self._hash_spec = spec
            self._hash_embed = hash_embed
            self._cache_ns = f"hash:{spec.name}:{spec.dim}"
            self.model = None
            self.model_name = spec.name
            self.dimension = spec.dim
            return

        threads = limit_torch_threads()
        logger.info(f"Loading embedding model: {model_name} on {device} ({threads} threads)")
        self.model = SentenceTransformer(model_name, device=device)
        self.model_name = model_name
        self.dimension = 384
        self._cache_ns = f"st:{model_name}"

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        if self.model is None:
            return np.array(
                [self._hash_embed(t, self._hash_spec.dim) for t in texts], dtype=np.float32
            )
        return self.model.encode(texts, convert_to_numpy=True, show_progress_bar=False)

    async def _encode_async(self, texts: list[str]) -> np.ndarray:
        # sentence-transformers encode is CPU-bound and blocking; run it in a
        # thread so it never stalls the event loop (audit 04-4) — matters most on
        # API-triggered paths like onboarding.
        return await asyncio.to_thread(self.encode, texts)

    async def encode_single(self, text: str) -> list[float]:
        cache_key = f"emb:{self._cache_ns}:{hashlib.sha256(text.encode()).hexdigest()[:16]}"
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached
        vectors = await self._encode_async([text])
        vector = vectors[0].tolist()
        await cache_set(cache_key, vector, ttl_seconds=7 * 24 * 3600)
        return vector

    async def encode_batch_cached(self, texts: list[str]) -> list[list[float]]:
        results = []
        to_encode: list[tuple[int, str]] = []

        for i, text in enumerate(texts):
            cache_key = f"emb:{self._cache_ns}:{hashlib.sha256(text.encode()).hexdigest()[:16]}"
            cached = await cache_get(cache_key)
            if cached is not None:
                results.append((i, cached))
            else:
                to_encode.append((i, text))

        if to_encode:
            indices, raw_texts = zip(*to_encode, strict=False)
            vectors = await self._encode_async(list(raw_texts))
            for idx, vec in zip(indices, vectors, strict=False):
                vec_list = vec.tolist()
                results.append((idx, vec_list))
                cache_key = f"emb:{self._cache_ns}:{hashlib.sha256(raw_texts[list(indices).index(idx)].encode()).hexdigest()[:16]}"
                await cache_set(cache_key, vec_list, ttl_seconds=7 * 24 * 3600)

        results.sort(key=lambda x: x[0])
        return [r[1] for r in results]

    @staticmethod
    def build_embedding_text(title: str, summary_brief: str | None) -> str:
        text = title
        if summary_brief:
            text += ". " + summary_brief
        return text[:2048]


def get_embedding_service() -> EmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        from app.services.embeddings.registry import active_spec

        spec = active_spec(
            model=settings.embedding_model,
            cutover=settings.embedding_cutover_enabled,
        )
        if spec.name != settings.embedding_model and settings.embedding_cutover_enabled:
            logger.warning(
                "Embedding model %s cannot be stored in the 384-d column; using %s",
                settings.embedding_model,
                spec.name,
            )
        _embedding_service = EmbeddingService(
            model_name=spec.name,
            device=settings.embedding_device,
        )
        _embedding_service.dimension = spec.dim
    return _embedding_service
