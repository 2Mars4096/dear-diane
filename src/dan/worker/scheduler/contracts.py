"""Task and proposal contracts for the universal scheduling agent."""

from __future__ import annotations

from enum import Enum
from typing import Any

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

    DISPATCH = "dispatch"
    SPLIT = "split"
    DUPLICATE = "duplicate"
    WAIT = "wait"
    CANCEL = "cancel"
    VALIDATE = "validate"
    IMPROVE_CONTEXT = "improve_context"
    FINALIZE = "finalize"
    REOPEN = "reopen"


class SchedulingProposal(BaseModel):
    """One model-proposed scheduler move subject to deterministic guards."""

    action: SchedulerAction
    target_task_id: str = ""
    reason: str = ""
    expected_duration_seconds: float | None = Field(default=None, ge=0.0)
    expected_quality_gain: float | None = Field(default=None, ge=0.0)
    required_dependency_ids: list[str] = Field(default_factory=list)
    requires_validation_pass: bool = False
    requires_audit_log: bool = True
    required_safety_envelope: str = ""
    branch_upper_confidence: float | None = None
    incumbent_lower_confidence: float | None = None
    pruning_slack: float | None = Field(default=None, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "DependencyEdgeKind",
    "SchedulerAction",
    "SchedulerTask",
    "SchedulingProposal",
    "TaskDependency",
]
