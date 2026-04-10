"""Reusable tissue-level worker coordination built on typed cell handoffs."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from dan.worker.composition import (
    CrossCellTraceLog,
    HandoffExecution,
    execute_cell_handoff,
    make_budget_pressure_signal,
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
    EscalationSignal,
    EvidenceRef,
    HandoffTask,
    SignalTrace,
    SupervisorySignalBase,
)


def _compact_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except Exception:
        return str(value).strip()


class TissuePatternKind(str, Enum):
    """Named reusable tissue patterns built from the same cell membrane."""

    PARALLEL_POOL = "parallel_pool"
    REVIEW_QUORUM = "review_quorum"
    RETRIEVAL_ENRICHMENT = "retrieval_enrichment"


class TissueMergeMode(str, Enum):
    """How successful member outputs are reconciled at the tissue boundary."""

    APPEND = "append"
    LAST_WRITE_WINS = "last_write_wins"
    COLLECT_TEXT = "collect_text"
    COLLECT_OUTPUT_REFS = "collect_output_refs"


class TissueMember(BaseModel):
    """One same-type cell participating in a reusable tissue pool."""

    member_id: str
    address: CellAddress
    worker: WorkerDefinition
    instruction_suffix: str = ""
    scope_override: str | None = None
    hard_constraints: list[str] = Field(default_factory=list)
    soft_constraints: list[str] = Field(default_factory=list)
    input_payload_overrides: dict[str, Any] = Field(default_factory=dict)
    budget_limits: CellBudgetLimits | None = None
    authority_limits: CellAuthorityLimits | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TissuePoolLimits(BaseModel):
    """Shared concurrency, budget, and failure limits enforced across a tissue."""

    max_members: int | None = Field(default=None, ge=1)
    max_concurrency: int = Field(default=1, ge=1)
    max_failures: int = Field(default=0, ge=0)
    max_total_selected_refs_per_source: int | None = Field(default=None, ge=0)
    max_total_expanded_refs_per_source: int | None = Field(default=None, ge=0)
    max_total_completion_rounds: int | None = Field(default=None, ge=0)
    max_total_tool_calls: int | None = Field(default=None, ge=0)
    max_total_runtime_budget_seconds: int | None = Field(default=None, ge=1)


class TissueQuorumPolicy(BaseModel):
    """How a review-like tissue resolves agreement or escalates disagreement."""

    required_agreement: int = Field(default=2, ge=1)
    vote_output_key: str = "result"
    normalize_case: bool = True
    minimum_successful_members: int | None = Field(default=None, ge=1)
    tie_break_priority: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_minimum_successful_members(self) -> "TissueQuorumPolicy":
        if (
            self.minimum_successful_members is not None
            and self.minimum_successful_members < self.required_agreement
        ):
            raise ValueError("minimum_successful_members must be >= required_agreement")
        return self


class TissuePattern(BaseModel):
    """Reusable coordination contract for a pool of same-type cells."""

    pattern_id: str
    kind: TissuePatternKind = TissuePatternKind.PARALLEL_POOL
    members: list[TissueMember] = Field(default_factory=list)
    limits: TissuePoolLimits = Field(default_factory=TissuePoolLimits)
    merge_mode: TissueMergeMode = TissueMergeMode.APPEND
    quorum: TissueQuorumPolicy | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_pattern(self) -> "TissuePattern":
        if not self.members:
            raise ValueError("Tissue patterns require at least one member")
        member_ids = [member.member_id for member in self.members]
        if len(set(member_ids)) != len(member_ids):
            raise ValueError("Tissue member_ids must be unique")

        tissue_ids = {member.address.tissue_id for member in self.members if member.address.tissue_id}
        if len(tissue_ids) > 1:
            raise ValueError("Tissue members must share one tissue_id")

        roles = {member.worker.role for member in self.members if member.worker.role}
        if len(roles) > 1:
            raise ValueError("Tissue members must share one worker role")

        if self.kind == TissuePatternKind.REVIEW_QUORUM and self.quorum is None:
            raise ValueError("Review quorum tissues require a quorum policy")
        return self


class TissueBudgetEnvelope(BaseModel):
    """Projected summed budget envelope for all member handoffs in one tissue run."""

    member_count: int = 0
    total_selected_refs_per_source: int | None = None
    total_expanded_refs_per_source: int | None = None
    total_completion_rounds: int | None = None
    total_tool_calls: int | None = None
    total_runtime_budget_seconds: int | None = None


class TissueQuorumDecision(BaseModel):
    """Resolved or unresolved tissue-level quorum outcome."""

    reached: bool
    decision: str | None = None
    agreement_count: int = 0
    resolved_by: Literal["quorum", "tie_break_priority", "none"] = "none"
    vote_counts: dict[str, int] = Field(default_factory=dict)
    member_votes: dict[str, str] = Field(default_factory=dict)


class TissueExecutionResult(BaseModel):
    """Normalized result returned by a reusable tissue pattern."""

    status: Literal["completed", "escalated", "failed"]
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    budget_envelope: TissueBudgetEnvelope = Field(default_factory=TissueBudgetEnvelope)
    quorum: TissueQuorumDecision | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class TissueExecution:
    """Recorded outcome of one tissue-level coordination run."""

    pattern: TissuePattern
    packet: CellHandoffPacket
    result: TissueExecutionResult
    member_packets: list[CellHandoffPacket] = field(default_factory=list)
    member_executions: list[HandoffExecution] = field(default_factory=list)
    signals: list[SupervisorySignalBase] = field(default_factory=list)


def parallel_worker_pool(
    pattern_id: str,
    *,
    members: list[TissueMember],
    limits: TissuePoolLimits | None = None,
    merge_mode: TissueMergeMode = TissueMergeMode.APPEND,
    metadata: dict[str, Any] | None = None,
) -> TissuePattern:
    """Build the narrow reusable parallel pool surface."""

    return TissuePattern(
        pattern_id=pattern_id,
        kind=TissuePatternKind.PARALLEL_POOL,
        members=list(members),
        limits=limits or TissuePoolLimits(),
        merge_mode=merge_mode,
        metadata=dict(metadata or {}),
    )


def review_quorum_pool(
    pattern_id: str,
    *,
    members: list[TissueMember],
    required_agreement: int = 2,
    vote_output_key: str = "result",
    normalize_case: bool = True,
    minimum_successful_members: int | None = None,
    tie_break_priority: list[str] | None = None,
    limits: TissuePoolLimits | None = None,
    metadata: dict[str, Any] | None = None,
) -> TissuePattern:
    """Build the first hardened production-quality review/quorum tissue."""

    return TissuePattern(
        pattern_id=pattern_id,
        kind=TissuePatternKind.REVIEW_QUORUM,
        members=list(members),
        limits=limits or TissuePoolLimits(),
        merge_mode=TissueMergeMode.APPEND,
        quorum=TissueQuorumPolicy(
            required_agreement=required_agreement,
            vote_output_key=vote_output_key,
            normalize_case=normalize_case,
            minimum_successful_members=minimum_successful_members,
            tie_break_priority=list(tie_break_priority or []),
        ),
        metadata=dict(metadata or {}),
    )


def retrieval_enrichment_pool(
    pattern_id: str,
    *,
    members: list[TissueMember],
    limits: TissuePoolLimits | None = None,
    metadata: dict[str, Any] | None = None,
) -> TissuePattern:
    """Build a retrieval-style tissue that fans out and returns output refs."""

    return TissuePattern(
        pattern_id=pattern_id,
        kind=TissuePatternKind.RETRIEVAL_ENRICHMENT,
        members=list(members),
        limits=limits or TissuePoolLimits(),
        merge_mode=TissueMergeMode.COLLECT_OUTPUT_REFS,
        metadata=dict(metadata or {}),
    )


def _merge_budget_limits(
    base: CellBudgetLimits,
    override: CellBudgetLimits | None,
) -> CellBudgetLimits:
    if override is None:
        return base.model_copy(deep=True)
    payload = base.model_dump(mode="json", exclude_none=True)
    payload.update(override.model_dump(mode="json", exclude_none=True))
    return CellBudgetLimits.model_validate(payload)


def _merge_authority_limits(
    base: CellAuthorityLimits,
    override: CellAuthorityLimits | None,
) -> CellAuthorityLimits:
    if override is None:
        return base.model_copy(deep=True)
    payload = base.model_dump(mode="json", exclude_none=True)
    payload.update(override.model_dump(mode="json", exclude_none=True))
    return CellAuthorityLimits.model_validate(payload)


def _build_member_hooks(packet: CellHandoffPacket) -> ContinuationHooks:
    hooks = packet.continuation_hooks.model_copy(deep=True)
    hooks.reply_to_cell_id = packet.recipient.cell_id
    hooks.resume_from_packet_id = packet.packet_id
    return hooks


def build_tissue_member_packet(
    *,
    packet: CellHandoffPacket,
    pattern: TissuePattern,
    member: TissueMember,
    parent_signal_id: str,
) -> CellHandoffPacket:
    """Derive one child handoff packet from the tissue coordinator packet."""

    instruction = packet.task.instruction
    if member.instruction_suffix:
        instruction = f"{instruction}\n\n{member.instruction_suffix}"

    return CellHandoffPacket(
        trace=SignalTrace(
            trace_id=packet.trace.trace_id,
            root_task_id=packet.trace.root_task_id or packet.task.task_id,
            parent_packet_id=packet.packet_id,
            parent_signal_id=parent_signal_id,
            lineage=[
                *packet.trace.lineage,
                f"tissue:{pattern.pattern_id}",
                f"member:{member.member_id}",
            ],
        ),
        sender=packet.recipient.model_copy(deep=True),
        recipient=member.address.model_copy(deep=True),
        task=HandoffTask(
            task_id=f"{packet.task.task_id}:{member.member_id}",
            instruction=instruction,
            scope=member.scope_override if member.scope_override is not None else packet.task.scope,
            hard_constraints=[
                *packet.task.hard_constraints,
                *member.hard_constraints,
            ],
            soft_constraints=[
                *packet.task.soft_constraints,
                *member.soft_constraints,
            ],
            input_payload={
                **packet.task.input_payload,
                **member.input_payload_overrides,
            },
        ),
        evidence_refs=[ref.model_copy(deep=True) for ref in packet.evidence_refs],
        output_contract=packet.output_contract.model_copy(deep=True),
        budget_limits=_merge_budget_limits(packet.budget_limits, member.budget_limits),
        authority_limits=_merge_authority_limits(packet.authority_limits, member.authority_limits),
        continuation=packet.continuation.model_copy(deep=True) if packet.continuation is not None else None,
        continuation_hooks=_build_member_hooks(packet),
        metadata={
            **dict(packet.metadata),
            **dict(member.metadata),
            "tissue_pattern_id": pattern.pattern_id,
            "tissue_pattern_kind": pattern.kind.value,
            "tissue_member_id": member.member_id,
        },
    )


def _sum_budget_field(member_packets: list[CellHandoffPacket], field_name: str) -> int | None:
    values: list[int] = []
    for member_packet in member_packets:
        value = getattr(member_packet.budget_limits, field_name)
        if value is None:
            return None
        values.append(int(value))
    return sum(values)


def _budget_envelope(member_packets: list[CellHandoffPacket]) -> TissueBudgetEnvelope:
    return TissueBudgetEnvelope(
        member_count=len(member_packets),
        total_selected_refs_per_source=_sum_budget_field(member_packets, "max_selected_refs_per_source"),
        total_expanded_refs_per_source=_sum_budget_field(member_packets, "max_expanded_refs_per_source"),
        total_completion_rounds=_sum_budget_field(member_packets, "max_completion_rounds"),
        total_tool_calls=_sum_budget_field(member_packets, "max_tool_calls"),
        total_runtime_budget_seconds=_sum_budget_field(member_packets, "max_runtime_seconds"),
    )


def _projected_budget_breaches(
    limits: TissuePoolLimits,
    envelope: TissueBudgetEnvelope,
) -> dict[str, dict[str, Any]]:
    limit_pairs = {
        "max_members": (limits.max_members, envelope.member_count),
        "max_total_selected_refs_per_source": (
            limits.max_total_selected_refs_per_source,
            envelope.total_selected_refs_per_source,
        ),
        "max_total_expanded_refs_per_source": (
            limits.max_total_expanded_refs_per_source,
            envelope.total_expanded_refs_per_source,
        ),
        "max_total_completion_rounds": (
            limits.max_total_completion_rounds,
            envelope.total_completion_rounds,
        ),
        "max_total_tool_calls": (
            limits.max_total_tool_calls,
            envelope.total_tool_calls,
        ),
        "max_total_runtime_budget_seconds": (
            limits.max_total_runtime_budget_seconds,
            envelope.total_runtime_budget_seconds,
        ),
    }
    breaches: dict[str, dict[str, Any]] = {}
    for field_name, (limit_value, projected_value) in limit_pairs.items():
        if limit_value is None:
            continue
        if projected_value is None:
            breaches[field_name] = {
                "limit": limit_value,
                "projected": None,
                "reason": "member budget is unbounded",
            }
            continue
        if projected_value > limit_value:
            breaches[field_name] = {
                "limit": limit_value,
                "projected": projected_value,
            }
    return breaches


async def _execute_member_pool(
    *,
    executor: WorkerCoreExecutor,
    pattern: TissuePattern,
    members: list[TissueMember],
    member_packets: list[CellHandoffPacket],
    trace_log: CrossCellTraceLog | None,
) -> list[HandoffExecution]:
    async def _invoke(member: TissueMember, member_packet: CellHandoffPacket) -> HandoffExecution:
        return await execute_cell_handoff(
            executor=executor,
            worker=member.worker,
            packet=member_packet,
            trace_log=trace_log,
        )

    if pattern.limits.max_concurrency == 1 or len(member_packets) <= 1:
        return [
            await _invoke(member, member_packet)
            for member, member_packet in zip(members, member_packets, strict=True)
        ]

    semaphore = asyncio.Semaphore(pattern.limits.max_concurrency)

    async def _bounded(member: TissueMember, member_packet: CellHandoffPacket) -> HandoffExecution:
        async with semaphore:
            return await _invoke(member, member_packet)

    return list(
        await asyncio.gather(*[
            _bounded(member, member_packet)
            for member, member_packet in zip(members, member_packets, strict=True)
        ])
    )


def _member_result_maps(
    members: list[TissueMember],
    member_executions: list[HandoffExecution],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    member_results: dict[str, dict[str, Any]] = {}
    failed_members: dict[str, str] = {}
    for member, execution in zip(members, member_executions, strict=True):
        if execution.result.status == "completed":
            member_results[member.member_id] = dict(execution.result.outputs)
        else:
            failed_members[member.member_id] = execution.result.error or "member execution failed"
    return member_results, failed_members


def _derive_output_refs(
    pattern: TissuePattern,
    members: list[TissueMember],
    member_executions: list[HandoffExecution],
) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    seen: set[str] = set()
    for member, execution in zip(members, member_executions, strict=True):
        terminal_signal = execution.signals[-1] if execution.signals else None
        if not isinstance(terminal_signal, CompletionSignal):
            continue
        candidate_refs = list(terminal_signal.output_refs)
        if not candidate_refs:
            candidate_refs = [
                terminal_signal.as_evidence_ref(
                    ref_id=f"{execution.packet.packet_id}:output",
                    label=f"{member.member_id} output",
                    source_id="tissue-member-output",
                    metadata={
                        "tissue_pattern_id": pattern.pattern_id,
                        "tissue_member_id": member.member_id,
                    },
                )
            ]
        for ref in candidate_refs:
            if ref.ref_id in seen:
                continue
            seen.add(ref.ref_id)
            refs.append(ref)
    return refs


def _merge_parallel_outputs(
    *,
    merge_mode: TissueMergeMode,
    member_results: dict[str, dict[str, Any]],
    output_refs: list[EvidenceRef],
) -> Any:
    if merge_mode == TissueMergeMode.APPEND:
        return dict(member_results)
    if merge_mode == TissueMergeMode.LAST_WRITE_WINS:
        merged: dict[str, Any] = {}
        for payload in member_results.values():
            merged.update(payload)
        return merged
    if merge_mode == TissueMergeMode.COLLECT_TEXT:
        texts: list[str] = []
        for payload in member_results.values():
            if "result" in payload:
                texts.append(_compact_value(payload["result"]))
            elif "text" in payload:
                texts.append(_compact_value(payload["text"]))
            else:
                texts.append(_compact_value(payload))
        return texts
    if merge_mode == TissueMergeMode.COLLECT_OUTPUT_REFS:
        return [ref.model_dump(mode="json", exclude_none=True) for ref in output_refs]
    return dict(member_results)


def _extract_vote(payload: dict[str, Any], quorum: TissueQuorumPolicy) -> str:
    candidate = payload.get(quorum.vote_output_key)
    if candidate is None and quorum.vote_output_key == "result":
        candidate = payload.get("text")
    vote = _compact_value(candidate)
    normalized = vote.lower() if quorum.normalize_case else vote
    if not normalized or not quorum.tie_break_priority:
        return normalized

    normalized_priority_votes = [
        candidate.lower() if quorum.normalize_case else candidate
        for candidate in quorum.tie_break_priority
    ]

    if normalized in set(normalized_priority_votes):
        return normalized

    matches: list[tuple[int, str]] = []
    for candidate_vote in quorum.tie_break_priority:
        target = candidate_vote.lower() if quorum.normalize_case else candidate_vote
        match = re.search(rf"\b{re.escape(target)}\b", normalized)
        if match is not None:
            matches.append((match.start(), target))
    if matches:
        matches.sort(key=lambda item: item[0])
        return matches[0][1]

    # If a reviewer completed successfully but returned prose that does not map back
    # onto the configured verdict vocabulary, fall back to the most conservative
    # tie-break priority instead of escalating on harmless output drift.
    return normalized_priority_votes[0]


def _resolve_quorum(
    *,
    quorum: TissueQuorumPolicy,
    member_results: dict[str, dict[str, Any]],
) -> TissueQuorumDecision:
    member_votes = {
        member_id: _extract_vote(payload, quorum)
        for member_id, payload in member_results.items()
    }
    vote_counts: dict[str, int] = {}
    for vote in member_votes.values():
        if not vote:
            continue
        vote_counts[vote] = vote_counts.get(vote, 0) + 1

    minimum_successes = quorum.minimum_successful_members or quorum.required_agreement
    if len(member_votes) < minimum_successes or not vote_counts:
        return TissueQuorumDecision(
            reached=False,
            vote_counts=vote_counts,
            member_votes=member_votes,
        )

    top_count = max(vote_counts.values())
    top_votes = sorted(vote for vote, count in vote_counts.items() if count == top_count)
    if top_count >= quorum.required_agreement and len(top_votes) == 1:
        return TissueQuorumDecision(
            reached=True,
            decision=top_votes[0],
            agreement_count=top_count,
            resolved_by="quorum",
            vote_counts=vote_counts,
            member_votes=member_votes,
        )

    if len(top_votes) > 1 and quorum.tie_break_priority:
        priority = {
            vote.lower() if quorum.normalize_case else vote: index
            for index, vote in enumerate(quorum.tie_break_priority)
        }
        ranked_votes = [vote for vote in top_votes if vote in priority]
        if ranked_votes:
            ranked_votes.sort(key=lambda vote: priority[vote])
            chosen = ranked_votes[0]
            return TissueQuorumDecision(
                reached=True,
                decision=chosen,
                agreement_count=top_count,
                resolved_by="tie_break_priority",
                vote_counts=vote_counts,
                member_votes=member_votes,
            )

    return TissueQuorumDecision(
        reached=False,
        agreement_count=top_count,
        vote_counts=vote_counts,
        member_votes=member_votes,
    )


def _result_payload(
    *,
    member_results: dict[str, dict[str, Any]],
    failed_members: dict[str, str],
    merged_result: Any,
    output_refs: list[EvidenceRef],
    quorum: TissueQuorumDecision | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "member_results": dict(member_results),
        "failed_members": dict(failed_members),
        "result": merged_result,
    }
    if output_refs:
        payload["output_refs"] = [
            ref.model_dump(mode="json", exclude_none=True)
            for ref in output_refs
        ]
    if quorum is not None:
        payload["quorum"] = quorum.model_dump(mode="json", exclude_none=True)
    return payload


def _tissue_terminal_completion(
    *,
    packet: CellHandoffPacket,
    pattern: TissuePattern,
    payload: dict[str, Any],
    output_refs: list[EvidenceRef],
    metadata: dict[str, Any],
) -> SupervisorySignalBase:
    return make_completion_signal(
        packet,
        WorkerExecutionResult(
            status="completed",
            outputs=payload,
            metadata=metadata,
        ),
        summary=f"{packet.recipient.cell_id} completed tissue pattern {pattern.pattern_id}",
        output_refs=output_refs,
        metadata=metadata,
    )


def _tissue_terminal_escalation(
    *,
    packet: CellHandoffPacket,
    pattern: TissuePattern,
    reason: str,
    summary: str,
    requested_action: str,
    metadata: dict[str, Any],
) -> EscalationSignal:
    return make_escalation_signal(
        packet,
        reason=reason,
        requested_action=requested_action,
        summary=f"{summary} ({pattern.pattern_id})",
        metadata=metadata,
    )


async def execute_tissue_pattern(
    *,
    executor: WorkerCoreExecutor,
    pattern: TissuePattern,
    packet: CellHandoffPacket,
    trace_log: CrossCellTraceLog | None = None,
) -> TissueExecution:
    """Run one reusable tissue pattern as a coordinator cell plus same-type fan-out."""

    if trace_log is not None:
        trace_log.record_handoff(packet)

    accepted = make_status_signal(
        packet,
        status="accepted",
        summary=f"{packet.recipient.cell_id} accepted tissue pattern {pattern.pattern_id}",
    )
    signals: list[SupervisorySignalBase] = [accepted]
    if trace_log is not None:
        trace_log.record_signal(accepted)

    member_packets = [
        build_tissue_member_packet(
            packet=packet,
            pattern=pattern,
            member=member,
            parent_signal_id=accepted.signal_id,
        )
        for member in pattern.members
    ]
    envelope = _budget_envelope(member_packets)
    budget_breaches = _projected_budget_breaches(pattern.limits, envelope)
    if budget_breaches:
        budget_signal = make_budget_pressure_signal(
            packet,
            summary=f"{packet.recipient.cell_id} hit a shared tissue budget boundary.",
            pressure_sources=sorted(budget_breaches),
            remaining={field_name: 0 for field_name in budget_breaches},
            metadata={
                "tissue_pattern_id": pattern.pattern_id,
                "budget_breaches": budget_breaches,
                "budget_envelope": envelope.model_dump(mode="json", exclude_none=False),
            },
        )
        escalation = _tissue_terminal_escalation(
            packet=packet,
            pattern=pattern,
            reason="tissue_budget_exceeded",
            summary=f"{packet.recipient.cell_id} cannot fan out within the shared tissue limits",
            requested_action="Reduce the pool size or increase the shared tissue budget before retrying.",
            metadata={
                "tissue_pattern_id": pattern.pattern_id,
                "tissue_pattern_kind": pattern.kind.value,
                "budget_breaches": budget_breaches,
                "budget_envelope": envelope.model_dump(mode="json", exclude_none=False),
            },
        )
        signals.extend([budget_signal, escalation])
        if trace_log is not None:
            trace_log.record_signal(budget_signal)
            trace_log.record_signal(escalation)
        result = TissueExecutionResult(
            status="escalated",
            outputs={
                "member_results": {},
                "failed_members": {},
                "result": {},
                "budget_breaches": budget_breaches,
            },
            error="Shared tissue limits would be exceeded by the projected member handoffs.",
            budget_envelope=envelope,
            metadata={"budget_breaches": budget_breaches},
        )
        return TissueExecution(
            pattern=pattern,
            packet=packet,
            result=result,
            member_packets=member_packets,
            member_executions=[],
            signals=signals,
        )

    member_executions = await _execute_member_pool(
        executor=executor,
        pattern=pattern,
        members=pattern.members,
        member_packets=member_packets,
        trace_log=trace_log,
    )
    member_results, failed_members = _member_result_maps(pattern.members, member_executions)
    output_refs = _derive_output_refs(pattern, pattern.members, member_executions)
    merged_result = _merge_parallel_outputs(
        merge_mode=pattern.merge_mode,
        member_results=member_results,
        output_refs=output_refs,
    )

    quorum_decision: TissueQuorumDecision | None = None
    if pattern.quorum is not None:
        quorum_decision = _resolve_quorum(
            quorum=pattern.quorum,
            member_results=member_results,
        )
        merged_result = {
            "decision": quorum_decision.decision,
            "agreement_count": quorum_decision.agreement_count,
            "resolved_by": quorum_decision.resolved_by,
            "vote_counts": dict(quorum_decision.vote_counts),
            "member_votes": dict(quorum_decision.member_votes),
            "reviews": dict(member_results),
        }

    payload = _result_payload(
        member_results=member_results,
        failed_members=failed_members,
        merged_result=merged_result,
        output_refs=output_refs,
        quorum=quorum_decision,
    )
    terminal_metadata = {
        "tissue_pattern_id": pattern.pattern_id,
        "tissue_pattern_kind": pattern.kind.value,
        "successful_member_ids": sorted(member_results),
        "failed_member_ids": sorted(failed_members),
        "budget_envelope": envelope.model_dump(mode="json", exclude_none=False),
    }

    failure_budget_exhausted = len(failed_members) > pattern.limits.max_failures
    quorum_escalation_reason: str | None = None
    if pattern.quorum is not None and (quorum_decision is None or not quorum_decision.reached):
        quorum_escalation_reason = (
            "quorum_tie"
            if quorum_decision is not None and len([
                vote
                for vote, count in quorum_decision.vote_counts.items()
                if count == quorum_decision.agreement_count and count > 0
            ]) > 1
            else "quorum_not_reached"
        )

    if failure_budget_exhausted:
        escalation = _tissue_terminal_escalation(
            packet=packet,
            pattern=pattern,
            reason="tissue_failure_budget_exhausted",
            summary=f"{packet.recipient.cell_id} exceeded the shared tissue failure budget",
            requested_action="Escalate to a higher-order supervisor or retry with a smaller pool.",
            metadata={
                **terminal_metadata,
                "failure_budget": pattern.limits.max_failures,
                "failed_members": failed_members,
            },
        )
        signals.append(escalation)
        if trace_log is not None:
            trace_log.record_signal(escalation)
        result = TissueExecutionResult(
            status="escalated",
            outputs=payload,
            error="Too many tissue members failed for the shared failure budget.",
            budget_envelope=envelope,
            quorum=quorum_decision,
            metadata={
                **terminal_metadata,
                "failure_budget": pattern.limits.max_failures,
            },
        )
        return TissueExecution(
            pattern=pattern,
            packet=packet,
            result=result,
            member_packets=member_packets,
            member_executions=member_executions,
            signals=signals,
        )

    if quorum_escalation_reason is not None:
        escalation = _tissue_terminal_escalation(
            packet=packet,
            pattern=pattern,
            reason=quorum_escalation_reason,
            summary=f"{packet.recipient.cell_id} could not self-resolve the review quorum",
            requested_action="Escalate to a tie-break reviewer or broader validator pool.",
            metadata={
                **terminal_metadata,
                "quorum": quorum_decision.model_dump(mode="json", exclude_none=True)
                if quorum_decision is not None
                else {},
            },
        )
        signals.append(escalation)
        if trace_log is not None:
            trace_log.record_signal(escalation)
        result = TissueExecutionResult(
            status="escalated",
            outputs=payload,
            error="The tissue quorum did not converge on a reusable decision.",
            budget_envelope=envelope,
            quorum=quorum_decision,
            metadata=terminal_metadata,
        )
        return TissueExecution(
            pattern=pattern,
            packet=packet,
            result=result,
            member_packets=member_packets,
            member_executions=member_executions,
            signals=signals,
        )

    completion = _tissue_terminal_completion(
        packet=packet,
        pattern=pattern,
        payload=payload,
        output_refs=output_refs,
        metadata=terminal_metadata,
    )
    signals.append(completion)
    if trace_log is not None:
        trace_log.record_signal(completion)
    result = TissueExecutionResult(
        status="completed",
        outputs=payload,
        budget_envelope=envelope,
        quorum=quorum_decision,
        metadata=terminal_metadata,
    )
    return TissueExecution(
        pattern=pattern,
        packet=packet,
        result=result,
        member_packets=member_packets,
        member_executions=member_executions,
        signals=signals,
    )


__all__ = [
    "TissueBudgetEnvelope",
    "TissueExecution",
    "TissueExecutionResult",
    "TissueMember",
    "TissueMergeMode",
    "TissuePattern",
    "TissuePatternKind",
    "TissuePoolLimits",
    "TissueQuorumDecision",
    "TissueQuorumPolicy",
    "build_tissue_member_packet",
    "execute_tissue_pattern",
    "parallel_worker_pool",
    "retrieval_enrichment_pool",
    "review_quorum_pool",
]
