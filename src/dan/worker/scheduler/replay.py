"""Replay-only scheduler diagnostics built on top of ``organism_log_v1`` traces."""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

from dan.worker.organism_log import (
    ORGANISM_LOG_SCHEMA_VERSION,
    OrganismLogRow,
    read_organism_log_rows,
)
from dan.worker.organism_log_analysis import (
    OrganismLogAnalysis,
    OrganismLogSpanAnalysis,
    analyze_organism_log_rows,
)


class SchedulerReplayBarrier(BaseModel):
    """Terminal suffix that remained after all earlier work drained."""

    started_at: str = ""
    ended_at: str = ""
    start_ms: int | None = None
    end_ms: int | None = None
    duration_ms: int | None = None
    span_ids: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    lane_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class SchedulerReplayBottleneck(BaseModel):
    """One high-leverage bottleneck span in the replay trace."""

    span_id: str
    label: str = ""
    lane_id: str = ""
    critical_path_rank: int | None = None
    exclusive_duration_ms: int = 0
    total_duration_ms: int = 0
    waiting_duration_ms: int | None = None
    dependent_count: int = 0
    reason: str = ""


class SchedulerReplayOpportunity(BaseModel):
    """One possible missed-parallelism opportunity inferred from the trace."""

    source_span_id: str
    source_label: str = ""
    target_span_id: str
    target_label: str = ""
    lane_id: str = ""
    estimated_gain_upper_bound_ms: int = 0
    serial_gap_ms: int | None = None
    waiting_duration_ms: int | None = None
    reason: str = ""


class SchedulerReplayReadiness(BaseModel):
    """Capsule/readiness timing metrics for downstream-unlock replay."""

    capsule_event_count: int = 0
    readiness_event_count: int = 0
    first_useful_artifact_at: str = ""
    first_downstream_ready_at: str = ""
    first_useful_artifact_ms: int | None = None
    first_downstream_ready_ms: int | None = None
    downstream_unlock_latency_ms: int | None = None
    ready_signal_ids: list[str] = Field(default_factory=list)
    predicates: list[str] = Field(default_factory=list)
    blocker_event_count: int = 0


class SchedulerReplayAnalysis(BaseModel):
    """Replay diagnostics for the shared scheduler layer."""

    schema_version: str = ORGANISM_LOG_SCHEMA_VERSION
    event_count: int = 0
    span_count: int = 0
    capacity_hint: int = Field(default=1, ge=1)
    capacity_source: Literal["provided", "observed_parallelism", "default"] = "default"
    observed_makespan_ms: int | None = None
    max_parallel_spans: int = 0
    total_exclusive_work_ms: int = 0
    average_parallelism: float = 0.0
    critical_path_lower_bound_ms: int | None = None
    work_capacity_lower_bound_ms: int | None = None
    scheduler_lower_bound_ms: int | None = None
    slack_ms: int | None = None
    terminal_barrier: SchedulerReplayBarrier = Field(default_factory=SchedulerReplayBarrier)
    readiness: SchedulerReplayReadiness = Field(default_factory=SchedulerReplayReadiness)
    top_bottlenecks: list[SchedulerReplayBottleneck] = Field(default_factory=list)
    missed_parallelism: list[SchedulerReplayOpportunity] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def analyze_scheduler_replay_rows(
    rows: Iterable[OrganismLogRow | dict[str, Any]],
    *,
    capacity_hint: int | None = None,
    limit: int = 5,
) -> SchedulerReplayAnalysis:
    """Project one scheduler-diagnostic view over normalized or raw rows."""

    materialized_rows = list(rows)
    readiness = _readiness_metrics(materialized_rows)
    analysis = analyze_organism_log_rows(materialized_rows)
    return analyze_scheduler_replay_analysis(
        analysis,
        capacity_hint=capacity_hint,
        limit=limit,
        readiness=readiness,
    )


