from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from dan.llm_surface import (
    complete_chat_surface,
    complete_tool_chat_surface,
    resolve_llm_provider,
    resolve_tool_capable_provider,
    stream_chat_surface,
)


class _Provider:
    def __init__(self, name: str, *, supports_tool_calls: bool) -> None:
        self.name = name
        self.supports_tool_calls = supports_tool_calls


class _WrappedProvider:
    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def complete(self, *args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(provider=self._provider, args=args, kwargs=kwargs)


@dataclass
class _Chunk:
    delta: str
    accumulated: str
    done: bool
    usage: dict[str, int]


class _StreamingProvider(_WrappedProvider):
    def __init__(self, provider: Any) -> None:
        super().__init__(provider)
        self.stream_calls: list[dict[str, Any]] = []

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        self.stream_calls.append({"args": args, "kwargs": kwargs})
        yield _Chunk(delta="hi", accumulated="hi", done=True, usage={})


class _Manager:
    def __init__(self, resolved: Any | None, default: Any | None) -> None:
        self._providers: dict[str, Any] = {}
        if default is not None:
            self._providers["default"] = default
        if resolved is not None:
            self._providers["selected"] = resolved


def test_resolve_llm_provider_applies_shared_pii_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager(
        resolved=_Provider("selected", supports_tool_calls=True),
        default=_Provider("default", supports_tool_calls=True),
    )
    captured: dict[str, Any] = {}

    monkeypatch.setenv("DAN_PII_PROTECTION", "1")
    monkeypatch.setattr(
        "dan.llm_core.pii_tokenizer.SensitiveWordRegistry.load",
        classmethod(lambda cls: SimpleNamespace()),
    )
    monkeypatch.setattr(
        "dan.llm_core.pii_tokenizer.set_current_pii_session",
        lambda session: captured.setdefault("session", session),
    )

    provider = resolve_llm_provider(
        manager,
        model="selected",
        pii_session_key="session-1",
    )

    assert provider.__class__.__name__ == "TokenizingProviderWrapper"
    assert getattr(provider, "_provider").name == "selected"
    assert captured["session"] is provider._session


def test_resolve_tool_capable_provider_falls_back_to_default(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager(
        resolved=_Provider("selected", supports_tool_calls=False),
        default=_Provider("default", supports_tool_calls=True),
    )
    wrapped = {}

    def _wrap(provider: Any, *, pii_session_key: str | None = None) -> _WrappedProvider:
        wrapped["provider"] = _WrappedProvider(provider)
        return wrapped["provider"]

    monkeypatch.setattr("dan.llm_surface.wrap_provider_for_pii", _wrap)

    with caplog.at_level(logging.WARNING):
        provider = resolve_tool_capable_provider(
            manager,
            model="selected",
            pii_session_key="session-1",
            logger_override=logging.getLogger("test.llm_surface"),
        )

    assert isinstance(provider, _WrappedProvider)
    assert provider._provider.name == "default"
    assert any(
        "falling back to default provider for tool loop" in record.message
        for record in caplog.records
    )
    assert wrapped["provider"]._provider.name == "default"


def test_resolve_tool_capable_provider_warns_when_no_tool_capable_default_available(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = _Manager(
        resolved=_Provider("selected", supports_tool_calls=False),
        default=_Provider("default", supports_tool_calls=False),
    )

    with caplog.at_level(logging.WARNING):
        provider = resolve_tool_capable_provider(
            manager,
            model="selected",
            logger_override=logging.getLogger("test.llm_surface"),
        )

    assert isinstance(provider, _Provider)
    assert provider.name == "selected"
    assert any(
        "no tool-capable default provider is available" in record.message
        for record in caplog.records
    )


def test_resolve_llm_provider_uses_wrapped_default_when_model_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager(
        resolved=None,
        default=_Provider("default", supports_tool_calls=True),
    )
    wrapped: dict[str, Any] = {}

    def _wrap(provider: Any, *, pii_session_key: str | None = None) -> _WrappedProvider:
        wrapped["provider"] = _WrappedProvider(provider)
        return wrapped["provider"]

    monkeypatch.setattr("dan.llm_surface.wrap_provider_for_pii", _wrap)

    provider = resolve_llm_provider(
        manager,
        model="missing-model",
        pii_session_key="session-1",
    )

    assert isinstance(provider, _WrappedProvider)
    assert provider._provider.name == "default"
    assert wrapped["provider"] is provider


def test_resolve_llm_provider_uses_public_resolver_when_registry_is_missing() -> None:
    provider = _WrappedProvider(_Provider("resolved", supports_tool_calls=True))
    resolve_calls: list[dict[str, Any]] = []

    class _ResolverOnlyManager:
        def resolve_llm_provider(
            self,
            *,
            model: str | None = None,
            pii_session_key: str | None = None,
        ) -> Any:
            resolve_calls.append(
                {
                    "model": model,
                    "pii_session_key": pii_session_key,
                }
            )
            return provider

    resolved = resolve_llm_provider(
        _ResolverOnlyManager(),
        model="selected",
        pii_session_key="session-1",
    )

    assert resolved is provider
    assert resolve_calls == [
        {"model": "selected", "pii_session_key": "session-1"}
    ]


@pytest.mark.asyncio
async def test_complete_chat_surface_prefers_gateway_when_available() -> None:
    gateway_calls: list[dict[str, Any]] = []

    class _Gateway:
        async def complete(self, **kwargs: Any) -> Any:
            gateway_calls.append(kwargs)
            return SimpleNamespace(text="ok")

    manager = SimpleNamespace(model_gateway=_Gateway())

    result = await complete_chat_surface(
        manager,
        messages=[{"role": "user", "content": "hello"}],
        model="selected",
        temperature=0.0,
        max_tokens=7,
    )

    assert result.text == "ok"
    assert gateway_calls == [
        {
            "messages": [{"role": "user", "content": "hello"}],
            "model": "selected",
            "temperature": 0.0,
            "max_tokens": 7,
        }
    ]


@pytest.mark.asyncio
async def test_complete_tool_chat_surface_prefers_gateway_when_available() -> None:
    gateway_calls: list[dict[str, Any]] = []

    class _Gateway:
        def __init__(self) -> None:
            self.registry = SimpleNamespace(get=lambda name: None)

        def resolve(self, model: str) -> Any:
            return _Provider("selected", supports_tool_calls=True)

        async def complete(self, **kwargs: Any) -> Any:
            gateway_calls.append(kwargs)
            return SimpleNamespace(text="ok")

    manager = SimpleNamespace(model_gateway=_Gateway())

    result = await complete_tool_chat_surface(
        manager,
        messages=[{"role": "user", "content": "hello"}],
        model="selected",
        temperature=0.0,
        max_tokens=7,
        tools=[{"type": "function", "function": {"name": "tool_a"}}],
        tool_choice="auto",
    )

    assert result.text == "ok"
    assert gateway_calls == [
        {
            "messages": [{"role": "user", "content": "hello"}],
            "model": "selected",
            "temperature": 0.0,
            "max_tokens": 7,
            "tools": [{"type": "function", "function": {"name": "tool_a"}}],
            "tool_choice": "auto",
        }
    ]


@pytest.mark.asyncio
async def test_complete_tool_chat_surface_uses_gateway_default_provider_override_for_tool_fallback() -> None:
    gateway_calls: list[dict[str, Any]] = []
    selected_provider = _Provider("selected", supports_tool_calls=False)
    default_provider = _Provider("default", supports_tool_calls=True)

    class _Gateway:
        def __init__(self) -> None:
            self.registry = SimpleNamespace(
                get=lambda name: default_provider if name == "default" else None,
            )

        def resolve(self, model: str) -> Any:
            return selected_provider

        async def complete(self, **kwargs: Any) -> Any:
            gateway_calls.append(kwargs)
            return SimpleNamespace(text="ok")

    manager = SimpleNamespace(model_gateway=_Gateway())

    result = await complete_tool_chat_surface(
        manager,
        messages=[{"role": "user", "content": "hello"}],
        model="selected",
        tools=[{"type": "function", "function": {"name": "tool_a"}}],
        tool_choice="auto",
    )

    assert result.text == "ok"
    assert gateway_calls == [
        {
            "messages": [{"role": "user", "content": "hello"}],
            "model": "selected",
            "temperature": 0.7,
            "max_tokens": None,
            "tools": [{"type": "function", "function": {"name": "tool_a"}}],
            "tool_choice": "auto",
            "provider_name": "default",
        }
    ]


@pytest.mark.asyncio
async def test_stream_chat_surface_uses_public_resolver_when_registry_is_missing() -> None:
    provider = _StreamingProvider(_Provider("resolved", supports_tool_calls=True))
    resolve_calls: list[dict[str, Any]] = []

    class _ResolverOnlyManager:
        def resolve_llm_provider(
            self,
            *,
            model: str | None = None,
            pii_session_key: str | None = None,
        ) -> Any:
            resolve_calls.append(
                {
                    "model": model,
                    "pii_session_key": pii_session_key,
                }
            )
            return provider

    chunks = [
        chunk async for chunk in stream_chat_surface(
            _ResolverOnlyManager(),
            messages=[{"role": "user", "content": "hello"}],
            model="selected",
            temperature=0.2,
            pii_session_key="session-1",
        )
    ]

    assert [chunk.accumulated for chunk in chunks] == ["hi"]
    assert resolve_calls == [
        {"model": "selected", "pii_session_key": "session-1"}
    ]
    assert provider.stream_calls == [
        {
            "args": (),
            "kwargs": {
                "messages": [{"role": "user", "content": "hello"}],
                "model": "selected",
                "temperature": 0.2,
            },
        }
    ]
