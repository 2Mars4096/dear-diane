"""Tests for chat thread title generation and protection."""

from __future__ import annotations

from pathlib import Path

import pytest

from dan.providers import CompletionResult
from dan.providers.registry import ProviderRegistry
from dan.server.chat_store import ChatMessage, ChatStore
from dan.server.chat_titles import (
    ThreadTitleGenerator,
    autogenerate_thread_title,
    apply_generated_title,
    ensure_fallback_title,
    mark_manual_title,
)


class FakeProvider:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict[str, object]] = []

    async def complete(
        self,
        messages: list[dict[str, object]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: object,
    ) -> CompletionResult:
        self.calls.append({
            "messages": messages,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "kwargs": kwargs,
        })
        return CompletionResult(text=self._text, model=model)


def test_ensure_fallback_title_uses_first_user_message_only(tmp_path: Path) -> None:
    store = ChatStore(str(tmp_path))
    thread = store.create_thread("wf-1", title="")
    thread.messages = [
        ChatMessage(role="assistant", content="Working on it"),
        ChatMessage(
            role="user",
            content="Help me understand recent oil prices fluctuations and what drives them.",
        ),
        ChatMessage(role="user", content="A later turn should not matter"),
    ]
    store.save_thread(thread)

    fallback = ensure_fallback_title(store, "wf-1", thread.id)
    saved = store.get_thread("wf-1", thread.id)
    assert fallback is not None
    assert fallback.startswith("Help me understand recent oil prices")
    assert "later turn" not in fallback
    assert saved is not None
    assert saved.title == fallback


def test_ensure_fallback_title_strips_paths_and_request_prefixes(tmp_path: Path) -> None:
    store = ChatStore(str(tmp_path))
    thread = store.create_thread("wf-1", title="")
    thread.messages = [
        ChatMessage(
            role="user",
            content=(
                "can you /Users/lizhi/Dropbox/CUHK-phd/projects/supply-chain-report "
                "in this folder, Help me write a comprehensive auto supply chain report "
                "of 2026. in tex. you can download and cite figures by searching online."
            ),
        ),
    ]
    store.save_thread(thread)

    fallback = ensure_fallback_title(store, "wf-1", thread.id)
    assert fallback == "comprehensive auto supply chain report of 2026"
    assert "/Users/" not in fallback
    assert "can you" not in fallback.lower()


@pytest.mark.asyncio
async def test_thread_title_generator_uses_micro_tier_model() -> None:
    registry = ProviderRegistry()
    provider = FakeProvider('"Oil Price Outlook"')
    registry.register("openai", provider)

    generator = ThreadTitleGenerator(registry, chat_model="gpt-4o")
    title = await generator.generate_title(
        "Help me understand recent oil prices fluctuations and what drives them.",
    )

    assert title == "Oil Price Outlook"
    assert provider.calls
    assert provider.calls[0]["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_thread_title_generator_prefers_chat_manager_llm_seam() -> None:
    registry = ProviderRegistry()
    registry.register("default", FakeProvider("unused"))

    provider = FakeProvider('"Runtime Oil Outlook"')
    registry.resolve = lambda model: (_ for _ in ()).throw(AssertionError("registry.resolve should not be used"))  # type: ignore[assignment]

    class _ChatManagerLike:
        def __init__(self, registry: ProviderRegistry) -> None:
            self.provider_registry = registry
            self.calls: list[dict[str, object]] = []

        def resolve_llm_provider(
            self,
            *,
            model: str,
            pii_session_key: str | None = None,
        ) -> FakeProvider:
            self.calls.append({
                "model": model,
                "pii_session_key": pii_session_key,
            })
            return provider

        def default_llm_model(self) -> str:
            return "runtime-model"

    chat_manager = _ChatManagerLike(registry)

    generator = ThreadTitleGenerator(
        registry,
        chat_model="gpt-4o",
        chat_manager=chat_manager,
    )
    title = await generator.generate_title(
        "Help me understand recent oil prices fluctuations and what drives them.",
    )

    assert title == "Runtime Oil Outlook"
    assert chat_manager.calls == [{"model": "gpt-4o", "pii_session_key": None}]
    assert provider.calls
    assert provider.calls[0]["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_autogenerate_thread_title_updates_thread_and_meta(tmp_path: Path) -> None:
    store = ChatStore(str(tmp_path))
    thread = store.create_thread("wf-1", title="")
    thread.messages = [
        ChatMessage(
            role="user",
            content="Help me understand recent oil prices fluctuations and what drives them.",
        ),
    ]
    store.save_thread(thread)

    registry = ProviderRegistry()
    provider = FakeProvider("Oil Price Outlook")
    registry.register("openai", provider)

    updated = await autogenerate_thread_title(
        store,
        "wf-1",
        thread.id,
        providers=registry,
        chat_model="gpt-4o",
    )

    saved = store.get_thread("wf-1", thread.id)
    meta = store.get_thread_meta("wf-1", thread.id)
    assert updated is True
    assert saved is not None
    assert saved.title == "Oil Price Outlook"
    assert meta["title_source"] == "generated"
    assert meta["title_generation_started"] is False


@pytest.mark.asyncio
async def test_autogenerate_thread_title_uses_chat_manager_seam(tmp_path: Path) -> None:
    store = ChatStore(str(tmp_path))
    thread = store.create_thread("wf-1", title="")
    thread.messages = [
        ChatMessage(
            role="user",
            content="Help me understand recent oil prices fluctuations and what drives them.",
        ),
    ]
    store.save_thread(thread)

    registry = ProviderRegistry()
    registry.register("default", FakeProvider("unused"))
    registry.resolve = lambda model: (_ for _ in ()).throw(AssertionError("registry.resolve should not be used"))  # type: ignore[assignment]

    provider = FakeProvider("Runtime Oil Outlook")

    class _ChatManagerLike:
        def __init__(self, registry: ProviderRegistry) -> None:
            self.provider_registry = registry
            self.calls: list[dict[str, object]] = []

        def resolve_llm_provider(
            self,
            *,
            model: str,
            pii_session_key: str | None = None,
        ) -> FakeProvider:
            self.calls.append({
                "model": model,
                "pii_session_key": pii_session_key,
            })
            return provider

        def default_llm_model(self) -> str:
            return "runtime-model"

    chat_manager = _ChatManagerLike(registry)

    updated = await autogenerate_thread_title(
        store,
        "wf-1",
        thread.id,
        providers=registry,
        chat_model="gpt-4o",
        chat_manager=chat_manager,
    )

    saved = store.get_thread("wf-1", thread.id)
    meta = store.get_thread_meta("wf-1", thread.id)
    assert updated is True
    assert saved is not None
    assert saved.title == "Runtime Oil Outlook"
    assert meta["title_source"] == "generated"
    assert chat_manager.calls == [{"model": "gpt-4o", "pii_session_key": None}]


def test_apply_generated_title_does_not_override_manual_title(tmp_path: Path) -> None:
    store = ChatStore(str(tmp_path))
    thread = store.create_thread("wf-1", title="")
    thread.messages = [
        ChatMessage(role="user", content="Compare recent oil prices and explain the drivers."),
    ]
    store.save_thread(thread)

    mark_manual_title(store, "wf-1", thread.id, "My Manual Title")
    updated = apply_generated_title(store, "wf-1", thread.id, "Oil Market Drivers")
    saved = store.get_thread("wf-1", thread.id)

    assert updated is False
    assert saved is not None
    assert saved.title == "My Manual Title"