def analyze_scheduler_replay_analysis(
    analysis: OrganismLogAnalysis,
    *,
    capacity_hint: int | None = None,
    limit: int = 5,
    readiness: SchedulerReplayReadiness | None = None,
) -> SchedulerReplayAnalysis:
    """Project one scheduler-diagnostic view over organism-log analysis."""

    spans = list(analysis.timeline.spans)
    resolved_limit = max(1, int(limit))
    resolved_capacity, capacity_source = _resolve_capacity_hint(
        capacity_hint=capacity_hint,
        observed_parallelism=analysis.timeline.max_parallel_spans,
    )

    if not spans:
        notes = _diagnostic_notes(
            capacity_source=capacity_source,
            capacity_hint=resolved_capacity,
            missed_parallelism_count=0,
        )
        return SchedulerReplayAnalysis(
            event_count=analysis.event_count,
            span_count=analysis.span_count,
            capacity_hint=resolved_capacity,
            capacity_source=capacity_source,
            observed_makespan_ms=analysis.timeline.duration_ms,
            max_parallel_spans=analysis.timeline.max_parallel_spans,
            readiness=readiness or SchedulerReplayReadiness(),
            notes=notes,
        )

    total_work_ms = sum(_span_weight(span) for span in spans)
    makespan_ms = analysis.timeline.duration_ms
    average_parallelism = (
        round(total_work_ms / makespan_ms, 3)
        if makespan_ms and makespan_ms > 0
        else 0.0
    )
    critical_path_lower_bound_ms = analysis.graph.critical_path_duration_ms
    work_capacity_lower_bound_ms = math.ceil(total_work_ms / resolved_capacity)
    scheduler_lower_bound_ms = _max_nullable(
        critical_path_lower_bound_ms,
        work_capacity_lower_bound_ms,
    )
    slack_ms = None
    if makespan_ms is not None and scheduler_lower_bound_ms is not None:
        slack_ms = max(int(makespan_ms) - int(scheduler_lower_bound_ms), 0)

    span_index = {span.span_id: span for span in spans}
    top_bottlenecks = _top_bottlenecks(
        spans=spans,
        critical_path_span_ids=analysis.graph.critical_path_span_ids,
        limit=resolved_limit,
    )
    terminal_barrier = _terminal_barrier(
        spans=spans,
        critical_path_span_ids=analysis.graph.critical_path_span_ids,
        observed_makespan_ms=makespan_ms,
    )
    missed_parallelism = _missed_parallelism_opportunities(
        span_index=span_index,
        analysis=analysis,
        capacity_hint=resolved_capacity,
        limit=resolved_limit,
    )

    return SchedulerReplayAnalysis(
        event_count=analysis.event_count,
        span_count=analysis.span_count,
        capacity_hint=resolved_capacity,
        capacity_source=capacity_source,
        observed_makespan_ms=makespan_ms,
        max_parallel_spans=analysis.timeline.max_parallel_spans,
        total_exclusive_work_ms=total_work_ms,
        average_parallelism=average_parallelism,
        critical_path_lower_bound_ms=critical_path_lower_bound_ms,
        work_capacity_lower_bound_ms=work_capacity_lower_bound_ms,
        scheduler_lower_bound_ms=scheduler_lower_bound_ms,
        slack_ms=slack_ms,
        terminal_barrier=terminal_barrier,
        readiness=readiness or SchedulerReplayReadiness(),
        top_bottlenecks=top_bottlenecks,
        missed_parallelism=missed_parallelism,
        notes=_diagnostic_notes(
            capacity_source=capacity_source,
            capacity_hint=resolved_capacity,
            missed_parallelism_count=len(missed_parallelism),
        ),
    )


def analyze_scheduler_replay(
    path: str | Path,
    *,
    capacity_hint: int | None = None,
    limit: int = 5,
) -> SchedulerReplayAnalysis:
    """Analyze one on-disk organism log for scheduler diagnostics."""

    return analyze_scheduler_replay_rows(
        read_organism_log_rows(path),
        capacity_hint=capacity_hint,
        limit=limit,
    )


def _resolve_capacity_hint(
    *,
    capacity_hint: int | None,
    observed_parallelism: int,
) -> tuple[int, Literal["provided", "observed_parallelism", "default"]]:
    if capacity_hint is not None:
        return max(int(capacity_hint), 1), "provided"
    if observed_parallelism > 0:
        return int(observed_parallelism), "observed_parallelism"
    return 1, "default"


