from __future__ import annotations

from typing import Any

import pytest

from dan.agent_runtime.mutation_preview import (
    build_auto_apply_followup_messages,
    build_mutation_failure_payload,
    build_mutation_repair_messages,
    build_stale_replan_messages,
    compile_mutation_preview,
    format_mutation_preview_content,
    prepare_mutation_auto_apply,
    resolve_mutation_auto_apply_requested,
    workflow_contract_errors,
)
from dan.chat_events import GraphSummary
from dan.graph_mutator import MutationFailureClass, MutationResult, OperationError


def _empty_graph() -> dict[str, Any]:
    return {
        "version": "dan_graph_v1",
        "metadata": {"name": "test", "description": ""},
        "nodes": [],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
        "hyperedges": [],
        "shared_context": [],
        "artifact_refs": [],
    }


def _summary() -> GraphSummary:
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


def test_compile_mutation_preview_compiles_and_dry_runs_successfully() -> None:
    preview = compile_mutation_preview(
        mutation_payload={
            "description": "Add an input node",
            "operations": [
                {
                    "op": "add_node",
                    "id": "input_1",
                    "node_type": "input",
                    "name": "Input",
                }
            ],
        },
        graph_snapshot=_empty_graph(),
        base_revision="rev-1",
        is_empty_graph=True,
        normalize_mutation_ops=lambda graph, ops, is_empty_graph: (
            list(ops),
            ["Filled missing defaults."],
        ),
    )

    assert preview.plan is not None
    assert preview.plan.base_graph_revision == "rev-1"
    assert preview.plan_payload["mechanical_repairs"] == ["Filled missing defaults."]
    assert preview.dry_result.success is True
    assert preview.dry_result.new_graph is not None
    assert [item["stage"] for item in preview.dry_result.stage_timings] == [
        "wf_mutate_compile",
        "wf_dryrun",
    ]
    assert any(
        node["id"] == "input_1"
        for node in preview.dry_result.new_graph["nodes"]
    )


