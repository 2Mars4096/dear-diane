"""DAN-independent worker-core contracts.

The reusable core accepts normalized execution requests instead of DAN-specific
prompt plumbing. Callers provide task, constraints, evidence, trust labels, and
an output contract; the core decides how to execute that request.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable

from pydantic import BaseModel, Field


class TrustLabel(str, Enum):
    """Weighting hints for evidence blocks carried into execution."""

    AUTHORITATIVE = "authoritative"
    ADVISORY = "advisory"
    RETRIEVED = "retrieved"
    HISTORICAL = "historical"
    USER_PREFERENCE = "user-preference"


class ConstraintSet(BaseModel):
    """Normalized scope and execution constraints for a task."""

    scope: str | None = None
    hard_constraints: list[str] = Field(default_factory=list)
    soft_constraints: list[str] = Field(default_factory=list)


class EvidenceBlock(BaseModel):
    """Caller-supplied context block with explicit provenance weight."""

    label: str
    content: Any
    trust_label: TrustLabel = TrustLabel.RETRIEVED
    source: str | None = None


class OutputContract(BaseModel):
    """What the caller needs back from execution."""

    definition_of_done: str = ""
    expected_return_shape: str = ""
    output_schema: dict[str, Any] | None = None


class ExecutionRequest(BaseModel):
    """Normalized worker execution payload shared across callers."""

    task: str
    constraints: ConstraintSet = Field(default_factory=ConstraintSet)
    evidence: list[EvidenceBlock] = Field(default_factory=list)
    output_contract: OutputContract = Field(default_factory=OutputContract)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_handoff(
        cls,
        *,
        task: str,
        scope: str | None = None,
        hard_constraints: Iterable[str] | None = None,
        soft_constraints: Iterable[str] | None = None,
        evidence_blocks: Iterable[EvidenceBlock | dict[str, Any]] | None = None,
        definition_of_done: str = "",
        expected_return_shape: str = "",
        output_schema: dict[str, Any] | None = None,
        input_payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ExecutionRequest":
        """Build the normalized request from a 46-6-style handoff envelope."""

        return cls(
            task=task,
            constraints=ConstraintSet(
                scope=scope,
                hard_constraints=list(hard_constraints or []),
                soft_constraints=list(soft_constraints or []),
            ),
            evidence=[
                block if isinstance(block, EvidenceBlock) else EvidenceBlock.model_validate(block)
                for block in (evidence_blocks or [])
            ],
            output_contract=OutputContract(
                definition_of_done=definition_of_done,
                expected_return_shape=expected_return_shape,
                output_schema=output_schema,
            ),
            input_payload=dict(input_payload or {}),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_harness(
        cls,
        *,
        task: str,
        constraints: ConstraintSet | dict[str, Any] | None = None,
        evidence: Iterable[EvidenceBlock | dict[str, Any]] | None = None,
        output_contract: OutputContract | dict[str, Any] | None = None,
        input_payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ExecutionRequest":
        """Build the same request shape from a non-DAN harness."""

        resolved_constraints = constraints
        if resolved_constraints is None:
            resolved_constraints = ConstraintSet()
        elif not isinstance(resolved_constraints, ConstraintSet):
            resolved_constraints = ConstraintSet.model_validate(resolved_constraints)

        resolved_output_contract = output_contract
        if resolved_output_contract is None:
            resolved_output_contract = OutputContract()
        elif not isinstance(resolved_output_contract, OutputContract):
            resolved_output_contract = OutputContract.model_validate(resolved_output_contract)

        return cls(
            task=task,
            constraints=resolved_constraints,
            evidence=[
                block if isinstance(block, EvidenceBlock) else EvidenceBlock.model_validate(block)
                for block in (evidence or [])
            ],
            output_contract=resolved_output_contract,
            input_payload=dict(input_payload or {}),
            metadata=dict(metadata or {}),
        )
