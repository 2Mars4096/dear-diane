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
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {"result": raw}
        try:
            parsed = json.loads(text)
        except Exception:
            return {"result": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"result": parsed}
    return {"result": raw}


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
                error="Organ output contract failed.",
                metadata={"contract_errors": output_errors},
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
    )


def deep_research_organ(
    *,
    organism_id: str,
    model: str | None = None,
    organ_id: str = "deep-research",
) -> OrganPattern:
    """Preset for a bounded deep-research organ."""

    tissue_id = f"{organ_id}.retrieval"
    coordinator = CellAddress(
        cell_id=f"{tissue_id}.coordinator",
        tissue_id=tissue_id,
        organ_id=organ_id,
        organism_id=organism_id,
    )
    tissue = retrieval_enrichment_pool(
        f"{organ_id}.retrieval-pool",
        members=[
            _member(
                member_id="reader-a",
                cell_id=f"{organ_id}.reader-a",
                tissue_id=tissue_id,
                organ_id=organ_id,
                organism_id=organism_id,
                role="research_reader",
                instruction="Extract grounded findings from the provided evidence.",
                model=model,
                instruction_suffix="Focus on the core defect and evidence that directly supports it.",
            ),
            _member(
                member_id="reader-b",
                cell_id=f"{organ_id}.reader-b",
                tissue_id=tissue_id,
                organ_id=organ_id,
                organism_id=organism_id,
                role="research_reader",
                instruction="Extract grounded findings from the provided evidence.",
                model=model,
                instruction_suffix="Focus on acceptance criteria, risks, and missing evidence.",
            ),
        ],
        limits=TissuePoolLimits(max_members=2, max_concurrency=2, max_failures=0),
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
            instruction="Synthesize grounded findings, open questions, and recommended change shape.",
            model=model,
        ),
        lead_instruction="Synthesize the tissue output into a bounded research report with grounded findings and a recommended change.",
        tissue=tissue,
        tissue_coordinator=coordinator,
        boundary_contract=OrganBoundaryContract(
            required_input_keys=["objective", "acceptance_criteria", "delivery_target"],
            required_output_keys=["findings", "evidence_summary", "open_questions", "recommended_change"],
        ),
        metadata={
            "promotion_path": "Later meta-workflow builder promotion stays gated behind 45-4 and consumes this organ's grounded findings rather than replacing the organ membrane.",
        },
    )


def universal_validator_organ(
    *,
    organism_id: str,
    model: str | None = None,
    organ_id: str = "universal-validator",
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
                instruction="Return a one-word verdict for the candidate: pass or repair.",
                model=model,
            ),
            _member(
                member_id="reviewer-b",
                cell_id=f"{organ_id}.reviewer-b",
                tissue_id=tissue_id,
                organ_id=organ_id,
                organism_id=organism_id,
                role="validator_reviewer",
                instruction="Return a one-word verdict for the candidate: pass or repair.",
                model=model,
            ),
        ],
        required_agreement=2,
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
        ),
        metadata={
            "score_report_schema": "The validator emits evaluation fields rather than direct task output.",
        },
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
            instruction="Merge worker outputs into one bounded coding candidate with explicit files, focused validation, and risks.",
            model=model,
        ),
        lead_instruction=(
            "Merge the worker outputs into one bounded candidate. Reuse the strongest "
            "parts, keep the change narrow, and return explicit target files, focused "
            "validation, and inspectable risks."
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
    "deep_research_organ",
    "execute_organ_pattern",
    "synthesis_organ",
    "universal_validator_organ",
]
