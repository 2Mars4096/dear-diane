from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from dan.builder import workflow
from dan.server.capabilities.runs import handle_start_run
from dan.server.capability_registry import CapabilityContext
from dan.server.concierge.scheduler import ScheduleStore, handle_schedule_command
from dan.server.concierge.tier_executors import _apply_workflow_continuity_context
from dan.server.graph_store import GraphStore
from dan.server.workflow_guards import WorkflowContractError
from dan.server.workflow_identity import (
    build_workflow_context_pack,
    resolve_workflow_reference,
    workflow_resolution_context_from_session,
)
from dan.meta.workflow_contract import WorkflowBuildContractReport


def _sample_graph(*, display_name: str) -> dict:
    wf = workflow("sample")
    inputs = wf.input_node(
        "workflow_inputs",
        variables=[{"name": "topic", "type": "string"}],
    )
    writer = wf.code(
        "writer",
        code="result = {'text': topic}",
        input_ports=[{"name": "topic", "required": False}],
        output_ports=[{"name": "result"}],
    )
    wf.edge(inputs["topic"], writer["topic"])
    graph = wf.build().model_dump(mode="json")
    graph["metadata"]["name"] = display_name
    return graph


def _store_with_graphs(tmp_path, *graph_specs: tuple[str, str]) -> GraphStore:
    store = GraphStore(str(tmp_path / "graphs"))
    for graph_id, display_name in graph_specs:
        store.create_graph(graph_id, _sample_graph(display_name=display_name))
    return store


def _empty_graph(*, display_name: str) -> dict:
    return {
        "version": "dan_graph_v1",
        "metadata": {"name": display_name, "description": ""},
        "nodes": [],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
        "shared_context": [],
        "artifact_refs": [],
        "hyperedges": [],
    }


def _watchlist_graph(*, display_name: str, description: str) -> dict:
    wf = workflow("watchlist")
    watchlist = wf.input_node(
        "watchlist_path",
        description="Path to watchlist CSV file",
        variables=[{"name": "path", "type": "string"}],
    )
    reader = wf.code(
        "read_watchlist_csv",
        code="rows = []",
        input_ports=[{"name": "path", "required": False}],
        output_ports=[{"name": "rows"}],
    )
    processor = wf.code(
        "process_tickers",
        code="report = ''",
        input_ports=[{"name": "rows", "required": False}],
        output_ports=[{"name": "report"}],
    )
    writer = wf.code(
        "write_aggregate_report",
        code="saved = True",
        input_ports=[{"name": "report", "required": False}],
        output_ports=[{"name": "saved"}],
    )
    wf.edge(watchlist["path"], reader["path"])
    wf.edge(reader["rows"], processor["rows"])
    wf.edge(processor["report"], writer["report"])
    graph = wf.build().model_dump(mode="json")
    graph["metadata"]["name"] = display_name
    graph["metadata"]["description"] = description
    return graph


def test_resolve_workflow_reference_matches_exact_id(tmp_path) -> None:
    store = _store_with_graphs(tmp_path, ("wf-quarterly", "Quarterly Review"))

    resolved = resolve_workflow_reference(store, "wf-quarterly")

    assert resolved.resolved is True
    assert resolved.graph_id == "wf-quarterly"
    assert resolved.resolution_source == "explicit_id"
    assert resolved.revision


def test_resolve_workflow_reference_matches_exact_and_normalized_name(tmp_path) -> None:
    store = _store_with_graphs(tmp_path, ("wf-quarterly", "Quarterly Review"))

    by_name = resolve_workflow_reference(store, "Quarterly Review")
    by_normalized = resolve_workflow_reference(store, "quarterly-review")

    assert by_name.graph_id == "wf-quarterly"
    assert by_name.resolution_source == "exact_name"
    assert by_normalized.graph_id == "wf-quarterly"
    assert by_normalized.resolution_source == "normalized_name"


def test_resolve_workflow_reference_uses_current_workflow_context(tmp_path) -> None:
    store = _store_with_graphs(tmp_path, ("wf-quarterly", "Quarterly Review"))

    resolved = resolve_workflow_reference(
        store,
        "current",
        context=workflow_resolution_context_from_session(
            current_workflow_id="wf-quarterly",
        ),
    )

    assert resolved.graph_id == "wf-quarterly"
    assert resolved.resolution_source == "current_workflow"


def test_resolve_workflow_reference_uses_project_linked_fallback(tmp_path) -> None:
    store = _store_with_graphs(tmp_path, ("wf-quarterly", "Quarterly Review"))

    resolved = resolve_workflow_reference(
        store,
        "",
        context=workflow_resolution_context_from_session(
            current_workflow_id=None,
            project_workflow_ids=["wf-quarterly"],
        ),
    )

    assert resolved.graph_id == "wf-quarterly"
    assert resolved.resolution_source == "project_linked_workflow"


