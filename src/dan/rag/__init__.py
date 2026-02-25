"""RAG subsystem — embedding providers, vector stores, and indexing pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Embedding result
# ---------------------------------------------------------------------------


@dataclass
class EmbeddingResult:
    """Result of an embedding request."""

    vectors: list[list[float]]
    model: str
    usage: dict[str, Any] | None = None
    dimensions: int = 0


# ---------------------------------------------------------------------------
# EmbeddingProvider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol that all embedding providers must satisfy."""

    async def embed(self, texts: list[str], model: str) -> EmbeddingResult: ...


# ---------------------------------------------------------------------------
# OpenAI embedding provider
# ---------------------------------------------------------------------------


class OpenAIEmbeddingProvider:
    """Wraps ``AsyncOpenAI`` for text embedding.

    Uses ``text-embedding-3-small`` by default.  Batches texts in a
    single API call (OpenAI supports up to 2048 inputs per request).
    """

    DEFAULT_MODEL = "text-embedding-3-small"

    def __init__(
        self,
        api_key: str = "",
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError(
                "openai package required for OpenAIEmbeddingProvider. "
                "Install with: pip install 'deep-agent-network[embeddings]'"
            ) from exc

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._default_model = default_model or self.DEFAULT_MODEL

    async def embed(self, texts: list[str], model: str = "") -> EmbeddingResult:
        model = model or self._default_model
        response = await self._client.embeddings.create(input=texts, model=model)
        vectors = [item.embedding for item in response.data]
        dimensions = len(vectors[0]) if vectors else 0
        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        return EmbeddingResult(
            vectors=vectors, model=model, usage=usage, dimensions=dimensions,
        )


# ---------------------------------------------------------------------------
# Local (sentence-transformers) embedding provider
# ---------------------------------------------------------------------------


class LocalEmbeddingProvider:
    """Wraps ``sentence-transformers`` for local CPU/GPU embedding.

    Runs the synchronous ``SentenceTransformer.encode()`` in a thread via
    ``asyncio.to_thread()`` to avoid blocking the event loop.
    """

    DEFAULT_MODEL = "all-MiniLM-L6-v2"

    def __init__(self, default_model: str | None = None) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers package required for LocalEmbeddingProvider. "
                "Install with: pip install 'deep-agent-network[embeddings]'"
            ) from exc
        self._default_model = default_model or self.DEFAULT_MODEL
        self._models: dict[str, Any] = {}

    def _get_model(self, model_name: str):
        if model_name not in self._models:
            from sentence_transformers import SentenceTransformer
            self._models[model_name] = SentenceTransformer(model_name)
        return self._models[model_name]

    async def embed(self, texts: list[str], model: str = "") -> EmbeddingResult:
        import asyncio

        model = model or self._default_model
        st_model = self._get_model(model)
        raw = await asyncio.to_thread(st_model.encode, texts)
        vectors = [v.tolist() if hasattr(v, "tolist") else list(v) for v in raw]
        dimensions = len(vectors[0]) if vectors else 0
        return EmbeddingResult(
            vectors=vectors, model=model, usage=None, dimensions=dimensions,
        )


# ---------------------------------------------------------------------------
# Embedding registry
# ---------------------------------------------------------------------------

_DEFAULT_EMBEDDING_PREFIXES: list[tuple[str, str]] = [
    ("text-embedding-", "openai"),
    ("all-MiniLM-", "local"),
    ("paraphrase-", "local"),
    ("sentence-transformers/", "local"),
]


@dataclass
class EmbeddingRegistry:
    """Routes embedding model names to the correct provider.

    Resolution order mirrors ``ProviderRegistry``:
    1. Exact model → provider override map
    2. Prefix pattern match
    3. ``"default"`` provider as final fallback
    """

    _providers: dict[str, EmbeddingProvider] = field(default_factory=dict)
    _model_overrides: dict[str, str] = field(default_factory=dict)
    _prefix_patterns: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self._prefix_patterns:
            self._prefix_patterns = list(_DEFAULT_EMBEDDING_PREFIXES)

    def register(self, name: str, provider: EmbeddingProvider) -> None:
        self._providers[name] = provider

    def set_model_override(self, model: str, provider_name: str) -> None:
        self._model_overrides[model] = provider_name

    def add_prefix_pattern(self, prefix: str, provider_name: str) -> None:
        self._prefix_patterns.append((prefix, provider_name))

    def resolve(self, model: str) -> EmbeddingProvider:
        """Resolve a model name to its embedding provider.

        Raises ``KeyError`` if no matching provider is found.
        """
        if model in self._model_overrides:
            provider_name = self._model_overrides[model]
            if provider_name in self._providers:
                return self._providers[provider_name]

        for prefix, provider_name in self._prefix_patterns:
            if model.startswith(prefix) and provider_name in self._providers:
                return self._providers[provider_name]

        if "default" in self._providers:
            return self._providers["default"]

        raise KeyError(
            f"No embedding provider found for model '{model}'. "
            f"Registered providers: {sorted(self._providers)}. "
            f"Model overrides: {self._model_overrides}"
        )

    def has_provider(self, name: str) -> bool:
        return name in self._providers

    def provider_names(self) -> list[str]:
        return sorted(self._providers)
