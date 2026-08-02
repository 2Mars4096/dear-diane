"""Regression tests for simple workflow builds through chat."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import pytest

import dan.server.chat_manager as chat_manager_module
from dan.providers import CompletionResult
from dan.providers.registry import ProviderRegistry
from dan.server.chat_manager import (
    ChatCompleteEvent,
    ChatGraphCreatedEvent,
    ChatManager,
    ChatMutationEvent,
)
from dan.server.graph_store import GraphStore


EMPTY_GRAPH: dict[str, Any] = {
    "version": "dan_graph_v1",
    "metadata": {"name": "empty"},
    "nodes": [],
    "edges": [],
}

SIMPLE_BUILT_GRAPH: dict[str, Any] = {
    "version": "dan_graph_v1",
    "metadata": {"name": "Simple Writing Workflow"},
    "nodes": [
        {
            "id": "topic_input",
            "name": "Topic Input",
            "node_type": "input",
            "variables": [
                {
                    "name": "topic",
                    "type": "string",
                    "description": "Topic to write about",
                }
            ],
            "output_ports": [
                {
                    "name": "topic",
                    "json_schema": {"type": "string"},
                }
            ],
        },
        {
            "id": "writer",
            "name": "Writer",
            "node_type": "llm_operator",
            "model": "test-model",
            "prompt_template": "Write a short paragraph about {topic}.",
            "input_ports": [
                {
                    "name": "topic",
                    "json_schema": {"type": "string"},
                    "required": True,
                }
            ],
            "output_ports": [
                {
                    "name": "draft",
                    "json_schema": {"type": "string"},
                }
            ],
        },
    ],
    "edges": [
        {
            "id": "edge-topic-to-writer",
            "edge_type": "data",
            "source_node_id": "topic_input",
            "source_port": "topic",
            "target_node_id": "writer",
            "target_port": "topic",
        }
    ],
    "entry_points": ["topic_input"],
    "exit_points": ["writer"],
}


class _NeverUsedProvider:
    """Provider stub that should not be reached in the codegen fast path."""

    async def complete(self, **kwargs: Any) -> Any:
        raise AssertionError("provider.complete() should not run for empty-graph chat builds")

    async def stream(self, **kwargs: Any) -> Any:
        raise AssertionError("provider.stream() should not run for empty-graph chat builds")


class _SequenceProvider:
    supports_tool_calls = True
    supports_required_tool_choice = True

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
        if args:
            normalized = dict(kwargs)
            if len(args) >= 1:
                normalized.setdefault("messages", args[0])
            if len(args) >= 2:
                normalized.setdefault("model", args[1])
            kwargs = normalized
        self.requests.append(kwargs)
        if not self._responses:
            raise AssertionError("No more provider responses configured")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


async def _fake_build_messages(self, *args: Any, **kwargs: Any) -> list[dict[str, str]]:
    return [{"role": "user", "content": "hello"}]


async def _collect_events(gen: Any) -> list[Any]:
    events: list[Any] = []
    async for event in gen:
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_agent_mode_simple_chat_build_emits_graph_created_and_persists_graph(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_store = GraphStore(base_dir=str(tmp_path / "graphs"))
    graph_store.save_graph("wf-simple", copy.deepcopy(EMPTY_GRAPH))

    registry = ProviderRegistry()
    registry.register("default", _NeverUsedProvider())

    manager = ChatManager(registry, graph_store=graph_store)
    manager._chat_model = "test-model"

    seen_codegen_calls: list[dict[str, Any]] = []

    async def _fake_codegen(
        *,
        user_message: str,
        workflow_id: str,
        channel_id: str,
        effective_model: str | None = None,
    ) -> tuple[dict[str, Any], list[Any]]:
        seen_codegen_calls.append(
            {
                "user_message": user_message,
                "workflow_id": workflow_id,
                "channel_id": channel_id,
                "effective_model": effective_model,
            }
        )
        return copy.deepcopy(SIMPLE_BUILT_GRAPH), []

    monkeypatch.setattr(chat_manager_module, "_DAN_USE_CODEGEN_BUILD", "1")
    monkeypatch.setattr(manager, "_generate_workflow_from_intent", _fake_codegen)

    events = await _collect_events(
        manager.send_message_with_tools(
            workflow_id="wf-simple",
            message="Create a simple writing workflow",
            history=[],
            mode="agent",
            allow_mutation_tool=True,
        )
    )

    assert seen_codegen_calls == [
        {
            "user_message": "Create a simple writing workflow",
            "workflow_id": "wf-simple",
            "channel_id": "wf-simple",
            "effective_model": "test-model",
        }
    ]

    graph_created_events = [
        event for event in events if isinstance(event, ChatGraphCreatedEvent)
    ]
    assert len(graph_created_events) == 1
    assert graph_created_events[0].workflow_id == "wf-simple"
    assert graph_created_events[0].node_count == 2
    assert graph_created_events[0].edge_count == 1

    complete_events = [event for event in events if isinstance(event, ChatCompleteEvent)]
    assert len(complete_events) == 1
    assert "Workflow saved to current id `wf-simple`" in complete_events[0].content
    assert "Created with 2 nodes and 1 edges." in complete_events[0].content
    assert "Validated and run-ready." in complete_events[0].content
    assert complete_events[0].detected_mode == "agent"

    saved_graph = graph_store.get_graph("wf-simple")
    assert saved_graph is not None
    assert len(saved_graph["nodes"]) == 2
    assert len(saved_graph["edges"]) == 1
    assert saved_graph["metadata"]["name"] == "Simple Writing Workflow"


@pytest.mark.asyncio
async def test_agent_mode_simple_chat_build_emits_progress_ack_before_terminal_events(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_store = GraphStore(base_dir=str(tmp_path / "graphs"))
    graph_store.save_graph("wf-progress", copy.deepcopy(EMPTY_GRAPH))

    registry = ProviderRegistry()
    registry.register("default", _NeverUsedProvider())

    manager = ChatManager(registry, graph_store=graph_store)
    manager._chat_model = "test-model"

    async def _fake_codegen(
        *,
        user_message: str,
        workflow_id: str,
        channel_id: str,
        effective_model: str | None = None,
    ) -> tuple[dict[str, Any], list[Any]]:
        await asyncio.sleep(0.02)
        return copy.deepcopy(SIMPLE_BUILT_GRAPH), []

    monkeypatch.setattr(chat_manager_module, "_DAN_USE_CODEGEN_BUILD", "1")
    monkeypatch.setattr(
        chat_manager_module,
        "_WORKFLOW_GENERATION_PROGRESS_TIMEOUT_SECONDS",
        0.001,
    )
    monkeypatch.setattr(manager, "_generate_workflow_from_intent", _fake_codegen)

    events = await _collect_events(
        manager.send_message_with_tools(
            workflow_id="wf-progress",
            message="Create a simple writing workflow",
            history=[],
            mode="agent",
            allow_mutation_tool=True,
        )
    )

    progress_events = [
        event
        for event in events
        if isinstance(event, ChatCompleteEvent)
        and getattr(event, "detected_mode", None) == "progress_ack"
    ]
    assert progress_events

    graph_created_index = next(
        index for index, event in enumerate(events)
        if isinstance(event, ChatGraphCreatedEvent)
    )
    complete_index = next(
        index for index, event in enumerate(events)
        if isinstance(event, ChatCompleteEvent)
        and getattr(event, "detected_mode", None) != "progress_ack"
    )
    progress_index = next(
        index for index, event in enumerate(events)
        if isinstance(event, ChatCompleteEvent)
        and getattr(event, "detected_mode", None) == "progress_ack"
    )

    assert progress_index < graph_created_index
    assert progress_index < complete_index


@pytest.mark.asyncio
async def test_empty_graph_codegen_fallback_auto_applies_recovered_mutation_preview(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved_graphs: list[tuple[str, dict[str, Any]]] = []
    graph_store = GraphStore(base_dir=str(tmp_path / "graphs"))
    graph_store.save_graph("wf-fallback", copy.deepcopy(EMPTY_GRAPH))
    original_save_graph = graph_store.save_graph

    def _recording_save_graph(workflow_id: str, graph_dict: dict[str, Any]) -> dict[str, Any]:
        saved = original_save_graph(workflow_id, graph_dict)
        saved_graphs.append((workflow_id, saved))
        return saved

    graph_store.save_graph = _recording_save_graph  # type: ignore[method-assign]

    provider = _SequenceProvider(
        [
            CompletionResult(
                text="Recovered build preview.",
                tool_calls=[
                    {
                        "id": "call_mut",
                        "type": "function",
                        "function": {
                            "name": "plan_graph_mutations",
                            "arguments": (
                                '{"description":"Add an input node",'
                                '"operations":[{"op":"add_node","node_type":"input","name":"My Input"}]}'
                            ),
                        },
                    },
                ],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            ),
            CompletionResult(
                text="Recovered workflow is saved.",
                tool_calls=[],
                usage={"prompt_tokens": 8, "completion_tokens": 3},
            ),
        ]
    )
    registry = ProviderRegistry()
    registry.register("default", provider)

    manager = ChatManager(registry, graph_store=graph_store)
    manager._chat_model = "test-model"

    async def _failed_codegen(
        *,
        user_message: str,
        workflow_id: str,
        channel_id: str,
        effective_model: str | None = None,
    ) -> tuple[dict | None, list[Any]]:
        return None, []

    monkeypatch.setattr(chat_manager_module, "_DAN_USE_CODEGEN_BUILD", "1")
    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)
    monkeypatch.setattr(manager, "_generate_workflow_from_intent", _failed_codegen)
    monkeypatch.setattr(
        "dan.meta.workflow_contract.validate_workflow_build_contract",
        lambda graph_dict, workflow_id="", apply_repairs=True: type(
            "ContractReport",
            (),
            {
                "validated": True,
                "run_ready": True,
                "errors": [],
                "run_readiness_issues": [],
                "graph_dict": graph_dict,
            },
        )(),
    )

    events = await _collect_events(
        manager.send_message_with_tools(
            workflow_id="wf-fallback",
            message="Build me a workflow from this empty graph.",
            history=[],
            mode="agent",
            allow_mutation_tool=True,
        )
    )

    mutation_events = [event for event in events if isinstance(event, ChatMutationEvent)]
    assert len(mutation_events) == 1
    assert mutation_events[0].applied is True
    assert "ready to run" in mutation_events[0].content.lower()

    assert len(provider.requests) == 2, "Recovered build should continue with the auto-apply follow-up"
    assert saved_graphs, "Recovered empty-graph mutation should persist the workflow"
    assert saved_graphs[-1][0] == "wf-fallback"
    assert len(saved_graphs[-1][1]["nodes"]) == 1

    saved_graph = graph_store.get_graph("wf-fallback")
    assert saved_graph is not None
    assert len(saved_graph["nodes"]) == 1

    terminal_events = [
        event for event in events
        if isinstance(event, ChatCompleteEvent)
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]
    assert terminal_events


@pytest.mark.asyncio
async def test_followup_refinement_auto_applies_existing_graph_and_persists_reviewer_step(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_store = GraphStore(base_dir=str(tmp_path / "graphs"))
    graph_store.save_graph("wf-refine", copy.deepcopy(SIMPLE_BUILT_GRAPH))

    provider = _SequenceProvider(
        [
            CompletionResult(
                text="Adding a review step.",
                tool_calls=[
                    {
                        "id": "call_mut_review",
                        "type": "function",
                        "function": {
                            "name": "plan_graph_mutations",
                            "arguments": json.dumps(
                                {
                                    "description": "Add a reviewer after the writer step",
                                    "auto_apply": True,
                                    "operations": [
                                        {
                                            "op": "add_node",
                                            "id": "reviewer",
                                            "node_type": "llm_operator",
                                            "name": "Reviewer",
                                            "config": {
                                                "prompt_template": "Review and improve {input}.",
                                                "input_ports": [
                                                    {
                                                        "name": "input",
                                                        "json_schema": {"type": "string"},
                                                        "required": True,
                                                    }
                                                ],
                                                "output_ports": [
                                                    {
                                                        "name": "review",
                                                        "json_schema": {"type": "string"},
                                                    }
                                                ],
                                            },
                                        },
                                        {
                                            "op": "add_edge",
                                            "source_id": "writer",
                                            "source_port": "draft",
                                            "target_id": "reviewer",
                                            "target_port": "input",
                                        },
                                    ],
                                }
                            ),
                        },
                    }
                ],
                usage={"prompt_tokens": 12, "completion_tokens": 6},
            ),
            CompletionResult(
                text="Review step applied.",
                tool_calls=[],
                usage={"prompt_tokens": 8, "completion_tokens": 3},
            ),
        ]
    )
    registry = ProviderRegistry()
    registry.register("default", provider)

    manager = ChatManager(registry, graph_store=graph_store)
    manager._chat_model = "test-model"

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        manager.send_message_with_tools(
            workflow_id="wf-refine",
            message="Add a reviewer step after the writer and apply it.",
            history=[],
            mode="agent",
            allow_mutation_tool=True,
        )
    )

    mutation_events = [event for event in events if isinstance(event, ChatMutationEvent)]
    assert len(mutation_events) == 1
    assert mutation_events[0].applied is True
    assert "ready to run" in mutation_events[0].content.lower()

    assert len(provider.requests) == 2

    saved_graph = graph_store.get_graph("wf-refine")
    assert saved_graph is not None
    assert {node["id"] for node in saved_graph["nodes"]} == {
        "topic_input",
        "writer",
        "reviewer",
    }
    assert any(
        edge["source_node_id"] == "writer"
        and edge["source_port"] == "draft"
        and edge["target_node_id"] == "reviewer"
        and edge["target_port"] == "input"
        for edge in saved_graph["edges"]
    )
    assert saved_graph["exit_points"] == ["reviewer"]

    terminal_events = [
        event
        for event in events
        if isinstance(event, ChatCompleteEvent)
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]
    assert terminal_events
    assert "review step applied" in terminal_events[-1].content.lower()


@pytest.mark.asyncio
async def test_agent_mode_simple_chat_build_emits_progress_ack_before_terminal_events(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_store = GraphStore(base_dir=str(tmp_path / "graphs"))
    graph_store.save_graph("wf-simple", copy.deepcopy(EMPTY_GRAPH))

    registry = ProviderRegistry()
    registry.register("default", _NeverUsedProvider())

    manager = ChatManager(registry, graph_store=graph_store)
    manager._chat_model = "test-model"

    release_codegen = asyncio.Event()
    first_wait_for_call = True
    real_wait_for = chat_manager_module.asyncio.wait_for

    async def _fake_codegen(
        *,
        user_message: str,
        workflow_id: str,
        channel_id: str,
        effective_model: str | None = None,
    ) -> tuple[dict[str, Any], list[Any]]:
        await release_codegen.wait()
        return copy.deepcopy(SIMPLE_BUILT_GRAPH), []

    async def _fake_wait_for(awaitable: Any, timeout: float) -> Any:
        nonlocal first_wait_for_call
        if first_wait_for_call:
            first_wait_for_call = False
            raise asyncio.TimeoutError()
        release_codegen.set()
        return await real_wait_for(awaitable, timeout=0.01)

    monkeypatch.setattr(chat_manager_module.asyncio, "wait_for", _fake_wait_for)
    monkeypatch.setattr(chat_manager_module, "_DAN_USE_CODEGEN_BUILD", "1")
    monkeypatch.setattr(manager, "_generate_workflow_from_intent", _fake_codegen)

    events = await _collect_events(
        manager.send_message_with_tools(
            workflow_id="wf-simple",
            message="Create a simple writing workflow",
            history=[],
            mode="agent",
            allow_mutation_tool=True,
        )
    )

    progress_ack_indexes = [
        idx
        for idx, event in enumerate(events)
        if isinstance(event, ChatCompleteEvent)
        and getattr(event, "detected_mode", None) == "progress_ack"
    ]
    graph_created_indexes = [
        idx
        for idx, event in enumerate(events)
        if isinstance(event, ChatGraphCreatedEvent)
    ]
    terminal_complete_indexes = [
        idx
        for idx, event in enumerate(events)
        if isinstance(event, ChatCompleteEvent)
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]

    assert progress_ack_indexes
    assert graph_created_indexes
    assert terminal_complete_indexes
    assert progress_ack_indexes[0] < graph_created_indexes[0] < terminal_complete_indexes[0]
