"""Adapters from organism event rows into Chat/Agent V2 events."""

from __future__ import annotations

from typing import Any

from dan.server.chat_v2 import (
    AgentRunEvent,
    AgentRunEventType,
    format_token_usage,
    normalize_token_usage,
)


def map_organism_log_row_to_agent_event(
    row: dict[str, Any],
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    source_event_path: str | None = None,
) -> AgentRunEvent:
    """Project a raw organism log row into a normalized Agent event."""

    event_name = _event_name(row)
    event_type = _agent_event_type(event_name, row)
    payload = dict(row)
    artifact_refs = _artifact_refs(payload)
    usage_delta = _token_usage_delta(payload)
    usage_total = _token_usage_total(payload)
    return AgentRunEvent(
        type=event_type,
        run_id=run_id,
        task_id=task_id or _clean(row.get("task_id")),
        summary=_summary(row, event_name, usage_delta=usage_delta, usage_total=usage_total),
        artifact_refs=artifact_refs,
        source_event_id=_clean(
            row.get("record_id")
            or row.get("event_id")
            or row.get("id")
            or row.get("sequence")
        ),
        source_event_type=event_name,
        source_event_path=source_event_path,
        token_usage_delta=usage_delta,
        token_usage_total=usage_total,
        token_usage_round=_token_usage_round(payload, usage_delta=usage_delta, usage_total=usage_total),
        payload=payload,
    )


def map_organism_log_rows_to_agent_events(
    rows: list[dict[str, Any]],
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    source_event_path: str | None = None,
) -> list[AgentRunEvent]:
    return [
        map_organism_log_row_to_agent_event(
            row,
            run_id=run_id,
            task_id=task_id,
            source_event_path=source_event_path,
        )
        for row in rows
    ]


def _agent_event_type(event_name: str, row: dict[str, Any]) -> AgentRunEventType:
    status = _clean(row.get("status")).lower()
    lower = event_name.lower()
    if status in {"failed", "error", "timeout"} or lower.endswith(".failed"):
        return "failed"
    if status in {"blocked", "invalid"} or lower.endswith(".blocked"):
        return "blocked"
    if status in {"cancelled", "canceled", "stopped"} or lower.endswith(".stopped"):
        return "stopped"
    if lower in {"organism.completed", "run.log.completed"}:
        return "completed"
    if lower.endswith(".completed") and lower.startswith(("agent.", "organism.")):
        return "completed"
    if lower.startswith("contract.validation.started"):
        return "validation_started"
    if lower.startswith("contract.repair.started"):
        return "repair_started"
    if lower == "model.responded" and _token_usage_delta(row):
        return "token_usage_recorded"
    if lower.startswith("tool."):
        return "tool_used"
    if lower.startswith("model."):
        return "model_text_delta"
    if lower.startswith("worker.started") or lower == "worker.started":
        return "worker_started"
    if lower.startswith("worker."):
        return "artifact_changed"
    if lower.startswith("stage."):
        return "planned"
    if lower.startswith("agent.mailbox."):
        return "queue_item_injected"
    if lower.startswith("super.hook."):
        return "artifact_changed"
    if lower.startswith("live."):
        return "worker_started"
    return "planned"


def _event_name(row: dict[str, Any]) -> str:
    for key in ("event", "event_type", "type", "kind", "name"):
        text = _clean(row.get(key))
        if text:
            return text
    return "organism.event"


def _summary(
    row: dict[str, Any],
    event_name: str,
    *,
    usage_delta: dict[str, int] | None = None,
    usage_total: dict[str, int] | None = None,
) -> str:
    usage_delta = usage_delta or {}
    usage_total = usage_total or {}
    if event_name.lower() == "model.responded" and usage_delta:
        round_id = _clean(row.get("round"))
        prefix = f"Token usage for round {round_id}" if round_id else "Token usage"
        total_text = format_token_usage(usage_total) if usage_total else ""
        if total_text and total_text != "unavailable":
            return (
                f"{prefix}: {format_token_usage(usage_delta)} "
                f"(run total: {total_text})."
            )
        return f"{prefix}: {format_token_usage(usage_delta)}."
    for key in ("summary", "message", "text", "status"):
        text = _clean(row.get(key))
        if text:
            return text
    return event_name


def _artifact_refs(row: dict[str, Any]) -> list[dict[str, Any]]:
    payload = row.get("payload")
    candidates: list[Any] = []
    if isinstance(payload, dict):
        candidates.extend(
            [
                payload.get("artifact_refs"),
                payload.get("artifacts"),
                payload.get("changed_files"),
                payload.get("changed_paths"),
            ]
        )
    candidates.extend(
        [
            row.get("artifact_refs"),
            row.get("artifacts"),
            row.get("changed_files"),
            row.get("changed_paths"),
        ]
    )
    refs: list[dict[str, Any]] = []
    for value in candidates:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    refs.append(dict(item))
                elif item:
                    refs.append({"path": str(item)})
        elif isinstance(value, str) and value:
            refs.append({"path": value})
    return refs


def _token_usage_delta(row: dict[str, Any]) -> dict[str, int]:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    for value in (
        row.get("token_usage_delta"),
        row.get("usage"),
        payload.get("token_usage_delta"),
        payload.get("usage"),
    ):
        usage = normalize_token_usage(value)
        if usage:
            return usage
    provider_result = row.get("provider_result")
    if isinstance(provider_result, dict):
        usage = normalize_token_usage(provider_result.get("usage"))
        if usage:
            return usage
    return {}


def _token_usage_total(row: dict[str, Any]) -> dict[str, int]:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    for value in (
        row.get("token_usage_total"),
        row.get("usage_totals"),
        row.get("token_usage"),
        payload.get("token_usage_total"),
        payload.get("usage_totals"),
        payload.get("token_usage"),
    ):
        usage = normalize_token_usage(value)
        if usage:
            return usage
    return {}


def _token_usage_round(
    row: dict[str, Any],
    *,
    usage_delta: dict[str, int],
    usage_total: dict[str, int],
) -> dict[str, Any]:
    if not usage_delta and not usage_total:
        return {}
    record: dict[str, Any] = {}
    for key in (
        "round",
        "model",
        "model_call_id",
        "worker_id",
        "phase",
        "span_id",
        "trace_id",
    ):
        value = row.get(key)
        if value not in (None, ""):
            record[key] = value
    if usage_delta:
        record["delta"] = dict(usage_delta)
    if usage_total:
        record["total"] = dict(usage_total)
    return record


def _clean(value: Any) -> str:
    return str(value or "").strip()
