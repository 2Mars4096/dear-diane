from __future__ import annotations

from dan.worker import (
    SchedulerAction,
    SchedulerGuardrailState,
    SchedulingProposal,
    SpecializedAgentKind,
    build_universal_scheduling_worker,
    evaluate_scheduler_proposal,
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