def test_compile_mutation_preview_returns_failure_result_when_normalization_raises() -> None:
    def _raise_normalization(
        _graph: dict[str, Any],
        _ops: list[dict[str, Any]],
        _is_empty: bool,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        raise ValueError("bad ops")

    preview = compile_mutation_preview(
        mutation_payload={
            "description": "Broken plan",
            "operations": [{"node_type": "input"}],
        },
        graph_snapshot=_empty_graph(),
        base_revision="rev-1",
        is_empty_graph=True,
        normalize_mutation_ops=_raise_normalization,
    )

    assert preview.plan is None
    assert preview.plan_payload["description"] == "Broken plan"
    assert preview.dry_result.success is False
    assert preview.dry_result.errors[0].message == "ValueError: bad ops"


def test_compile_mutation_preview_converts_dry_run_exception_to_failure_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ExplodingGraphMutator:
        def dry_run(self, _graph: dict[str, Any], _plan: Any, current_revision: str) -> MutationResult:
            raise RuntimeError(f"dry-run exploded at {current_revision}")

    monkeypatch.setattr(
        "dan.agent_runtime.mutation_preview.GraphMutator",
        _ExplodingGraphMutator,
    )

    preview = compile_mutation_preview(
        mutation_payload={
            "description": "Add an input node",
            "operations": [
                {
                    "op": "add_node",
                    "id": "input_1",
                    "node_type": "input",
                    "name": "Input",
                }
            ],
        },
        graph_snapshot=_empty_graph(),
        base_revision="rev-1",
        is_empty_graph=True,
        normalize_mutation_ops=lambda graph, ops, is_empty_graph: (list(ops), []),
    )

    assert preview.plan is not None
    assert preview.dry_result.success is False
    assert preview.dry_result.errors[0].message == "RuntimeError: dry-run exploded at rev-1"


def test_build_mutation_repair_messages_includes_contract_summary_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "dan.agent_runtime.mutation_preview.workflow_generation_contract_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "dan.agent_runtime.mutation_preview.render_workflow_generation_contract",
        lambda mode, tools_available=False: "Workflow Generation Contract",
    )

    messages = build_mutation_repair_messages(
        user_message="Build the workflow.",
        workflow_summary=_summary(),
        current_mutation={"description": "Add node", "operations": [{"node_type": "input"}]},
        current_plan_payload={},
        current_dry_result=MutationResult(
            success=False,
            errors=[
                OperationError(
                    op_index=0,
                    op_type="add_node",
                    message="Missing op discriminator",
                    stage="compilation",
                    failure_class=MutationFailureClass.COMPILATION,
                )
            ],
            diagnostics=["filled missing id"],
            stale_plan=True,
        ),
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "repair dan workflow mutation plans" in messages[0]["content"].lower()
    assert "Workflow Generation Contract" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "Current workflow summary" in messages[1]["content"]
    assert "Current mutation proposal" in messages[1]["content"]
    assert "Compilation or validation failures" in messages[1]["content"]
    assert "Missing op discriminator" in messages[1]["content"]
    assert '"stale_plan": true' in messages[1]["content"]
    assert '"failure_class": "compilation"' in messages[1]["content"]
    assert '"stage": "compilation"' in messages[1]["content"]


def test_build_mutation_failure_payload_trims_and_preserves_primary_failure() -> None:
    payload = build_mutation_failure_payload(
        MutationResult(
            success=False,
            errors=[
                OperationError(
                    op_index=2,
                    op_type="add_edge",
                    message="Source node 'input' has no output port 'response'",
                    stage="apply",
                    failure_class=MutationFailureClass.STALE_PORT_ALIAS,
                    context={
                        "source_id": "input",
                        "source_port": "response",
                        "available_ports": ["result", "body", "text"],
                    },
                )
            ],
            diagnostics=["A" * 500],
        )
    )

    assert payload["primary_failure"]["failure_class"] == "stale_port_alias"
    assert payload["primary_failure"]["stage"] == "apply"
    assert payload["errors"][0]["context"]["source_id"] == "input"
    assert payload["diagnostics"][0].endswith("...")


def test_build_stale_replan_messages_appends_default_prompt() -> None:
    messages = build_stale_replan_messages(
        [{"role": "system", "content": "base"}],
    )

    assert messages[:-1] == [{"role": "system", "content": "base"}]
    assert messages[-1]["role"] == "user"
    assert "The graph has changed since your last plan." in messages[-1]["content"]


def test_build_auto_apply_followup_messages_uses_tool_message_when_tool_call_present() -> None:
    messages = build_auto_apply_followup_messages(
        messages=[{"role": "system", "content": "base"}],
        assistant_text="Applying now.",
        mutation_tool_call={
            "id": "call_mut",
            "type": "function",
            "function": {"name": "plan_graph_mutations", "arguments": "{}"},
        },
        tool_call_id="fallback_id",
        apply_result_text='{"success": true}',
    )

    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["id"] == "call_mut"
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": "call_mut",
        "content": '{"success": true}',
    }


def test_build_auto_apply_followup_messages_uses_user_message_without_tool_call() -> None:
    messages = build_auto_apply_followup_messages(
        messages=[{"role": "system", "content": "base"}],
        assistant_text="Applied.",
        mutation_tool_call=None,
        tool_call_id="fallback_id",
        apply_result_text='{"success": true}',
    )

    assert messages[1] == {"role": "assistant", "content": "Applied."}
    assert messages[2]["role"] == "user"
    assert "applied automatically" in messages[2]["content"]


def test_format_mutation_preview_content_reflects_applied_and_proposed_states() -> None:
    proposed = format_mutation_preview_content(
        description="Add an input node",
        dry_result=MutationResult(success=True),
        is_empty_graph=True,
        applied=False,
    )
    applied = format_mutation_preview_content(
        description="Add an input node",
        dry_result=MutationResult(success=True),
        is_empty_graph=True,
        applied=True,
    )

    assert "Prepared a workflow build preview" in proposed
    assert "proposed, not applied yet" in proposed
    assert "Built and applied the workflow" in applied
    assert "ready to run" in applied


