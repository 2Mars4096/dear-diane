from __future__ import annotations

from dan.worker.context_capsules import (
    build_tool_context_capsules,
    readiness_signal_from_capsules,
)
from dan.worker.scheduler import (
    ArtifactPartition,
    SchedulerAction,
    SchedulerGuardrailState,
    SchedulerTask,
    SchedulingProposal,
    build_universal_scheduling_worker,
    evaluate_artifact_partition_admission,
    evaluate_scheduler_proposal,
    evaluate_task_readiness_from_capsules,
    select_scheduler_proposal,
)
from dan.worker.specialized_agents import (
    SpecializedAgentKind,
    specialized_agent_membrane,
)


def test_build_universal_scheduling_worker_attaches_scheduler_membrane() -> None:
    worker = build_universal_scheduling_worker(
        worker_id="scheduler-1",
        model="gpt-test",
    )

    membrane = specialized_agent_membrane(worker)

    assert membrane is not None
    assert membrane.specialization == SpecializedAgentKind.SCHEDULER
    assert membrane.contract_name == "universal_scheduling_agent"
    assert membrane.deterministic_guardrails.enforce_capacity is True
    assert membrane.deterministic_guardrails.enforce_hard_dependencies is True
    assert membrane.deterministic_guardrails.enforce_confidence_pruning is True


def test_scheduler_guardrail_blocks_dispatch_without_capacity_or_dependencies() -> None:
    proposal = SchedulingProposal(
        action=SchedulerAction.DISPATCH,
        target_task_id="task-a",
        required_dependency_ids=["dep-1"],
    )
    state = SchedulerGuardrailState(
        satisfied_dependency_ids=[],
        available_capacity=0,
        max_capacity=2,
        audit_log_enabled=True,
        safety_envelope="bounded_task_dispatch",
    )

    result = evaluate_scheduler_proposal(proposal, state=state)

    assert result.admissible is False
    assert "capacity_exhausted" in result.rejection_reasons
    assert "missing_dependencies:dep-1" in result.rejection_reasons


def test_scheduler_guardrail_blocks_parallel_when_required_capacity_is_missing() -> None:
    proposal = SchedulingProposal(
        action=SchedulerAction.PARALLEL,
        target_task_id="task-a",
        required_capacity=2,
        expected_value=1.0,
    )
    state = SchedulerGuardrailState(
        available_capacity=1,
        max_capacity=2,
        audit_log_enabled=True,
        safety_envelope="bounded_task_dispatch",
    )

    result = evaluate_scheduler_proposal(proposal, state=state)

    assert result.admissible is False
    assert result.rejection_reasons == ["capacity_insufficient:2"]


def test_scheduler_guardrail_blocks_cancel_without_bounds() -> None:
    proposal = SchedulingProposal(
        action=SchedulerAction.CANCEL,
        target_task_id="branch-a",
    )
    state = SchedulerGuardrailState(
        available_capacity=1,
        max_capacity=2,
        audit_log_enabled=True,
        safety_envelope="bounded_task_dispatch",
    )

    result = evaluate_scheduler_proposal(proposal, state=state)

    assert result.admissible is False
    assert result.rejection_reasons == ["missing_pruning_bounds"]


def test_scheduler_guardrail_allows_cancel_when_margin_is_clear() -> None:
    proposal = SchedulingProposal(
        action=SchedulerAction.CANCEL,
        target_task_id="branch-a",
        branch_upper_confidence=0.45,
        incumbent_lower_confidence=0.70,
        pruning_slack=0.10,
    )
    state = SchedulerGuardrailState(
        available_capacity=1,
        max_capacity=2,
        audit_log_enabled=True,
        safety_envelope="bounded_task_dispatch",
    )

    result = evaluate_scheduler_proposal(proposal, state=state)

    assert result.admissible is True
    assert result.rejection_reasons == []


def test_scheduler_guardrail_rejects_duplicate_without_uncertainty_or_value() -> None:
    proposal = SchedulingProposal(
        action=SchedulerAction.DUPLICATE,
        target_task_id="task-a",
        expected_quality_gain=0.2,
        uncertainty=0.1,
        failure_probability=0.05,
        latency_cost=0.3,
        rework_risk=0.1,
        material_yield_probability=0.7,
    )
    state = SchedulerGuardrailState(
        available_capacity=1,
        max_capacity=2,
        audit_log_enabled=True,
        safety_envelope="bounded_task_dispatch",
    )

    result = evaluate_scheduler_proposal(proposal, state=state)

    assert result.admissible is False
    assert "duplicate_without_uncertainty_or_failure_risk" in result.rejection_reasons
    assert "non_positive_hedge_value" in result.rejection_reasons
    assert result.metadata["hedge_net_value"] < 0


