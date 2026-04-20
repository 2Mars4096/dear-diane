"""Adapters for importing foreign logs into ``organism_log_v1``."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

from dan.worker.organism_log import (
    ORGANISM_LOG_SCHEMA,
    ORGANISM_LOG_SCHEMA_VERSION,
    normalize_organism_log_rows,
    summarize_organism_log_rows,
    write_organism_log,
)

SUPPORTED_ORGANISM_LOG_ADAPTERS = (
    "auto",
    "organism_log_v1",
    "generic_json",
)

_CANONICAL_IMPORT_FIELDS = frozenset(
    {
        "timestamp",
        "start_timestamp",
        "end_timestamp",
        "event",
        "status",
        "span_id",
        "parent_span_id",
        "trace_id",
        "session_id",
        "turn_id",
        "task_id",
        "worker_id",
        "tool_id",
        "tool_call_id",
        "model_call_id",
        "contract_id",
        "attempt",
        "parallel_lane",
        "blocked_by",
        "wait_reason",
    }
)

_COMMON_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "ts", "time", "created_at", "emitted_at", "occurred_at"),
    "start_timestamp": (
        "started_at",
        "start_timestamp",
        "start_time",
        "request_started_at",
        "began_at",
    ),
    "end_timestamp": (
        "ended_at",
        "end_timestamp",
        "end_time",
        "finished_at",
        "completed_at",
        "response_completed_at",
    ),
    "event": ("event", "event_type", "type", "kind", "name", "action"),
    "status": ("status", "state", "result", "outcome", "finish_reason"),
    "span_id": (
        "span_id",
        "call_id",
        "operation_id",
        "op_id",
        "activity_id",
        "request_id",
        "id",
    ),
    "parent_span_id": (
        "parent_span_id",
        "parent_id",
        "parent",
        "parent_call_id",
        "parent_request_id",
    ),
    "trace_id": ("trace_id", "trace", "run_id", "workflow_id"),
    "session_id": ("session_id", "conversation_id", "thread_id"),
    "turn_id": ("turn_id", "turn", "step_id"),
    "task_id": ("task_id", "task", "job_id"),
    "worker_id": ("worker_id", "worker", "agent_id", "agent", "node_id", "actor_id"),
    "tool_id": ("tool_id", "tool", "tool_name", "function_name", "function"),
    "tool_call_id": ("tool_call_id", "tool_request_id", "function_call_id"),
    "model_call_id": (
        "model_call_id",
        "llm_call_id",
        "completion_id",
        "response_id",
        "model_request_id",
    ),
    "contract_id": ("contract_id",),
    "attempt": ("attempt", "retry", "retry_count", "review_round"),
    "parallel_lane": ("parallel_lane", "lane", "thread", "worker", "agent_id"),
    "blocked_by": ("blocked_by", "blocked_on", "blocked_by_ids", "blocked_on_ids", "waiting_for"),
    "wait_reason": ("wait_reason", "blocked_reason", "reason", "error"),
}

_START_TOKENS = {
    "start",
    "started",
    "request",
    "requested",
    "begin",
    "began",
    "queue",
    "queued",
    "open",
    "opened",
}
_END_TOKENS = {
    "end",
    "ended",
    "complete",
    "completed",
    "finish",
    "finished",
    "respond",
    "responded",
    "success",
    "succeeded",
    "done",
    "close",
    "closed",
}
_FAIL_TOKENS = {
    "fail",
    "failed",
    "error",
    "denied",
    "cancel",
    "cancelled",
    "canceled",
    "timeout",
    "timed",
    "blocked",
}
_SUCCESS_STATUSES = {"completed", "success", "succeeded", "responded", "done", "passed"}
_FAIL_STATUSES = {"failed", "error", "denied", "cancelled", "canceled", "timeout", "blocked"}
_CARRY_FIELDS = (
    "message",
    "summary",
    "detail",
    "error",
    "objective",
    "model",
    "arguments",
    "result",
    "output",
    "input",
    "name",
    "kind",
    "type",
    "event_type",
)


class OrganismLogImportConfig(BaseModel):
    """Import-time config for normalizing foreign logs."""

    adapter: Literal["auto", "organism_log_v1", "generic_json"] = "auto"
    product: str = ""
    stream_kind: str = ""
    session_id: str = ""
    turn_id: str = ""
    task_id: str = ""
    trace_id: str = ""
    organism_id: str = ""
    organ_id: str = ""
    field_aliases: dict[str, str] = Field(default_factory=dict)


def parse_field_aliases(items: Iterable[str] | None) -> dict[str, str]:
    """Parse repeated ``canonical=source`` CLI overrides."""

    aliases: dict[str, str] = {}
    for item in items or []:
        text = str(item or "").strip()
        if not text:
            continue
        canonical, marker, source = text.partition("=")
        if marker != "=":
            raise ValueError(f"Invalid field override {text!r}; expected canonical=source")
        canonical_key = canonical.strip()
        source_key = source.strip()
        if canonical_key not in _CANONICAL_IMPORT_FIELDS:
            supported = ", ".join(sorted(_CANONICAL_IMPORT_FIELDS))
            raise ValueError(
                f"Unsupported field override {canonical_key!r}; expected one of: {supported}"
            )
        if not source_key:
            raise ValueError(f"Invalid field override {text!r}; source field is empty")
        aliases[canonical_key] = source_key
    return aliases


def read_import_rows(path: str | Path) -> list[dict[str, Any]]:
    """Read JSON or JSONL input rows for organism-log import."""

    resolved = Path(path).expanduser().resolve()
    text = resolved.read_text(encoding="utf-8")
    if not text.strip():
        return []

    if resolved.suffix.lower() == ".json" or text.lstrip().startswith("["):
        payload = json.loads(text)
        if isinstance(payload, list):
            return [dict(row) for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("rows", "events", "items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [dict(row) for row in value if isinstance(row, dict)]
            return [dict(payload)]
        return []

    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            rows.append(dict(payload))
    return rows


def detect_organism_log_adapter(rows: Iterable[dict[str, Any]]) -> str:
    """Return the best import adapter for the given raw rows."""

    sample = [dict(row) for row in rows]
    if sample and all(str(row.get("schema") or "") == ORGANISM_LOG_SCHEMA for row in sample):
        return "organism_log_v1"
    return "generic_json"


def normalize_import_rows(
    rows: Iterable[dict[str, Any]],
    *,
    config: OrganismLogImportConfig | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Normalize foreign rows into raw ``organism_log_v1`` rows."""

    resolved_config = config or OrganismLogImportConfig()
    sample = [dict(row) for row in rows]
    adapter = (
        detect_organism_log_adapter(sample)
        if resolved_config.adapter == "auto"
        else resolved_config.adapter
    )
    if adapter == "organism_log_v1":
        return adapter, [_normalize_passthrough_row(row, config=resolved_config) for row in sample]
    return adapter, _normalize_generic_rows(sample, config=resolved_config)


