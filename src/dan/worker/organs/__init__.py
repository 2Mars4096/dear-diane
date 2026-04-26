"""Bounded organ patterns built from tissues and typed cell handoffs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from dan.worker.composition import (
    CrossCellTraceLog,
    HandoffExecution,
    execute_cell_handoff,
    make_completion_signal,
    make_escalation_signal,
    make_status_signal,
)
from dan.worker.core.contracts import OutputContract
from dan.worker.core.executor import WorkerCoreExecutor, WorkerExecutionResult
from dan.worker.core.model import WorkerDefinition
from dan.worker.signaling import (
    CellAddress,
    CellAuthorityLimits,
    CellBudgetLimits,
    CellHandoffPacket,
    CompletionSignal,
    ContinuationHooks,
    EvidenceRef,
    HandoffTask,
    SignalTrace,
    SupervisorySignalBase,
)
from dan.worker.structured_payload import parse_jsonish_payload
from dan.worker.tissue import (
    TissueExecution,
    TissueMember,
    TissuePattern,
    TissuePoolLimits,
    execute_tissue_pattern,
    parallel_worker_pool,
    retrieval_enrichment_pool,
    review_quorum_pool,
)

MAX_DEEP_RESEARCH_READERS = 8
_DEEP_RESEARCH_READER_SUFFIXES: list[tuple[str, str]] = [
    (
        "reader-a",
        "Focus on the core defect, the strongest supporting evidence, and the exact "
        "evidence refs or source identifiers that best ground the conclusion.",
    ),
    (
        "reader-b",
        "Focus on acceptance criteria, contradictions, risks, missing evidence, and "
        "any open questions that lower confidence.",
    ),
    (
        "reader-c",
        "Focus on the most relevant repo implementation surface, affected modules, "
        "and concrete evidence that narrows where the change should land.",
    ),
    (
        "reader-d",
        "Focus on validation/test evidence, expected checks, and what would count as "
        "real proof that the change works.",
    ),
    (
        "reader-e",
        "Focus on external or adjacent evidence available through read-only tools, "
        "including current docs, standards, or web-grounded constraints.",
    ),
    (
        "reader-f",
        "Focus on alternative explanations, weaker interpretations of the evidence, "
        "and places where the current story may be over-claiming.",
    ),
    (
        "reader-g",
        "Focus on delivery shape, downstream operator needs, and the minimum bounded "
        "change recommendation that still closes the request honestly.",
    ),
    (
        "reader-h",
        "Focus on residual risk, unresolved ambiguity, and what evidence would most "
        "increase confidence if another pass were needed.",
    ),
]
_DEEP_RESEARCH_READER_BASE_INSTRUCTION = (
    "Extract grounded findings from the provided evidence and any available "
    "read-only tools. Keep the output anchored to concrete evidence. Before using "
    "tools, inspect any temporal_mode, temporal_anchor, temporal_window, or "
    "temporal_guidance in the input and keep relative-time language consistent "
    "with that frame. When live external facts matter and `web_search` is available, "
    "prefer one targeted search query as the first step. Every `web_search` call must "
    "include exactly one concrete `query` string or one concrete `url`; never emit an "
    "empty `{}` tool call. If you still cannot form a concrete search, do not call a "
    "tool yet; instead return compact `follow_up_queries` that name the missing search "
    "terms. Return only one compact evidence note for your lane; do not attempt the "
    "full final report or a final recommendation."
)


def _deep_research_failure_budget(reader_count: int) -> int:
    if reader_count <= 1:
        return 0
    if reader_count <= 5:
        return 1
    return 2


def _deep_research_reader_output_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Return one compact evidence note for this reader lane. Use a few "
            "targeted tool calls when needed, then stop and summarize only the "
            "grounded findings you actually gathered."
        ),
        expected_return_shape=json.dumps(
            {
                "findings": ["<required>"],
                "evidence_refs": ["<required>"],
                "contradictions": [],
                "open_questions": [],
                "reasoning_notes": [],
                "follow_up_queries": [],
            },
            sort_keys=True,
        ),
        output_schema={
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "evidence_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "contradictions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "open_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "reasoning_notes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "follow_up_queries": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "findings",
                "evidence_refs",
                "contradictions",
                "open_questions",
            ],
            "additionalProperties": False,
        },
    )


def _compact_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except Exception:
        return str(value).strip()


def _parse_structured_payload(outputs: dict[str, Any]) -> dict[str, Any]:
    raw = outputs.get("result", outputs.get("text", outputs))
    if isinstance(raw, dict):
        return dict(raw)
    parsed = parse_jsonish_payload(raw)
    if isinstance(parsed, dict):
        return dict(parsed)
    return {"result": parsed}


def resolve_deep_research_reader_briefs(
    *,
    reader_count: int,
    reader_briefs: list[str] | tuple[str, ...] | None = None,
) -> list[tuple[str, str]]:
    """Return normalized reader ids plus task-facing briefs for the research tissue."""

    normalized_briefs = [
        str(brief).strip()
        for brief in list(reader_briefs or [])
        if str(brief).strip()
    ]
    if reader_count < 1 or reader_count > MAX_DEEP_RESEARCH_READERS:
        raise ValueError(
            f"reader_count must be between 1 and {MAX_DEEP_RESEARCH_READERS}"
        )

    resolved: list[tuple[str, str]] = []
    for index, (member_id, fallback_brief) in enumerate(
        _DEEP_RESEARCH_READER_SUFFIXES[:reader_count]
    ):
        brief = (
            normalized_briefs[index]
            if index < len(normalized_briefs)
            else fallback_brief
        )
        resolved.append((member_id, brief))
    return resolved


class OrganPatternKind(str, Enum):
    """First bounded organ roles used by the 52 substrate."""

    DEEP_RESEARCH = "deep_research"
    UNIVERSAL_VALIDATOR = "universal_validator"
    CODING_BUILD = "coding_build"
    CODING_AGGREGATION = "coding_aggregation"
    SYNTHESIS = "synthesis"


class OrganCellStage(str, Enum):
    """Inspectable internal organ stages."""

    BOUNDARY = "boundary"
    TISSUE = "tissue"
    LEAD = "lead"


class OrganBoundaryContract(BaseModel):
    """Strict organ membrane for public input/output keys."""

    required_input_keys: list[str] = Field(default_factory=list)
    required_output_keys: list[str] = Field(default_factory=list)
    allow_additional_input_keys: bool = True
    allow_additional_output_keys: bool = False

    def input_errors(self, payload: dict[str, Any]) -> list[str]:
        errors = [
            f"missing_input:{key}"
            for key in self.required_input_keys
            if key not in payload
        ]
        if not self.allow_additional_input_keys:
            errors.extend(
                f"unexpected_input:{key}"
                for key in sorted(set(payload) - set(self.required_input_keys))
            )
        return errors

    def output_errors(self, payload: dict[str, Any]) -> list[str]:
        errors = [
            f"missing_output:{key}"
            for key in self.required_output_keys
            if key not in payload
        ]
        if not self.allow_additional_output_keys:
            errors.extend(
                f"unexpected_output:{key}"
                for key in sorted(set(payload) - set(self.required_output_keys))
            )
        return errors


class OrganEscalationSurface(BaseModel):
    """Stable escalation codes surfaced by a bounded organ."""

    input_contract_reason: str = "organ_input_contract_failed"
    tissue_failure_reason: str = "organ_tissue_failed"
    lead_failure_reason: str = "organ_lead_failed"
    output_contract_reason: str = "organ_output_contract_failed"


class OrganPattern(BaseModel):
    """Reusable mixed-role organ built from one tissue plus one lead cell."""

    organ_id: str
    kind: OrganPatternKind
    boundary_address: CellAddress
    lead_address: CellAddress
    lead_worker: WorkerDefinition
    lead_instruction: str
    tissue: TissuePattern | None = None
    tissue_coordinator: CellAddress | None = None
    boundary_contract: OrganBoundaryContract = Field(default_factory=OrganBoundaryContract)
    escalation_surface: OrganEscalationSurface = Field(default_factory=OrganEscalationSurface)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_pattern(self) -> "OrganPattern":
        if self.tissue is not None and self.tissue_coordinator is None:
            raise ValueError("Organ patterns with a tissue require a tissue_coordinator")
        return self


class OrganExecutionResult(BaseModel):
    """Normalized result returned by a bounded organ."""

    status: Literal["completed", "escalated", "failed"]
    outputs: dict[str, Any] = Field(default_factory=dict)
    output_refs: list[EvidenceRef] = Field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class OrganExecution:
    """Recorded outcome of one organ-level boundary run."""

    pattern: OrganPattern
    packet: CellHandoffPacket
    result: OrganExecutionResult
    tissue_packet: CellHandoffPacket | None = None
    tissue_execution: TissueExecution | None = None
    lead_packet: CellHandoffPacket | None = None
    lead_execution: HandoffExecution | None = None
    signals: list[SupervisorySignalBase] = field(default_factory=list)


def _hooks_for_child(packet: CellHandoffPacket) -> ContinuationHooks:
    hooks = packet.continuation_hooks.model_copy(deep=True)
    hooks.reply_to_cell_id = packet.recipient.cell_id
    hooks.resume_from_packet_id = packet.packet_id
    return hooks


def _child_trace(
    packet: CellHandoffPacket,
    *,
    parent_signal_id: str,
    lineage: str,
) -> SignalTrace:
    return SignalTrace(
        trace_id=packet.trace.trace_id,
        root_task_id=packet.trace.root_task_id or packet.task.task_id,
        parent_packet_id=packet.packet_id,
        parent_signal_id=parent_signal_id,
        lineage=[*packet.trace.lineage, lineage],
    )


def _organ_output_ref(
    *,
    packet: CellHandoffPacket,
    pattern: OrganPattern,
    payload: dict[str, Any],
) -> EvidenceRef:
    return EvidenceRef(
        ref_id=f"{packet.packet_id}:organ-output",
        label=f"{pattern.organ_id} output",
        summary=_compact_value(payload),
        source=pattern.lead_address.cell_id,
        source_id="organ-output",
        family="organ_output",
        metadata={
            "organ_id": pattern.organ_id,
            "organ_kind": pattern.kind.value,
            "boundary_packet_id": packet.packet_id,
        },
    )


def build_organ_lead_packet(
    *,
    packet: CellHandoffPacket,
    pattern: OrganPattern,
    parent_signal_id: str,
    evidence_refs: list[EvidenceRef],
    input_payload: dict[str, Any],
) -> CellHandoffPacket:
    """Derive the lead-cell handoff from the organ boundary packet."""

    return CellHandoffPacket(
        trace=_child_trace(
            packet,
            parent_signal_id=parent_signal_id,
            lineage=f"organ:{pattern.organ_id}:lead",
        ),
        sender=packet.recipient.model_copy(deep=True),
        recipient=pattern.lead_address.model_copy(deep=True),
        task=HandoffTask(
            task_id=f"{packet.task.task_id}:lead",
            instruction=pattern.lead_instruction,
            scope=packet.task.scope,
            hard_constraints=list(packet.task.hard_constraints),
            soft_constraints=list(packet.task.soft_constraints),
            input_payload=dict(input_payload),
        ),
        evidence_refs=[ref.model_copy(deep=True) for ref in evidence_refs],
        output_contract=packet.output_contract.model_copy(deep=True),
        budget_limits=packet.budget_limits.model_copy(deep=True),
        authority_limits=packet.authority_limits.model_copy(deep=True),
        continuation=packet.continuation.model_copy(deep=True) if packet.continuation is not None else None,
        continuation_hooks=_hooks_for_child(packet),
        metadata={
            **dict(packet.metadata),
            "organ_id": pattern.organ_id,
            "organ_kind": pattern.kind.value,
            "organ_stage": OrganCellStage.LEAD.value,
        },
    )


def _build_tissue_packet(
    *,
    packet: CellHandoffPacket,
    pattern: OrganPattern,
    parent_signal_id: str,
) -> CellHandoffPacket:
    assert pattern.tissue_coordinator is not None
    return CellHandoffPacket(
        trace=_child_trace(
            packet,
            parent_signal_id=parent_signal_id,
            lineage=f"organ:{pattern.organ_id}:tissue",
        ),
        sender=packet.recipient.model_copy(deep=True),
        recipient=pattern.tissue_coordinator.model_copy(deep=True),
        task=HandoffTask(
            task_id=f"{packet.task.task_id}:tissue",
            instruction=packet.task.instruction,
            scope=packet.task.scope,
            hard_constraints=list(packet.task.hard_constraints),
            soft_constraints=list(packet.task.soft_constraints),
            input_payload=dict(packet.task.input_payload),
        ),
        evidence_refs=[ref.model_copy(deep=True) for ref in packet.evidence_refs],
        output_contract=packet.output_contract.model_copy(deep=True),
        budget_limits=packet.budget_limits.model_copy(deep=True),
        authority_limits=packet.authority_limits.model_copy(deep=True),
        continuation=packet.continuation.model_copy(deep=True) if packet.continuation is not None else None,
        continuation_hooks=_hooks_for_child(packet),
        metadata={
            **dict(packet.metadata),
            "organ_id": pattern.organ_id,
            "organ_kind": pattern.kind.value,
            "organ_stage": OrganCellStage.TISSUE.value,
        },
    )


def _lead_input_payload(
    packet: CellHandoffPacket,
    tissue_execution: TissueExecution | None,
) -> dict[str, Any]:
    if tissue_execution is None:
        return dict(packet.task.input_payload)
    return {
        **dict(packet.task.input_payload),
        "tissue_result": tissue_execution.result.outputs,
    }


async def execute_organ_pattern(
    *,
    executor: WorkerCoreExecutor,
    pattern: OrganPattern,
    packet: CellHandoffPacket,
    trace_log: CrossCellTraceLog | None = None,
) -> OrganExecution:
    """Run one bounded organ through its membrane, tissue, and lead cell."""

    if trace_log is not None:
        trace_log.record_handoff(packet)

    accepted = make_status_signal(
        packet,
        status="accepted",
        summary=f"{packet.recipient.cell_id} accepted organ {pattern.organ_id}",
    )
    signals: list[SupervisorySignalBase] = [accepted]
    if trace_log is not None:
        trace_log.record_signal(accepted)

    input_errors = pattern.boundary_contract.input_errors(packet.task.input_payload)
    if input_errors:
        escalation = make_escalation_signal(
            packet,
            reason=pattern.escalation_surface.input_contract_reason,
            requested_action="Repair the organ input payload before retrying.",
            summary=f"{packet.recipient.cell_id} rejected the organ input contract",
            metadata={"contract_errors": input_errors, "organ_id": pattern.organ_id},
        )
        signals.append(escalation)
        if trace_log is not None:
            trace_log.record_signal(escalation)
        return OrganExecution(
            pattern=pattern,
            packet=packet,
            result=OrganExecutionResult(
                status="escalated",
                error="Organ input contract failed.",
                metadata={"contract_errors": input_errors},
            ),
            signals=signals,
        )

    tissue_packet: CellHandoffPacket | None = None
    tissue_execution: TissueExecution | None = None
    evidence_refs = [ref.model_copy(deep=True) for ref in packet.evidence_refs]
    parent_signal_id = accepted.signal_id
    if pattern.tissue is not None:
        tissue_packet = _build_tissue_packet(
            packet=packet,
            pattern=pattern,
            parent_signal_id=accepted.signal_id,
        )
        tissue_execution = await execute_tissue_pattern(
            executor=executor,
            pattern=pattern.tissue,
            packet=tissue_packet,
            trace_log=trace_log,
        )
        parent_signal_id = tissue_execution.signals[-1].signal_id
        if tissue_execution.result.status != "completed":
            escalation = make_escalation_signal(
                packet,
                reason=pattern.escalation_surface.tissue_failure_reason,
                requested_action="Inspect the organ tissue result and retry with a smaller or repaired pool.",
                summary=f"{packet.recipient.cell_id} could not complete organ tissue work",
                metadata={
                    "organ_id": pattern.organ_id,
                    "tissue_status": tissue_execution.result.status,
                    "tissue_error": tissue_execution.result.error,
                },
            )
            signals.append(escalation)
            if trace_log is not None:
                trace_log.record_signal(escalation)
            return OrganExecution(
                pattern=pattern,
                packet=packet,
                result=OrganExecutionResult(
                    status="escalated",
                    error=tissue_execution.result.error or "Organ tissue execution failed.",
                    metadata={
                        "tissue_status": tissue_execution.result.status,
                        "tissue_error": tissue_execution.result.error,
                    },
                ),
                tissue_packet=tissue_packet,
                tissue_execution=tissue_execution,
                signals=signals,
            )
        terminal_tissue_signal = tissue_execution.signals[-1]
        if isinstance(terminal_tissue_signal, CompletionSignal):
            evidence_refs = [ref.model_copy(deep=True) for ref in terminal_tissue_signal.output_refs] or evidence_refs

    lead_packet = build_organ_lead_packet(
        packet=packet,
        pattern=pattern,
        parent_signal_id=parent_signal_id,
        evidence_refs=evidence_refs,
        input_payload=_lead_input_payload(packet, tissue_execution),
    )
    lead_execution = await execute_cell_handoff(
        executor=executor,
        worker=pattern.lead_worker,
        packet=lead_packet,
        trace_log=trace_log,
    )
    if lead_execution.result.status != "completed":
        escalation = make_escalation_signal(
            packet,
            reason=pattern.escalation_surface.lead_failure_reason,
            requested_action="Inspect the organ lead-cell failure and retry once the internal organ work is repaired.",
            summary=f"{packet.recipient.cell_id} failed in organ lead synthesis",
            metadata={
                "organ_id": pattern.organ_id,
                "lead_error": lead_execution.result.error,
            },
        )
        signals.append(escalation)
        if trace_log is not None:
            trace_log.record_signal(escalation)
        return OrganExecution(
            pattern=pattern,
            packet=packet,
            result=OrganExecutionResult(
                status="failed",
                error=lead_execution.result.error or "Organ lead synthesis failed.",
            ),
            tissue_packet=tissue_packet,
            tissue_execution=tissue_execution,
            lead_packet=lead_packet,
            lead_execution=lead_execution,
            signals=signals,
        )

    payload = _parse_structured_payload(lead_execution.result.outputs)
    output_errors = pattern.boundary_contract.output_errors(payload)
    if output_errors:
        escalation = make_escalation_signal(
            packet,
            reason=pattern.escalation_surface.output_contract_reason,
            requested_action="Repair the organ output schema before treating the result as public.",
            summary=f"{packet.recipient.cell_id} blocked organ output leakage",
            metadata={
                "organ_id": pattern.organ_id,
                "contract_errors": output_errors,
                "raw_outputs": dict(lead_execution.result.outputs),
            },
        )
        signals.append(escalation)
        if trace_log is not None:
            trace_log.record_signal(escalation)
        return OrganExecution(
            pattern=pattern,
            packet=packet,
            result=OrganExecutionResult(
                status="escalated",
                outputs=dict(payload),
                error="Organ output contract failed.",
                metadata={
                    "contract_errors": output_errors,
                    "raw_outputs": dict(lead_execution.result.outputs),
                },
            ),
            tissue_packet=tissue_packet,
            tissue_execution=tissue_execution,
            lead_packet=lead_packet,
            lead_execution=lead_execution,
            signals=signals,
        )

    output_ref = _organ_output_ref(packet=packet, pattern=pattern, payload=payload)
    completion = make_completion_signal(
        packet,
        WorkerExecutionResult(
            status="completed",
            outputs=payload,
            metadata={
                "organ_id": pattern.organ_id,
                "organ_kind": pattern.kind.value,
            },
        ),
        summary=f"{packet.recipient.cell_id} completed organ {pattern.organ_id}",
        output_refs=[output_ref],
        metadata={
            "organ_id": pattern.organ_id,
            "organ_kind": pattern.kind.value,
            "output_keys": sorted(payload),
        },
    )
    signals.append(completion)
    if trace_log is not None:
        trace_log.record_signal(completion)
    return OrganExecution(
        pattern=pattern,
        packet=packet,
        result=OrganExecutionResult(
            status="completed",
            outputs=payload,
            output_refs=[output_ref],
            metadata={
                "organ_id": pattern.organ_id,
                "organ_kind": pattern.kind.value,
            },
        ),
        tissue_packet=tissue_packet,
        tissue_execution=tissue_execution,
        lead_packet=lead_packet,
        lead_execution=lead_execution,
        signals=signals,
    )


def _member(
    *,
    member_id: str,
    cell_id: str,
    tissue_id: str,
    organ_id: str,
    organism_id: str,
    role: str,
    instruction: str,
    model: str | None,
    instruction_suffix: str = "",
    input_payload_overrides: dict[str, Any] | None = None,
    output_contract_override: OutputContract | None = None,
    metadata: dict[str, Any] | None = None,
) -> TissueMember:
    return TissueMember(
        member_id=member_id,
        address=CellAddress(
            cell_id=cell_id,
            tissue_id=tissue_id,
            organ_id=organ_id,
            organism_id=organism_id,
        ),
        worker=WorkerDefinition(
            id=cell_id,
            role=role,
            instruction=instruction,
            model=model,
        ),
        instruction_suffix=instruction_suffix,
        input_payload_overrides=dict(input_payload_overrides or {}),
        output_contract_override=output_contract_override.model_copy(deep=True)
        if output_contract_override is not None
        else None,
        metadata=dict(metadata or {}),
    )


def deep_research_organ(
    *,
    organism_id: str,
    model: str | None = None,
    organ_id: str = "deep-research",
    reader_count: int = MAX_DEEP_RESEARCH_READERS,
    reader_briefs: list[str] | tuple[str, ...] | None = None,
) -> OrganPattern:
    """Preset for a bounded deep-research organ."""

    if reader_count < 1 or reader_count > MAX_DEEP_RESEARCH_READERS:
        raise ValueError(
            f"deep_research_organ reader_count must be between 1 and {MAX_DEEP_RESEARCH_READERS}"
        )

    tissue_id = f"{organ_id}.retrieval"
    coordinator = CellAddress(
        cell_id=f"{tissue_id}.coordinator",
        tissue_id=tissue_id,
        organ_id=organ_id,
        organism_id=organism_id,
    )
    resolved_reader_briefs = resolve_deep_research_reader_briefs(
        reader_count=reader_count,
        reader_briefs=reader_briefs,
    )
    reader_output_contract = _deep_research_reader_output_contract()
    members = [
        _member(
            member_id=member_id,
            cell_id=f"{organ_id}.{member_id}",
            tissue_id=tissue_id,
            organ_id=organ_id,
            organism_id=organism_id,
            role="research_reader",
            instruction=_DEEP_RESEARCH_READER_BASE_INSTRUCTION,
            model=model,
            instruction_suffix=brief,
            input_payload_overrides={"reader_brief": brief},
            output_contract_override=reader_output_contract,
            metadata={"reader_brief": brief},
        )
        for member_id, brief in resolved_reader_briefs
    ]
    tissue = retrieval_enrichment_pool(
        f"{organ_id}.retrieval-pool",
        members=members,
        limits=TissuePoolLimits(
            max_members=reader_count,
            max_concurrency=min(reader_count, MAX_DEEP_RESEARCH_READERS),
            max_failures=_deep_research_failure_budget(reader_count),
        ),
        metadata={"organ_id": organ_id},
    )
    return OrganPattern(
        organ_id=organ_id,
        kind=OrganPatternKind.DEEP_RESEARCH,
        boundary_address=CellAddress(
            cell_id=f"{organ_id}.boundary",
            organ_id=organ_id,
            organism_id=organism_id,
        ),
        lead_address=CellAddress(
            cell_id=f"{organ_id}.lead",
            organ_id=organ_id,
            organism_id=organism_id,
        ),
        lead_worker=WorkerDefinition(
            id=f"{organ_id}.lead",
            role="research_synthesizer",
            instruction=(
                "Synthesize grounded findings into a bounded research report that includes "
                "findings, evidence summary, explicit evidence refs, contradictions, open "
                "questions, a verification appendix for critical facts, an audit appendix "
                "for unresolved logic/freshness/authority/scope-fit issues, a report "
                "readiness label, standard quality_gates for time anchoring, scope boundary, "
                "source authority, numeric reconciliation, claim-object fit, and final status, "
                "confidence, and a recommended change shape. Respect any temporal frame in "
                "the input and anchor relative-time claims to that frame instead of drifting "
                "between historical and current context. Do not mark the report actionable "
                "when critical facts remain unverified or conflicted, when material audit issues "
                "remain unresolved, or when a standard quality gate is missing or failed."
            ),
            model=model,
        ),
        lead_instruction=(
            "Synthesize the tissue output into a bounded research report with grounded findings, "
            "evidence refs, contradictions, a verification appendix for critical facts, an "
            "audit appendix for reasoning and sourcing gaps, an explicit readiness label, "
            "standard quality gates, confidence, and a recommended change. Respect the "
            "input temporal frame before grounding time-sensitive claims."
        ),
        tissue=tissue,
        tissue_coordinator=coordinator,
        boundary_contract=OrganBoundaryContract(
            required_input_keys=["objective", "acceptance_criteria", "delivery_target"],
            required_output_keys=[
                "findings",
                "evidence_summary",
                "evidence_refs",
                "contradictions",
                "open_questions",
                "verification_facts",
                "audit_issues",
                "quality_gates",
                "report_readiness",
                "readiness_note",
                "confidence",
                "recommended_change",
            ],
        ),
        metadata={
            "promotion_path": "Later meta-workflow builder promotion stays gated behind 45-4 and consumes this organ's grounded findings rather than replacing the organ membrane.",
            "research_contract_version": "v3",
            "reader_count": reader_count,
            "max_reader_count": MAX_DEEP_RESEARCH_READERS,
            "failure_budget": _deep_research_failure_budget(reader_count),
            "reader_briefs": [brief for _member_id, brief in resolved_reader_briefs],
        },
    )


def universal_validator_organ(
    *,
    organism_id: str,
    model: str | None = None,
    organ_id: str = "universal-validator",
    review_tie_break_priority: list[str] | None = None,
) -> OrganPattern:
    """Preset for a bounded universal validator organ."""

    tissue_id = f"{organ_id}.review"
    coordinator = CellAddress(
        cell_id=f"{tissue_id}.coordinator",
        tissue_id=tissue_id,
        organ_id=organ_id,
        organism_id=organism_id,
    )
    tissue = review_quorum_pool(
        f"{organ_id}.review-pool",
        members=[
            _member(
                member_id="reviewer-a",
                cell_id=f"{organ_id}.reviewer-a",
                tissue_id=tissue_id,
                organ_id=organ_id,
                organism_id=organism_id,
                role="validator_reviewer",
                instruction="Return a one-word verdict for the candidate: pass or repair. When uncertain, choose repair.",
                model=model,
            ),
            _member(
                member_id="reviewer-b",
                cell_id=f"{organ_id}.reviewer-b",
                tissue_id=tissue_id,
                organ_id=organ_id,
                organism_id=organism_id,
                role="validator_reviewer",
                instruction="Return a one-word verdict for the candidate: pass or repair. When uncertain, choose repair.",
                model=model,
            ),
        ],
        required_agreement=2,
        tie_break_priority=list(review_tie_break_priority or ["repair", "pass"]),
        limits=TissuePoolLimits(max_members=2, max_concurrency=2, max_failures=0),
        metadata={"organ_id": organ_id},
    )
    return OrganPattern(
        organ_id=organ_id,
        kind=OrganPatternKind.UNIVERSAL_VALIDATOR,
        boundary_address=CellAddress(
            cell_id=f"{organ_id}.boundary",
            organ_id=organ_id,
            organism_id=organism_id,
        ),
        lead_address=CellAddress(
            cell_id=f"{organ_id}.lead",
            organ_id=organ_id,
            organism_id=organism_id,
        ),
        lead_worker=WorkerDefinition(
            id=f"{organ_id}.lead",
            role="validator_synthesizer",
            instruction="Score the candidate, explain missing requirements, and produce a bounded repair brief.",
            model=model,
        ),
        lead_instruction="Turn the review tissue output into a universal validation report with scores, missing requirements, and a repair brief.",
        tissue=tissue,
        tissue_coordinator=coordinator,
        boundary_contract=OrganBoundaryContract(
            required_input_keys=["candidate", "acceptance_criteria", "quality_bar", "comparison_context"],
            required_output_keys=[
                "passed",
                "overall_score",
                "dimension_scores",
                "repair_brief",
                "missing_requirements",
                "comparison_note",
            ],
            allow_additional_output_keys=True,
        ),
        metadata={
            "score_report_schema": "The validator emits evaluation fields rather than direct task output.",
            "review_tie_break_priority": list(review_tie_break_priority or ["repair", "pass"]),
        },
    )


def compact_universal_validator_organ(
    *,
    organism_id: str,
    model: str | None = None,
    organ_id: str = "universal-validator",
) -> OrganPattern:
    """Preset for a one-lead validator after deterministic prechecks have run."""

    pattern = universal_validator_organ(
        organism_id=organism_id,
        model=model,
        organ_id=organ_id,
    )
    return pattern.model_copy(
        update={
            "tissue": None,
            "tissue_coordinator": None,
            "lead_instruction": (
                "Use the deterministic_precheck payload first, then perform one compact "
                "model review of unresolved quality risks. Return the universal validation "
                "report with scores, missing requirements, and a bounded repair brief."
            ),
            "metadata": {
                **dict(pattern.metadata),
                "validation_mode": "compact_model_review",
                "deterministic_precheck_required": True,
            },
        }
    )


def coding_build_organ(
    *,
    organism_id: str,
    model: str | None = None,
    organ_id: str = "coding-build",
) -> OrganPattern:
    """Preset for a bounded coding/build organ."""

    tissue_id = f"{organ_id}.variants"
    coordinator = CellAddress(
        cell_id=f"{tissue_id}.coordinator",
        tissue_id=tissue_id,
        organ_id=organ_id,
        organism_id=organism_id,
    )
    tissue = parallel_worker_pool(
        f"{organ_id}.variant-pool",
        members=[
            _member(
                member_id="builder-a",
                cell_id=f"{organ_id}.builder-a",
                tissue_id=tissue_id,
                organ_id=organ_id,
                organism_id=organism_id,
                role="builder_variant",
                instruction="Draft one bounded candidate change plan.",
                model=model,
                instruction_suffix="Prefer the smallest viable fix.",
            ),
            _member(
                member_id="builder-b",
                cell_id=f"{organ_id}.builder-b",
                tissue_id=tissue_id,
                organ_id=organ_id,
                organism_id=organism_id,
                role="builder_variant",
                instruction="Draft one bounded candidate change plan.",
                model=model,
                instruction_suffix="Prefer the candidate with the clearest test strategy.",
            ),
        ],
        limits=TissuePoolLimits(max_members=2, max_concurrency=2, max_failures=0),
        metadata={"organ_id": organ_id},
    )
    return OrganPattern(
        organ_id=organ_id,
        kind=OrganPatternKind.CODING_BUILD,
        boundary_address=CellAddress(
            cell_id=f"{organ_id}.boundary",
            organ_id=organ_id,
            organism_id=organism_id,
        ),
        lead_address=CellAddress(
            cell_id=f"{organ_id}.lead",
            organ_id=organ_id,
            organism_id=organism_id,
        ),
        lead_worker=WorkerDefinition(
            id=f"{organ_id}.lead",
            role="build_synthesizer",
            instruction="Choose the best candidate change plan and return a bounded build artifact.",
            model=model,
        ),
        lead_instruction="Choose or merge the internal build variants into one bounded candidate with target files, focused tests, and risks.",
        tissue=tissue,
        tissue_coordinator=coordinator,
        boundary_contract=OrganBoundaryContract(
            required_input_keys=["objective", "acceptance_criteria", "research_findings", "repair_brief"],
            required_output_keys=["candidate_id", "change_summary", "target_files", "test_plan", "risks"],
            allow_additional_output_keys=True,
        ),
    )


def coding_aggregation_organ(
    *,
    organism_id: str,
    model: str | None = None,
    organ_id: str = "coding-aggregation",
) -> OrganPattern:
    """Preset for a bounded coding aggregation organ."""

    return OrganPattern(
        organ_id=organ_id,
        kind=OrganPatternKind.CODING_AGGREGATION,
        boundary_address=CellAddress(
            cell_id=f"{organ_id}.boundary",
            organ_id=organ_id,
            organism_id=organism_id,
        ),
        lead_address=CellAddress(
            cell_id=f"{organ_id}.lead",
            organ_id=organ_id,
            organism_id=organism_id,
        ),
        lead_worker=WorkerDefinition(
            id=f"{organ_id}.lead",
            role="coding_aggregator",
            instruction=(
                "Merge worker outputs into one bounded coding candidate with explicit files, focused validation, "
                "and risks. This stage should materialize the chosen patch, not rediscover the repository. If "
                "worker results already identify the target file or edit, read only that concrete file if needed "
                "and then use `file_edit` or `file_write` directly. Prefer structured file tools for edits and "
                "avoid shell-based file creation when a direct file-writing tool is available."
            ),
            model=model,
        ),
        lead_instruction=(
            "Merge the worker outputs into one bounded candidate. Reuse the strongest "
            "parts, keep the change narrow, perform concrete edits with the most specific "
            "structured tools available, and return explicit target files, focused validation, "
            "and inspectable risks. Do not repeat broad repo discovery or git archaeology unless "
            "the worker outputs still leave the target file genuinely unknown. If the workspace "
            "already satisfies the brief and no mutation is needed, say that explicitly in the "
            "change summary instead of implying a fix."
        ),
        boundary_contract=OrganBoundaryContract(
            required_input_keys=[
                "objective",
                "acceptance_criteria",
                "worker_results",
                "orchestration_plan",
                "repair_brief",
            ],
            required_output_keys=[
                "candidate_id",
                "change_summary",
                "target_files",
                "test_plan",
                "risks",
            ],
            allow_additional_output_keys=True,
        ),
        metadata={
            "aggregation_role": "Keep parallel worker fan-out separate from final candidate selection and merge.",
        },
    )


def synthesis_organ(
    *,
    organism_id: str,
    model: str | None = None,
    organ_id: str = "synthesis",
) -> OrganPattern:
    """Preset for a bounded synthesis/reporting organ."""

    tissue_id = f"{organ_id}.report"
    coordinator = CellAddress(
        cell_id=f"{tissue_id}.coordinator",
        tissue_id=tissue_id,
        organ_id=organ_id,
        organism_id=organism_id,
    )
    tissue = parallel_worker_pool(
        f"{organ_id}.report-pool",
        members=[
            _member(
                member_id="reporter-a",
                cell_id=f"{organ_id}.reporter-a",
                tissue_id=tissue_id,
                organ_id=organ_id,
                organism_id=organism_id,
                role="report_fragment",
                instruction="Draft one final delivery fragment from the chosen candidate and validator report.",
                model=model,
            ),
            _member(
                member_id="reporter-b",
                cell_id=f"{organ_id}.reporter-b",
                tissue_id=tissue_id,
                organ_id=organ_id,
                organism_id=organism_id,
                role="report_fragment",
                instruction="Draft one final accountability fragment from the chosen candidate and validator report.",
                model=model,
            ),
        ],
        limits=TissuePoolLimits(max_members=2, max_concurrency=2, max_failures=0),
        metadata={"organ_id": organ_id},
    )
    return OrganPattern(
        organ_id=organ_id,
        kind=OrganPatternKind.SYNTHESIS,
        boundary_address=CellAddress(
            cell_id=f"{organ_id}.boundary",
            organ_id=organ_id,
            organism_id=organism_id,
        ),
        lead_address=CellAddress(
            cell_id=f"{organ_id}.lead",
            organ_id=organ_id,
            organism_id=organism_id,
        ),
        lead_worker=WorkerDefinition(
            id=f"{organ_id}.lead",
            role="report_synthesizer",
            instruction="Produce the final bounded delivery summary with accountability.",
            model=model,
        ),
        lead_instruction="Synthesize the chosen candidate, validation report, and accountability trail into one external delivery summary.",
        tissue=tissue,
        tissue_coordinator=coordinator,
        boundary_contract=OrganBoundaryContract(
            required_input_keys=[
                "objective",
                "selected_candidate",
                "validation_report",
                "research_findings",
                "repair_history",
            ],
            required_output_keys=[
                "delivery_summary",
                "final_candidate",
                "validation_summary",
                "accountability",
            ],
        ),
        metadata={
            "reporting_role": "Hide internal tissue chatter behind one coherent outward report.",
        },
    )


__all__ = [
    "MAX_DEEP_RESEARCH_READERS",
    "OrganBoundaryContract",
    "OrganCellStage",
    "OrganEscalationSurface",
    "OrganExecution",
    "OrganExecutionResult",
    "OrganPattern",
    "OrganPatternKind",
    "build_organ_lead_packet",
    "coding_aggregation_organ",
    "coding_build_organ",
    "compact_universal_validator_organ",
    "deep_research_organ",
    "execute_organ_pattern",
    "resolve_deep_research_reader_briefs",
    "synthesis_organ",
    "universal_validator_organ",
]
