"""Shared provider-resolution helpers for executor modules."""

from __future__ import annotations

import inspect
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_pii_session(context: Any, model: str) -> tuple[bool, Any | None]:
    pii_required = os.environ.get("DAN_PII_PROTECTION") == "1"
    try:
        from dan.llm_core import pii_tokenizer

        pii_required = pii_tokenizer.is_pii_enabled()
        if pii_required:
            session_fn = getattr(context, "pii_session_key", None)
            session_key = session_fn(model) if callable(session_fn) else model
            return True, pii_tokenizer.get_pii_session(str(session_key))
    except Exception as exc:
        if pii_required:
            raise RuntimeError(
                "PII protection is enabled but provider wrapping failed.",
            ) from exc
        logger.debug("LLM executor PII wrapping unavailable", exc_info=True)
    return False, None


class _GatewayBackedProviderAdapter:
    """Provider-shaped adapter that routes execution through a ModelGateway."""

    def __init__(self, gateway: Any, *, context: Any, model: str) -> None:
        self._gateway = gateway
        self._context = context
        self._model = model
        try:
            self._provider = gateway.resolve(model)
        except Exception:
            self._provider = None

    def __getattr__(self, name: str) -> Any:
        provider = object.__getattribute__(self, "_provider")
        if provider is None:
            raise AttributeError(name)
        return getattr(provider, name)

    def apply_cache_hints(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        provider = self._provider
        if provider is not None and hasattr(provider, "apply_cache_hints"):
            return provider.apply_cache_hints(messages)
        return messages

    def _gateway_kwargs(self, model: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        gateway_kwargs = dict(kwargs)
        pii_required, pii_session = _resolve_pii_session(self._context, model)
        if pii_required and pii_session is not None:
            gateway_kwargs.setdefault("pii", True)
            gateway_kwargs.setdefault("pii_session", pii_session)
        return gateway_kwargs

    async def complete(
        self,
        *args: Any,
        messages: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Any:
        if messages is None and args:
            messages = args[0]
        if model is None and len(args) >= 2:
            model = str(args[1] or "")
        if messages is None:
            raise TypeError("messages are required for gateway-backed completion")
        effective_model = str(model or self._model)
        return await self._gateway.complete(
            messages=messages,
            model=effective_model,
            temperature=temperature,
            max_tokens=max_tokens,
            **self._gateway_kwargs(effective_model, kwargs),
        )

    async def stream(
        self,
        *args: Any,
        messages: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ):
        if messages is None and args:
            messages = args[0]
        if model is None and len(args) >= 2:
            model = str(args[1] or "")
        if messages is None:
            raise TypeError("messages are required for gateway-backed streaming")
        effective_model = str(model or self._model)
        stream_iter = self._gateway.stream(
            messages=messages,
            model=effective_model,
            temperature=temperature,
            max_tokens=max_tokens,
            **self._gateway_kwargs(effective_model, kwargs),
        )
        if inspect.isawaitable(stream_iter):
            stream_iter = await stream_iter
        async for chunk in stream_iter:
            yield chunk

def _cache_runtime_gateway(context: Any, gateway: Any) -> Any:
    if gateway is None:
        return None
    try:
        setattr(context, "model_gateway", gateway)
    except Exception:
        logger.debug("Unable to cache runtime model gateway on context", exc_info=True)
    try:
        if getattr(context, "provider_registry", None) is None:
            setattr(context, "provider_registry", gateway.registry)
    except Exception:
        logger.debug("Unable to cache runtime provider registry on context", exc_info=True)
    return gateway


def _runtime_gateway_config():
    from dan.llm_core.config import GatewayConfig

    return GatewayConfig(
        pii_enabled=False,
        retry_enabled=False,
        telemetry_enabled=False,
        budget_enabled=False,
        fallback_model=None,
    )


def _build_runtime_gateway(context: Any):
    config = getattr(context, "config", None)
    if config is None:
        return None
    try:
        from dan.llm_core.factory import build_gateway

        if hasattr(config, "providers"):
            return build_gateway(
                engine_config=config,
                gateway_config=_runtime_gateway_config(),
            )
        return build_gateway(
            api_key=str(getattr(config, "llm_api_key", "") or ""),
            base_url=str(getattr(config, "llm_base_url", "") or ""),
            model_provider_map=getattr(config, "model_provider_map", None),
            gateway_config=_runtime_gateway_config(),
        )
    except Exception:
        logger.debug("Executor runtime gateway build failed", exc_info=True)
        return None


def resolve_gateway(context: Any, *, allow_config_fallback: bool = False):
    """Try to get a ModelGateway from the execution context.

    When ``allow_config_fallback`` is true, lazily builds and caches a gateway
    from the runtime config so completion-style executors still use the shared
    llm_core path even outside full Engine composition.
    """
    gateway = getattr(context, "model_gateway", None)
    if gateway is not None:
        return gateway
    registry = getattr(context, "provider_registry", None)
    if registry is not None:
        from dan.llm_core.gateway import ModelGateway

        return _cache_runtime_gateway(
            context,
            ModelGateway(
                registry=registry,
                config=_runtime_gateway_config(),
            ),
        )
    if allow_config_fallback:
        return _cache_runtime_gateway(context, _build_runtime_gateway(context))
    return None


def resolve_completion_provider(
    context: Any,
    model: str,
    *,
    allow_legacy_openai_fallback: bool = False,
) -> Any | None:
    """Resolve a completion-capable provider for *model*.

    Returns a completion-capable gateway-backed interface when possible.
    The legacy flag is kept for compatibility but now means "build a runtime
    gateway from config if the context was not fully prewired" rather than
    bypassing ``llm_core`` with a direct client.
    """
    gateway = resolve_gateway(
        context,
        allow_config_fallback=allow_legacy_openai_fallback,
    )
    if gateway is not None:
        return gateway
    return None


def resolve_llm_provider(
    context: Any,
    model: str,
) -> Any | None:
    """Resolve an LLM provider through the gateway-backed runtime seam."""
    gateway = resolve_gateway(context, allow_config_fallback=True)
    if gateway is not None:
        return _GatewayBackedProviderAdapter(
            gateway,
            context=context,
            model=model,
        )
    return None


def resolve_embedding_provider(
    node: Any,
    context: Any,
) -> tuple[Any | None, str]:
    """Resolve an embedding provider, preferring the registry when present."""
    from dan.rag import DEFAULT_EMBEDDING_MODEL

    embedding_registry = getattr(context, "embedding_registry", None)
    default_model = getattr(
        getattr(context, "config", None),
        "default_embedding_model",
        DEFAULT_EMBEDDING_MODEL,
    )
    model = getattr(node, "embedding_model", "") or default_model

    if embedding_registry is not None:
        try:
            return embedding_registry.resolve(model), model
        except KeyError:
            pass

    vs_config = getattr(node, "vector_store_config", {}) or {}
    if "embedding_provider" in vs_config:
        return vs_config["embedding_provider"], model

    return None, model
