from __future__ import annotations

from dan.meta.intent_compiler import IntentCompiler
from dan.meta.workflow_contract import validate_workflow_build_contract
from dan.server.audit import ChatAuditRecord, ToolCallRecord
from dan.server.trace_workflow_distiller import (
    distill_workflow_trace_from_audit,
    prepare_trace_workflow_graph_for_promotion,
    suggest_trace_workflow_id,
    suggest_trace_workflow_name,
)


def _draft():
    record = ChatAuditRecord(
        surface_id="chat",
        turn_id="turn-distiller-1",
        workflow_id="seed-workflow",
        run_id="run-distiller-1",
        user_message="Research climate adaptation grants and write a markdown summary to /tmp/grants.md",
        tool_calls=[
            ToolCallRecord(
                tool_name="search_web",
                args={"query": "climate adaptation grants 2026"},
            ),
            ToolCallRecord(
                tool_name="write_file",
                args={
                    "path": "/tmp/grants.md",
                    "content": "# grants\n- item",
                },
                result_summary="saved",
            ),
        ],
    )
    draft = distill_workflow_trace_from_audit(record)
    assert draft is not None
    return draft


def test_distill_research_then_write_adds_prepare_output_stage():
    record = ChatAuditRecord(
        surface_id="chat",
        turn_id="turn-trace-1",
        user_message="Research DAN orchestration patterns and save a short summary to /tmp/summary.md",
        tool_calls=[
            ToolCallRecord(
                tool_name="search_web",
                args={"query": "DAN orchestration patterns"},
                result_summary="3 results",
            ),
            ToolCallRecord(
                tool_name="write_file",
                args={"path": "/tmp/summary.md"},
                result_summary="saved",
            ),
        ],
    )

    draft = distill_workflow_trace_from_audit(record)
    assert draft is not None
    assert draft.source_turn_id == "turn-trace-1"
    assert {param.name for param in draft.parameters} >= {"query", "output_path"}
    assert [stage.name for stage in draft.workflow_intent.stages] == [
        "research",
        "prepare_output",
        "write_output",
    ]

    graph = IntentCompiler().build_graph(draft.workflow_intent)
    report = validate_workflow_build_contract(graph.model_dump(mode="json"), apply_repairs=True)
    assert report.validated is True
    assert report.run_ready is True


def test_distill_nested_paths_preserves_multiple_parameter_refs():
    record = ChatAuditRecord(
        surface_id="chat",
        turn_id="turn-trace-2",
        user_message="Load a CSV, transform it with Python, then save the final report.",
        tool_calls=[
            ToolCallRecord(
                tool_name="run_python",
                args={
                    "job": {
                        "input_file": "./input/data.csv",
                        "output_file": "./build/intermediate.json",
                    }
                },
                result_summary="transformed",
            ),
            ToolCallRecord(
                tool_name="save_file",
                args={"path": "./reports/final.md"},
                result_summary="saved",
            ),
        ],
    )

    draft = distill_workflow_trace_from_audit(record)
    assert draft is not None
    assert [stage.name for stage in draft.workflow_intent.stages] == [
        "transform_data",
        "write_output",
    ]
    assert "prepare_output" not in [stage.name for stage in draft.workflow_intent.stages]

    transform_action = draft.action_trace[0]
    assert transform_action.generalized_tool_name == "code_execution"
    assert set(transform_action.parameter_refs) >= {"input_path", "output_path"}

    transform_stage = draft.workflow_intent.stages[0]
    assert transform_stage.config["code"]
    assert any(
        port["name"] == "input_path"
        for port in transform_stage.config["input_ports"]
    )
    assert any(
        port["name"] == "output_path"
        for port in transform_stage.config["input_ports"]
    )

    params = {param.name: param for param in draft.parameters}
    assert params["input_path"].source_keys == ["job.input_file"]
    assert params["output_path"].source_keys == ["job.output_file", "path"]
    assert params["input_path"].inferred_type == "path"
    assert params["output_path"].inferred_type == "path"

    graph = IntentCompiler().build_graph(draft.workflow_intent)
    report = validate_workflow_build_contract(graph.model_dump(mode="json"), apply_repairs=True)
    assert report.validated is True
    assert report.run_ready is True


def test_distill_returns_none_for_single_meaningful_action():
    record = ChatAuditRecord(
        surface_id="chat",
        turn_id="turn-trace-3",
        user_message="Open the file",
        tool_calls=[
            ToolCallRecord(
                tool_name="read_file",
                args={"path": "/tmp/input.txt"},
                result_summary="loaded",
            ),
        ],
    )

    assert distill_workflow_trace_from_audit(record) is None


def test_suggest_trace_workflow_identity_uses_stable_goal_words() -> None:
    draft = _draft()

    assert suggest_trace_workflow_id(draft) == (
        "distilled-research-climate-adaptation-grants-write-markdown"
    )
    assert suggest_trace_workflow_name(draft) == (
        "Research climate adaptation grants and write a markdown summary to {path}"
    )


def test_prepare_trace_workflow_graph_for_promotion_enriches_metadata_without_mutating_input() -> None:
    draft = _draft()
    graph_dict = {
        "version": "dan_graph_v1",
        "metadata": {
            "name": "raw-slug",
            "tags": ["existing"],
        },
        "nodes": [],
        "edges": [],
        "entry_points": [],
        "exit_points": [],
    }

    promoted = prepare_trace_workflow_graph_for_promotion(
        graph_dict,
        draft,
        workflow_id="distilled-research-climate-adaptation-grants-write-markdown",
    )

    assert graph_dict["metadata"]["name"] == "raw-slug"
    assert graph_dict["metadata"]["tags"] == ["existing"]
    assert promoted["metadata"]["name"] == (
        "Research climate adaptation grants and write a markdown summary to {path}"
    )
    assert promoted["metadata"]["tags"] == ["existing", "distilled", "trace-promoted"]
    assert "turn-distiller-1" in promoted["metadata"]["description"]
    assert "seed-workflow" in promoted["metadata"]["description"]
