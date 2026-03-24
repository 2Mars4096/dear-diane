"""Thin runtime helpers for model selection and provider resolution."""

from __future__ import annotations

from typing import Any


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
    return getattr(chat_manager, "_providers", None)


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


def resolve_llm_provider(
    chat_manager: Any,
    *,
    model: str,
    pii_session_key: str | None = None,
) -> Any:
    """Resolve an LLM provider, preferring the public chat-manager seam."""

    resolver = _explicit_attr(chat_manager, "resolve_llm_provider")
    if callable(resolver):
        return resolver(model=model, pii_session_key=pii_session_key)

    internal = _explicit_attr(chat_manager, "_resolve_provider")
    if callable(internal):
        return internal(model=model, pii_session_key=pii_session_key)

    registry = _provider_registry(chat_manager)
    if registry is None or not hasattr(registry, "resolve"):
        raise RuntimeError("No provider registry available")
    provider = registry.resolve(model)
    wrapper = _explicit_attr(chat_manager, "_wrap_provider_for_pii")
    if callable(wrapper):
        try:
            return wrapper(provider, pii_session_key=pii_session_key)
        except TypeError:
            return wrapper(provider)
    return provider
