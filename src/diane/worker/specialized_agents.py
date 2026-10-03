"""Reusable universal-agent specializations with deterministic guardrails."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from diane.worker.core.model import WorkerDefinition


class SpecializedAgentKind(str, Enum):
    """High-level universal-agent specialization classes."""

    CONTROLLER = "controller"
    SCHEDULER = "scheduler"
    WORKER = "worker"
    REDUCER = "reducer"
    VALIDATOR = "validator"


class DeterministicPolicyGuardrails(BaseModel):
    """Deterministic admissibility rules that surround a specialized agent."""

    require_audit_log: bool = True
    enforce_capacity: bool = False
    enforce_hard_dependencies: bool = False
    enforce_validation_gate: bool = False
    enforce_confidence_pruning: bool = False
    safety_envelope: str = ""
    notes: list[str] = Field(default_factory=list)


class SpecializedAgentMembrane(BaseModel):
    """Typed contract metadata attached to a specialized universal agent."""

    specialization: SpecializedAgentKind
    contract_name: str
    recurrent_loop: str = ""
    deterministic_guardrails: DeterministicPolicyGuardrails = Field(
        default_factory=DeterministicPolicyGuardrails
    )
    typed_action_contract: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


def default_controller_guardrails(
    *,
    safety_envelope: str = "lane_routing",
    notes: list[str] | None = None,
) -> DeterministicPolicyGuardrails:
    return DeterministicPolicyGuardrails(
        require_audit_log=True,
        safety_envelope=safety_envelope,
        notes=list(notes or ["controller decisions must preserve lane/audit context"]),
    )


def default_scheduler_guardrails(
    *,
    safety_envelope: str = "bounded_task_dispatch",
    notes: list[str] | None = None,
) -> DeterministicPolicyGuardrails:
    return DeterministicPolicyGuardrails(
        require_audit_log=True,
        enforce_capacity=True,
        enforce_hard_dependencies=True,
        enforce_validation_gate=True,
        enforce_confidence_pruning=True,
        safety_envelope=safety_envelope,
        notes=list(
            notes
            or [
                "schedule moves must satisfy hard dependencies and capacity",
                "branch cancellation requires confidence-bound evidence plus slack",
            ]
        ),
    )


def default_worker_guardrails(
    *,
    safety_envelope: str = "bounded_execution",
    notes: list[str] | None = None,
) -> DeterministicPolicyGuardrails:
    return DeterministicPolicyGuardrails(
        require_audit_log=True,
        safety_envelope=safety_envelope,
        notes=list(notes or ["bounded workers stay inside their delegated tool envelope"]),
    )


def default_reducer_guardrails(
    *,
    safety_envelope: str = "artifact_reduction",
    notes: list[str] | None = None,
) -> DeterministicPolicyGuardrails:
    return DeterministicPolicyGuardrails(
        require_audit_log=True,
        enforce_validation_gate=True,
        safety_envelope=safety_envelope,
        notes=list(notes or ["reducers only promote validated artifacts"]),
    )


def default_validator_guardrails(
    *,
    safety_envelope: str = "read_only_validation",
    notes: list[str] | None = None,
) -> DeterministicPolicyGuardrails:
    return DeterministicPolicyGuardrails(
        require_audit_log=True,
        enforce_validation_gate=True,
        safety_envelope=safety_envelope,
        notes=list(notes or ["validators stay read-only and gate artifact promotion"]),
    )


def build_specialized_agent_worker(
    *,
    worker_id: str,
    role: str,
    instruction: str,
    model: str | None,
    specialization: SpecializedAgentKind,
    contract_name: str,
    deterministic_guardrails: DeterministicPolicyGuardrails | None = None,
    recurrent_loop: str = "",
    typed_action_contract: str = "",
    metadata: dict[str, Any] | None = None,
) -> WorkerDefinition:
    """Build a worker-core definition with explicit specialization metadata."""

    membrane = SpecializedAgentMembrane(
        specialization=specialization,
        contract_name=contract_name,
        recurrent_loop=recurrent_loop,
        deterministic_guardrails=deterministic_guardrails
        or DeterministicPolicyGuardrails(),
        typed_action_contract=typed_action_contract,
        metadata=dict(metadata or {}),
    )
    resolved_metadata = dict(metadata or {})
    resolved_metadata["specialized_agent"] = membrane.model_dump(mode="json")
    return WorkerDefinition(
        id=worker_id,
        role=role,
        instruction=instruction,
        model=model,
        metadata=resolved_metadata,
    )


def specialized_agent_membrane(
    worker: WorkerDefinition,
) -> SpecializedAgentMembrane | None:
    """Read the specialized-agent membrane from a worker definition."""

    raw = worker.metadata.get("specialized_agent")
    if not isinstance(raw, dict):
        return None
    return SpecializedAgentMembrane.model_validate(raw)


__all__ = [
    "DeterministicPolicyGuardrails",
    "SpecializedAgentKind",
    "SpecializedAgentMembrane",
    "build_specialized_agent_worker",
    "default_controller_guardrails",
    "default_reducer_guardrails",
    "default_scheduler_guardrails",
    "default_validator_guardrails",
    "default_worker_guardrails",
    "specialized_agent_membrane",
]