def test_resolve_workflow_reference_refuses_ambiguous_name(tmp_path) -> None:
    store = _store_with_graphs(
        tmp_path,
        ("wf-quarterly-a", "Quarterly Review"),
        ("wf-quarterly-b", "Quarterly Review"),
    )

    resolved = resolve_workflow_reference(store, "Quarterly Review")

    assert resolved.resolved is False
    assert resolved.status == "ambiguous"
    assert set(resolved.matched_graph_ids) == {"wf-quarterly-a", "wf-quarterly-b"}


def test_resolve_workflow_reference_marks_stale_revision(tmp_path) -> None:
    store = _store_with_graphs(tmp_path, ("wf-quarterly", "Quarterly Review"))

    resolved = resolve_workflow_reference(
        store,
        "wf-quarterly",
        context=workflow_resolution_context_from_session(
            expected_revision="rev-stale",
        ),
    )

    assert resolved.graph_id == "wf-quarterly"
    assert resolved.stale is True
    assert resolved.status == "stale"


def test_build_workflow_context_pack_keeps_identity_fields(tmp_path) -> None:
    store = _store_with_graphs(tmp_path, ("wf-quarterly", "Quarterly Review"))
    resolution = resolve_workflow_reference(store, "wf-quarterly")

    pack_json, pack = build_workflow_context_pack(
        graph_store=store,
        workflow_resolution=resolution,
    )

    assert '"workflow_id":"wf-quarterly"' in pack_json
    assert pack["identity"]["revision"] == resolution.revision
    assert pack["structure_summary"]["key_steps"] == ["workflow inputs", "writer"]
    assert pack["structure_summary"]["inferred_purpose"] == "Likely flow: workflow inputs -> writer."
    assert pack["required_inputs"] == ["topic"]


def test_build_workflow_context_pack_flags_stale_metadata_against_structure(tmp_path) -> None:
    store = GraphStore(str(tmp_path / "graphs"))
    store.create_graph(
        "wf-watchlist",
        _watchlist_graph(
            display_name="code_gen_validation",
            description="Generate Python code, validate syntax, and save",
        ),
    )
    resolution = resolve_workflow_reference(store, "wf-watchlist")

    _pack_json, pack = build_workflow_context_pack(
        graph_store=store,
        workflow_resolution=resolution,
    )

    assert pack["structure_summary"]["metadata_consistency"] == "low"
    assert "stale" in pack["structure_summary"]["metadata_note"].lower()
    assert pack["structure_summary"]["key_steps"][:3] == [
        "Path to watchlist CSV file",
        "read watchlist csv",
        "process tickers",
    ]


@pytest.mark.asyncio
async def test_start_run_handler_resolves_display_name(tmp_path) -> None:
    store = _store_with_graphs(tmp_path, ("wf-quarterly", "Quarterly Review"))
    started: list[str] = []

    async def _launch_run(graph, *, graph_id, inputs=None, run_id=None, session_id=None, run_policy=None, bus=None):
        started.append(graph_id)
        return SimpleNamespace(record=SimpleNamespace(run_id="run-1", status=SimpleNamespace(value="running")))

    result = await handle_start_run(
        {"workflow_id": "Quarterly Review"},
        CapabilityContext(
            workflow_id="_scratch",
            graph_store=store,
            run_manager=SimpleNamespace(launch_run=AsyncMock(side_effect=_launch_run)),
        ),
    )

    assert result.success is True
    assert started == ["wf-quarterly"]


@pytest.mark.asyncio
async def test_start_run_handler_rejects_non_run_ready_workflow(tmp_path) -> None:
    store = GraphStore(str(tmp_path / "graphs"))
    store.create_graph("wf-empty", _empty_graph(display_name="Empty Workflow"))
    report = WorkflowBuildContractReport(
        workflow_id="wf-empty",
        run_readiness_issues=["Workflow requires at least one runnable step."],
    )

    async def _launch_run(*args, **kwargs):
        raise WorkflowContractError(
            "Workflow `wf-empty` is not run-ready.",
            workflow_id="wf-empty",
            report=report,
            action="run",
            failure_mode="missing_runnable_steps",
        )

    result = await handle_start_run(
        {"workflow_id": "wf-empty"},
        CapabilityContext(
            workflow_id="_scratch",
            graph_store=store,
            run_manager=SimpleNamespace(launch_run=AsyncMock(side_effect=_launch_run)),
        ),
    )

    assert result.success is False
    assert result.error_type == "validation_failed"
    assert "not run-ready" in result.message.lower()


def test_schedule_workflow_command_resolves_name_and_records_revision(tmp_path) -> None:
    store = _store_with_graphs(tmp_path, ("wf-quarterly", "Quarterly Review"))
    schedule_store = ScheduleStore(path=str(tmp_path / "schedules.json"))
    schedule_store.load()

    result = handle_schedule_command(
        "/schedule workflow Quarterly Review daily at 9am",
        schedule_store,
        graph_store=store,
    )

    entry = schedule_store.list_all()[0]
    assert "Scheduled workflow" in result
    assert entry.workflow_id == "wf-quarterly"
    assert entry.workflow_revision
    assert entry.workflow_resolution_source == "exact_name"