def _parse_row_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _row_payload(row: OrganismLogRow | dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, OrganismLogRow):
        return dict(row.payload or {})
    return dict(row or {})


def _row_value(row: OrganismLogRow | dict[str, Any], key: str) -> Any:
    if isinstance(row, OrganismLogRow):
        value = getattr(row, key, None)
        if value not in {None, "", []}:
            return value
        return dict(row.payload or {}).get(key)
    return dict(row or {}).get(key)


def _row_event(row: OrganismLogRow | dict[str, Any]) -> str:
    return str(_row_value(row, "event") or "").strip()


def _row_timestamp(row: OrganismLogRow | dict[str, Any]) -> str:
    return str(_row_value(row, "timestamp") or "").strip()


def _row_relative_ms(
    timestamp: str,
    *,
    baseline: datetime | None,
) -> int | None:
    current = _parse_row_timestamp(timestamp)
    if baseline is None or current is None:
        return None
    return max(int((current - baseline).total_seconds() * 1000), 0)


def _capsules_include_useful_artifact(payload: dict[str, Any]) -> bool:
    capsules = payload.get("capsules")
    if isinstance(capsules, list):
        for capsule in capsules:
            if not isinstance(capsule, dict):
                continue
            if str(capsule.get("kind") or "") == "blocker":
                continue
            state = str(capsule.get("artifact_state") or "useful_for_downstream")
            if state in {"useful_for_downstream", "validated"}:
                return True
    kinds = payload.get("capsule_kinds")
    if isinstance(kinds, list):
        return any(str(kind or "").strip() and str(kind) != "blocker" for kind in kinds)
    return False


def _readiness_metrics(
    rows: list[OrganismLogRow | dict[str, Any]],
) -> SchedulerReplayReadiness:
    timestamps = [
        parsed
        for row in rows
        if (parsed := _parse_row_timestamp(_row_timestamp(row))) is not None
    ]
    baseline = min(timestamps) if timestamps else None

    capsule_event_count = 0
    readiness_event_count = 0
    first_useful_at = ""
    first_ready_at = ""
    first_useful_ms: int | None = None
    first_ready_ms: int | None = None
    ready_signal_ids: list[str] = []
    predicates: list[str] = []
    blocker_event_count = 0

    for row in rows:
        event = _row_event(row)
        payload = _row_payload(row)
        timestamp = _row_timestamp(row)
        if event == "context.capsule.emitted":
            capsule_event_count += 1
            if not first_useful_at and _capsules_include_useful_artifact(payload):
                first_useful_at = timestamp
                first_useful_ms = _row_relative_ms(timestamp, baseline=baseline)
            kinds = payload.get("capsule_kinds")
            if isinstance(kinds, list) and any(str(kind) == "blocker" for kind in kinds):
                blocker_event_count += 1
            continue

        if event != "context.readiness.emitted":
            continue
        readiness_event_count += 1
        readiness = payload.get("readiness")
        readiness_payload = readiness if isinstance(readiness, dict) else payload
        predicate = str(
            readiness_payload.get("predicate")
            or payload.get("predicate")
            or ""
        ).strip()
        if predicate and predicate not in predicates:
            predicates.append(predicate)
        readiness_id = str(
            readiness_payload.get("readiness_id")
            or payload.get("readiness_id")
            or ""
        ).strip()
        ready = bool(
            readiness_payload.get("ready_for_downstream")
            if "ready_for_downstream" in readiness_payload
            else payload.get("ready_for_downstream")
        )
        blockers = readiness_payload.get("blockers")
        blocker_count = payload.get("blocker_count")
        if (
            isinstance(blockers, list)
            and any(str(item).strip() for item in blockers)
        ) or (isinstance(blocker_count, int) and blocker_count > 0):
            blocker_event_count += 1
        if ready:
            if readiness_id and readiness_id not in ready_signal_ids:
                ready_signal_ids.append(readiness_id)
            if not first_ready_at:
                first_ready_at = timestamp
                first_ready_ms = _row_relative_ms(timestamp, baseline=baseline)

    unlock_latency_ms = None
    if first_useful_ms is not None and first_ready_ms is not None:
        unlock_latency_ms = max(int(first_ready_ms) - int(first_useful_ms), 0)

    return SchedulerReplayReadiness(
        capsule_event_count=capsule_event_count,
        readiness_event_count=readiness_event_count,
        first_useful_artifact_at=first_useful_at,
        first_downstream_ready_at=first_ready_at,
        first_useful_artifact_ms=first_useful_ms,
        first_downstream_ready_ms=first_ready_ms,
        downstream_unlock_latency_ms=unlock_latency_ms,
        ready_signal_ids=ready_signal_ids,
        predicates=predicates,
        blocker_event_count=blocker_event_count,
    )


