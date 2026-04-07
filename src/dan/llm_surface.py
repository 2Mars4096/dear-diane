"""Neutral helpers for resolving chat-surface LLM seams.

These helpers operate on duck-typed chat-manager objects and avoid forcing
shared layers like concierge to import through ``dan.server``.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from dan.providers import supports_tool_calls

logger = logging.getLogger(__name__)


def _explicit_attr(chat_manager: Any, name: str) -> Any | None:
    instance_dict = getattr(chat_manager, "__dict__", None)
    if isinstance(instance_dict, dict) and name in instance_dict:
        return instance_dict[name]

    class_attr = getattr(type(chat_manager), name, None)
    if class_attr is not None:
        if isinstance(class_attr, property):
            return getattr(chat_manager, name)
        if callable(class_attr):
            return getattr(chat_manager, name)
        return class_attr
    return None


def _provider_registry(chat_manager: Any) -> Any | None:
    registry = _explicit_attr(chat_manager, "provider_registry")
    if registry is not None:
        return registry
    instance_dict = getattr(chat_manager, "__dict__", None)
    if isinstance(instance_dict, dict) and "_providers" in instance_dict:
        return instance_dict["_providers"]
    return None


def _provider_resolver(chat_manager: Any) -> Any | None:
    resolver = _explicit_attr(chat_manager, "resolve_llm_provider")
    return resolver if callable(resolver) else None


def _instance_callable_attr(chat_manager: Any, name: str) -> Any | None:
    instance_dict = getattr(chat_manager, "__dict__", None)
    if not isinstance(instance_dict, dict):
        return None
    value = instance_dict.get(name)
    return value if callable(value) else None


def _resolve_registry_provider(chat_manager: Any, model: str) -> Any:
    registry = _provider_registry(chat_manager)
    if registry is None:
        raise RuntimeError("No provider registry available")

    resolver = getattr(registry, "resolve", None)
    if callable(resolver):
        return resolver(model)

    getter = getattr(registry, "get", None)
    if callable(getter):
        provider = getter(model)
        if provider is None and model != "default":
            provider = getter("default")
        if provider is not None:
            return provider

    if isinstance(registry, dict):
        provider = registry.get(model)
        if provider is None and model != "default":
            provider = registry.get("default")
        if provider is not None:
            return provider

    raise RuntimeError(f"No provider registered for model {model!r}")


def _default_registry_provider(chat_manager: Any) -> Any | None:
    registry = _provider_registry(chat_manager)
    if registry is None:
        return None

    getter = getattr(registry, "get", None)
    if callable(getter):
        return getter("default")

    if isinstance(registry, dict):
        return registry.get("default")

    providers = getattr(registry, "_providers", None)
    if isinstance(providers, dict):
        return providers.get("default")

    return None


def _resolve_wrapped_registry_provider(
    chat_manager: Any,
    *,
    model: str,
    pii_session_key: str | None = None,
) -> Any:
    """Resolve a provider from the shared registry and apply PII wrapping."""
    return wrap_provider_for_pii(
        _resolve_registry_provider(chat_manager, model),
        pii_session_key=pii_session_key,
    )


def wrap_provider_for_pii(
    provider: Any,
    *,
    pii_session_key: str | None = None,
) -> Any:
    """Wrap a provider with request-scoped PII tokenization when enabled."""
    try:
        from dan.llm_core.pii_tokenizer import (
            SensitiveWordRegistry,
            TokenizingProviderWrapper,
            get_pii_session,
            is_pii_enabled,
            set_current_pii_session,
        )

        if is_pii_enabled():
            session = get_pii_session(pii_session_key)
            set_current_pii_session(session)
            return TokenizingProviderWrapper(
                provider=provider,
                session=session,
                registry=SensitiveWordRegistry.load(),
            )
    except Exception:
        logger.debug("PII provider wrapping unavailable", exc_info=True)
    return provider


def default_llm_model(chat_manager: Any) -> str:
    """Resolve the runtime default model from a public seam when possible."""
    value = _explicit_attr(chat_manager, "default_llm_model")
    if callable(value):
        return str(value() or "").strip()
    if value is not None:
        text = str(value or "").strip()
        if text:
            return text
    return str(getattr(chat_manager, "_chat_model", "") or "").strip()


def provider_names(chat_manager: Any) -> list[str]:
    """Return registered provider names from a public seam when possible."""
    registry = _provider_registry(chat_manager)
    if registry is None:
        return []
    getter = getattr(registry, "provider_names", None)
    if callable(getter):
        try:
            return list(getter())
        except Exception:
            return []
    providers = getattr(registry, "_providers", None)
    if isinstance(providers, dict):
        return list(providers.keys())
    return []


def resolve_model_gateway(chat_manager: Any) -> Any | None:
    """Return a shared ModelGateway, synthesizing one from the registry when needed."""
    gateway = _explicit_attr(chat_manager, "model_gateway")
    if gateway is not None:
        return gateway

    registry = _provider_registry(chat_manager)
    if registry is None or not hasattr(registry, "resolve"):
        return None
    resolver = _provider_resolver(chat_manager)
    provider_names = getattr(registry, "provider_names", None)
    if resolver is not None and callable(provider_names) and not provider_names():
        return None

    try:
        from dan.llm_core.config import GatewayConfig
        from dan.llm_core.gateway import ModelGateway

        gateway = ModelGateway(
            registry=registry,
            config=GatewayConfig(
                pii_enabled=False,
                retry_enabled=False,
                telemetry_enabled=False,
                budget_enabled=False,
                fallback_model=None,
            ),
        )
        try:
            setattr(chat_manager, "model_gateway", gateway)
        except Exception:
            logger.debug("Unable to cache synthesized chat model gateway", exc_info=True)
        return gateway
    except Exception:
        logger.debug("Failed to synthesize chat model gateway from registry", exc_info=True)
        return None


def resolve_llm_provider(
    chat_manager: Any,
    *,
    model: str,
    pii_session_key: str | None = None,
) -> Any:
    """Resolve an LLM provider from the chat surface and apply PII wrapping."""
    resolver = _provider_resolver(chat_manager)
    internal_resolver = _explicit_attr(chat_manager, "_resolve_provider")
    instance_internal_resolver = _instance_callable_attr(chat_manager, "_resolve_provider")
    if resolver is not None and not callable(internal_resolver):
        return resolver(
            model=model,
            pii_session_key=pii_session_key,
        )

    registry = _provider_registry(chat_manager)
    if registry is not None:
        try:
            return _resolve_wrapped_registry_provider(
                chat_manager,
                model=model,
                pii_session_key=pii_session_key,
            )
        except KeyError:
            default_provider = _default_registry_provider(chat_manager)
            if default_provider is not None:
                logger.debug(
                    "Falling back to default provider after registry lookup failed for model %s",
                    model,
                    exc_info=True,
                )
                return _resolve_wrapped_registry_provider(
                    chat_manager,
                    model="default",
                    pii_session_key=pii_session_key,
                )
            if instance_internal_resolver is not None:
                logger.debug(
                    "Falling back to explicit provider resolver after registry lookup failed for model %s",
                    model,
                    exc_info=True,
                )
                return instance_internal_resolver(
                    model=model,
                    pii_session_key=pii_session_key,
                )
            raise
    if instance_internal_resolver is not None:
        return instance_internal_resolver(
            model=model,
            pii_session_key=pii_session_key,
        )
    raise RuntimeError("No provider registry available")


def resolve_tool_capable_provider(
    chat_manager: Any,
    *,
    model: str,
    pii_session_key: str | None = None,
    logger_override: logging.Logger | None = None,
) -> Any:
    """Resolve a provider, falling back to a tool-capable default when needed."""
    log = logger_override or logger
    registry = _provider_registry(chat_manager)
    if registry is None:
        provider = resolve_llm_provider(
            chat_manager,
            model=model,
            pii_session_key=pii_session_key,
        )
        if supports_tool_calls(provider):
            return provider
        raw_provider = getattr(provider, "_provider", provider)
        log.warning(
            "Resolved provider %s for model %s does not support tool-calling "
            "and no tool-capable default provider is available",
            type(raw_provider).__name__,
            model,
        )
        return provider

    resolver = _provider_resolver(chat_manager)
    used_explicit_resolver = False
    default_provider = _default_registry_provider(chat_manager)
    try:
        provider = _resolve_registry_provider(chat_manager, model)
    except KeyError:
        if default_provider is not None:
            provider = default_provider
        elif resolver is None:
            raise
        else:
            log.debug(
                "Falling back to explicit provider resolver after tool-capable registry lookup failed for model %s",
                model,
                exc_info=True,
            )
            provider = resolver(
                model=model,
                pii_session_key=pii_session_key,
            )
            used_explicit_resolver = True
    raw_provider = getattr(provider, "_provider", provider)
    if supports_tool_calls(provider):
        if used_explicit_resolver:
            return provider
        wrapped_model = model
        if default_provider is not None and raw_provider is default_provider:
            wrapped_model = "default"
        return _resolve_wrapped_registry_provider(
            chat_manager,
            model=wrapped_model,
            pii_session_key=pii_session_key,
        )

    default_provider = _default_registry_provider(chat_manager)
    if (
        default_provider is not None
        and default_provider is not raw_provider
        and supports_tool_calls(default_provider)
    ):
        log.warning(
            "Resolved provider %s for model %s does not support tool-calling; "
            "falling back to default provider for tool loop",
            type(raw_provider).__name__,
            model,
        )
        return _resolve_wrapped_registry_provider(
            chat_manager,
            model="default",
            pii_session_key=pii_session_key,
        )

    log.warning(
        "Resolved provider %s for model %s does not support tool-calling "
        "and no tool-capable default provider is available",
        type(raw_provider).__name__,
        model,
    )
    return _resolve_wrapped_registry_provider(
        chat_manager,
        model=model,
        pii_session_key=pii_session_key,
    )


async def complete_chat_surface(
    chat_manager: Any,
    *,
    messages: list[dict[str, Any]],
    model: str,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    pii_session_key: str | None = None,
    **kwargs: Any,
) -> Any:
    """Complete via shared gateway when available, else public provider seam."""
    gateway = resolve_model_gateway(chat_manager)
    if gateway is not None:
        if pii_session_key:
            from dan.llm_core.pii_tokenizer import get_pii_session

            kwargs = {**kwargs, "pii_session": get_pii_session(pii_session_key)}
        return await gateway.complete(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    provider = resolve_llm_provider(
        chat_manager,
        model=model,
        pii_session_key=pii_session_key,
    )
    return await provider.complete(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )


async def complete_tool_chat_surface(
    chat_manager: Any,
    *,
    messages: list[dict[str, Any]],
    model: str,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    pii_session_key: str | None = None,
    logger_override: logging.Logger | None = None,
    **kwargs: Any,
) -> Any:
    """Complete a tool-capable chat turn via the shared gateway when possible."""
    log = logger_override or logger
    gateway = resolve_model_gateway(chat_manager)
    if gateway is not None:
        provider_name_override: str | None = None
        provider = gateway.resolve(model)
        raw_provider = getattr(provider, "_provider", provider)
        if not supports_tool_calls(provider):
            registry = getattr(gateway, "registry", None)
            default_provider = (
                registry.get("default")
                if registry is not None and hasattr(registry, "get")
                else None
            )
            if (
                default_provider is not None
                and default_provider is not raw_provider
                and supports_tool_calls(default_provider)
            ):
                log.warning(
                    "Resolved provider %s for model %s does not support tool-calling; "
                    "falling back to default provider for tool loop",
                    type(raw_provider).__name__,
                    model,
                )
                provider_name_override = "default"
            else:
                log.warning(
                    "Resolved provider %s for model %s does not support tool-calling "
                    "and no tool-capable default provider is available",
                    type(raw_provider).__name__,
                    model,
                )
        if pii_session_key:
            from dan.llm_core.pii_tokenizer import get_pii_session

            kwargs = {**kwargs, "pii_session": get_pii_session(pii_session_key)}
        if provider_name_override:
            kwargs = {**kwargs, "provider_name": provider_name_override}
        return await gateway.complete(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    provider = resolve_tool_capable_provider(
        chat_manager,
        model=model,
        pii_session_key=pii_session_key,
        logger_override=log,
    )
    return await provider.complete(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )


async def stream_chat_surface(
    chat_manager: Any,
    *,
    messages: list[dict[str, Any]],
    model: str,
    temperature: float = 0.7,
    pii_session_key: str | None = None,
    **kwargs: Any,
) -> AsyncIterator[Any]:
    """Stream via shared gateway when available, else public provider seam."""
    gateway = resolve_model_gateway(chat_manager)
    gateway_stream = getattr(gateway, "stream", None) if gateway is not None else None
    if callable(gateway_stream):
        if pii_session_key:
            from dan.llm_core.pii_tokenizer import get_pii_session

            kwargs = {**kwargs, "pii_session": get_pii_session(pii_session_key)}
        async for chunk in gateway_stream(
            messages=messages,
            model=model,
            temperature=temperature,
            **kwargs,
        ):
            yield chunk
        return

    provider = resolve_llm_provider(
        chat_manager,
        model=model,
        pii_session_key=pii_session_key,
    )
    async for chunk in provider.stream(
        messages=messages,
        model=model,
        temperature=temperature,
        **kwargs,
    ):
        yield chunk


__all__ = [
    "complete_chat_surface",
    "complete_tool_chat_surface",
    "default_llm_model",
    "provider_names",
    "resolve_llm_provider",
    "resolve_tool_capable_provider",
    "resolve_model_gateway",
    "stream_chat_surface",
    "wrap_provider_for_pii",
]
