"""Helpers for traceable multicellular worker composition."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from dan.worker.core.contracts import ExecutionRequest
from dan.worker.core.executor import WorkerCoreExecutor, WorkerExecutionResult
from dan.worker.core.model import WorkerDefinition
from dan.worker.signaling import (
    BroadcastScope,
    BudgetPressureSignal,
    CellBudgetLimits,
    CellHandoffPacket,
    CompletionSignal,
    EscalationSignal,
    EvidenceRef,
    FailureSignal,
    SignalTrace,
    StatusSignal,
    SupervisorySignalBase,
    WarningSignal,
    summarize_outputs,
)


@dataclass(slots=True)
class HandoffExecution:
    """Recorded result of running one typed handoff packet."""

    packet: CellHandoffPacket
    request: ExecutionRequest
    result: WorkerExecutionResult
    signals: list[SupervisorySignalBase] = field(default_factory=list)


class CrossCellTraceLog:
    """Append-only trace log for point-to-point packets and supervisory signals."""

    def __init__(self) -> None:
        self._entries: list[CellHandoffPacket | SupervisorySignalBase] = []

    def record_handoff(self, packet: CellHandoffPacket) -> CellHandoffPacket:
        stored = packet.model_copy(deep=True)
        self._entries.append(stored)
        return stored

    def record_signal(self, signal: SupervisorySignalBase) -> SupervisorySignalBase:
        if signal.related_packet_id is not None and signal.related_packet_id not in self._known_packet_ids:
            raise ValueError(
                f"Unknown handoff packet '{signal.related_packet_id}' for trace '{signal.trace.trace_id}'"
            )
        stored = signal.model_copy(deep=True)
        self._entries.append(stored)
        return stored

    @property
    def _known_packet_ids(self) -> set[str]:
        return {entry.packet_id for entry in self._entries if isinstance(entry, CellHandoffPacket)}

    def packets_for_trace(self, trace_id: str) -> list[CellHandoffPacket]:
        return [
            entry
            for entry in self._entries
            if isinstance(entry, CellHandoffPacket) and entry.trace.trace_id == trace_id
        ]

    def signals_for_trace(self, trace_id: str) -> list[SupervisorySignalBase]:
        return [
            entry
            for entry in self._entries
            if isinstance(entry, SupervisorySignalBase) and entry.trace.trace_id == trace_id
        ]

    def inspect_trace(self, trace_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for entry in self._entries:
            if entry.trace.trace_id != trace_id:
                continue
            if isinstance(entry, CellHandoffPacket):
                rows.append(
                    {
                        "kind": "handoff",
                        "message_id": entry.packet_id,
                        "trace_id": trace_id,
                        "sender_cell_id": entry.sender.cell_id,
                        "recipient_cell_id": entry.recipient.cell_id,
                        "task_id": entry.task.task_id,
                        "evidence_refs": [ref.ref_id for ref in entry.evidence_refs],
                        "created_at": entry.created_at,
                    }
                )
                continue
            rows.append(
                {
                    "kind": "signal",
                    "message_id": entry.signal_id,
                    "trace_id": trace_id,
                    "signal_type": entry.signal_type,
                    "source_cell_id": entry.source.cell_id,
                    "topic": entry.topic,
                    "broadcast_scope": entry.broadcast_scope.value,
                    "related_packet_id": entry.related_packet_id,
                    "summary": entry.summary,
                    "emitted_at": entry.emitted_at,
                }
            )
        return rows


def _signal_trace(packet: CellHandoffPacket) -> SignalTrace:
    return packet.trace.model_copy(deep=True)


def make_status_signal(
    packet: CellHandoffPacket,
    *,
    status: Literal["accepted", "running", "waiting", "blocked"],
    summary: str,
    progress: float | None = None,
    topic: str | None = None,
    broadcast_scope: BroadcastScope = BroadcastScope.SUPERVISORS,
    metadata: dict[str, Any] | None = None,
) -> StatusSignal:
    return StatusSignal(
        trace=_signal_trace(packet),
        source=packet.recipient,
        topic=topic or packet.continuation_hooks.status_topic or "cell.status",
        broadcast_scope=broadcast_scope,
        related_packet_id=packet.packet_id,
        summary=summary,
        status=status,
        progress=progress,
        metadata=dict(metadata or {}),
    )


def make_warning_signal(
    packet: CellHandoffPacket,
    *,
    code: str,
    detail: str,
    summary: str,
    topic: str | None = None,
    broadcast_scope: BroadcastScope = BroadcastScope.SUPERVISORS,
    metadata: dict[str, Any] | None = None,
) -> WarningSignal:
    return WarningSignal(
        trace=_signal_trace(packet),
        source=packet.recipient,
        topic=topic or packet.continuation_hooks.status_topic or "cell.warning",
        broadcast_scope=broadcast_scope,
        related_packet_id=packet.packet_id,
        summary=summary,
        code=code,
        detail=detail,
        metadata=dict(metadata or {}),
    )


def make_failure_signal(
    packet: CellHandoffPacket,
    *,
    error: str,
    summary: str,
    retryable: bool = False,
    topic: str | None = None,
    broadcast_scope: BroadcastScope = BroadcastScope.SUPERVISORS,
    metadata: dict[str, Any] | None = None,
) -> FailureSignal:
    return FailureSignal(
        trace=_signal_trace(packet),
        source=packet.recipient,
        topic=topic or packet.continuation_hooks.status_topic or "cell.failure",
        broadcast_scope=broadcast_scope,
        related_packet_id=packet.packet_id,
        summary=summary,
        error=error,
        retryable=retryable,
        metadata=dict(metadata or {}),
    )


def make_budget_pressure_signal(
    packet: CellHandoffPacket,
    *,
    summary: str,
    pressure_sources: list[str],
    remaining: dict[str, Any],
    budget_limits: CellBudgetLimits | None = None,
    topic: str | None = None,
    broadcast_scope: BroadcastScope = BroadcastScope.SUPERVISORS,
    metadata: dict[str, Any] | None = None,
) -> BudgetPressureSignal:
    return BudgetPressureSignal(
        trace=_signal_trace(packet),
        source=packet.recipient,
        topic=topic or packet.continuation_hooks.status_topic or "cell.budget",
        broadcast_scope=broadcast_scope,
        related_packet_id=packet.packet_id,
        summary=summary,
        pressure_sources=list(pressure_sources),
        remaining=dict(remaining),
        budget_limits=budget_limits or packet.budget_limits,
        metadata=dict(metadata or {}),
    )


def make_completion_signal(
    packet: CellHandoffPacket,
    result: WorkerExecutionResult,
    *,
    summary: str | None = None,
    output_refs: list[EvidenceRef] | None = None,
    completion_status: Literal["completed", "partial"] = "completed",
    topic: str | None = None,
    broadcast_scope: BroadcastScope = BroadcastScope.SUPERVISORS,
    metadata: dict[str, Any] | None = None,
) -> CompletionSignal:
    result_summary = summarize_outputs(result.outputs)
    return CompletionSignal(
        trace=_signal_trace(packet),
        source=packet.recipient,
        topic=topic or packet.continuation_hooks.completion_topic or "cell.completion",
        broadcast_scope=broadcast_scope,
        related_packet_id=packet.packet_id,
        summary=summary or f"{packet.recipient.cell_id} completed {packet.task.task_id}",
        completion_status=completion_status,
        result_summary=result_summary,
        outputs=dict(result.outputs),
        output_refs=list(output_refs or []),
        metadata={
            "result_status": result.status,
            **dict(metadata or {}),
        },
    )


def make_escalation_signal(
    packet: CellHandoffPacket,
    *,
    reason: str,
    requested_action: str,
    summary: str,
    topic: str | None = None,
    broadcast_scope: BroadcastScope = BroadcastScope.SUPERVISORS,
    metadata: dict[str, Any] | None = None,
) -> EscalationSignal:
    return EscalationSignal(
        trace=_signal_trace(packet),
        source=packet.recipient,
        topic=topic or packet.continuation_hooks.escalation_topic or "cell.escalation",
        broadcast_scope=broadcast_scope,
        related_packet_id=packet.packet_id,
        summary=summary,
        reason=reason,
        requested_action=requested_action,
        metadata=dict(metadata or {}),
    )


async def execute_cell_handoff(
    *,
    executor: WorkerCoreExecutor,
    worker: WorkerDefinition,
    packet: CellHandoffPacket,
    trace_log: CrossCellTraceLog | None = None,
    output_refs: list[EvidenceRef] | None = None,
) -> HandoffExecution:
    if trace_log is not None:
        trace_log.record_handoff(packet)

    request = packet.to_execution_request()
    accepted = make_status_signal(
        packet,
        status="accepted",
        summary=f"{packet.recipient.cell_id} accepted {packet.task.task_id}",
    )
    if trace_log is not None:
        trace_log.record_signal(accepted)

    runtime_budget_seconds = packet.budget_limits.max_runtime_seconds
    if runtime_budget_seconds is None:
        result = await executor.execute(worker, request)
    else:
        execution_task = asyncio.create_task(executor.execute(worker, request))
        try:
            result = await asyncio.wait_for(
                execution_task,
                timeout=float(runtime_budget_seconds),
            )
        except asyncio.TimeoutError:
            execution_task.cancel()
            try:
                await execution_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            result = WorkerExecutionResult(
                status="failed",
                error=(
                    "runtime_budget_exceeded: "
                    f"{packet.recipient.cell_id} exceeded {int(runtime_budget_seconds)}s"
                ),
                metadata={
                    "runtime_budget_seconds": int(runtime_budget_seconds),
                    "worker_id": worker.id,
                },
            )
    if result.status == "completed":
        terminal_signal = make_completion_signal(packet, result, output_refs=output_refs)
    else:
        terminal_signal = make_failure_signal(
            packet,
            error=result.error or "cell handoff failed",
            summary=f"{packet.recipient.cell_id} failed {packet.task.task_id}",
        )
    if trace_log is not None:
        trace_log.record_signal(terminal_signal)

    return HandoffExecution(
        packet=packet,
        request=request,
        result=result,
        signals=[accepted, terminal_signal],
    )


__all__ = [
    "CrossCellTraceLog",
    "HandoffExecution",
    "execute_cell_handoff",
    "make_budget_pressure_signal",
    "make_completion_signal",
    "make_escalation_signal",
    "make_failure_signal",
    "make_status_signal",
    "make_warning_signal",
]