def test_schedule_workflow_command_rejects_non_run_ready_workflow(tmp_path) -> None:
    store = GraphStore(str(tmp_path / "graphs"))
    store.create_graph("wf-empty", _empty_graph(display_name="Empty Workflow"))
    schedule_store = ScheduleStore(path=str(tmp_path / "schedules.json"))
    schedule_store.load()

    result = handle_schedule_command(
        "/schedule workflow wf-empty daily at 9am",
        schedule_store,
        graph_store=store,
    )

    assert "not run-ready" in result.lower()
    assert schedule_store.list_all() == []


def test_apply_workflow_continuity_context_injects_context_pack(tmp_path) -> None:
    store = _store_with_graphs(tmp_path, ("wf-quarterly", "Quarterly Review"))
    concierge = SimpleNamespace(
        chat_manager=SimpleNamespace(
            _graph_store=store,
            _chat_store=None,
            _capability_context=SimpleNamespace(run_manager=None, run_store=None),
        ),
        _schedule_store=None,
    )
    session = SimpleNamespace(
        triage=SimpleNamespace(
            route=SimpleNamespace(target="workflow", action_hints=["workflow_edit"]),
        ),
        context=SimpleNamespace(
            project=SimpleNamespace(linked_workflow_ids=["wf-quarterly"]),
        ),
    )
    chat_params = {
        "workflow_id": "wf-quarterly",
        "mode": "agent",
        "allow_mutation_tool": True,
        "required_action_hints": ["workflow_edit"],
        "client_graph_revision": None,
        "thread_id": None,
        "extra_system_instructions": "",
        "audit_metadata": {},
    }

    event = _apply_workflow_continuity_context(concierge, session, chat_params)

    assert event is None
    assert "Workflow Context Pack" in chat_params["extra_system_instructions"]
    assert chat_params["audit_metadata"]["workflow_resolution_id"] == "wf-quarterly"
    assert [item["stage"] for item in chat_params["audit_metadata"]["workflow_stage_timings"]] == [
        "wf_route",
        "wf_resolve",
        "wf_context",
    ]


def test_apply_workflow_continuity_context_refuses_missing_current_workflow(tmp_path) -> None:
    store = _store_with_graphs(tmp_path, ("wf-quarterly", "Quarterly Review"))
    concierge = SimpleNamespace(
        chat_manager=SimpleNamespace(
            _graph_store=store,
            _chat_store=None,
            _capability_context=SimpleNamespace(run_manager=None, run_store=None),
        ),
        _schedule_store=None,
    )
    session = SimpleNamespace(
        triage=SimpleNamespace(
            route=SimpleNamespace(target="workflow", action_hints=["workflow_edit"]),
        ),
        context=SimpleNamespace(
            project=SimpleNamespace(linked_workflow_ids=[]),
        ),
    )
    chat_params = {
        "workflow_id": "wf-missing",
        "mode": "agent",
        "allow_mutation_tool": True,
        "required_action_hints": ["workflow_edit"],
        "client_graph_revision": None,
        "thread_id": None,
        "extra_system_instructions": "",
        "audit_metadata": {},
    }

    event = _apply_workflow_continuity_context(concierge, session, chat_params)

    assert event is not None
    assert "can't inspect the current workflow" in event.content.lower()
    assert "saved workflow catalog" in event.content.lower()


def test_apply_workflow_continuity_context_allows_project_linked_metadata_gap() -> None:
    concierge = SimpleNamespace(
        chat_manager=SimpleNamespace(
            _graph_store=SimpleNamespace(
                list_graphs=lambda: [],
                get_graph=lambda graph_id: None,
            ),
            _chat_store=None,
            _capability_context=SimpleNamespace(run_manager=None, run_store=None),
        ),
        _schedule_store=None,
    )
    session = SimpleNamespace(
        triage=SimpleNamespace(
            route=SimpleNamespace(target="workflow", action_hints=["workflow_edit"]),
        ),
        context=SimpleNamespace(
            project=SimpleNamespace(linked_workflow_ids=["wf-current"]),
        ),
    )
    chat_params = {
        "workflow_id": "wf-current",
        "mode": "agent",
        "allow_mutation_tool": True,
        "required_action_hints": ["workflow_edit"],
        "client_graph_revision": None,
        "thread_id": None,
        "extra_system_instructions": "",
        "audit_metadata": {},
    }

    event = _apply_workflow_continuity_context(concierge, session, chat_params)

    assert event is None
    assert chat_params["workflow_id"] == "wf-current"
    assert chat_params["audit_metadata"]["workflow_resolution_id"] == "wf-current"
    assert chat_params["audit_metadata"]["workflow_resolution_status"] == (
        "project_linked_compatibility_fallback"
    )
    assert [item["stage"] for item in chat_params["audit_metadata"]["workflow_stage_timings"]] == [
        "wf_route",
        "wf_resolve",
        "wf_context",
    ]