def test_scheduler_guardrail_allows_duplicate_when_failure_risk_and_value_clear_costs() -> None:
    proposal = SchedulingProposal(
        action=SchedulerAction.DUPLICATE,
        target_task_id="task-a",
        expected_quality_gain=1.3,
        uncertainty=0.45,
        failure_probability=0.35,
        latency_cost=0.15,
        token_cost=20,
        rework_risk=0.1,
        material_yield_probability=0.75,
    )
    state = SchedulerGuardrailState(
        available_capacity=1,
        max_capacity=2,
        audit_log_enabled=True,
        safety_envelope="bounded_task_dispatch",
    )

    result = evaluate_scheduler_proposal(proposal, state=state)

    assert result.admissible is True
    assert result.rejection_reasons == []
    assert result.warnings == []
    assert result.metadata["hedge_net_value"] > 0


def test_scheduler_guardrail_rejects_duplicate_with_low_material_yield_probability() -> None:
    proposal = SchedulingProposal(
        action=SchedulerAction.DUPLICATE,
        target_task_id="task-a",
        expected_value=2.0,
        uncertainty=0.8,
        failure_probability=0.5,
        latency_cost=0.1,
        rework_risk=0.1,
        material_yield_probability=0.1,
    )
    state = SchedulerGuardrailState(
        available_capacity=1,
        max_capacity=2,
        audit_log_enabled=True,
        safety_envelope="bounded_task_dispatch",
    )

    result = evaluate_scheduler_proposal(proposal, state=state)

    assert result.admissible is False
    assert result.rejection_reasons == ["low_material_yield_probability"]
    assert result.metadata["hedge_material_yield_probability"] == 0.1


def test_scheduler_selector_prefers_best_admissible_marginal_value() -> None:
    proposals = [
        SchedulingProposal(
            action=SchedulerAction.SERIAL,
            target_task_id="task-serial",
            expected_value=0.7,
            latency_cost=0.2,
            material_yield_probability=0.9,
        ),
        SchedulingProposal(
            action=SchedulerAction.PARALLEL,
            target_task_id="task-parallel",
            required_capacity=2,
            expected_value=1.2,
            latency_cost=0.2,
            material_yield_probability=0.85,
        ),
        SchedulingProposal(
            action=SchedulerAction.PARALLEL,
            target_task_id="task-overwide",
            required_capacity=4,
            expected_value=4.0,
            latency_cost=0.1,
            material_yield_probability=0.9,
        ),
    ]
    state = SchedulerGuardrailState(
        available_capacity=2,
        max_capacity=2,
        audit_log_enabled=True,
        safety_envelope="bounded_task_dispatch",
    )

    selection = select_scheduler_proposal(proposals, state=state)

    assert selection.selected_action == SchedulerAction.PARALLEL
    assert selection.selected_target_task_id == "task-parallel"
    assert selection.metadata["selection_reason"] == "max_marginal_value"
    assert selection.rankings[2].admissible is False
    assert selection.rankings[2].rejection_reasons == ["capacity_insufficient:4"]


def test_scheduler_selector_returns_empty_selection_when_all_actions_blocked() -> None:
    proposals = [
        SchedulingProposal(
            action=SchedulerAction.PARALLEL,
            target_task_id="task-a",
            required_capacity=2,
            expected_value=1.0,
        ),
        SchedulingProposal(
            action=SchedulerAction.DISPATCH,
            target_task_id="task-b",
            required_dependency_ids=["dep-missing"],
            expected_value=1.0,
        ),
    ]
    state = SchedulerGuardrailState(
        available_capacity=1,
        max_capacity=1,
        audit_log_enabled=True,
        safety_envelope="bounded_task_dispatch",
    )

    selection = select_scheduler_proposal(proposals, state=state)

    assert selection.selected_index is None
    assert selection.selected_action is None
    assert selection.metadata["selection_reason"] == "no_admissible_proposal"
    assert [ranking.admissible for ranking in selection.rankings] == [False, False]


