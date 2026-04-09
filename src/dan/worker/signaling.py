"""Typed cross-cell handoff packets and supervisory signals."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import json
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from dan.worker.core.contracts import (
    AcquisitionPolicy,
    AcquisitionRequest,
    AcquisitionSelection,
    AcquisitionStage,
    CommunicationChannel,
    CommunicationChannelKind,
    CommunicationContract,
    ConstraintSet,
    ContinuationPayload,
    EvidenceBlock,
    ExecutionRequest,
    ExpandedContext,
    MemoryRequest,
    OutputContract,
    ToolUseContract,
    TrustLabel,
)
from dan.worker.model import AuthorityPolicy, TaskTier, WorkerAuthority


def _new_id(prefix: str) -> str:
    return f"{prefix}:{uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _compact_text(value: Any, *, limit: int = 400) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, sort_keys=True, default=str)
        except Exception:
            text = str(value)
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


class BroadcastScope(str, Enum):
    """Supervisory audiences for non-point-to-point cell signals."""

    SUPERVISORS = "supervisors"
    TISSUE = "tissue"
    ORGAN = "organ"
    ORGANISM = "organism"


class CellAddress(BaseModel):
    """Stable address for one cell inside a broader organism."""

    cell_id: str
    tissue_id: str | None = None
    organ_id: str | None = None
    organism_id: str | None = None


class SignalTrace(BaseModel):
    """Trace lineage carried across handoffs and broadcasts."""

    trace_id: str = Field(default_factory=lambda: _new_id("trace"))
    root_task_id: str | None = None
    parent_packet_id: str | None = None
    parent_signal_id: str | None = None
    lineage: list[str] = Field(default_factory=list)


class HandoffTask(BaseModel):
    """Compact point-to-point task contract for a child cell."""

    task_id: str
    instruction: str
    scope: str | None = None
    hard_constraints: list[str] = Field(default_factory=list)
    soft_constraints: list[str] = Field(default_factory=list)
    input_payload: dict[str, Any] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    """Compact evidence pointer shared across cells."""

    ref_id: str
    label: str
    summary: str = ""
    trust_label: TrustLabel = TrustLabel.RETRIEVED
    source: str | None = None
    source_id: str = "cell-handoff"
    family: str = "evidence_source"
    locator: str | None = None
    required: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    def as_evidence_block(self) -> EvidenceBlock:
        return EvidenceBlock(
            label=self.label,
            content=self.summary or self.label,
            trust_label=self.trust_label,
            source=self.source,
            ref_id=self.ref_id,
            metadata={
                "source_id": self.source_id,
                "family": self.family,
                "locator": self.locator,
                "required": self.required,
                **self.metadata,
            },
        )

    def as_selection(self, *, reason: str = "provided in cell handoff") -> AcquisitionSelection:
        return AcquisitionSelection(
            ref_id=self.ref_id,
            source_id=self.source_id,
            family=self.family,
            reason=reason,
            selected_by="request",
        )

    def as_expanded_context(self) -> ExpandedContext:
        return ExpandedContext(
            ref_id=self.ref_id,
            source_id=self.source_id,
            family=self.family,
            title=self.label,
            content=self.summary or self.label,
            summary=self.summary or self.label,
            trust_label=self.trust_label,
            source=self.source,
            metadata={
                "locator": self.locator,
                "required": self.required,
                **self.metadata,
            },
        )


class CellBudgetLimits(BaseModel):
    """Explicit execution budget carried with a handoff packet."""

    max_selected_refs_per_source: int | None = Field(default=None, ge=0)
    max_expanded_refs_per_source: int | None = Field(default=None, ge=0)
    max_completion_rounds: int | None = Field(default=None, ge=0)
    max_tool_calls: int | None = Field(default=None, ge=0)
    max_runtime_seconds: int | None = Field(default=None, ge=1)

    def as_acquisition_policy(self) -> AcquisitionPolicy | None:
        payload: dict[str, Any] = {}
        if self.max_selected_refs_per_source is not None:
            payload["max_selected_items_per_source"] = self.max_selected_refs_per_source
        if self.max_expanded_refs_per_source is not None:
            payload["max_expanded_items_per_source"] = self.max_expanded_refs_per_source
        if not payload:
            return None
        return AcquisitionPolicy.model_validate(payload)


class CellAuthorityLimits(BaseModel):
    """Delegation and authority ceiling carried with a handoff packet."""

    acting_authority: WorkerAuthority = WorkerAuthority.LEAF
    max_spawned_cells: int | None = Field(default=None, ge=0)
    task_tier_cap: TaskTier | None = None
    allow_delegate: bool = False
    allow_memory_write_scopes: list[str] = Field(default_factory=list)
    allowed_toolset_refs: list[str] = Field(default_factory=list)

    def as_authority_policy(self) -> AuthorityPolicy:
        return AuthorityPolicy(
            max_spawned_workers=self.max_spawned_cells,
            task_tier_cap=self.task_tier_cap,
            allow_delegate=self.allow_delegate,
            allow_memory_write_scopes=list(self.allow_memory_write_scopes),
            allowed_toolset_refs=list(self.allowed_toolset_refs),
        )


class ContinuationHooks(BaseModel):
    """How the organism expects work to continue after this handoff."""

    reply_to_cell_id: str | None = None
    status_topic: str | None = None
    completion_topic: str | None = None
    escalation_topic: str | None = None
    resume_from_packet_id: str | None = None

    def as_communication_contract(self) -> CommunicationContract:
        channels: list[CommunicationChannel] = []
        if self.reply_to_cell_id:
            channels.append(
                CommunicationChannel(
                    kind=CommunicationChannelKind.REPLY,
                    address=self.reply_to_cell_id,
                    metadata={"resume_from_packet_id": self.resume_from_packet_id},
                )
            )
        if self.status_topic:
            channels.append(
                CommunicationChannel(
                    kind=CommunicationChannelKind.STATUS,
                    address=self.status_topic,
                )
            )
        if self.completion_topic:
            channels.append(
                CommunicationChannel(
                    kind=CommunicationChannelKind.COMPLETION,
                    address=self.completion_topic,
                )
            )
        if self.escalation_topic:
            channels.append(
                CommunicationChannel(
                    kind=CommunicationChannelKind.ESCALATION,
                    address=self.escalation_topic,
                )
            )
        return CommunicationContract(channels=channels)


class CellHandoffPacket(BaseModel):
    """Typed point-to-point cell handoff that stays separate from broadcasts."""

    packet_id: str = Field(default_factory=lambda: _new_id("handoff"))
    created_at: str = Field(default_factory=_utc_now)
    trace: SignalTrace = Field(default_factory=SignalTrace)
    sender: CellAddress
    recipient: CellAddress
    task: HandoffTask
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    output_contract: OutputContract = Field(default_factory=OutputContract)
    budget_limits: CellBudgetLimits = Field(default_factory=CellBudgetLimits)
    authority_limits: CellAuthorityLimits = Field(default_factory=CellAuthorityLimits)
    continuation: ContinuationPayload | None = None
    continuation_hooks: ContinuationHooks = Field(default_factory=ContinuationHooks)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def seeded_continuation(self) -> ContinuationPayload | None:
        if self.continuation is None and not self.evidence_refs:
            return None

        continuation = (
            self.continuation.model_copy(deep=True)
            if self.continuation is not None
            else ContinuationPayload(stage=AcquisitionStage.ACT)
        )
        selections_by_ref = {selection.ref_id: selection for selection in continuation.selections}
        expanded_by_ref = {
            context.ref_id: context for context in continuation.expanded_context if context.ref_id
        }
        for ref in self.evidence_refs:
            selections_by_ref.setdefault(ref.ref_id, ref.as_selection())
            expanded_by_ref.setdefault(ref.ref_id, ref.as_expanded_context())

        continuation.stage = AcquisitionStage.ACT
        continuation.selections = list(selections_by_ref.values())
        continuation.expanded_context = list(expanded_by_ref.values())
        continuation.task_state = {
            **continuation.task_state,
            "handoff_packet_id": self.packet_id,
            "sender_cell_id": self.sender.cell_id,
            "recipient_cell_id": self.recipient.cell_id,
        }
        return continuation

    def to_execution_request(self) -> ExecutionRequest:
        acquisition_policy = self.budget_limits.as_acquisition_policy()
        acquisition = AcquisitionRequest(
            selections=[ref.as_selection() for ref in self.evidence_refs],
            policy=acquisition_policy,
        )
        memory = MemoryRequest(
            allowed_write_scopes=list(self.authority_limits.allow_memory_write_scopes),
        )
        tooling = ToolUseContract(
            max_tool_calls=self.budget_limits.max_tool_calls,
        )
        communication = self.continuation_hooks.as_communication_contract()
        return ExecutionRequest.from_harness(
            task=self.task.instruction,
            constraints=ConstraintSet(
                scope=self.task.scope,
                hard_constraints=list(self.task.hard_constraints),
                soft_constraints=list(self.task.soft_constraints),
            ),
            evidence=[ref.as_evidence_block() for ref in self.evidence_refs],
            acquisition=acquisition,
            tooling=tooling,
            memory=memory,
            communication=communication,
            continuation=self.seeded_continuation(),
            output_contract=self.output_contract,
            input_payload=dict(self.task.input_payload),
            metadata={
                "handoff_packet_id": self.packet_id,
                "trace_id": self.trace.trace_id,
                "root_task_id": self.trace.root_task_id or self.task.task_id,
                "sender": self.sender.model_dump(mode="json", exclude_none=True),
                "recipient": self.recipient.model_dump(mode="json", exclude_none=True),
                "budget_limits": self.budget_limits.model_dump(mode="json", exclude_none=True),
                "authority_limits": self.authority_limits.model_dump(mode="json", exclude_none=True),
                "continuation_hooks": self.continuation_hooks.model_dump(mode="json", exclude_none=True),
                **self.metadata,
            },
        )


class SupervisorySignalBase(BaseModel):
    """Base class for organism-level broadcast and event signals."""

    signal_id: str = Field(default_factory=lambda: _new_id("signal"))
    emitted_at: str = Field(default_factory=_utc_now)
    signal_type: str
    trace: SignalTrace
    source: CellAddress
    topic: str
    broadcast_scope: BroadcastScope = BroadcastScope.SUPERVISORS
    related_packet_id: str | None = None
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class StatusSignal(SupervisorySignalBase):
    """Low-cost progress signal for organism supervision."""

    signal_type: Literal["status"] = "status"
    status: Literal["accepted", "running", "waiting", "blocked"]
    progress: float | None = Field(default=None, ge=0.0, le=1.0)


class WarningSignal(SupervisorySignalBase):
    """Recoverable issue surfaced to supervisors."""

    signal_type: Literal["warning"] = "warning"
    code: str
    detail: str = ""


class FailureSignal(SupervisorySignalBase):
    """Non-recoverable or terminal issue surfaced to supervisors."""

    signal_type: Literal["failure"] = "failure"
    error: str
    retryable: bool = False


class BudgetPressureSignal(SupervisorySignalBase):
    """Signal that a cell is nearing one of its explicit budgets."""

    signal_type: Literal["budget_pressure"] = "budget_pressure"
    pressure_sources: list[str] = Field(default_factory=list)
    remaining: dict[str, Any] = Field(default_factory=dict)
    budget_limits: CellBudgetLimits = Field(default_factory=CellBudgetLimits)


class CompletionSignal(SupervisorySignalBase):
    """Terminal completion signal with compact outputs or output refs."""

    signal_type: Literal["completed"] = "completed"
    completion_status: Literal["completed", "partial"] = "completed"
    result_summary: str = ""
    outputs: dict[str, Any] = Field(default_factory=dict)
    output_refs: list[EvidenceRef] = Field(default_factory=list)

    def as_evidence_ref(
        self,
        *,
        ref_id: str | None = None,
        label: str | None = None,
        source_id: str = "cell-completion",
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceRef:
        summary = self.result_summary or self.summary or _compact_text(self.outputs)
        return EvidenceRef(
            ref_id=ref_id or f"{self.signal_id}:output",
            label=label or self.summary or "Completed cell output",
            summary=summary,
            trust_label=TrustLabel.RETRIEVED,
            source=self.source.cell_id,
            source_id=source_id,
            family="cell_completion",
            metadata={
                "completion_signal_id": self.signal_id,
                "related_packet_id": self.related_packet_id,
                **dict(metadata or {}),
            },
        )


class EscalationSignal(SupervisorySignalBase):
    """Signal that asks a broader supervisory layer to intervene."""

    signal_type: Literal["escalated"] = "escalated"
    reason: str
    requested_action: str = ""


SupervisorySignal = Annotated[
    StatusSignal
    | WarningSignal
    | FailureSignal
    | BudgetPressureSignal
    | CompletionSignal
    | EscalationSignal,
    Field(discriminator="signal_type"),
]


def summarize_outputs(outputs: dict[str, Any]) -> str:
    """Return a short inspectable summary for completion signals."""

    if "result" in outputs:
        return _compact_text(outputs["result"])
    if "text" in outputs:
        return _compact_text(outputs["text"])
    return _compact_text(outputs)


__all__ = [
    "BroadcastScope",
    "BudgetPressureSignal",
    "CellAddress",
    "CellAuthorityLimits",
    "CellBudgetLimits",
    "CellHandoffPacket",
    "CompletionSignal",
    "ContinuationHooks",
    "EscalationSignal",
    "EvidenceRef",
    "FailureSignal",
    "HandoffTask",
    "SignalTrace",
    "StatusSignal",
    "SupervisorySignal",
    "SupervisorySignalBase",
    "WarningSignal",
    "summarize_outputs",
]
