"""Task and proposal contracts for the universal scheduling agent."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class DependencyEdgeKind(str, Enum):
    """Supported dependency confidence classes for the task DAG."""

    HARD = "hard"
    SOFT = "soft"
    SUSPECTED = "suspected"
    DISPROVEN = "disproven"


class TaskDependency(BaseModel):
    """One dependency edge attached to a scheduler task."""

    task_id: str
    kind: DependencyEdgeKind = DependencyEdgeKind.HARD
    reason: str = ""


class SchedulerTask(BaseModel):
    """Task contract optimized by the universal scheduling agent."""

    task_id: str
    title: str
    dependencies: list[TaskDependency] = Field(default_factory=list)
    required_context_refs: list[str] = Field(default_factory=list)
    required_readiness_predicates: list[str] = Field(default_factory=list)
    required_artifact_kinds: list[str] = Field(default_factory=list)
    required_unlocks: list[str] = Field(default_factory=list)
    expected_duration_seconds: float | None = Field(default=None, ge=0.0)
    expected_quality_gain: float | None = Field(default=None, ge=0.0)
    uncertainty: float | None = Field(default=None, ge=0.0)
    failure_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    coordination_cost: float | None = Field(default=None, ge=0.0)
    artifact_type: str = ""
    validator_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SchedulerAction(str, Enum):
    """Actions the universal scheduling agent may propose."""

    SERIAL = "serial"
    PARALLEL = "parallel"
    DISPATCH = "dispatch"
    SPLIT = "split"
    DUPLICATE = "duplicate"
    WAIT = "wait"
    CANCEL = "cancel"
    VALIDATE = "validate"
    IMPROVE_CONTEXT = "improve_context"
    FINALIZE = "finalize"
    REOPEN = "reopen"
    STOP = "stop"


class SchedulingProposal(BaseModel):
    """One model-proposed scheduler move subject to deterministic guards."""

    action: SchedulerAction
    target_task_id: str = ""
    reason: str = ""
    required_capacity: int = Field(default=1, ge=0)
    expected_duration_seconds: float | None = Field(default=None, ge=0.0)
    expected_quality_gain: float | None = Field(default=None, ge=0.0)
    expected_value: float | None = None
    uncertainty: float | None = Field(default=None, ge=0.0)
    failure_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    latency_cost: float | None = Field(default=None, ge=0.0)
    token_cost: float | None = Field(default=None, ge=0.0)
    rework_risk: float | None = Field(default=None, ge=0.0, le=1.0)
    material_yield_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    required_dependency_ids: list[str] = Field(default_factory=list)
    requires_validation_pass: bool = False
    requires_audit_log: bool = True
    required_safety_envelope: str = ""
    branch_upper_confidence: float | None = None
    incumbent_lower_confidence: float | None = None
    pruning_slack: float | None = Field(default=None, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SchedulerProposalRanking(BaseModel):
    """One proposal after deterministic admissibility and reward scoring."""

    proposal_index: int
    action: SchedulerAction
    target_task_id: str = ""
    admissible: bool
    score: float
    rejection_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SchedulerActionSelection(BaseModel):
    """Selected scheduler action plus full ranking trace for replay."""

    selected_index: int | None = None
    selected_action: SchedulerAction | None = None
    selected_target_task_id: str = ""
    selected_proposal: SchedulingProposal | None = None
    rankings: list[SchedulerProposalRanking] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SchedulerReadinessEvaluation(BaseModel):
    """Readiness decision for starting a downstream task from context capsules."""

    task_id: str
    ready: bool
    readiness_signal_ids: list[str] = Field(default_factory=list)
    capsule_ids: list[str] = Field(default_factory=list)
    missing_readiness_predicates: list[str] = Field(default_factory=list)
    missing_artifact_kinds: list[str] = Field(default_factory=list)
    missing_unlocks: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    context_packet: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactPartition(BaseModel):
    """One candidate artifact group that could be owned by one scheduled agent."""

    partition_id: str
    artifact_paths: list[str] = Field(default_factory=list)
    artifact_kind: str = ""
    owner_id: str = ""
    expected_latency_seconds: float | None = Field(default=None, ge=0.0)
    expected_quality_gain: float | None = Field(default=None, ge=0.0)
    rework_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    conflict_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    coordination_cost: float = Field(default=0.0, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactPartitionAdmission(BaseModel):
    """Deterministic admission result for an artifact partition."""

    partition_id: str
    admitted: bool
    score: float
    disposition: Literal["parallel", "serial", "repair", "rejected"]
    reasons: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)


__all__ = [
    "ArtifactPartition",
    "ArtifactPartitionAdmission",
    "DependencyEdgeKind",
    "SchedulerAction",
    "SchedulerActionSelection",
    "SchedulerProposalRanking",
    "SchedulerReadinessEvaluation",
    "SchedulerTask",
    "SchedulingProposal",
    "TaskDependency",
]