def test_scheduler_readiness_uses_capsule_predicates_artifact_kinds_and_unlocks() -> None:
    capsules = build_tool_context_capsules(
        {
            "tool_id": "file_read",
            "tool_call_id": "tool-read",
            "model_call_id": "model-1",
            "arguments": {"path": "src/app.py"},
            "ok": True,
            "result": {"path": "src/app.py", "content": "print('ready')\n"},
        },
        source_task_id="upstream",
        source_worker_id="worker-a",
    )
    signals = [
        readiness_signal_from_capsules(
            capsules,
            source_task_id="upstream",
            source_worker_id="worker-a",
            predicate="file_context_ready",
            downstream_task_ids=["downstream"],
        )
    ]
    task = SchedulerTask(
        task_id="downstream",
        title="Patch downstream file",
        required_readiness_predicates=["file_context_ready"],
        required_artifact_kinds=["file_context"],
        required_unlocks=["file_context:src/app.py"],
    )

    readiness = evaluate_task_readiness_from_capsules(
        task,
        capsules,
        readiness_signals=signals,
    )

    assert readiness.ready is True
    assert readiness.missing_readiness_predicates == []
    assert readiness.missing_artifact_kinds == []
    assert readiness.missing_unlocks == []
    assert readiness.context_packet["target_task_id"] == "downstream"
    assert readiness.context_packet["capsules"][0]["kind"] == "file_context"
    assert readiness.metadata["available_unlocks"] == ["file_context:src/app.py"]


def test_scheduler_readiness_reports_missing_requirements_and_blockers() -> None:
    capsules = build_tool_context_capsules(
        {
            "tool_id": "file_read",
            "tool_call_id": "tool-missing",
            "model_call_id": "model-1",
            "arguments": {"path": "src/other.py"},
            "ok": True,
            "result": {"path": "src/other.py", "content": "x = 1\n"},
        }
    )
    capsules.extend(
        build_tool_context_capsules(
            {
                "tool_id": "file_read",
                "tool_call_id": "tool-fail",
                "model_call_id": "model-1",
                "arguments": {"path": "src/app.py"},
                "ok": False,
                "error": "File not found",
            }
        )
    )
    task = SchedulerTask(
        task_id="downstream",
        title="Patch downstream file",
        required_readiness_predicates=["file_context_ready"],
        required_unlocks=["file_context:src/app.py"],
    )

    readiness = evaluate_task_readiness_from_capsules(task, capsules)

    assert readiness.ready is False
    assert readiness.missing_readiness_predicates == ["file_context_ready"]
    assert readiness.missing_unlocks == ["file_context:src/app.py"]
    assert readiness.blockers == ["file_read failed: File not found"]


def test_artifact_partition_admission_allows_non_overlapping_parallel_work() -> None:
    admissions = evaluate_artifact_partition_admission(
        [
            ArtifactPartition(
                partition_id="p:api",
                artifact_paths=["src/api.py"],
                artifact_kind="python",
                expected_quality_gain=1.0,
                expected_latency_seconds=20,
            ),
            ArtifactPartition(
                partition_id="p:docs",
                artifact_paths=["docs/usage.md"],
                artifact_kind="markdown",
                expected_quality_gain=0.8,
                expected_latency_seconds=10,
            ),
        ],
        max_parallel=4,
    )

    assert [item.disposition for item in admissions] == ["parallel", "parallel"]
    assert [item.admitted for item in admissions] == [True, True]


def test_artifact_partition_admission_serializes_overlapping_artifacts() -> None:
    admissions = evaluate_artifact_partition_admission(
        [
            ArtifactPartition(
                partition_id="p:package",
                artifact_paths=["src/dan"],
                expected_quality_gain=1.0,
            ),
            ArtifactPartition(
                partition_id="p:module",
                artifact_paths=["src/dan/worker/scheduler/policy.py"],
                expected_quality_gain=1.0,
            ),
        ],
        max_parallel=4,
    )

    assert admissions[0].admitted is True
    assert admissions[1].admitted is False
    assert admissions[1].disposition == "serial"
    assert admissions[1].reasons == ["artifact_conflict"]


def test_artifact_partition_admission_rejects_low_value_or_high_conflict() -> None:
    admissions = evaluate_artifact_partition_admission(
        [
            ArtifactPartition(
                partition_id="p:low",
                artifact_paths=["src/low.py"],
                expected_quality_gain=0.1,
                coordination_cost=0.2,
            ),
            ArtifactPartition(
                partition_id="p:risky",
                artifact_paths=["src/risky.py"],
                expected_quality_gain=2.0,
                conflict_risk=0.9,
            ),
        ],
        max_parallel=4,
    )

    assert admissions[0].disposition == "rejected"
    assert admissions[0].reasons == ["non_positive_marginal_value"]
    assert admissions[1].disposition == "repair"
    assert admissions[1].reasons == ["high_conflict_risk"]
