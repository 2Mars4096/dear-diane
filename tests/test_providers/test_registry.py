from __future__ import annotations

import pytest

from dan.providers.registry import ProviderRegistry


class _DummyProvider:
    async def complete(self, *args, **kwargs):  # pragma: no cover - protocol stub
        raise NotImplementedError

    async def stream(self, *args, **kwargs):  # pragma: no cover - protocol stub
        raise NotImplementedError


def test_claude_prefix_requires_anthropic_provider() -> None:
    registry = ProviderRegistry()
    registry.register("default", _DummyProvider())

    with pytest.raises(KeyError, match="requires provider 'anthropic'"):
        registry.resolve_name("claude-sonnet-4-6")


def test_gemini_prefix_requires_google_provider() -> None:
    registry = ProviderRegistry()
    registry.register("default", _DummyProvider())

    with pytest.raises(KeyError, match="requires provider 'google'"):
        registry.resolve_name("gemini-2.5-pro")


def test_gpt_prefix_can_still_fall_back_to_default() -> None:
    registry = ProviderRegistry()
    registry.register("default", _DummyProvider())

    assert registry.resolve_name("gpt-4o") == "default"


def test_model_override_to_missing_provider_raises() -> None:
    registry = ProviderRegistry()
    registry.register("default", _DummyProvider())
    registry.set_model_override("claude-sonnet-4-6", "anthropic")

    with pytest.raises(KeyError, match="pinned to provider 'anthropic'"):
        registry.resolve_name("claude-sonnet-4-6")


def test_model_override_to_missing_provider_raises_for_direct_resolve() -> None:
    registry = ProviderRegistry()
    registry.register("default", _DummyProvider())
    registry.set_model_override("claude-sonnet-4-6", "anthropic")

    with pytest.raises(KeyError, match="pinned to provider 'anthropic'"):
        registry.resolve("claude-sonnet-4-6")


def test_runtime_name_reports_strict_prefix_fallback() -> None:
    registry = ProviderRegistry()
    registry.register("default", _DummyProvider())

    provider_name, warning = registry.resolve_runtime_name("claude-sonnet-4-6")

    assert provider_name == "default"
    assert warning is not None
    assert "fall back to the configured `default` provider" in warning


def test_runtime_name_has_no_warning_for_registered_prefix_provider() -> None:
    registry = ProviderRegistry()
    registry.register("default", _DummyProvider())
    registry.register("anthropic", _DummyProvider())

    provider_name, warning = registry.resolve_runtime_name("claude-sonnet-4-6")

    assert provider_name == "anthropic"
    assert warning is None
