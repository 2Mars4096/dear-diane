from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

import dan.meta.graph_quality as graph_quality_module
import dan.meta.intent_compiler as intent_compiler_module
import dan.meta.planner as planner_module
import dan.server.agent_runtime.workflow_generation as workflow_generation_module
from dan.providers import CompletionResult
from dan.providers.registry import ProviderRegistry
from dan.server.chat_manager import ChatManager


class _SequenceProvider:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)

    async def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
        item = self._responses.pop(0)
        return item


def _make_manager(responses: list[Any]) -> ChatManager:
    registry = ProviderRegistry()
    registry.register("default", _SequenceProvider(responses))
    manager = ChatManager(registry, graph_store=SimpleNamespace())
    manager._chat_model = "default"
    return manager


def _validation_success_for_graph(graph_dict: dict[str, Any]) -> Any:
    return SimpleNamespace(
        success=True,
        run_ready=True,
        errors=[],
        graph=SimpleNamespace(model_dump=lambda mode="json": graph_dict),
        contract_report=None,
    )


def _intent_response(payload: dict) -> CompletionResult:
    return CompletionResult(
        text="",
        tool_calls=[
            {
                "function": {
                    "name": "emit_workflow_intent",
                    "arguments": json.dumps(payload),
                }
            }
        ],
    )


@pytest.mark.asyncio
async def test_structured_generation_runtime_uses_structured_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_STRUCTURED_GENERATION", "enabled")
    monkeypatch.setattr(planner_module, "validate_codegen_output", _validation_success_for_graph)
    monkeypatch.setattr(graph_quality_module, "is_acceptable_simple_graph", lambda _graph, _prompt: True)

    manager = _make_manager(
        [
            _intent_response(
                {
                    "goal": "Build a weekly notes digest workflow",
                    "global_inputs": ["notes_path"],
                    "global_outputs": ["digest"],
                    "stages": [
                        {
                            "name": "read_notes",
                            "description": "Read the notes folder",
                            "stage_type": "tool_call",
                            "inputs": ["notes_path"],
                            "outputs": ["notes"],
                            "config": {"tool_id": "file_read", "chapter_label": "ingest"},
                        },
                        {
                            "name": "summarize",
                            "description": "Summarize the notes into a digest",
                            "stage_type": "transform",
                            "inputs": ["notes"],
                            "outputs": ["digest"],
                            "config": {"chapter_label": "report"},
                        },
                    ],
                }
            )
        ]
    )

    graph, events = await manager._generate_workflow_from_intent(
        "Build a weekly notes digest workflow",
        "wf-structured",
        "ch-structured",
    )

    assert graph is not None
    node_ids = [node["id"] for node in graph["nodes"]]
    assert node_ids[0] == "workflow_inputs"
    assert node_ids[1:] == ["read_notes", "summarize"]
    assert graph["metadata"]["structured_generation"]["schedule"]["trigger"] == "weekly"
    summary_event = next(event for event in events if getattr(event, "path_taken", None))
    assert summary_event.path_taken == "structured_generation"