def _span_weight(span: OrganismLogSpanAnalysis) -> int:
    return int(span.exclusive_duration_ms or span.duration_ms or 0)


def _max_nullable(*values: int | None) -> int | None:
    present = [int(value) for value in values if value is not None]
    if not present:
        return None
    return max(present)


def _top_bottlenecks(
    *,
    spans: list[OrganismLogSpanAnalysis],
    critical_path_span_ids: list[str],
    limit: int,
) -> list[SchedulerReplayBottleneck]:
    critical_path_set = set(critical_path_span_ids)
    ranked = sorted(
        spans,
        key=lambda span: (
            span.span_id not in critical_path_set,
            -_span_weight(span),
            -len(span.dependent_span_ids),
            -(span.waiting_duration_ms or 0),
            span.critical_path_rank or 999999,
        ),
    )
    return [
        SchedulerReplayBottleneck(
            span_id=span.span_id,
            label=span.label,
            lane_id=span.lane_id,
            critical_path_rank=span.critical_path_rank,
            exclusive_duration_ms=_span_weight(span),
            total_duration_ms=int(span.duration_ms or 0),
            waiting_duration_ms=span.waiting_duration_ms,
            dependent_count=len(span.dependent_span_ids),
            reason=_bottleneck_reason(span),
        )
        for span in ranked[:limit]
        if _span_weight(span) > 0
    ]


def _bottleneck_reason(span: OrganismLogSpanAnalysis) -> str:
    parts: list[str] = []
    if span.critical_path_rank is not None:
        parts.append("critical_path")
    if span.dependent_span_ids:
        parts.append("fanout_blocker")
    if span.waiting_duration_ms is not None and span.waiting_duration_ms > 0:
        parts.append("observed_wait")
    if not parts:
        parts.append("large_span")
    return ",".join(parts)


def _terminal_barrier(
    *,
    spans: list[OrganismLogSpanAnalysis],
    critical_path_span_ids: list[str],
    observed_makespan_ms: int | None,
) -> SchedulerReplayBarrier:
    if not spans or not critical_path_span_ids:
        return SchedulerReplayBarrier()
    span_index = {span.span_id: span for span in spans}
    critical_path = [
        span_index[span_id]
        for span_id in critical_path_span_ids
        if span_id in span_index
    ]
    if not critical_path:
        return SchedulerReplayBarrier()

    all_span_ids = set(span_index)
    chosen_start_index = len(critical_path) - 1
    for index, span in enumerate(critical_path):
        if span.start_ms is None:
            continue
        suffix_ids = {item.span_id for item in critical_path[index:]}
        latest_non_suffix_end = max(
            (
                span_index[span_id].end_ms
                for span_id in all_span_ids - suffix_ids
                if span_index[span_id].end_ms is not None
            ),
            default=-1,
        )
        if latest_non_suffix_end <= span.start_ms:
            chosen_start_index = index
            break

    suffix = critical_path[chosen_start_index:]
    start_ms = suffix[0].start_ms
    end_ms = max(
        (
            span.end_ms
            for span in suffix
            if span.end_ms is not None
        ),
        default=observed_makespan_ms,
    )
    duration_ms = None
    if start_ms is not None and end_ms is not None:
        duration_ms = max(int(end_ms) - int(start_ms), 0)
    return SchedulerReplayBarrier(
        started_at=suffix[0].start_timestamp,
        ended_at=next(
            (span.end_timestamp for span in reversed(suffix) if span.end_timestamp),
            "",
        ),
        start_ms=start_ms,
        end_ms=end_ms,
        duration_ms=duration_ms,
        span_ids=[span.span_id for span in suffix],
        labels=[span.label or span.span_id for span in suffix],
        lane_ids=sorted({span.lane_id for span in suffix if span.lane_id}),
        reason="terminal_suffix_after_other_work_drained",
    )


