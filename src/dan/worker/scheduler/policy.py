"""Deterministic guardrails around universal scheduling-agent proposals."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from dan.worker.scheduler.contracts import SchedulerAction, SchedulingProposal
from dan.worker.specialized_agents import (
    DeterministicPolicyGuardrails,
    default_scheduler_guardrails,
)


class SchedulerGuardrailState(BaseModel):
    """Observed scheduler state used for deterministic admissibility checks."""

    satisfied_dependency_ids: list[str] = Field(default_factory=list)
    blocked_dependency_ids: list[str] = Field(default_factory=list)
    available_capacity: int = Field(default=0, ge=0)
    max_capacity: int = Field(default=0, ge=0)
    audit_log_enabled: bool = True
    validation_passed_task_ids: list[str] = Field(default_factory=list)
    safety_envelope: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SchedulerGuardrailResult(BaseModel):
    """Deterministic decision about whether a proposal may execute."""

    admissible: bool
    rejection_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def evaluate_scheduler_proposal(
    proposal: SchedulingProposal,
    *,
    state: SchedulerGuardrailState,
    guardrails: DeterministicPolicyGuardrails | None = None,
) -> SchedulerGuardrailResult:
    """Check whether a scheduling proposal is admissible."""

    policy = guardrails or default_scheduler_guardrails()
    rejection_reasons: list[str] = []
    warnings: list[str] = []

    if proposal.requires_audit_log and policy.require_audit_log and not state.audit_log_enabled:
        rejection_reasons.append("audit_log_required")

    if proposal.required_safety_envelope and policy.safety_envelope:
        if proposal.required_safety_envelope != state.safety_envelope:
            rejection_reasons.append("safety_envelope_mismatch")

    if policy.enforce_capacity and proposal.action in {
        SchedulerAction.DISPATCH,
        SchedulerAction.SPLIT,
        SchedulerAction.DUPLICATE,
    }:
        if state.available_capacity <= 0:
            rejection_reasons.append("capacity_exhausted")
        elif state.max_capacity and state.available_capacity > state.max_capacity:
            warnings.append("capacity_state_exceeds_max")

    if policy.enforce_hard_dependencies and proposal.action in {
        SchedulerAction.DISPATCH,
        SchedulerAction.SPLIT,
        SchedulerAction.VALIDATE,
        SchedulerAction.FINALIZE,
    }:
        missing = [
            dep_id
            for dep_id in proposal.required_dependency_ids
            if dep_id not in state.satisfied_dependency_ids
        ]
        if missing:
            rejection_reasons.append(
                "missing_dependencies:" + ",".join(sorted(set(missing)))
            )

    if policy.enforce_validation_gate and proposal.requires_validation_pass:
        if proposal.target_task_id and proposal.target_task_id not in state.validation_passed_task_ids:
            rejection_reasons.append("validation_gate_unmet")

    if policy.enforce_confidence_pruning and proposal.action == SchedulerAction.CANCEL:
        if (
            proposal.branch_upper_confidence is None
            or proposal.incumbent_lower_confidence is None
            or proposal.pruning_slack is None
        ):
            rejection_reasons.append("missing_pruning_bounds")
        elif (
            proposal.branch_upper_confidence + proposal.pruning_slack
            >= proposal.incumbent_lower_confidence
        ):
            rejection_reasons.append("pruning_margin_not_met")

    if proposal.action == SchedulerAction.WAIT and proposal.required_dependency_ids:
        unsatisfied = [
            dep_id
            for dep_id in proposal.required_dependency_ids
            if dep_id not in state.satisfied_dependency_ids
        ]
        if not unsatisfied:
            warnings.append("wait_without_blocker")

    return SchedulerGuardrailResult(
        admissible=not rejection_reasons,
        rejection_reasons=rejection_reasons,
        warnings=warnings,
    )


__all__ = [
    "SchedulerGuardrailResult",
    "SchedulerGuardrailState",
    "evaluate_scheduler_proposal",
]
