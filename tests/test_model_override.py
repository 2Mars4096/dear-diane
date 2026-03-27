"""Tests for ChatManager model_override parameter (Plan 31-26 Task 3)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from dan.providers import CompletionResult, StreamChunk
from dan.providers.registry import ProviderRegistry
from dan.server.chat_manager import (
    ChatCompleteEvent,
    ChatGenerationSummaryEvent,
    ChatGraphCreatedEvent,
    ChatManager,
    ChatTokenEvent,
)


MINIMAL_GRAPH: dict[str, Any] = {
    "version": "dan_graph_v1",
    "metadata": {"name": "test"},
    "nodes": [
        {
            "id": "n1",
            "name": "Node 1",
            "node_type": "llm_operator",
            "model": "test-model",
            "prompt_template": "Hello",
        },
    ],
    "edges": [],
}


EMPTY_GRAPH: dict[str, Any] = {
    "version": "dan_graph_v1",
    "metadata": {"name": "empty"},
    "nodes": [],
    "edges": [],
}


class _RecordingProvider:
    """Provider that records ``model`` kwarg from every call."""

    def __init__(self) -> None:
        self.recorded_models: list[str] = []

    async def stream(self, *args: Any, **kwargs: Any):
        model = kwargs.get("model", "")
        if not model and len(args) >= 2:
            model = str(args[1] or "")
        self.recorded_models.append(model)
        text = "Hello from the model."
        yield StreamChunk(
            delta=text,
            accumulated=text,
            done=True,
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    async def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
        model = kwargs.get("model", "")
        if not model and len(args) >= 2:
            model = str(args[1] or "")
        self.recorded_models.append(model)
        return CompletionResult(
            text="Done.",
            tool_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )


class _NonToolProvider(_RecordingProvider):
    supports_tool_calls = False


def _make_manager(
    provider: _RecordingProvider,
    default_model: str = "default-model",
    graph: dict[str, Any] | None = None,
) -> ChatManager:
    registry = ProviderRegistry()
    registry.register("default", provider)
    graph_payload = dict(graph or MINIMAL_GRAPH)
    graph_store = SimpleNamespace(
        get_graph=lambda wf_id: dict(graph_payload),
    )
    mgr = ChatManager(registry, graph_store=graph_store)
    mgr._chat_model = default_model
    return mgr


async def _collect_events(gen: Any) -> list[Any]:
    events = []
    async for event in gen:
        events.append(event)
    return events


# ------------------------------------------------------------------
# send_message tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_uses_override_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_message with model_override should pass the override to provider.stream()."""
    provider = _RecordingProvider()
    mgr = _make_manager(provider, default_model="default-model")

    # Stub _build_messages to avoid needing full system prompt infra
    async def _fake_build_messages(self, *args, **kwargs):
        return [{"role": "user", "content": "hi"}]

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message(
            workflow_id="wf1",
            message="hello",
            history=[],
            model_override="override-model-42",
        )
    )

    assert any(isinstance(e, ChatCompleteEvent) for e in events)
    assert provider.recorded_models == ["override-model-42"]


@pytest.mark.asyncio
async def test_send_message_passes_override_model_to_build_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_message should build prompts using the override model context."""
    provider = _RecordingProvider()
    mgr = _make_manager(provider, default_model="default-model")
    seen_models: list[str | None] = []

    async def _fake_build_messages(self, *args, **kwargs):
        seen_models.append(kwargs.get("model"))
        return [{"role": "user", "content": "hi"}]

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    _ = await _collect_events(
        mgr.send_message(
            workflow_id="wf1",
            message="hello",
            history=[],
            model_override="override-model-42",
        )
    )

    assert seen_models
    assert all(model == "override-model-42" for model in seen_models)


@pytest.mark.asyncio
async def test_send_message_default_model_when_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_message without model_override should use the default _chat_model."""
    provider = _RecordingProvider()
    mgr = _make_manager(provider, default_model="my-default-llm")

    async def _fake_build_messages(self, *args, **kwargs):
        return [{"role": "user", "content": "hi"}]

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message(
            workflow_id="wf1",
            message="hello",
            history=[],
        )
    )

    assert any(isinstance(e, ChatCompleteEvent) for e in events)
    assert provider.recorded_models == ["my-default-llm"]


# ------------------------------------------------------------------
# send_message_with_tools tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_with_tools_uses_override_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_message_with_tools with model_override should pass it to provider.complete()."""
    provider = _RecordingProvider()
    mgr = _make_manager(provider, default_model="default-model")

    async def _fake_build_messages(self, *args, **kwargs):
        return [{"role": "user", "content": "hi"}]

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="hello",
            history=[],
            model_override="gpt-4o-override",
        )
    )

    complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
    assert len(complete_events) >= 1
    assert "gpt-4o-override" in provider.recorded_models
    assert "default-model" not in provider.recorded_models