def _missed_parallelism_opportunities(
    *,
    span_index: dict[str, OrganismLogSpanAnalysis],
    analysis: OrganismLogAnalysis,
    capacity_hint: int,
    limit: int,
) -> list[SchedulerReplayOpportunity]:
    if capacity_hint <= 1:
        return []

    blocked_pairs = {
        (edge.source_span_id, edge.target_span_id)
        for edge in analysis.graph.edges
        if edge.relationship == "blocked_by"
    }
    parent_pairs = {
        (edge.source_span_id, edge.target_span_id)
        for edge in analysis.graph.edges
        if edge.relationship == "parent"
    }
    lane_sequence_edges = [
        edge
        for edge in analysis.graph.edges
        if edge.relationship == "lane_sequence"
    ]
    lane_sequence_pairs = {
        (edge.source_span_id, edge.target_span_id)
        for edge in lane_sequence_edges
    }

    opportunities: list[SchedulerReplayOpportunity] = []
    for edge in lane_sequence_edges:
        pair = (edge.source_span_id, edge.target_span_id)
        if pair in blocked_pairs or pair in parent_pairs:
            continue
        source = span_index.get(edge.source_span_id)
        target = span_index.get(edge.target_span_id)
        if source is None or target is None:
            continue
        if any(
            (blocker_id, target.span_id) in blocked_pairs
            or (blocker_id, target.span_id) in parent_pairs
            for blocker_id in target.direct_blocker_span_ids
        ):
            continue
        if any(
            blocker_id != source.span_id
            and (blocker_id, target.span_id) not in lane_sequence_pairs
            for blocker_id in target.direct_blocker_span_ids
        ):
            continue
        gain_upper_bound = min(_span_weight(source), _span_weight(target))
        if gain_upper_bound <= 0:
            continue
        serial_gap_ms = None
        if source.end_ms is not None and target.start_ms is not None:
            serial_gap_ms = max(int(target.start_ms) - int(source.end_ms), 0)
        opportunities.append(
            SchedulerReplayOpportunity(
                source_span_id=source.span_id,
                source_label=source.label,
                target_span_id=target.span_id,
                target_label=target.label,
                lane_id=target.lane_id or source.lane_id,
                estimated_gain_upper_bound_ms=gain_upper_bound,
                serial_gap_ms=serial_gap_ms,
                waiting_duration_ms=target.waiting_duration_ms,
                reason="lane_sequence_only_serialization",
            )
        )
    opportunities.sort(
        key=lambda item: (
            -int(item.estimated_gain_upper_bound_ms or 0),
            -(item.waiting_duration_ms or 0),
            -(item.serial_gap_ms or 0),
            item.lane_id,
            item.target_span_id,
        )
    )
    return opportunities[:limit]


def _diagnostic_notes(
    *,
    capacity_source: Literal["provided", "observed_parallelism", "default"],
    capacity_hint: int,
    missed_parallelism_count: int,
) -> list[str]:
    notes: list[str] = []
    if capacity_source == "provided":
        notes.append(f"capacity_hint={capacity_hint} provided by caller")
    elif capacity_source == "observed_parallelism":
        notes.append(f"capacity_hint={capacity_hint} inferred from observed max_parallel_spans")
    else:
        notes.append("capacity_hint defaulted to 1 because the trace had no parallelism signal")
    notes.append(
        "terminal_barrier measures the final suffix that remained after all earlier work drained"
    )
    if missed_parallelism_count:
        notes.append(
            "missed_parallelism only flags lane-sequence-only serialization, not hard dependency bugs"
        )
    return notes


__all__ = [
    "SchedulerReplayAnalysis",
    "SchedulerReplayBarrier",
    "SchedulerReplayBottleneck",
    "SchedulerReplayOpportunity",
    "SchedulerReplayReadiness",
    "analyze_scheduler_replay",
    "analyze_scheduler_replay_analysis",
    "analyze_scheduler_replay_rows",
]
