"""Higher-level analysis helpers for ``organism_log_v1`` traces."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

from dan.worker.organism_log import (
    ORGANISM_LOG_SCHEMA_VERSION,
    OrganismLogRow,
    normalize_organism_log_rows,
    read_organism_log_rows,
)


class OrganismLogLaneAnalysis(BaseModel):
    """One timeline lane for visualization."""

    lane_id: str
    label: str
    started_at: str = ""
    ended_at: str = ""
    span_ids: list[str] = Field(default_factory=list)


class OrganismLogSpanAnalysis(BaseModel):
    """One analyzed span node for timeline + dependency rendering."""

    span_id: str
    label: str = ""
    lane_id: str = ""
    lane_label: str = ""
    parent_span_id: str = ""
    span_kind: str = ""
    event: str = ""
    event_family: str = ""
    status: str = ""
    summary: str = ""
    worker_id: str = ""
    tool_id: str = ""
    tool_call_id: str = ""
    model_call_id: str = ""
    contract_id: str = ""
    start_timestamp: str = ""
    end_timestamp: str = ""
    start_ms: int | None = None
    end_ms: int | None = None
    duration_ms: int | None = None
    exclusive_duration_ms: int | None = None
    waiting_duration_ms: int | None = None
    direct_blocker_span_ids: list[str] = Field(default_factory=list)
    dependent_span_ids: list[str] = Field(default_factory=list)
    critical_path_rank: int | None = None


class OrganismLogDependencyEdge(BaseModel):
    """One dependency edge between analyzed spans."""

    edge_id: str
    source_span_id: str
    target_span_id: str
    relationship: Literal["blocked_by", "lane_sequence", "parent"]
    lag_ms: int | None = None


class OrganismLogBlockingChain(BaseModel):
    """One blocker chain for a target span."""

    target_span_id: str
    direct_blocker_span_ids: list[str] = Field(default_factory=list)
    blocker_chain_span_ids: list[str] = Field(default_factory=list)
    waiting_duration_ms: int | None = None
    status: str = ""
    wait_reason: str = ""


class OrganismLogTimelineAnalysis(BaseModel):
    """Timeline-oriented analysis payload."""

    started_at: str = ""
    ended_at: str = ""
    duration_ms: int | None = None
    max_parallel_spans: int = 0
    lanes: list[OrganismLogLaneAnalysis] = Field(default_factory=list)
    spans: list[OrganismLogSpanAnalysis] = Field(default_factory=list)


class OrganismLogGraphAnalysis(BaseModel):
    """Dependency-graph-oriented analysis payload."""

    nodes: list[OrganismLogSpanAnalysis] = Field(default_factory=list)
    edges: list[OrganismLogDependencyEdge] = Field(default_factory=list)
    blocker_chains: list[OrganismLogBlockingChain] = Field(default_factory=list)
    critical_path_span_ids: list[str] = Field(default_factory=list)
    critical_path_duration_ms: int | None = None


class OrganismLogAnalysis(BaseModel):
    """Combined analysis payload for CLI and future visualizers."""

    schema_version: str = ORGANISM_LOG_SCHEMA_VERSION
    event_count: int = 0
    span_count: int = 0
    timeline: OrganismLogTimelineAnalysis = Field(default_factory=OrganismLogTimelineAnalysis)
    graph: OrganismLogGraphAnalysis = Field(default_factory=OrganismLogGraphAnalysis)


def analyze_organism_log_rows(
    rows: Iterable[OrganismLogRow | dict[str, Any]],
) -> OrganismLogAnalysis:
    """Analyze normalized or raw organism-log rows for visualization."""

    normalized = _normalize_analysis_rows(rows)
    events = [row for row in normalized if row.record_kind == "event"]
    spans = [row for row in normalized if row.record_kind == "span" and row.span_id]
    if not spans:
        return OrganismLogAnalysis(event_count=len(events), span_count=0)

    span_meta = _build_span_meta(spans)
    ordered_ids = sorted(
        span_meta,
        key=lambda span_id: (
            _sort_timestamp(span_meta[span_id]["start_dt"]),
            _sort_timestamp(span_meta[span_id]["end_dt"]),
            int(span_meta[span_id]["row"].sequence or 0),
        ),
    )
    parent_children: dict[str, list[str]] = defaultdict(list)
    for span_id, meta in span_meta.items():
        parent_id = str(meta["row"].parent_span_id or "").strip()
        if parent_id and parent_id in span_meta:
            parent_children[parent_id].append(span_id)

    edges: list[OrganismLogDependencyEdge] = []
    edge_keys: set[tuple[str, str, str]] = set()
    direct_blockers: dict[str, list[str]] = defaultdict(list)

    def add_edge(source: str, target: str, relationship: str) -> None:
        if source == target or source not in span_meta or target not in span_meta:
            return
        key = (source, target, relationship)
        if key in edge_keys:
            return
        edge_keys.add(key)
        lag_ms = _dependency_lag_ms(span_meta[source], span_meta[target])
        edges.append(
            OrganismLogDependencyEdge(
                edge_id=f"{relationship}:{source}->{target}",
                source_span_id=source,
                target_span_id=target,
                relationship=relationship,  # type: ignore[arg-type]
                lag_ms=lag_ms,
            )
        )

    for span_id, meta in span_meta.items():
        parent_id = str(meta["row"].parent_span_id or "").strip()
        if parent_id and parent_id in span_meta:
            add_edge(parent_id, span_id, "parent")

    for span_id, meta in span_meta.items():
        blockers = [
            blocker
            for blocker in list(meta["row"].blocked_by or [])
            if blocker in span_meta and blocker != span_id
        ]
        for blocker in blockers:
            add_edge(blocker, span_id, "blocked_by")
            if blocker not in direct_blockers[span_id]:
                direct_blockers[span_id].append(blocker)

    lane_groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for span_id, meta in span_meta.items():
        lane_groups[(str(meta["lane_id"]), str(meta["row"].parent_span_id or ""))].append(span_id)
    for span_ids in lane_groups.values():
        sorted_ids = sorted(
            span_ids,
            key=lambda span_id: (
                _sort_timestamp(span_meta[span_id]["start_dt"]),
                _sort_timestamp(span_meta[span_id]["end_dt"]),
                int(span_meta[span_id]["row"].sequence or 0),
            ),
        )
        last_finished_id: str | None = None
        for span_id in sorted_ids:
            if (
                last_finished_id
                and _sequential_dependency_candidate(
                    span_meta[last_finished_id],
                    span_meta[span_id],
                )
            ):
                add_edge(last_finished_id, span_id, "lane_sequence")
                if last_finished_id not in direct_blockers[span_id]:
                    direct_blockers[span_id].append(last_finished_id)
            if _is_better_lane_predecessor(
                current=span_meta[span_id],
                previous=span_meta.get(last_finished_id or "", {}),
            ):
                last_finished_id = span_id

    dependents: dict[str, list[str]] = defaultdict(list)
    for target_id, blockers in direct_blockers.items():
        for blocker_id in blockers:
            dependents[blocker_id].append(target_id)

    critical_path_span_ids, critical_path_duration_ms = _critical_path(
        ordered_ids=ordered_ids,
        span_meta=span_meta,
        direct_blockers=direct_blockers,
        parent_children=parent_children,
    )
    critical_ranks = {
        span_id: position
        for position, span_id in enumerate(critical_path_span_ids, start=1)
    }

    analyzed_spans: list[OrganismLogSpanAnalysis] = []
    for span_id in ordered_ids:
        meta = span_meta[span_id]
        row: OrganismLogRow = meta["row"]
        blocker_ids = list(direct_blockers.get(span_id, []))
        analyzed_spans.append(
            OrganismLogSpanAnalysis(
                span_id=span_id,
                label=str(meta["label"]),
                lane_id=str(meta["lane_id"]),
                lane_label=str(meta["lane_label"]),
                parent_span_id=str(row.parent_span_id or ""),
                span_kind=str(row.span_kind or ""),
                event=str(row.event or ""),
                event_family=str(row.event_family or ""),
                status=str(row.status or ""),
                summary=str(row.summary or ""),
                worker_id=str(row.worker_id or ""),
                tool_id=str(row.tool_id or ""),
                tool_call_id=str(row.tool_call_id or ""),
                model_call_id=str(row.model_call_id or ""),
                contract_id=str(row.contract_id or ""),
                start_timestamp=str(meta["start_text"]),
                end_timestamp=str(meta["end_text"]),
                start_ms=meta["start_ms"],
                end_ms=meta["end_ms"],
                duration_ms=meta["duration_ms"],
                exclusive_duration_ms=_exclusive_duration_ms(
                    span_id=span_id,
                    span_meta=span_meta,
                    child_ids=parent_children.get(span_id, []),
                ),
                waiting_duration_ms=_waiting_duration_ms(
                    span_id=span_id,
                    span_meta=span_meta,
                    blocker_ids=blocker_ids,
                ),
                direct_blocker_span_ids=blocker_ids,
                dependent_span_ids=sorted(
                    dependents.get(span_id, []),
                    key=lambda target_id: (
                        span_meta[target_id]["start_ms"] is None,
                        span_meta[target_id]["start_ms"] or 0,
                        int(span_meta[target_id]["row"].sequence or 0),
                    ),
                ),
                critical_path_rank=critical_ranks.get(span_id),
            )
        )

    analyzed_by_id = {span.span_id: span for span in analyzed_spans}
    lane_ids = sorted(
        {span.lane_id for span in analyzed_spans},
        key=lambda lane_id: (
            min(
                (
                    analyzed_by_id[span_id].start_ms
                    for span_id in [
                        span.span_id for span in analyzed_spans if span.lane_id == lane_id
                    ]
                    if analyzed_by_id[span_id].start_ms is not None
                ),
                default=0,
            ),
            lane_id,
        ),
    )
    lanes: list[OrganismLogLaneAnalysis] = []
    for lane_id in lane_ids:
        lane_spans = [span for span in analyzed_spans if span.lane_id == lane_id]
        if not lane_spans:
            continue
        lanes.append(
            OrganismLogLaneAnalysis(
                lane_id=lane_id,
                label=lane_spans[0].lane_label or lane_id,
                started_at=next((span.start_timestamp for span in lane_spans if span.start_timestamp), ""),
                ended_at=next(
                    (
                        span.end_timestamp
                        for span in reversed(lane_spans)
                        if span.end_timestamp
                    ),
                    "",
                ),
                span_ids=[span.span_id for span in lane_spans],
            )
        )

    blocker_targets = [
        span
        for span in analyzed_spans
        if span.direct_blocker_span_ids or span.status in {"failed", "denied", "cancelled", "blocked", "timeout"} or _clean_text(span_meta[span.span_id]["row"].wait_reason)
    ]
    blocker_targets = sorted(
        blocker_targets,
        key=lambda span: (
            span.waiting_duration_ms is None,
            -(span.waiting_duration_ms or 0),
            -(span.duration_ms or 0),
        ),
    )
    blocker_chains = [
        OrganismLogBlockingChain(
            target_span_id=span.span_id,
            direct_blocker_span_ids=list(span.direct_blocker_span_ids),
            blocker_chain_span_ids=_blocker_chain(
                target_span_id=span.span_id,
                direct_blockers=direct_blockers,
                span_meta=span_meta,
            ),
            waiting_duration_ms=span.waiting_duration_ms,
            status=span.status,
            wait_reason=str(span_meta[span.span_id]["row"].wait_reason or ""),
        )
        for span in blocker_targets
    ]

    started_at = next((span.start_timestamp for span in analyzed_spans if span.start_timestamp), "")
    ended_at = next((span.end_timestamp for span in reversed(analyzed_spans) if span.end_timestamp), "")
    duration_ms = None
    if started_at and ended_at:
        duration_ms = max(
            int((_parse_timestamp(ended_at) - _parse_timestamp(started_at)).total_seconds() * 1000),
            0,
        )

    return OrganismLogAnalysis(
        event_count=len(events),
        span_count=len(analyzed_spans),
        timeline=OrganismLogTimelineAnalysis(
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            max_parallel_spans=_max_parallel_spans(span_meta),
            lanes=lanes,
            spans=analyzed_spans,
        ),
        graph=OrganismLogGraphAnalysis(
            nodes=analyzed_spans,
            edges=edges,
            blocker_chains=blocker_chains,
            critical_path_span_ids=critical_path_span_ids,
            critical_path_duration_ms=critical_path_duration_ms,
        ),
    )


def analyze_organism_log(path: str | Path) -> OrganismLogAnalysis:
    """Analyze one on-disk organism-log file."""

    return analyze_organism_log_rows(read_organism_log_rows(path))


def _normalize_analysis_rows(
    rows: Iterable[OrganismLogRow | dict[str, Any]],
) -> list[OrganismLogRow]:
    normalized: list[OrganismLogRow] = []
    raw_rows: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, OrganismLogRow):
            normalized.append(row)
            continue
        payload = dict(row)
        if str(payload.get("record_kind") or "").strip():
            normalized.append(OrganismLogRow.model_validate(payload))
        else:
            raw_rows.append(payload)
    if raw_rows:
        normalized.extend(normalize_organism_log_rows(raw_rows))
    return sorted(
        normalized,
        key=lambda row: (int(row.sequence or 0), 0 if row.record_kind == "event" else 1),
    )


def _build_span_meta(spans: list[OrganismLogRow]) -> dict[str, dict[str, Any]]:
    anchor_dt = min(
        (
            _span_start_dt(span)
            for span in spans
            if _span_start_dt(span) is not None
        ),
        default=None,
    )
    meta: dict[str, dict[str, Any]] = {}
    for span in spans:
        start_dt = _span_start_dt(span)
        end_dt = _span_end_dt(span, start_dt=start_dt)
        lane_id = _resolve_lane_id(span)
        label = _resolve_label(span)
        meta[span.span_id] = {
            "row": span,
            "start_dt": start_dt,
            "end_dt": end_dt,
            "start_text": str(span.start_timestamp or span.timestamp or ""),
            "end_text": str(span.end_timestamp or span.timestamp or ""),
            "start_ms": _relative_ms(anchor_dt, start_dt),
            "end_ms": _relative_ms(anchor_dt, end_dt),
            "duration_ms": _span_duration_ms(span, start_dt=start_dt, end_dt=end_dt),
            "lane_id": lane_id,
            "lane_label": lane_id,
            "label": label,
        }
    return meta


def _resolve_lane_id(span: OrganismLogRow) -> str:
    return (
        _clean_text(span.parallel_lane)
        or _clean_text(span.worker_id)
        or _clean_text(span.span_kind)
        or _clean_text(span.event_family)
        or "main"
    )


def _resolve_label(span: OrganismLogRow) -> str:
    return (
        _clean_text(span.summary)
        or _clean_text(span.tool_id)
        or _clean_text(span.worker_id)
        or _clean_text(span.event)
        or span.span_id
    )


def _span_start_dt(span: OrganismLogRow) -> datetime | None:
    return _parse_timestamp(span.start_timestamp or span.timestamp or "")


def _span_end_dt(
    span: OrganismLogRow,
    *,
    start_dt: datetime | None,
) -> datetime | None:
    end_dt = _parse_timestamp(span.end_timestamp or span.timestamp or "")
    if end_dt is not None:
        return end_dt
    if start_dt is not None and span.duration_ms is not None:
        return start_dt + timedelta(milliseconds=int(span.duration_ms))
    return None


def _span_duration_ms(
    span: OrganismLogRow,
    *,
    start_dt: datetime | None,
    end_dt: datetime | None,
) -> int | None:
    if span.duration_ms is not None:
        return int(span.duration_ms)
    if start_dt is None or end_dt is None:
        return None
    return max(int((end_dt - start_dt).total_seconds() * 1000), 0)


def _relative_ms(anchor_dt: datetime | None, value: datetime | None) -> int | None:
    if anchor_dt is None or value is None:
        return None
    return max(int((value - anchor_dt).total_seconds() * 1000), 0)


def _dependency_lag_ms(source_meta: dict[str, Any], target_meta: dict[str, Any]) -> int | None:
    source_end = source_meta.get("end_dt")
    target_start = target_meta.get("start_dt")
    if source_end is None or target_start is None:
        return None
    return max(int((target_start - source_end).total_seconds() * 1000), 0)


def _exclusive_duration_ms(
    *,
    span_id: str,
    span_meta: dict[str, dict[str, Any]],
    child_ids: list[str],
) -> int | None:
    meta = span_meta[span_id]
    duration_ms = meta.get("duration_ms")
    start_dt = meta.get("start_dt")
    end_dt = meta.get("end_dt")
    if duration_ms is None or start_dt is None or end_dt is None:
        return duration_ms
    child_ranges: list[tuple[datetime, datetime]] = []
    for child_id in child_ids:
        child_meta = span_meta.get(child_id)
        if not child_meta:
            continue
        child_start = child_meta.get("start_dt")
        child_end = child_meta.get("end_dt")
        if child_start is None or child_end is None:
            continue
        clipped_start = max(start_dt, child_start)
        clipped_end = min(end_dt, child_end)
        if clipped_end > clipped_start:
            child_ranges.append((clipped_start, clipped_end))
    if not child_ranges:
        return int(duration_ms)
    child_ranges.sort(key=lambda item: item[0])
    merged: list[tuple[datetime, datetime]] = []
    for start, end in child_ranges:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    covered_ms = sum(
        max(int((end - start).total_seconds() * 1000), 0)
        for start, end in merged
    )
    return max(int(duration_ms) - covered_ms, 0)


def _waiting_duration_ms(
    *,
    span_id: str,
    span_meta: dict[str, dict[str, Any]],
    blocker_ids: list[str],
) -> int | None:
    row: OrganismLogRow = span_meta[span_id]["row"]
    if str(row.span_kind or "").endswith("_wait") and span_meta[span_id].get("duration_ms") is not None:
        return int(span_meta[span_id]["duration_ms"])
    if not blocker_ids:
        return None
    start_dt = span_meta[span_id].get("start_dt")
    if start_dt is None:
        return None
    blocker_ends = [
        span_meta[blocker_id].get("end_dt")
        for blocker_id in blocker_ids
        if blocker_id in span_meta and span_meta[blocker_id].get("end_dt") is not None
    ]
    if not blocker_ends:
        return None
    latest_end = max(blocker_ends)
    return max(int((start_dt - latest_end).total_seconds() * 1000), 0)


def _critical_path(
    *,
    ordered_ids: list[str],
    span_meta: dict[str, dict[str, Any]],
    direct_blockers: dict[str, list[str]],
    parent_children: dict[str, list[str]],
) -> tuple[list[str], int | None]:
    scores: dict[str, int] = {}
    back: dict[str, str | None] = {}
    best_id: str | None = None
    for span_id in ordered_ids:
        weight = _critical_path_weight(
            span_id=span_id,
            span_meta=span_meta,
            child_ids=parent_children.get(span_id, []),
        )
        best_score = weight
        best_prev: str | None = None
        for blocker_id in direct_blockers.get(span_id, []):
            if blocker_id not in scores:
                continue
            lag_ms = _dependency_lag_ms(span_meta[blocker_id], span_meta[span_id]) or 0
            candidate = scores[blocker_id] + lag_ms + weight
            if candidate > best_score:
                best_score = candidate
                best_prev = blocker_id
        scores[span_id] = best_score
        back[span_id] = best_prev
        if best_id is None or best_score > scores.get(best_id, 0):
            best_id = span_id
    if best_id is None:
        return [], None
    chain: list[str] = []
    cursor: str | None = best_id
    seen: set[str] = set()
    while cursor and cursor not in seen:
        seen.add(cursor)
        chain.append(cursor)
        cursor = back.get(cursor)
    chain.reverse()
    return chain, scores.get(best_id)


def _critical_path_weight(
    *,
    span_id: str,
    span_meta: dict[str, dict[str, Any]],
    child_ids: list[str],
) -> int:
    exclusive = _exclusive_duration_ms(
        span_id=span_id,
        span_meta=span_meta,
        child_ids=child_ids,
    )
    if exclusive is not None:
        return int(exclusive)
    duration_ms = span_meta[span_id].get("duration_ms")
    return int(duration_ms or 0)


def _blocker_chain(
    *,
    target_span_id: str,
    direct_blockers: dict[str, list[str]],
    span_meta: dict[str, dict[str, Any]],
) -> list[str]:
    chain: list[str] = []
    current = target_span_id
    seen: set[str] = set()
    while direct_blockers.get(current):
        blockers = [
            blocker_id
            for blocker_id in direct_blockers[current]
            if blocker_id in span_meta
        ]
        if not blockers:
            break
        blocker_id = max(
            blockers,
            key=lambda item: (
                _sort_timestamp(span_meta[item].get("end_dt")),
                _sort_timestamp(span_meta[item].get("start_dt")),
                int(span_meta[item]["row"].sequence or 0),
            ),
        )
        if blocker_id in seen:
            break
        chain.append(blocker_id)
        seen.add(blocker_id)
        current = blocker_id
    chain.reverse()
    return chain


def _max_parallel_spans(span_meta: dict[str, dict[str, Any]]) -> int:
    boundaries: list[tuple[datetime, int]] = []
    for meta in span_meta.values():
        start_dt = meta.get("start_dt")
        end_dt = meta.get("end_dt")
        if start_dt is None or end_dt is None:
            continue
        boundaries.append((start_dt, 1))
        boundaries.append((end_dt, -1))
    if not boundaries:
        return 0
    boundaries.sort(key=lambda item: (item[0], item[1]))
    active = 0
    maximum = 0
    for _timestamp, delta in boundaries:
        active += delta
        maximum = max(maximum, active)
    return maximum


def _sequential_dependency_candidate(
    source_meta: dict[str, Any],
    target_meta: dict[str, Any],
) -> bool:
    source_end = source_meta.get("end_dt")
    target_start = target_meta.get("start_dt")
    if source_end is None or target_start is None:
        return False
    return source_end <= target_start


def _is_better_lane_predecessor(
    *,
    current: dict[str, Any],
    previous: dict[str, Any],
) -> bool:
    previous_end = previous.get("end_dt")
    current_end = current.get("end_dt")
    if previous_end is None:
        return True
    if current_end is None:
        return False
    return current_end >= previous_end


def _parse_timestamp(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _sort_timestamp(value: datetime | None) -> float:
    if value is None:
        return float("inf")
    return value.timestamp()


__all__ = [
    "OrganismLogAnalysis",
    "OrganismLogBlockingChain",
    "OrganismLogDependencyEdge",
    "OrganismLogGraphAnalysis",
    "OrganismLogLaneAnalysis",
    "OrganismLogSpanAnalysis",
    "OrganismLogTimelineAnalysis",
    "analyze_organism_log",
    "analyze_organism_log_rows",
]