@pytest.mark.asyncio
async def test_send_message_with_tools_passes_override_model_to_build_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_message_with_tools should build prompts using override model context."""
    provider = _RecordingProvider()
    mgr = _make_manager(provider, default_model="default-model")
    seen_models: list[str | None] = []

    async def _fake_build_messages(self, *args, **kwargs):
        seen_models.append(kwargs.get("model"))
        return [{"role": "user", "content": "hi"}]

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    _ = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="hello",
            history=[],
            model_override="gpt-4o-override",
        )
    )

    assert seen_models
    assert all(model == "gpt-4o-override" for model in seen_models)


@pytest.mark.asyncio
async def test_send_message_with_tools_uses_model_gateway_tool_completion_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingProvider()
    mgr = _make_manager(provider, default_model="default-model")
    gateway_calls: list[dict[str, Any]] = []

    class _Gateway:
        def __init__(self) -> None:
            self.registry = SimpleNamespace(get=lambda name: provider if name == "default" else None)

        def resolve(self, model: str) -> Any:
            return _NonToolProvider()

        async def complete(self, **kwargs: Any) -> CompletionResult:
            gateway_calls.append(kwargs)
            return CompletionResult(
                text="Done via gateway.",
                tool_calls=[],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )

    async def _fake_build_messages(self, *args, **kwargs):
        return [{"role": "user", "content": "hi"}]

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)
    mgr.model_gateway = _Gateway()

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="hello",
            history=[],
            model_override="gpt-4o-override",
        )
    )

    complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
    assert complete_events
    assert provider.recorded_models == []
    assert gateway_calls


@pytest.mark.asyncio
async def test_send_message_with_tools_synthesizes_model_gateway_from_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingProvider()
    mgr = _make_manager(provider, default_model="default-model")
    resolve_provider_calls: list[str | None] = []

    class _GatewayAwareProvider(_RecordingProvider):
        supports_tool_calls = True

    tool_provider = _GatewayAwareProvider()
    mgr._providers.register("default", tool_provider)

    async def _fake_build_messages(self, *args, **kwargs):
        return [{"role": "user", "content": "hi"}]

    original_resolve_provider = mgr._resolve_provider

    def _recording_resolve_provider(
        *,
        pii_session_key: str | None = None,
        model: str | None = None,
    ) -> Any:
        resolve_provider_calls.append(model)
        return original_resolve_provider(
            pii_session_key=pii_session_key,
            model=model,
        )

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)
    monkeypatch.setattr(mgr, "_resolve_provider", _recording_resolve_provider)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="hello",
            history=[],
            model_override="gpt-4o-override",
        )
    )

    complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
    assert complete_events
    assert resolve_provider_calls == []
    assert getattr(mgr, "model_gateway", None) is not None
    assert tool_provider.recorded_models == ["gpt-4o-override"]


@pytest.mark.asyncio
async def test_send_message_with_tools_passes_override_model_to_codegen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty-graph codegen path should inherit the request's override model."""
    provider = _RecordingProvider()
    mgr = _make_manager(provider, default_model="default-model", graph=EMPTY_GRAPH)
    seen_codegen_models: list[str | None] = []

    async def _fake_codegen(self, *args, **kwargs):
        seen_codegen_models.append(kwargs.get("effective_model"))
        return None, []

    async def _fake_build_messages(self, *args, **kwargs):
        return [{"role": "user", "content": "hi"}]

    monkeypatch.setattr(ChatManager, "_generate_workflow_from_intent", _fake_codegen)
    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="hello",
            history=[],
            model_override="gpt-4o-override",
        )
    )

    assert seen_codegen_models == ["gpt-4o-override"]
    assert any(isinstance(e, ChatCompleteEvent) for e in events)


@pytest.mark.asyncio
async def test_send_message_with_tools_codegen_success_saves_graph_and_emits_created_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingProvider()
    registry = ProviderRegistry()
    registry.register("default", provider)
    saved_calls: list[tuple[str, dict[str, Any]]] = []
    generated_graph = {
        "version": "dan_graph_v1",
        "metadata": {"name": "generated"},
        "nodes": [
            {
                "id": "n1",
                "name": "Draft Plan",
                "node_type": "llm_operator",
                "model": "test-model",
                "prompt_template": "Write the draft",
            }
        ],
        "edges": [],
    }
    graph_store = SimpleNamespace(
        get_graph=lambda workflow_id: dict(EMPTY_GRAPH),
        save_graph=lambda workflow_id, graph: (
            saved_calls.append((workflow_id, dict(graph))) or dict(graph)
        ),
    )
    mgr = ChatManager(registry, graph_store=graph_store)
    mgr._chat_model = "default-model"

    async def _fake_codegen(self, *args, **kwargs):
        return generated_graph, [
            ChatGenerationSummaryEvent(path_taken="codegen", wall_clock_ms=420),
        ]

    monkeypatch.setattr(ChatManager, "_generate_workflow_from_intent", _fake_codegen)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf-build",
            message="build me a workflow",
            history=[],
        )
    )

    assert saved_calls == [("wf-build", generated_graph)]
    created = [e for e in events if isinstance(e, ChatGraphCreatedEvent)]
    assert len(created) == 1
    assert created[0].workflow_id == "wf-build"
    assert created[0].node_count == 1
    assert created[0].edge_count == 0

    complete = [e for e in events if isinstance(e, ChatCompleteEvent)]
    assert len(complete) == 1
    assert "Workflow saved to current id `wf-build`" in complete[0].content
    assert "Created with 1 nodes and 0 edges." in complete[0].content
    assert "Validated and run-ready." in complete[0].content
    assert any(isinstance(e, ChatGenerationSummaryEvent) for e in events)