@pytest.mark.asyncio
async def test_structured_generation_runtime_runs_execution_smoke_when_candidate_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_STRUCTURED_GENERATION", "enabled")
    monkeypatch.setattr(planner_module, "validate_codegen_output", _validation_success_for_graph)
    monkeypatch.setattr(graph_quality_module, "is_acceptable_simple_graph", lambda _graph, _prompt: True)

    manager = _make_manager(
        [
            _intent_response(
                {
                    "goal": "Build a local CSV ingest workflow",
                    "global_inputs": ["csv_path"],
                    "global_outputs": ["records"],
                    "stages": [
                        {
                            "name": "read_records",
                            "description": "Read the CSV data file",
                            "stage_type": "tool_call",
                            "inputs": ["csv_path"],
                            "outputs": ["records"],
                            "config": {
                                "chapter_label": "ingest",
                                "tool_id": "csv_read",
                            },
                        }
                    ],
                }
            )
        ]
    )

    smoke_calls: list[dict[str, Any]] = []

    async def _smoke(
        graph_dict: dict[str, Any],
        *,
        workflow_id: str,
        inputs: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        smoke_calls.append(
            {
                "workflow_id": workflow_id,
                "inputs": inputs,
                "timeout_seconds": timeout_seconds,
                "node_ids": [node["id"] for node in graph_dict["nodes"]],
            }
        )
        return {"success": True, "run_id": "run-smoke-123"}

    monkeypatch.setattr(manager, "_run_candidate_execution_smoke", _smoke)

    graph, events = await manager._generate_workflow_from_intent(
        "Build a local CSV ingest workflow",
        "wf-structured-smoke",
        "ch-structured-smoke",
    )

    assert graph is not None
    assert len(smoke_calls) == 1
    assert smoke_calls[0]["workflow_id"] == "wf-structured-smoke"
    assert smoke_calls[0]["timeout_seconds"] == 20.0
    assert smoke_calls[0]["node_ids"] == ["workflow_inputs", "read_records"]
    assert smoke_calls[0]["inputs"].keys() == {"csv_path"}
    assert str(smoke_calls[0]["inputs"]["csv_path"]).endswith(".csv")
    assert any(
        "Structured execution smoke passed" in str(getattr(event, "content", "") or "")
        for event in events
    )


@pytest.mark.asyncio
async def test_structured_generation_runtime_rejects_candidate_when_execution_smoke_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_STRUCTURED_GENERATION", "enabled")
    monkeypatch.setenv("DAN_STRUCTURED_EXECUTION_SMOKE", "required")
    monkeypatch.setattr(planner_module, "validate_codegen_output", _validation_success_for_graph)
    monkeypatch.setattr(graph_quality_module, "is_acceptable_simple_graph", lambda _graph, _prompt: True)

    manager = _make_manager(
        [
            _intent_response(
                {
                    "goal": "Build a local CSV ingest workflow",
                    "global_inputs": ["csv_path"],
                    "global_outputs": ["records"],
                    "stages": [
                        {
                            "name": "read_records",
                            "description": "Read the CSV data file",
                            "stage_type": "tool_call",
                            "inputs": ["csv_path"],
                            "outputs": ["records"],
                            "config": {
                                "chapter_label": "ingest",
                                "tool_id": "csv_read",
                            },
                        }
                    ],
                }
            )
        ]
    )

    async def _smoke(
        graph_dict: dict[str, Any],
        *,
        workflow_id: str,
        inputs: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        del graph_dict, workflow_id, inputs, timeout_seconds
        return {"success": False, "errors": ["smoke failed immediately"]}

    async def _no_codegen(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return SimpleNamespace(
            builder_code="",
            codegen_retries=0,
            terminal_failure="no_output",
            terminal_message="",
        )

    monkeypatch.setattr(manager, "_run_candidate_execution_smoke", _smoke)
    monkeypatch.setattr(workflow_generation_module, "request_builder_code", _no_codegen)
    monkeypatch.setattr(
        intent_compiler_module.CoverageChecker,
        "check",
        lambda self, intent: SimpleNamespace(
            fully_covered=False,
            recommendation=None,
            constituent_patterns=None,
        ),
    )

    graph, events = await manager._generate_workflow_from_intent(
        "Build a local CSV ingest workflow",
        "wf-structured-smoke-fail",
        "ch-structured-smoke-fail",
    )

    assert graph is None
    failure_events = [
        event
        for event in events
        if getattr(event, "failure_mode", None) == "structured_execution_smoke"
    ]
    assert failure_events
    assert "smoke failed immediately" in failure_events[-1].errors[0]


@pytest.mark.asyncio
async def test_structured_generation_runtime_skips_code_candidates_in_auto_smoke_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_STRUCTURED_GENERATION", "enabled")
    monkeypatch.setattr(planner_module, "validate_codegen_output", _validation_success_for_graph)
    monkeypatch.setattr(graph_quality_module, "is_acceptable_simple_graph", lambda _graph, _prompt: True)

    manager = _make_manager(
        [
            _intent_response(
                {
                    "goal": "Build a local normalization workflow",
                    "global_inputs": ["records"],
                    "global_outputs": ["normalized_records"],
                    "stages": [
                        {
                            "name": "normalize_records",
                            "description": "Normalize the incoming records locally",
                            "stage_type": "code_execution",
                            "inputs": ["records"],
                            "outputs": ["normalized_records"],
                            "config": {
                                "chapter_label": "transform",
                                "code": "result = {'normalized_records': records}",
                            },
                        }
                    ],
                }
            )
        ]
    )

    async def _should_not_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        raise AssertionError("Code-only structured candidates should not execute smoke in auto mode.")

    monkeypatch.setattr(manager, "_run_candidate_execution_smoke", _should_not_run)

    graph, events = await manager._generate_workflow_from_intent(
        "Build a local normalization workflow",
        "wf-structured-code-auto",
        "ch-structured-code-auto",
    )

    assert graph is not None
    assert any(
        "contains generated code operators" in str(getattr(event, "content", "") or "")
        for event in events
    )


@pytest.mark.asyncio
async def test_structured_generation_runtime_skips_optional_smoke_when_run_manager_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_STRUCTURED_GENERATION", "enabled")
    monkeypatch.setattr(planner_module, "validate_codegen_output", _validation_success_for_graph)
    monkeypatch.setattr(graph_quality_module, "is_acceptable_simple_graph", lambda _graph, _prompt: True)

    manager = _make_manager(
        [
            _intent_response(
                {
                    "goal": "Build a local CSV ingest workflow",
                    "global_inputs": ["csv_path"],
                    "global_outputs": ["records"],
                    "stages": [
                        {
                            "name": "read_records",
                            "description": "Read the CSV data file",
                            "stage_type": "tool_call",
                            "inputs": ["csv_path"],
                            "outputs": ["records"],
                            "config": {
                                "chapter_label": "ingest",
                                "tool_id": "csv_read",
                            },
                        }
                    ],
                }
            )
        ]
    )

    graph, events = await manager._generate_workflow_from_intent(
        "Build a local CSV ingest workflow",
        "wf-structured-no-run-manager",
        "ch-structured-no-run-manager",
    )

    assert graph is not None
    assert any(
        "RunManager is unavailable for structured execution smoke" in str(getattr(event, "content", "") or "")
        for event in events
    )


@pytest.mark.asyncio
async def test_structured_generation_runtime_rejects_code_candidates_when_required_smoke_is_unsafe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_STRUCTURED_GENERATION", "enabled")
    monkeypatch.setenv("DAN_STRUCTURED_EXECUTION_SMOKE", "required")
    monkeypatch.setattr(planner_module, "validate_codegen_output", _validation_success_for_graph)
    monkeypatch.setattr(graph_quality_module, "is_acceptable_simple_graph", lambda _graph, _prompt: True)

    manager = _make_manager(
        [
            _intent_response(
                {
                    "goal": "Build a local normalization workflow",
                    "global_inputs": ["records"],
                    "global_outputs": ["normalized_records"],
                    "stages": [
                        {
                            "name": "normalize_records",
                            "description": "Normalize the incoming records locally",
                            "stage_type": "code_execution",
                            "inputs": ["records"],
                            "outputs": ["normalized_records"],
                            "config": {
                                "chapter_label": "transform",
                                "code": "result = {'normalized_records': records}",
                            },
                        }
                    ],
                }
            )
        ]
    )

    async def _no_codegen(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return SimpleNamespace(
            builder_code="",
            codegen_retries=0,
            terminal_failure="no_output",
            terminal_message="",
        )

    monkeypatch.setattr(workflow_generation_module, "request_builder_code", _no_codegen)
    monkeypatch.setattr(
        intent_compiler_module.CoverageChecker,
        "check",
        lambda self, intent: SimpleNamespace(
            fully_covered=False,
            recommendation=None,
            constituent_patterns=None,
        ),
    )

    graph, events = await manager._generate_workflow_from_intent(
        "Build a local normalization workflow",
        "wf-structured-code-required",
        "ch-structured-code-required",
    )

    assert graph is None
    failure_events = [
        event
        for event in events
        if getattr(event, "failure_mode", None) == "structured_execution_smoke"
    ]
    assert failure_events
    assert "generated code operators" in failure_events[-1].errors[0]


@pytest.mark.asyncio
async def test_structured_generation_runtime_keeps_candidate_detached_from_graph_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_STRUCTURED_GENERATION", "enabled")
    monkeypatch.setattr(planner_module, "validate_codegen_output", _validation_success_for_graph)
    monkeypatch.setattr(graph_quality_module, "is_acceptable_simple_graph", lambda _graph, _prompt: True)

    registry = ProviderRegistry()
    registry.register(
        "default",
        _SequenceProvider(
            [
                _intent_response(
                    {
                        "goal": "Build a detached candidate workflow",
                        "global_inputs": ["notes_path"],
                        "global_outputs": ["digest"],
                        "stages": [
                            {
                                "name": "read_notes",
                                "description": "Read the notes file",
                                "stage_type": "tool_call",
                                "inputs": ["notes_path"],
                                "outputs": ["notes"],
                                "config": {"tool_id": "file_read", "chapter_label": "ingest"},
                            },
                            {
                                "name": "summarize",
                                "description": "Summarize the notes into a digest",
                                "stage_type": "transform",
                                "inputs": ["notes"],
                                "outputs": ["digest"],
                                "config": {"chapter_label": "report"},
                            },
                        ],
                    }
                )
            ]
        ),
    )

    def _unexpected_save(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Structured generation should not persist the graph during candidate build.")

    manager = ChatManager(
        registry,
        graph_store=SimpleNamespace(
            save_graph=_unexpected_save,
            get_graph=lambda *_args, **_kwargs: None,
        ),
    )
    manager._chat_model = "default"

    graph, _events = await manager._generate_workflow_from_intent(
        "Build a detached candidate workflow",
        "wf-structured-detached",
        "ch-structured-detached",
    )

    assert graph is not None


@pytest.mark.asyncio
async def test_candidate_execution_smoke_uses_detached_graph_id() -> None:
    manager = _make_manager([])

    class _RunManager:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self._tasks: dict[str, Any] = {}

        async def start_run(
            self,
            graph: Any,
            *,
            graph_id: str,
            inputs: dict[str, Any],
            goal_context: dict[str, Any],
            run_policy: dict[str, Any],
        ) -> Any:
            self.calls.append(
                {
                    "graph": graph,
                    "graph_id": graph_id,
                    "inputs": inputs,
                    "goal_context": goal_context,
                    "run_policy": run_policy,
                }
            )
            record = SimpleNamespace(
                run_id="run-detached-smoke",
                status=SimpleNamespace(value="completed"),
                result=SimpleNamespace(success=True, outputs={}),
                snapshot=lambda: {},
            )

            async def _done() -> None:
                return None

            self._tasks[record.run_id] = asyncio.create_task(_done())
            return record

    run_manager = _RunManager()
    manager._capability_context = SimpleNamespace(run_manager=run_manager)

    result = await manager._run_candidate_execution_smoke(
        {
            "version": "dan_graph_v1",
            "metadata": {
                "name": "candidate",
                "structured_generation": {"candidate_workspace_id": "cws-123"},
            },
            "nodes": [],
            "edges": [],
            "sub_graphs": {},
            "entry_points": [],
            "exit_points": [],
        },
        workflow_id="wf-real",
        inputs={},
        timeout_seconds=1.0,
    )

    assert result["success"] is True
    assert len(run_manager.calls) == 1
    assert run_manager.calls[0]["graph_id"] == "wf-real::candidate-smoke::cws-123"
    assert run_manager.calls[0]["goal_context"]["workflow_id"] == "wf-real"


@pytest.mark.asyncio
async def test_structured_generation_runtime_uses_structured_path_in_canary_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_STRUCTURED_GENERATION", "canary")
    monkeypatch.setattr(planner_module, "validate_codegen_output", _validation_success_for_graph)
    monkeypatch.setattr(graph_quality_module, "is_acceptable_simple_graph", lambda _graph, _prompt: True)

    manager = _make_manager(
        [
            _intent_response(
                {
                    "goal": "Build a weekly notes digest workflow",
                    "global_inputs": ["notes_path"],
                    "global_outputs": ["digest"],
                    "stages": [
                        {
                            "name": "read_notes",
                            "description": "Read the notes folder",
                            "stage_type": "tool_call",
                            "inputs": ["notes_path"],
                            "outputs": ["notes"],
                            "config": {"tool_id": "file_read", "chapter_label": "ingest"},
                        },
                        {
                            "name": "summarize",
                            "description": "Summarize the notes into a digest",
                            "stage_type": "transform",
                            "inputs": ["notes"],
                            "outputs": ["digest"],
                            "config": {"chapter_label": "report"},
                        },
                    ],
                }
            )
        ]
    )

    graph, events = await manager._generate_workflow_from_intent(
        "Build a weekly notes digest workflow",
        "wf-structured-canary",
        "ch-structured-canary",
    )

    assert graph is not None
    summary_event = next(event for event in events if getattr(event, "path_taken", None))
    assert summary_event.path_taken == "structured_generation"