def test_workflow_contract_errors_prefers_reported_issues_then_falls_back() -> None:
    report = type(
        "Report",
        (),
        {
            "errors": [type("Issue", (), {"message": "Missing output"})()],
            "run_readiness_issues": ["Missing output", "No entry point"],
        },
    )()

    assert workflow_contract_errors(
        report,
        default_message="fallback",
    ) == ["Missing output", "No entry point"]
    assert workflow_contract_errors(
        type("EmptyReport", (), {"errors": [], "run_readiness_issues": []})(),
        default_message="fallback",
    ) == ["fallback"]


def test_prepare_mutation_auto_apply_returns_ready_to_save_graph() -> None:
    report = type(
        "ContractReport",
        (),
        {"validated": True, "run_ready": True, "graph_dict": {"nodes": ["saved"]}},
    )()
    outcome = prepare_mutation_auto_apply(
        auto_apply_requested=True,
        dry_result=MutationResult(success=True, new_graph={"nodes": ["preview"]}),
        graph_snapshot=_empty_graph(),
        plan=object(),
        current_revision="rev-1",
        apply_mutation=lambda _graph, _plan, _revision: type(
            "ApplyResult",
            (),
            {"success": True, "new_graph": {"nodes": ["applied"]}},
        )(),
        validate_graph=lambda _graph: report,
    )

    assert outcome.status == "ready_to_save"
    assert outcome.graph_to_save == {"nodes": ["saved"]}
    assert outcome.contract_report is report


def test_prepare_mutation_auto_apply_reports_validation_exception() -> None:
    outcome = prepare_mutation_auto_apply(
        auto_apply_requested=True,
        dry_result=MutationResult(success=True, new_graph={"nodes": ["preview"]}),
        graph_snapshot=_empty_graph(),
        plan=object(),
        current_revision="rev-1",
        apply_mutation=lambda _graph, _plan, _revision: type(
            "ApplyResult",
            (),
            {"success": True, "new_graph": {"nodes": ["applied"]}},
        )(),
        validate_graph=lambda _graph: (_ for _ in ()).throw(RuntimeError("validation exploded")),
    )

    assert outcome.status == "validation_error"
    assert outcome.validation_errors == ("validation exploded",)


def test_prepare_mutation_auto_apply_reports_blocked_contract_errors() -> None:
    report = type(
        "ContractReport",
        (),
        {
            "validated": True,
            "run_ready": False,
            "errors": [],
            "run_readiness_issues": ["Not run-ready"],
        },
    )()
    outcome = prepare_mutation_auto_apply(
        auto_apply_requested=True,
        dry_result=MutationResult(success=True, new_graph={"nodes": ["preview"]}),
        graph_snapshot=_empty_graph(),
        plan=object(),
        current_revision="rev-1",
        apply_mutation=lambda _graph, _plan, _revision: type(
            "ApplyResult",
            (),
            {"success": True, "new_graph": {"nodes": ["applied"]}},
        )(),
        validate_graph=lambda _graph: report,
    )

    assert outcome.status == "blocked"
    assert outcome.validation_errors == ("Not run-ready",)
    assert outcome.contract_report is report


def test_resolve_mutation_auto_apply_requested_keeps_normal_preview_opt_in() -> None:
    requested = resolve_mutation_auto_apply_requested(
        explicit_auto_apply=None,
        is_empty_graph=True,
        generation_fallback_active=False,
        dry_result=MutationResult(success=True, new_graph={"nodes": ["preview"]}),
        plan=object(),
    )

    assert requested is False


def test_resolve_mutation_auto_apply_requested_promotes_empty_graph_recovery() -> None:
    requested = resolve_mutation_auto_apply_requested(
        explicit_auto_apply=None,
        is_empty_graph=True,
        generation_fallback_active=True,
        dry_result=MutationResult(success=True, new_graph={"nodes": ["preview"]}),
        plan=object(),
    )

    assert requested is True


def test_resolve_mutation_auto_apply_requested_respects_explicit_preview_request() -> None:
    requested = resolve_mutation_auto_apply_requested(
        explicit_auto_apply=False,
        is_empty_graph=True,
        generation_fallback_active=True,
        dry_result=MutationResult(success=True, new_graph={"nodes": ["preview"]}),
        plan=object(),
    )

    assert requested is False
