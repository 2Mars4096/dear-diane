"""Prompt-design regressions for the unified chat prompt."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import dan.server.mention_resolver as mention_resolver_module
from dan.models.graph import Graph
from dan.providers import StreamChunk
from dan.providers.registry import ProviderRegistry
from dan.server.chat_manager import (
    ChatManager,
    GraphSummary,
    _workflow_generation_contract_override_from_surface_context,
    generate_capability_reference,
)
from dan.workflow_generation_guidance import workflow_generation_contract_override


def _empty_summary() -> GraphSummary:
    return GraphSummary(
        workflow_id="wf-1",
        name="Test Workflow",
        description="",
        node_count=0,
        edge_count=0,
        nodes=[],
        edges=[],
        entry_points=[],
        exit_points=[],
        revision="rev-1",
    )


def _make_manager(provider: Any | None = None) -> ChatManager:
    registry = ProviderRegistry()
    if provider is not None:
        registry.register("default", provider)
    return ChatManager(registry, graph_store=SimpleNamespace())


class CaptureStreamProvider:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] | None = None

    async def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("provider.complete() not expected in this test")

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        self.messages = kwargs.get("messages") or []
        yield StreamChunk(
            delta="What should the workflow produce?",
            accumulated="What should the workflow produce?",
            done=True,
            usage={},
        )


@pytest.mark.asyncio
async def test_build_messages_injects_research_hint_only_for_research_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _make_manager()

    async def fake_preflight(_user_message: str) -> str:
        return "Today is Friday, 2026-03-13."

    monkeypatch.setattr(manager, "_run_preflight_hooks", fake_preflight)

    common_kwargs = {
        "summary": _empty_summary(),
        "history": [],
        "workflow_id": "wf-1",
        "graph_dict": {"nodes": [], "edges": []},
    }
    normal_messages = await manager._build_messages(
        user_message="Read /tmp/notes.txt and summarize the key points.",
        **common_kwargs,
    )
    research_messages = await manager._build_messages(
        user_message="Write a literature review on supply chain resilience and cite recent sources.",
        **common_kwargs,
    )

    normal_system = normal_messages[0]["content"]
    research_system = research_messages[0]["content"]

    assert "## Research & Report Behavior" not in normal_system
    assert "## Research & Report Behavior" in research_system


@pytest.mark.asyncio
async def test_build_messages_populates_workflow_prompt_metadata_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _make_manager()

    async def fake_preflight(_user_message: str) -> str:
        return "Today is Friday, 2026-03-13."

    async def _false_hint(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(manager, "_run_preflight_hooks", fake_preflight)
    monkeypatch.setattr(manager, "_should_inject_research_prompt_hint", _false_hint)
    monkeypatch.setattr(manager, "_should_inject_exploration_prompt_hint", _false_hint)

    prompt_metadata: dict[str, Any] = {}
    messages = await manager._build_messages(
        summary=_empty_summary(),
        user_message="Build the workflow and test it.",
        history=[],
        workflow_id="wf-1",
        graph_dict={"nodes": [], "edges": []},
        allow_mutation_tool=True,
        required_action_hints=["workflow_edit"],
        prompt_metadata_sink=prompt_metadata,
    )

    assert "workflow_generation_contract" in prompt_metadata["prompt_module_ids"]
    assert prompt_metadata["workflow_guidance_injected"] is True
    assert prompt_metadata["workflow_guidance_surface"] == "build"
    assert prompt_metadata["agent_profile"] == "build"
    assert prompt_metadata["requested_mode"] == "agent"
    assert prompt_metadata["normalized_mode"] == "agent"
    assert prompt_metadata["active_prompt_key"] == "prompts/runtime.unified_system"
    assert prompt_metadata["supports_load_prompt_detail"] == (
        "load_prompt_detail" in messages[0]["content"]
    )
    assert (
        messages[0]["content"].count(
            "`plan_graph_mutations` is the workflow-building/editing tool for the current workflow."
        )
        == 1
    )


@pytest.mark.asyncio
async def test_build_messages_omits_workflow_contract_when_override_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _make_manager()

    async def fake_preflight(_user_message: str) -> str:
        return "Today is Friday, 2026-03-13."

    async def _false_hint(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(manager, "_run_preflight_hooks", fake_preflight)
    monkeypatch.setattr(manager, "_should_inject_research_prompt_hint", _false_hint)
    monkeypatch.setattr(manager, "_should_inject_exploration_prompt_hint", _false_hint)
    monkeypatch.setenv("DAN_WORKFLOW_GENERATION_CONTRACT_ENABLED", "1")

    prompt_metadata: dict[str, Any] = {}
    with workflow_generation_contract_override(False):
        messages = await manager._build_messages(
            summary=_empty_summary(),
            user_message="Build the workflow and test it.",
            history=[],
            workflow_id="wf-1",
            graph_dict={"nodes": [], "edges": []},
            allow_mutation_tool=True,
            required_action_hints=["workflow_edit"],
            prompt_metadata_sink=prompt_metadata,
        )

    assert "workflow_generation_contract" not in prompt_metadata["prompt_module_ids"]
    assert prompt_metadata["workflow_guidance_injected"] is False
    assert prompt_metadata["workflow_guidance_surface"] == ""
    assert "Workflow Generation Contract" not in messages[0]["content"]
    assert "replace_body_graph" not in messages[0]["content"]


def test_generate_capability_reference_is_compact_and_has_no_stale_placeholders() -> None:
    tool_text = generate_capability_reference()

    assert "{mcp_block}" not in tool_text
    assert len(tool_text) < 2500


@pytest.mark.asyncio
async def test_clarify_intent_prompt_is_domain_agnostic() -> None:
    provider = CaptureStreamProvider()
    manager = _make_manager(provider)

    events = [event async for event in manager.clarify_intent("wf-1", "Build it", [])]

    assert events
    assert provider.messages is not None
    clarify_prompt = provider.messages[0]["content"]
    assert "paper writing" not in clarify_prompt.lower()
    assert "pdfs" not in clarify_prompt.lower()
    assert "main goal" in clarify_prompt.lower()
    assert "smallest runnable workflow" in clarify_prompt.lower()
    assert "do not emit a plan" in clarify_prompt.lower()


@pytest.mark.asyncio
async def test_send_message_persists_workflow_prompt_metadata_in_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CaptureStreamProvider()
    registry = ProviderRegistry()
    registry.register("default", provider)
    manager = ChatManager(
        registry,
        graph_store=SimpleNamespace(
            get_graph=lambda _workflow_id: Graph().model_dump(mode="json"),
        ),
    )

    async def fake_preflight(_user_message: str) -> str:
        return "Today is Friday, 2026-03-13."

    async def _false_hint(*_args: Any, **_kwargs: Any) -> bool:
        return False

    captured: dict[str, Any] = {}

    def fake_persist(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(manager, "_run_preflight_hooks", fake_preflight)
    monkeypatch.setattr(manager, "_should_inject_research_prompt_hint", _false_hint)
    monkeypatch.setattr(manager, "_should_inject_exploration_prompt_hint", _false_hint)
    monkeypatch.setattr("dan.server.chat_manager._try_persist_audit", fake_persist)
    monkeypatch.setenv("DAN_WORKFLOW_GENERATION_CONTRACT_ENABLED", "1")

    events = [
        event async for event in manager.send_message(
            "wf-1",
            "Build the workflow and test it.",
            [],
            mode="build",
            record_summary=False,
        )
    ]

    assert events
    assert "workflow_generation_contract" in captured["audit_metadata"]["prompt_module_ids"]
    assert captured["audit_metadata"]["workflow_guidance_injected"] is True
    assert captured["audit_metadata"]["workflow_guidance_surface"] == "build"


@pytest.mark.asyncio
async def test_send_message_persists_interrupted_text_only_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CaptureStreamProvider()
    registry = ProviderRegistry()
    registry.register("default", provider)
    manager = ChatManager(
        registry,
        graph_store=SimpleNamespace(
            get_graph=lambda _workflow_id: Graph().model_dump(mode="json"),
        ),
    )

    async def fake_preflight(_user_message: str) -> str:
        return "Today is Friday, 2026-03-13."

    async def _false_hint(*_args: Any, **_kwargs: Any) -> bool:
        return False

    captured: dict[str, Any] = {}

    def fake_persist(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(manager, "_run_preflight_hooks", fake_preflight)
    monkeypatch.setattr(manager, "_should_inject_research_prompt_hint", _false_hint)
    monkeypatch.setattr(manager, "_should_inject_exploration_prompt_hint", _false_hint)
    monkeypatch.setattr("dan.server.chat_manager._try_persist_audit", fake_persist)
    monkeypatch.setenv("DAN_WORKFLOW_GENERATION_CONTRACT_ENABLED", "1")

    cancel_event = asyncio.Event()
    cancel_event.set()
    events = [
        event async for event in manager.send_message(
            "wf-1",
            "Build the workflow and test it.",
            [],
            mode="build",
            cancel_event=cancel_event,
            record_summary=False,
        )
    ]

    assert events
    assert captured["error"] == "interrupted"
    assert "workflow_generation_contract" in captured["audit_metadata"]["prompt_module_ids"]


@pytest.mark.asyncio
async def test_surface_context_can_enable_or_disable_workflow_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _make_manager()

    async def fake_preflight(_user_message: str) -> str:
        return "Today is Friday, 2026-03-13."

    async def _false_hint(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(manager, "_run_preflight_hooks", fake_preflight)
    monkeypatch.setattr(manager, "_should_inject_research_prompt_hint", _false_hint)
    monkeypatch.setattr(manager, "_should_inject_exploration_prompt_hint", _false_hint)

    monkeypatch.setenv("DAN_WORKFLOW_GENERATION_CONTRACT_ENABLED", "0")
    prompt_metadata: dict[str, Any] = {}
    with workflow_generation_contract_override(
        _workflow_generation_contract_override_from_surface_context(
            {"workflow_generation_contract_enabled": True},
        )
    ):
        enabled_messages = await manager._build_messages(
            summary=_empty_summary(),
            user_message="Build a workflow that summarizes files.",
            history=[],
            workflow_id="wf-1",
            graph_dict={"nodes": [], "edges": []},
            allow_mutation_tool=True,
            required_action_hints=["workflow_edit"],
            prompt_metadata_sink=prompt_metadata,
        )
    enabled_prompt = enabled_messages[0]["content"]
    assert "Workflow Generation Contract" in enabled_prompt
    assert "workflow_generation_contract" in prompt_metadata["prompt_module_ids"]
    assert prompt_metadata["workflow_guidance_injected"] is True

    monkeypatch.setenv("DAN_WORKFLOW_GENERATION_CONTRACT_ENABLED", "1")
    prompt_metadata = {}
    with workflow_generation_contract_override(
        _workflow_generation_contract_override_from_surface_context(
            {"workflow_generation_contract_enabled": False},
        )
    ):
        disabled_messages = await manager._build_messages(
            summary=_empty_summary(),
            user_message="Build a workflow that summarizes files.",
            history=[],
            workflow_id="wf-1",
            graph_dict={"nodes": [], "edges": []},
            allow_mutation_tool=True,
            required_action_hints=["workflow_edit"],
            prompt_metadata_sink=prompt_metadata,
        )
    disabled_prompt = disabled_messages[0]["content"]
    assert "Workflow Generation Contract" not in disabled_prompt
    assert "workflow_generation_contract" not in prompt_metadata["prompt_module_ids"]
    assert prompt_metadata["workflow_guidance_injected"] is False


@pytest.mark.asyncio
async def test_build_messages_mention_path_reuses_runtime_history_context_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _make_manager()
    manager._mention_resolver = SimpleNamespace(
        resolve_all=lambda *_args, **_kwargs: [
            SimpleNamespace(
                type="file",
                identifier="README.md",
                resolved_content="README context",
                token_count=12,
            )
        ]
    )

    async def fake_preflight(_user_message: str) -> str:
        return "Today is Friday, 2026-03-13."

    async def _false_hint(*_args: Any, **_kwargs: Any) -> bool:
        return False

    captured: dict[str, Any] = {}

    def fake_pack_context(
        system_content: str,
        mention_blocks: list[Any],
        history: list[dict[str, str]],
        user_message: str,
        context_window: int,
        max_ratio: float = 0.8,
        model: str = "",
        recent_count: int = 10,
    ) -> list[dict[str, str]]:
        captured["system_content"] = system_content
        captured["mention_blocks"] = mention_blocks
        captured["history"] = history
        captured["user_message"] = user_message
        captured["context_window"] = context_window
        captured["max_ratio"] = max_ratio
        captured["model"] = model
        captured["recent_count"] = recent_count
        return [{"role": "system", "content": "packed"}]

    monkeypatch.setattr(manager, "_run_preflight_hooks", fake_preflight)
    monkeypatch.setattr(manager, "_should_inject_research_prompt_hint", _false_hint)
    monkeypatch.setattr(manager, "_should_inject_exploration_prompt_hint", _false_hint)
    monkeypatch.setattr(
        manager,
        "_compose_recent_context_message",
        lambda _user_message: "Recent context",
    )
    monkeypatch.setattr(mention_resolver_module, "pack_context", fake_pack_context)

    messages = await manager._build_messages(
        summary=_empty_summary(),
        user_message="Use @file:README.md",
        history=[
            {"role": "assistant", "content": "prior answer"},
            {"role": "tool", "content": "ignore"},
            {"role": "user", "content": "prior question"},
        ],
        mentions=["README.md"],
        workflow_id="wf-1",
        graph_dict={"nodes": [], "edges": []},
    )

    assert messages == [{"role": "system", "content": "packed"}]
    assert captured["history"] == [
        {"role": "assistant", "content": "Recent context"},
        {"role": "assistant", "content": "prior answer"},
        {"role": "user", "content": "prior question"},
    ]
