"""RAG subsystem — embedding providers, vector stores, and indexing pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

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

    DEFAULT_MODEL = DEFAULT_EMBEDDING_MODEL

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


def _create_embedding_provider(
    name: str,
    pconfig: Any,
    fallback_api_key: str = "",
    fallback_base_url: str = "",
    fallback_model: str = DEFAULT_EMBEDDING_MODEL,
) -> EmbeddingProvider | None:
    """Instantiate an embedding provider by name + config."""
    if name in {"default", "openai"}:
        api_key = getattr(pconfig, "api_key", "") or fallback_api_key
        base_url = getattr(pconfig, "base_url", None) or fallback_base_url or None
        default_model = getattr(pconfig, "default_model", "") or fallback_model
        try:
            return OpenAIEmbeddingProvider(
                api_key=api_key, base_url=base_url, default_model=default_model,
            )
        except Exception:
            logger.warning("Could not create OpenAI embedding provider '%s'", name)
            return None

    if name in {"local", "sentence-transformers"}:
        try:
            default_model = getattr(pconfig, "default_model", "") or "all-MiniLM-L6-v2"
            return LocalEmbeddingProvider(default_model=default_model)
        except Exception:
            logger.warning("Could not create local embedding provider '%s'", name)
            return None

    logger.warning("Unknown embedding provider '%s'; treating as OpenAI-compatible", name)
    api_key = getattr(pconfig, "api_key", "") or fallback_api_key
    base_url = getattr(pconfig, "base_url", None) or fallback_base_url or None
    default_model = getattr(pconfig, "default_model", "") or fallback_model
    try:
        return OpenAIEmbeddingProvider(
            api_key=api_key, base_url=base_url, default_model=default_model,
        )
    except Exception:
        return None


def build_embedding_registry(config: Any) -> EmbeddingRegistry:
    """Build an ``EmbeddingRegistry`` from an ``EngineConfig`` (or compatible).

    Shared helper used by both the execution engine and the server's
    RAG CRUD endpoints so they resolve embedding providers identically.
    """
    from dan.providers import ProviderConfig

    registry = EmbeddingRegistry()
    fallback_key = getattr(config, "llm_api_key", "")
    fallback_url = getattr(config, "llm_base_url", "")
    fallback_model = getattr(config, "default_embedding_model", DEFAULT_EMBEDDING_MODEL)

    embedding_providers = getattr(config, "embedding_providers", {}) or {}
    if embedding_providers:
        for name, pconf in embedding_providers.items():
            provider = _create_embedding_provider(name, pconf, fallback_key, fallback_url, fallback_model)
            if provider is not None:
                registry.register(name, provider)
    else:
        if fallback_key:
            fallback_conf = ProviderConfig(
                api_key=fallback_key, base_url=fallback_url, default_model=fallback_model,
            )
            provider = _create_embedding_provider("default", fallback_conf, fallback_key, fallback_url, fallback_model)
            if provider is not None:
                registry.register("default", provider)

    if not registry.has_provider("default") and "openai" in embedding_providers:
        provider = _create_embedding_provider("openai", embedding_providers["openai"], fallback_key, fallback_url, fallback_model)
        if provider is not None:
            registry.register("default", provider)

    model_map = getattr(config, "embedding_model_provider_map", {}) or {}
    for model, provider_name in model_map.items():
        registry.set_model_override(model, provider_name)

    return registry
