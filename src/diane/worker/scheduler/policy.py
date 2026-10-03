"""Deterministic guardrails around universal scheduling-agent proposals."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from diane.worker.context_capsules import (
    ContextCapsule,
    ReadinessSignal,
    assemble_context_packet,
    readiness_signal_from_capsules,
)
from diane.worker.scheduler.contracts import (
    ArtifactPartition,
    ArtifactPartitionAdmission,
    SchedulerAction,
    SchedulerActionSelection,
    SchedulerProposalRanking,
    SchedulerReadinessEvaluation,
    SchedulerTask,
    SchedulingProposal,
)
from diane.worker.specialized_agents import (
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
    metadata: dict[str, Any] = Field(default_factory=dict)


_CAPACITY_CONSUMING_ACTIONS = {
    SchedulerAction.SERIAL,
    SchedulerAction.PARALLEL,
    SchedulerAction.DISPATCH,
    SchedulerAction.SPLIT,
    SchedulerAction.DUPLICATE,
    SchedulerAction.IMPROVE_CONTEXT,
}

_WORK_PRODUCING_ACTIONS = {
    SchedulerAction.SERIAL,
    SchedulerAction.PARALLEL,
    SchedulerAction.DISPATCH,
    SchedulerAction.SPLIT,
    SchedulerAction.DUPLICATE,
    SchedulerAction.IMPROVE_CONTEXT,
}

_ACTION_TIE_BREAK_PRIORITY = {
    SchedulerAction.PARALLEL: 80,
    SchedulerAction.SPLIT: 75,
    SchedulerAction.DISPATCH: 70,
    SchedulerAction.SERIAL: 65,
    SchedulerAction.DUPLICATE: 60,
    SchedulerAction.IMPROVE_CONTEXT: 50,
    SchedulerAction.VALIDATE: 45,
    SchedulerAction.FINALIZE: 40,
    SchedulerAction.REOPEN: 35,
    SchedulerAction.CANCEL: 30,
    SchedulerAction.WAIT: 10,
    SchedulerAction.STOP: 0,
}
_REJECTED_PROPOSAL_SCORE = -1_000_000_000.0


def _artifact_path_key(path: str) -> str:
    cleaned = str(path or "").strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")
    return cleaned.strip("/")


def _artifact_paths_overlap(left: str, right: str) -> bool:
    left_key = _artifact_path_key(left)
    right_key = _artifact_path_key(right)
    if not left_key or not right_key:
        return False
    return (
        left_key == right_key
        or left_key.startswith(right_key + "/")
        or right_key.startswith(left_key + "/")
    )


def _artifact_partition_score(partition: ArtifactPartition) -> float:
    quality = 1.0 if partition.expected_quality_gain is None else partition.expected_quality_gain
    latency_penalty = 0.01 * (partition.expected_latency_seconds or 0.0)
    return (
        quality
        - latency_penalty
        - partition.coordination_cost
        - partition.rework_risk
        - partition.conflict_risk
    )


def _proposal_metric(
    proposal: SchedulingProposal,
    field_name: str,
    *,
    default: float = 0.0,
) -> float:
    raw = getattr(proposal, field_name)
    if raw is None:
        raw = proposal.metadata.get(field_name)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _proposal_material_yield_probability(
    proposal: SchedulingProposal,
    *,
    default: float,
) -> float:
    raw = proposal.material_yield_probability
    if raw is None and "material_yield_probability" in proposal.metadata:
        raw = proposal.metadata.get("material_yield_probability")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(1.0, value))


def _proposal_required_capacity(proposal: SchedulingProposal) -> int:
    if proposal.action not in _CAPACITY_CONSUMING_ACTIONS:
        return 0
    return max(1, int(proposal.required_capacity or 1))


def _normalize_capsules(
    capsules: list[ContextCapsule | dict[str, Any]],
) -> list[ContextCapsule]:
    normalized: list[ContextCapsule] = []
    for capsule in capsules:
        try:
            parsed = (
                capsule
                if isinstance(capsule, ContextCapsule)
                else ContextCapsule.model_validate(capsule)
            )
        except Exception:
            continue
        normalized.append(parsed)
    return normalized


def _normalize_readiness_signals(
    signals: list[ReadinessSignal | dict[str, Any]],
) -> list[ReadinessSignal]:
    normalized: list[ReadinessSignal] = []
    for signal in signals:
        try:
            parsed = (
                signal
                if isinstance(signal, ReadinessSignal)
                else ReadinessSignal.model_validate(signal)
            )
        except Exception:
            continue
        normalized.append(parsed)
    return normalized


def _capsule_artifact_kinds(capsule: ContextCapsule) -> set[str]:
    kinds = {str(capsule.kind)}
    for key in ("artifact_kind", "artifact_type", "kind"):
        value = capsule.metadata.get(key)
        if isinstance(value, str) and value.strip():
            kinds.add(value.strip())
    for ref in capsule.raw_refs:
        if ref.kind:
            kinds.add(str(ref.kind))
    return kinds


def _useful_capsule(capsule: ContextCapsule) -> bool:
    return (
        capsule.kind != "blocker"
        and capsule.artifact_state in {"useful_for_downstream", "validated"}
    )


def evaluate_task_readiness_from_capsules(
    task: SchedulerTask,
    capsules: list[ContextCapsule | dict[str, Any]],
    *,
    readiness_signals: list[ReadinessSignal | dict[str, Any]] | None = None,
    max_capsules: int = 12,
    max_evidence_chars: int = 5000,
) -> SchedulerReadinessEvaluation:
    """Evaluate whether capsules satisfy a downstream scheduler task boundary."""

    normalized_capsules = _normalize_capsules(capsules)
    normalized_signals = _normalize_readiness_signals(readiness_signals or [])
    if normalized_capsules and not normalized_signals:
        normalized_signals.append(
            readiness_signal_from_capsules(
                normalized_capsules,
                source_task_id=task.task_id,
                predicate="capsules_available",
            )
        )

    useful_capsules = [
        capsule for capsule in normalized_capsules if _useful_capsule(capsule)
    ]
    blockers = [
        capsule.summary
        for capsule in normalized_capsules
        if capsule.kind == "blocker" and capsule.summary
    ]
    for signal in normalized_signals:
        blockers.extend(str(item) for item in signal.blockers if str(item).strip())
    blockers = list(dict.fromkeys(blockers))

    ready_signals = [
        signal for signal in normalized_signals if signal.ready_for_downstream
    ]
    available_predicates = {
        str(signal.predicate).strip()
        for signal in ready_signals
        if str(signal.predicate).strip()
    }
    available_artifact_kinds: set[str] = set()
    available_unlocks: set[str] = set()
    for capsule in useful_capsules:
        available_artifact_kinds.update(_capsule_artifact_kinds(capsule))
        available_unlocks.update(str(item).strip() for item in capsule.unlocks if str(item).strip())

    missing_predicates = [
        predicate
        for predicate in task.required_readiness_predicates
        if predicate not in available_predicates
    ]
    missing_artifact_kinds = [
        kind for kind in task.required_artifact_kinds if kind not in available_artifact_kinds
    ]
    missing_unlocks = [
        unlock for unlock in task.required_unlocks if unlock not in available_unlocks
    ]
    packet = assemble_context_packet(
        useful_capsules,
        target_task_id=task.task_id,
        max_capsules=max_capsules,
        max_evidence_chars=max_evidence_chars,
    )
    ready = not (
        blockers
        or missing_predicates
        or missing_artifact_kinds
        or missing_unlocks
    )
    return SchedulerReadinessEvaluation(
        task_id=task.task_id,
        ready=ready,
        readiness_signal_ids=[
            signal.readiness_id for signal in normalized_signals if signal.readiness_id
        ],
        capsule_ids=[capsule.capsule_id for capsule in useful_capsules],
        missing_readiness_predicates=missing_predicates,
        missing_artifact_kinds=missing_artifact_kinds,
        missing_unlocks=missing_unlocks,
        blockers=blockers,
        context_packet=packet.model_dump(mode="json", exclude_none=True),
        metadata={
            "available_readiness_predicates": sorted(available_predicates),
            "available_artifact_kinds": sorted(available_artifact_kinds),
            "available_unlocks": sorted(available_unlocks),
            "capsule_count": len(normalized_capsules),
            "useful_capsule_count": len(useful_capsules),
        },
    )


def _evaluate_duplicate_hedge(
    proposal: SchedulingProposal,
    *,
    min_uncertainty: float = 0.35,
    min_failure_probability: float = 0.25,
    min_material_yield_probability: float = 0.35,
    token_cost_weight: float = 0.001,
) -> tuple[list[str], list[str], dict[str, Any]]:
    rejection_reasons: list[str] = []
    warnings: list[str] = []
    expected_value = _proposal_metric(
        proposal,
        "expected_value",
        default=_proposal_metric(proposal, "expected_quality_gain"),
    )
    uncertainty = _proposal_metric(proposal, "uncertainty")
    failure_probability = _proposal_metric(proposal, "failure_probability")
    latency_cost = _proposal_metric(
        proposal,
        "latency_cost",
        default=0.01 * _proposal_metric(proposal, "expected_duration_seconds"),
    )
    token_cost = _proposal_metric(proposal, "token_cost")
    rework_risk = _proposal_metric(proposal, "rework_risk")
    material_yield_probability = proposal.material_yield_probability
    if material_yield_probability is None and "material_yield_probability" in proposal.metadata:
        try:
            material_yield_probability = float(proposal.metadata["material_yield_probability"])
        except (TypeError, ValueError):
            material_yield_probability = None

    if expected_value <= 0.0:
        rejection_reasons.append("missing_positive_expected_value")
    if uncertainty < min_uncertainty and failure_probability < min_failure_probability:
        rejection_reasons.append("duplicate_without_uncertainty_or_failure_risk")
    if (
        material_yield_probability is not None
        and material_yield_probability < min_material_yield_probability
    ):
        rejection_reasons.append("low_material_yield_probability")

    risk_adjusted_value = expected_value * (
        1.0 + (0.25 * uncertainty) + (0.5 * failure_probability)
    )
    total_cost = latency_cost + (token_cost * token_cost_weight) + rework_risk
    net_value = risk_adjusted_value - total_cost
    if net_value <= 0.0:
        rejection_reasons.append("non_positive_hedge_value")
    if material_yield_probability is None:
        warnings.append("missing_material_yield_probability")

    metadata = {
        "hedge_expected_value": expected_value,
        "hedge_risk_adjusted_value": round(risk_adjusted_value, 6),
        "hedge_total_cost": round(total_cost, 6),
        "hedge_net_value": round(net_value, 6),
        "hedge_uncertainty": uncertainty,
        "hedge_failure_probability": failure_probability,
        "hedge_material_yield_probability": material_yield_probability,
    }
    return rejection_reasons, warnings, metadata


def evaluate_artifact_partition_admission(
    partitions: list[ArtifactPartition],
    *,
    in_flight: list[ArtifactPartition] | None = None,
    max_parallel: int | None = None,
    min_parallel_score: float = 0.0,
    high_conflict_risk: float = 0.75,
) -> list[ArtifactPartitionAdmission]:
    """Admit artifact partitions for parallel dispatch using generic features."""

    accepted_paths: list[str] = []
    for active in in_flight or []:
        accepted_paths.extend(_artifact_path_key(path) for path in active.artifact_paths)

    accepted_count = 0
    admissions: list[ArtifactPartitionAdmission] = []
    for partition in partitions:
        paths = [_artifact_path_key(path) for path in partition.artifact_paths if _artifact_path_key(path)]
        score = _artifact_partition_score(partition)
        reasons: list[str] = []
        disposition = "parallel"
        admitted = True

        if not paths:
            reasons.append("missing_artifact_paths")
            disposition = "rejected"
            admitted = False
        elif score <= min_parallel_score:
            reasons.append("non_positive_marginal_value")
            disposition = "rejected"
            admitted = False
        elif partition.conflict_risk >= high_conflict_risk:
            reasons.append("high_conflict_risk")
            disposition = "repair"
            admitted = False
        elif any(
            _artifact_paths_overlap(path, accepted_path)
            for path in paths
            for accepted_path in accepted_paths
        ):
            reasons.append("artifact_conflict")
            disposition = "serial"
            admitted = False
        elif max_parallel is not None and accepted_count >= max_parallel:
            reasons.append("parallel_capacity_limit")
            disposition = "serial"
            admitted = False

        if admitted:
            accepted_count += 1
            accepted_paths.extend(paths)

        admissions.append(
            ArtifactPartitionAdmission(
                partition_id=partition.partition_id,
                admitted=admitted,
                score=score,
                disposition=disposition,
                reasons=reasons,
                artifact_paths=paths,
            )
        )

    return admissions


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
    metadata: dict[str, Any] = {}

    if proposal.requires_audit_log and policy.require_audit_log and not state.audit_log_enabled:
        rejection_reasons.append("audit_log_required")

    if proposal.required_safety_envelope and policy.safety_envelope:
        if proposal.required_safety_envelope != state.safety_envelope:
            rejection_reasons.append("safety_envelope_mismatch")

    required_capacity = _proposal_required_capacity(proposal)
    if policy.enforce_capacity and proposal.action in _CAPACITY_CONSUMING_ACTIONS:
        if state.available_capacity <= 0:
            rejection_reasons.append("capacity_exhausted")
        elif state.available_capacity < required_capacity:
            rejection_reasons.append(f"capacity_insufficient:{required_capacity}")
        elif state.max_capacity and state.available_capacity > state.max_capacity:
            warnings.append("capacity_state_exceeds_max")
        if proposal.action == SchedulerAction.PARALLEL and required_capacity < 2:
            warnings.append("parallel_without_multiple_capacity")

    if policy.enforce_hard_dependencies and proposal.action in {
        SchedulerAction.SERIAL,
        SchedulerAction.PARALLEL,
        SchedulerAction.DISPATCH,
        SchedulerAction.SPLIT,
        SchedulerAction.DUPLICATE,
        SchedulerAction.VALIDATE,
        SchedulerAction.FINALIZE,
        SchedulerAction.STOP,
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

    if proposal.action == SchedulerAction.DUPLICATE:
        hedge_rejections, hedge_warnings, hedge_metadata = _evaluate_duplicate_hedge(proposal)
        rejection_reasons.extend(hedge_rejections)
        warnings.extend(hedge_warnings)
        metadata.update(hedge_metadata)

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
        metadata=metadata,
    )


def _score_scheduler_proposal(
    proposal: SchedulingProposal,
    *,
    result: SchedulerGuardrailResult,
    material_yield_default: float,
    latency_cost_weight: float,
    token_cost_weight: float,
    rework_risk_weight: float,
    parallelism_bonus: float,
) -> tuple[float, dict[str, Any]]:
    if not result.admissible:
        return _REJECTED_PROPOSAL_SCORE, {}

    if (
        proposal.action == SchedulerAction.DUPLICATE
        and "hedge_net_value" in result.metadata
    ):
        score = float(result.metadata["hedge_net_value"])
        return score, {"score_source": "hedge_net_value", "net_value": score}

    expected_value = _proposal_metric(
        proposal,
        "expected_value",
        default=_proposal_metric(proposal, "expected_quality_gain"),
    )
    material_yield_probability = 1.0
    if proposal.action in _WORK_PRODUCING_ACTIONS:
        material_yield_probability = _proposal_material_yield_probability(
            proposal,
            default=material_yield_default,
        )
    value = expected_value * material_yield_probability
    latency_cost = _proposal_metric(
        proposal,
        "latency_cost",
        default=0.01 * _proposal_metric(proposal, "expected_duration_seconds"),
    )
    token_cost = _proposal_metric(proposal, "token_cost")
    rework_risk = _proposal_metric(proposal, "rework_risk")
    required_capacity = _proposal_required_capacity(proposal)
    parallel_bonus = (
        parallelism_bonus * max(0, min(required_capacity, 4) - 1)
        if proposal.action in {SchedulerAction.PARALLEL, SchedulerAction.SPLIT}
        else 0.0
    )
    score = (
        value
        + parallel_bonus
        - (latency_cost_weight * latency_cost)
        - (token_cost_weight * token_cost)
        - (rework_risk_weight * rework_risk)
    )
    metadata = {
        "score_source": "expected_value_minus_cost",
        "score_expected_value": round(expected_value, 6),
        "score_material_yield_probability": round(material_yield_probability, 6),
        "score_value_after_yield": round(value, 6),
        "score_latency_cost": round(latency_cost, 6),
        "score_token_cost": round(token_cost, 6),
        "score_rework_risk": round(rework_risk, 6),
        "score_parallel_bonus": round(parallel_bonus, 6),
        "score_required_capacity": required_capacity,
    }
    return score, metadata


def rank_scheduler_proposals(
    proposals: list[SchedulingProposal],
    *,
    state: SchedulerGuardrailState,
    guardrails: DeterministicPolicyGuardrails | None = None,
    material_yield_default: float = 0.6,
    latency_cost_weight: float = 1.0,
    token_cost_weight: float = 0.001,
    rework_risk_weight: float = 1.0,
    parallelism_bonus: float = 0.05,
) -> list[SchedulerProposalRanking]:
    """Evaluate proposals with guardrails and reusable marginal-value scoring."""

    rankings: list[SchedulerProposalRanking] = []
    for index, proposal in enumerate(proposals):
        result = evaluate_scheduler_proposal(
            proposal,
            state=state,
            guardrails=guardrails,
        )
        score, score_metadata = _score_scheduler_proposal(
            proposal,
            result=result,
            material_yield_default=material_yield_default,
            latency_cost_weight=latency_cost_weight,
            token_cost_weight=token_cost_weight,
            rework_risk_weight=rework_risk_weight,
            parallelism_bonus=parallelism_bonus,
        )
        rankings.append(
            SchedulerProposalRanking(
                proposal_index=index,
                action=proposal.action,
                target_task_id=proposal.target_task_id,
                admissible=result.admissible,
                score=round(score, 6),
                rejection_reasons=list(result.rejection_reasons),
                warnings=list(result.warnings),
                metadata={**result.metadata, **score_metadata},
            )
        )
    return rankings


def select_scheduler_proposal(
    proposals: list[SchedulingProposal],
    *,
    state: SchedulerGuardrailState,
    guardrails: DeterministicPolicyGuardrails | None = None,
    material_yield_default: float = 0.6,
    latency_cost_weight: float = 1.0,
    token_cost_weight: float = 0.001,
    rework_risk_weight: float = 1.0,
    parallelism_bonus: float = 0.05,
) -> SchedulerActionSelection:
    """Select the best admissible scheduler proposal and keep the full trace."""

    rankings = rank_scheduler_proposals(
        proposals,
        state=state,
        guardrails=guardrails,
        material_yield_default=material_yield_default,
        latency_cost_weight=latency_cost_weight,
        token_cost_weight=token_cost_weight,
        rework_risk_weight=rework_risk_weight,
        parallelism_bonus=parallelism_bonus,
    )
    admissible_rankings = [ranking for ranking in rankings if ranking.admissible]
    if not admissible_rankings:
        return SchedulerActionSelection(
            rankings=rankings,
            metadata={"selection_reason": "no_admissible_proposal"},
        )

    selected = max(
        admissible_rankings,
        key=lambda ranking: (
            ranking.score,
            _ACTION_TIE_BREAK_PRIORITY.get(ranking.action, 0),
            -ranking.proposal_index,
        ),
    )
    selected_proposal = proposals[selected.proposal_index]
    return SchedulerActionSelection(
        selected_index=selected.proposal_index,
        selected_action=selected.action,
        selected_target_task_id=selected.target_task_id,
        selected_proposal=selected_proposal,
        rankings=rankings,
        metadata={"selection_reason": "max_marginal_value"},
    )


__all__ = [
    "evaluate_artifact_partition_admission",
    "evaluate_task_readiness_from_capsules",
    "rank_scheduler_proposals",
    "SchedulerGuardrailResult",
    "SchedulerGuardrailState",
    "evaluate_scheduler_proposal",
    "select_scheduler_proposal",
]
