from __future__ import annotations

from types import SimpleNamespace

from dan.server.agent_runtime.workflow_outcomes import (
    build_codegen_saved_message,
    build_persisted_workflow_reply,
    build_structural_macro_message,
    prepare_validated_workflow_save,
)
from dan.server.chat.events import ChatGenerationSummaryEvent


def test_prepare_validated_workflow_save_returns_ready_to_save_graph() -> None:
    report = SimpleNamespace(
        validated=True,
        run_ready=True,
        graph_dict={"nodes": [{"id": "saved"}]},
    )
    outcome = prepare_validated_workflow_save(
        {"nodes": [{"id": "n1"}]},
        validate_graph=lambda graph: report,
        collect_contract_errors=lambda *_args, **_kwargs: [],
        blocked_default_message="blocked",
    )

    assert outcome.status == "ready_to_save"
    assert outcome.graph_to_save == {"nodes": [{"id": "saved"}]}
    assert outcome.validation_errors == ()
    assert outcome.contract_report is report


def test_prepare_validated_workflow_save_reports_validation_error() -> None:
    outcome = prepare_validated_workflow_save(
        {"nodes": []},
        validate_graph=lambda _graph: (_ for _ in ()).throw(RuntimeError("validation blew up")),
        collect_contract_errors=lambda *_args, **_kwargs: [],
        blocked_default_message="blocked",
    )

    assert outcome.status == "validation_error"
    assert outcome.graph_to_save is None
    assert outcome.validation_errors == ("validation blew up",)


def test_prepare_validated_workflow_save_reports_blocked_contract_errors() -> None:
    report = SimpleNamespace(
        validated=True,
        run_ready=False,
        graph_dict={"nodes": []},
    )
    outcome = prepare_validated_workflow_save(
        {"nodes": []},
        validate_graph=lambda graph: report,
        collect_contract_errors=lambda report, default_message: [
            "Missing entry point",
            default_message,
        ],
        blocked_default_message="blocked",
    )

    assert outcome.status == "blocked"
    assert outcome.graph_to_save is None
    assert outcome.validation_errors == ("Missing entry point", "blocked")
    assert outcome.contract_report is report


def test_build_codegen_saved_message_formats_preview_and_path_suffix() -> None:
    message = build_codegen_saved_message(
        workflow_id="wf-1",
        workflow_name="Generated Workflow",
        node_count=3,
        edge_count=2,
        node_preview_items=["Input", "Writer"],
        generation_summary_event=ChatGenerationSummaryEvent(
            path_taken="codegen->repair",
            wall_clock_ms=12_500,
            fallback_chain=["codegen", "repair"],
            build_summary="Validated and run-ready. Auto-fixed 2 mechanical issues.",
        ),
    )

    assert "Workflow saved to current id `wf-1`" in message
    assert 'with name "Generated Workflow"' in message
    assert "Created with 3 nodes and 2 edges." in message
    assert "Nodes: `Input`, `Writer`, +1 more." in message
    assert "Auto-fixed 2 mechanical issues." in message
    assert "Built via codegen->repair in 12.5s." in message


def test_build_structural_macro_message_formats_single_and_multi_results() -> None:
    single = build_structural_macro_message(
        SimpleNamespace(
            macro_name="add_input",
            result=SimpleNamespace(edges_added=1, nodes_added=["n2", "n3"]),
        ),
        build_summary="Validated and run-ready. Auto-fixed 1 mechanical issue.",
    )
    multi = build_structural_macro_message(
        SimpleNamespace(
            macro_names=["add_input", "add_router"],
            results=[
                SimpleNamespace(edges_added=1, nodes_added=["n2"]),
                SimpleNamespace(edges_added=2, nodes_added=["n3", "n4"]),
            ],
        ),
        build_summary="Validated and run-ready.",
    )

    assert single == (
        "Applied `add_input`: 1 edges added, 2 nodes added. "
        "Validated and run-ready. Auto-fixed 1 mechanical issue."
    )
    assert multi == (
        "Applied 2 macros: `add_input` (1 nodes, 1 edges), "
        "`add_router` (2 nodes, 2 edges). Validated and run-ready."
    )


def test_build_persisted_workflow_reply_builds_created_and_complete_events() -> None:
    reply = build_persisted_workflow_reply(
        workflow_id="wf-1",
        saved_graph={
            "version": "dan_graph_v1",
            "metadata": {"name": "Generated Workflow"},
            "nodes": [
                {
                    "id": "n1",
                    "node_type": "input",
                    "name": "Input",
                    "input_ports": [],
                    "output_ports": [],
                    "position": {"x": 0, "y": 0},
                    "ui": {},
                    "metadata": {},
                }
            ],
            "edges": [],
            "sub_graphs": {},
            "entry_points": ["n1"],
            "exit_points": ["n1"],
            "shared_context": [],
            "artifact_refs": [],
        },
        message_id="msg-1",
        assistant_message="Workflow saved.",
        context_window=128_000,
        detected_mode="agent",
    )

    assert reply.assistant_message == "Workflow saved."
    assert reply.graph_revision
    assert reply.graph_created_event.workflow_id == "wf-1"
    assert reply.graph_created_event.node_count == 1
    assert reply.complete_event.message_id == "msg-1"
    assert reply.complete_event.content == "Workflow saved."
    assert reply.complete_event.context_window == 128_000
    assert reply.complete_event.detected_mode == "agent"
    assert reply.complete_event.graph_revision == reply.graph_revision