@pytest.mark.asyncio
async def test_send_message_with_tools_does_not_persist_when_generation_returns_no_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingProvider()
    registry = ProviderRegistry()
    registry.register("default", provider)
    saved_calls: list[tuple[str, dict[str, Any]]] = []
    graph_store = SimpleNamespace(
        get_graph=lambda workflow_id: dict(EMPTY_GRAPH),
        save_graph=lambda workflow_id, graph: (
            saved_calls.append((workflow_id, dict(graph))) or dict(graph)
        ),
    )
    mgr = ChatManager(registry, graph_store=graph_store)
    mgr._chat_model = "default-model"

    async def _failed_generation(self, *args, **kwargs):
        return None, [
            ChatGenerationSummaryEvent(
                path_taken="structured_generation",
                wall_clock_ms=420,
                failure_mode="structured_execution_smoke",
            )
        ]

    monkeypatch.setattr(ChatManager, "_generate_workflow_from_intent", _failed_generation)

    _ = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf-build",
            message="build me a workflow",
            history=[],
        )
    )

    assert saved_calls == []


@pytest.mark.asyncio
async def test_send_message_with_tools_default_model_when_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_message_with_tools without model_override should use _chat_model."""
    provider = _RecordingProvider()
    mgr = _make_manager(provider, default_model="claude-sonnet-default")

    async def _fake_build_messages(self, *args, **kwargs):
        return [{"role": "user", "content": "hi"}]

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="hello",
            history=[],
        )
    )

    complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
    assert len(complete_events) >= 1
    assert "claude-sonnet-default" in provider.recorded_models


@pytest.mark.asyncio
async def test_send_message_with_tools_falls_back_to_default_provider_for_non_tool_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tool-using chat should avoid providers that do not implement tool-calling."""
    default_provider = _RecordingProvider()
    google_provider = _NonToolProvider()

    registry = ProviderRegistry()
    registry.register("default", default_provider)
    registry.register("google", google_provider)
    graph_store = SimpleNamespace(get_graph=lambda wf_id: dict(MINIMAL_GRAPH))
    mgr = ChatManager(registry, graph_store=graph_store)
    mgr._chat_model = "default-model"

    async def _fake_build_messages(self, *args, **kwargs):
        return [{"role": "user", "content": "hi"}]

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="hello",
            history=[],
            model_override="gemini-3.1-pro-preview",
        )
    )

    assert any(isinstance(e, ChatCompleteEvent) for e in events)
    assert default_provider.recorded_models == ["gemini-3.1-pro-preview"]
    assert google_provider.recorded_models == []


# ------------------------------------------------------------------
# clarify_intent tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clarify_intent_uses_override_model() -> None:
    """clarify_intent should route through the override model when provided."""
    provider = _RecordingProvider()
    mgr = _make_manager(provider, default_model="default-model")

    events = await _collect_events(
        mgr.clarify_intent(
            workflow_id="wf1",
            message="help me build this",
            history=[],
            model_override="override-model-42",
        )
    )

    assert any(isinstance(e, ChatCompleteEvent) for e in events)
    assert provider.recorded_models == ["override-model-42"]


# ------------------------------------------------------------------
# _resolve_provider model routing
# ------------------------------------------------------------------


def test_resolve_provider_routes_by_model() -> None:
    """_resolve_provider(model=...) should resolve provider for the given model."""
    prov_a = SimpleNamespace()
    prov_b = SimpleNamespace()
    registry = ProviderRegistry()
    registry.register("default", prov_a)
    registry.register("openai", prov_b)
    registry.set_model_override("gpt-4o", "openai")

    graph_store = SimpleNamespace(get_graph=lambda wf_id: None)
    mgr = ChatManager(registry, graph_store=graph_store)
    mgr._chat_model = "claude-sonnet-4-6"

    resolved_default = mgr._resolve_provider(pii_session_key=None)
    resolved_override = mgr._resolve_provider(pii_session_key=None, model="gpt-4o")

    assert resolved_default is prov_a
    assert resolved_override is prov_b


def test_resolve_provider_falls_back_to_default_model() -> None:
    """_resolve_provider without model arg should use self._chat_model."""
    prov = SimpleNamespace()
    registry = ProviderRegistry()
    registry.register("default", prov)

    graph_store = SimpleNamespace(get_graph=lambda wf_id: None)
    mgr = ChatManager(registry, graph_store=graph_store)
    mgr._chat_model = "claude-sonnet-4-6"

    resolved = mgr._resolve_provider(pii_session_key=None)
    assert resolved is prov