def import_organism_log(
    source_path: str | Path,
    *,
    output_path: str | Path,
    config: OrganismLogImportConfig | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Normalize one source log file and write a canonical ``organism_log_v1`` file."""

    source_rows = read_import_rows(source_path)
    adapter, normalized_rows = normalize_import_rows(source_rows, config=config)
    resolved_output = write_organism_log(output_path, normalized_rows)
    normalized = normalize_organism_log_rows(read_import_rows(resolved_output))
    summary = summarize_organism_log_rows(normalized, limit=limit)
    return {
        "schema_version": ORGANISM_LOG_SCHEMA_VERSION,
        "adapter": adapter,
        "source_path": str(Path(source_path).expanduser().resolve()),
        "output_path": str(resolved_output),
        "source_row_count": len(source_rows),
        "normalized_row_count": len(normalized_rows),
        **summary,
    }


def summarize_import_rows(
    rows: Iterable[dict[str, Any]],
    *,
    config: OrganismLogImportConfig | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Summarize raw or normalized import rows without writing them to disk."""

    sample = [dict(row) for row in rows]
    adapter, normalized_rows = normalize_import_rows(sample, config=config)
    normalized = normalize_organism_log_rows(_with_sequence(normalized_rows))
    return {
        "schema_version": ORGANISM_LOG_SCHEMA_VERSION,
        "adapter": adapter,
        "source_row_count": len(sample),
        "normalized_row_count": len(normalized_rows),
        **summarize_organism_log_rows(normalized, limit=limit),
    }


def _normalize_passthrough_row(
    row: dict[str, Any],
    *,
    config: OrganismLogImportConfig,
) -> dict[str, Any]:
    payload = dict(row)
    payload.pop("sequence", None)
    payload["schema"] = ORGANISM_LOG_SCHEMA
    payload["stream"] = _resolved_text(
        config.stream_kind,
        payload.get("stream"),
        payload.get("stream_kind"),
        default="run",
    )
    payload["product"] = _resolved_text(config.product, payload.get("product"))
    payload["session_id"] = _resolved_text(config.session_id, payload.get("session_id"))
    payload["turn_id"] = _resolved_text(config.turn_id, payload.get("turn_id"))
    payload["task_id"] = _resolved_text(config.task_id, payload.get("task_id"))
    payload["trace_id"] = _resolved_text(config.trace_id, payload.get("trace_id"))
    payload["organism_id"] = _resolved_text(config.organism_id, payload.get("organism_id"))
    payload["organ_id"] = _resolved_text(config.organ_id, payload.get("organ_id"))
    payload["source_adapter"] = "organism_log_v1"
    return {key: value for key, value in payload.items() if value is not None}


def _normalize_generic_rows(
    rows: list[dict[str, Any]],
    *,
    config: OrganismLogImportConfig,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        normalized.extend(_normalize_generic_row(row, index=index, config=config))
    return normalized


def _normalize_generic_row(
    row: dict[str, Any],
    *,
    index: int,
    config: OrganismLogImportConfig,
) -> list[dict[str, Any]]:
    event_text = _clean_text(_lookup(row, "event", config.field_aliases)) or "external.event"
    status = _normalize_status(_lookup(row, "status", config.field_aliases))
    start_timestamp = _clean_text(_lookup(row, "start_timestamp", config.field_aliases))
    end_timestamp = _clean_text(_lookup(row, "end_timestamp", config.field_aliases))
    event_timestamp = _clean_text(_lookup(row, "timestamp", config.field_aliases))
    timestamp = event_timestamp or end_timestamp or start_timestamp or ""
    span_kind = _infer_span_kind(row, event_text=event_text, aliases=config.field_aliases)
    lifecycle = _infer_lifecycle(
        row,
        event_text=event_text,
        status=status,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )
    span_id = _infer_span_id(
        row,
        index=index,
        event_text=event_text,
        span_kind=span_kind,
        lifecycle=lifecycle,
        aliases=config.field_aliases,
    )
    parent_span_id = _clean_text(_lookup(row, "parent_span_id", config.field_aliases))
    blocked_by = _normalize_list(_lookup(row, "blocked_by", config.field_aliases))
    wait_reason = _clean_text(_lookup(row, "wait_reason", config.field_aliases))
    trace_id = _resolved_text(
        config.trace_id,
        _lookup(row, "trace_id", config.field_aliases),
        _lookup(row, "session_id", config.field_aliases),
    )
    session_id = _resolved_text(
        config.session_id,
        _lookup(row, "session_id", config.field_aliases),
        _lookup(row, "trace_id", config.field_aliases),
    )
    turn_id = _resolved_text(config.turn_id, _lookup(row, "turn_id", config.field_aliases))
    task_id = _resolved_text(
        config.task_id,
        _lookup(row, "task_id", config.field_aliases),
        trace_id,
    )
    worker_id = _clean_text(_lookup(row, "worker_id", config.field_aliases))
    tool_id = _clean_text(_lookup(row, "tool_id", config.field_aliases))
    tool_call_id = _clean_text(_lookup(row, "tool_call_id", config.field_aliases))
    model_call_id = _clean_text(_lookup(row, "model_call_id", config.field_aliases))
    contract_id = _clean_text(_lookup(row, "contract_id", config.field_aliases))
    attempt = _normalize_int(_lookup(row, "attempt", config.field_aliases))
    parallel_lane = _resolved_text(
        _lookup(row, "parallel_lane", config.field_aliases),
        worker_id,
    )
    base_row = {
        "schema": ORGANISM_LOG_SCHEMA,
        "stream": config.stream_kind or str(row.get("stream") or row.get("stream_kind") or "run"),
        "product": config.product or str(row.get("product") or "external"),
        "session_id": session_id,
        "turn_id": turn_id,
        "task_id": task_id,
        "trace_id": trace_id,
        "organism_id": _resolved_text(config.organism_id, row.get("organism_id")),
        "organ_id": _resolved_text(config.organ_id, row.get("organ_id")),
        "parent_span_id": parent_span_id,
        "span_kind": span_kind,
        "span_id": span_id,
        "worker_id": worker_id,
        "tool_id": tool_id,
        "tool_call_id": tool_call_id or (span_id if span_kind == "tool_call" else ""),
        "model_call_id": model_call_id or (span_id if span_kind == "model_call" else ""),
        "contract_id": contract_id,
        "attempt": attempt,
        "parallel_lane": parallel_lane,
        "blocked_by": blocked_by or None,
        "wait_reason": wait_reason,
        "source_adapter": "generic_json",
        "source_index": index,
        "source_event": event_text,
        "source_status": status,
        **_carry_source_fields(row),
    }
    if lifecycle == "closed_span" and span_id:
        start_row = _compact_row(
            {
                **base_row,
                "timestamp": start_timestamp or timestamp,
                "event": _canonical_event_name(
                    event_text=event_text,
                    span_kind=span_kind,
                    lifecycle="span_start",
                    status="started",
                ),
                "row_kind": "span_start",
                "status": "started",
            }
        )
        end_status = status or _status_from_event(event_text) or "completed"
        end_row = _compact_row(
            {
                **base_row,
                "timestamp": end_timestamp or timestamp or start_timestamp,
                "event": _canonical_event_name(
                    event_text=event_text,
                    span_kind=span_kind,
                    lifecycle="span_end",
                    status=end_status,
                ),
                "row_kind": "span_end",
                "status": end_status,
                "duration_ms": _duration_ms(start_timestamp or timestamp, end_timestamp or timestamp),
            }
        )
        return [start_row, end_row]
    if lifecycle in {"span_start", "span_end"} and span_id:
        row_kind = lifecycle
        resolved_status = "started" if lifecycle == "span_start" else (status or _status_from_event(event_text))
        normalized = _compact_row(
            {
                **base_row,
                "timestamp": timestamp or start_timestamp or end_timestamp,
                "event": _canonical_event_name(
                    event_text=event_text,
                    span_kind=span_kind,
                    lifecycle=lifecycle,
                    status=resolved_status,
                ),
                "row_kind": row_kind,
                "status": resolved_status,
                "duration_ms": (
                    _normalize_int(row.get("duration_ms"))
                    if lifecycle == "span_end"
                    else None
                ),
            }
        )
        return [normalized]
    return [
        _compact_row(
            {
                **base_row,
                "timestamp": timestamp or start_timestamp or end_timestamp,
                "event": _canonical_event_name(
                    event_text=event_text,
                    span_kind=span_kind,
                    lifecycle="event",
                    status=status,
                ),
                "row_kind": "event",
                "status": status,
                "span_id": None,
                "span_kind": None,
            }
        )
    ]


def _lookup(
    row: dict[str, Any],
    canonical_field: str,
    aliases: dict[str, str],
) -> Any:
    source_field = str(aliases.get(canonical_field) or "").strip()
    if source_field and source_field in row:
        return row[source_field]
    for candidate in _COMMON_FIELD_CANDIDATES.get(canonical_field, ()):
        if candidate in row:
            return row[candidate]
    return None


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _resolved_text(*values: Any, default: str = "") -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return default


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        values = [str(item).strip() for item in value if str(item).strip()]
        return values
    return [str(value).strip()] if str(value).strip() else []


def _normalize_int(value: Any) -> int | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _normalize_status(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    tokens = _event_tokens(text)
    if text.lower() in _SUCCESS_STATUSES or tokens & _SUCCESS_STATUSES:
        return "completed"
    if text.lower() in _FAIL_STATUSES or tokens & _FAIL_TOKENS:
        lowered = text.lower()
        if "deny" in lowered:
            return "denied"
        if "cancel" in lowered:
            return "cancelled"
        if "block" in lowered:
            return "blocked"
        if "time" in lowered:
            return "timeout"
        return "failed"
    if "start" in tokens or "request" in tokens:
        return "started"
    return text.lower()


def _infer_lifecycle(
    row: dict[str, Any],
    *,
    event_text: str,
    status: str,
    start_timestamp: str | None,
    end_timestamp: str | None,
) -> str:
    explicit = _clean_text(row.get("row_kind"))
    if explicit in {"event", "span_start", "span_end"}:
        return explicit
    if (start_timestamp and end_timestamp) or (
        _normalize_int(row.get("duration_ms")) is not None and (start_timestamp or end_timestamp)
    ):
        return "closed_span"
    if status == "started":
        return "span_start"
    if status in {"completed", "failed", "denied", "cancelled", "blocked", "timeout"}:
        return "span_end"
    tokens = _event_tokens(event_text)
    if tokens & _START_TOKENS and not tokens & (_END_TOKENS | _FAIL_TOKENS):
        return "span_start"
    if tokens & (_END_TOKENS | _FAIL_TOKENS):
        return "span_end"
    return "event"


def _infer_span_kind(
    row: dict[str, Any],
    *,
    event_text: str,
    aliases: dict[str, str],
) -> str:
    explicit = _clean_text(row.get("span_kind"))
    if explicit:
        return explicit
    tokens = _event_tokens(event_text)
    if _lookup(row, "tool_call_id", aliases) or _lookup(row, "tool_id", aliases) or "tool" in tokens:
        return "tool_call"
    if _lookup(row, "model_call_id", aliases) or {"model", "llm", "completion"} & tokens:
        return "model_call"
    if {"run", "workflow"} & tokens:
        return "run"
    if {"organism"} & tokens:
        return "organism"
    if {"stage", "phase"} & tokens:
        return "organism_stage"
    return "control_stage"


def _infer_span_id(
    row: dict[str, Any],
    *,
    index: int,
    event_text: str,
    span_kind: str,
    lifecycle: str,
    aliases: dict[str, str],
) -> str | None:
    for field in ("span_id", "tool_call_id", "model_call_id"):
        text = _clean_text(_lookup(row, field, aliases))
        if text:
            return text
    if lifecycle == "event":
        return None
    tokens = _event_tokens(event_text)
    trace_id = _clean_text(_lookup(row, "trace_id", aliases) or _lookup(row, "session_id", aliases))
    task_id = _clean_text(_lookup(row, "task_id", aliases))
    worker_id = _clean_text(_lookup(row, "worker_id", aliases))
    tool_id = _clean_text(_lookup(row, "tool_id", aliases))
    attempt = _clean_text(_lookup(row, "attempt", aliases))
    stage_name = _clean_text(row.get("stage"))
    event_base = _event_base(event_text)
    if span_kind == "run" and (task_id or trace_id):
        return f"run:{task_id or trace_id}"
    if span_kind == "organism" and (trace_id or task_id):
        return f"organism:{trace_id or task_id}"
    if span_kind == "organism_stage" and (trace_id or task_id or stage_name):
        return (
            f"stage:{trace_id or task_id or 'import'}:{attempt or '0'}:"
            f"{stage_name or event_base}"
        )
    digest_parts = [
        span_kind,
        event_base,
        trace_id or "",
        task_id or "",
        worker_id or "",
        tool_id or "",
        attempt or "",
    ]
    if sum(1 for value in digest_parts if value) >= 3:
        return f"imported-span:{_stable_digest(digest_parts)}"
    if lifecycle == "closed_span":
        return f"imported-span:{index}"
    if tokens & {"tool", "model", "llm", "stage", "run"}:
        return f"imported-span:{_stable_digest([event_text, trace_id or '', task_id or '', str(index)])}"
    return None


def _status_from_event(event_text: str) -> str:
    tokens = _event_tokens(event_text)
    if tokens & _FAIL_TOKENS:
        if {"deny", "denied"} & tokens:
            return "denied"
        if {"cancel", "cancelled", "canceled"} & tokens:
            return "cancelled"
        if {"block", "blocked"} & tokens:
            return "blocked"
        if {"timeout", "timed"} & tokens:
            return "timeout"
        return "failed"
    if tokens & _END_TOKENS:
        return "completed"
    if tokens & _START_TOKENS:
        return "started"
    return ""


def _canonical_event_name(
    *,
    event_text: str,
    span_kind: str,
    lifecycle: str,
    status: str,
) -> str:
    if span_kind == "tool_call":
        if lifecycle == "span_start":
            return "tool.started"
        if status == "denied":
            return "tool.denied"
        return "tool.failed" if status in _FAIL_STATUSES else "tool.completed"
    if span_kind == "model_call":
        if lifecycle == "span_start":
            return "model.requested"
        if status == "timeout":
            return "model.timeout"
        return "model.failed" if status in _FAIL_STATUSES else "model.responded"
    if span_kind == "run":
        if lifecycle == "span_start":
            return "run.log.started"
        return "run.log.failed" if status in _FAIL_STATUSES else "run.log.completed"
    if span_kind == "organism":
        if lifecycle == "span_start":
            return "organism.started"
        return "organism.failed" if status in _FAIL_STATUSES else "organism.completed"
    base = _event_base(event_text) or "external"
    if lifecycle == "span_start":
        return f"{base}.started"
    if lifecycle == "span_end":
        return f"{base}.failed" if status in _FAIL_STATUSES else f"{base}.completed"
    return f"{base}.event"


def _event_base(event_text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", ".", str(event_text or "").strip().lower()).strip(".")
    if not cleaned:
        return "external"
    parts = [part for part in cleaned.split(".") if part]
    while parts and parts[-1] in (_START_TOKENS | _END_TOKENS | _FAIL_TOKENS):
        parts.pop()
    return ".".join(parts) or "external"


def _event_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _stable_digest(parts: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _carry_source_fields(row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in _CARRY_FIELDS:
        if key in row and row[key] is not None:
            payload[key] = row[key]
    return payload


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if value is not None and not (isinstance(value, str) and not value.strip())
    }


def _with_sequence(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        payload = dict(row)
        payload["schema"] = str(payload.get("schema") or ORGANISM_LOG_SCHEMA)
        payload["sequence"] = index
        if not _clean_text(payload.get("stream")):
            payload["stream"] = str(payload.get("stream_kind") or "run")
        payloads.append(payload)
    return payloads


def _duration_ms(started_at: Any, ended_at: Any) -> int | None:
    start_text = _clean_text(started_at)
    end_text = _clean_text(ended_at)
    if not start_text or not end_text:
        return None
    try:
        start_dt = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(int((end_dt - start_dt).total_seconds() * 1000), 0)


__all__ = [
    "OrganismLogImportConfig",
    "SUPPORTED_ORGANISM_LOG_ADAPTERS",
    "detect_organism_log_adapter",
    "import_organism_log",
    "normalize_import_rows",
    "parse_field_aliases",
    "read_import_rows",
    "summarize_import_rows",
]
