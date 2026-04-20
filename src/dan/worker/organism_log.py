"""Shared per-run organism log contract and helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from dan.worker.core.contracts import OutputContract

ORGANISM_LOG_SCHEMA = "organism_log_v1"
ORGANISM_LOG_SCHEMA_VERSION = ORGANISM_LOG_SCHEMA


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _prefixed_id(prefix: str, value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    marker = f"{prefix}:"
    return text if text.startswith(marker) else f"{marker}{text}"


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(started_at: Any, ended_at: Any) -> int | None:
    start_dt = _parse_timestamp(started_at)
    end_dt = _parse_timestamp(ended_at)
    if start_dt is None or end_dt is None:
        return None
    return max(int((end_dt - start_dt).total_seconds() * 1000), 0)


def new_trace_id() -> str:
    """Return a stable trace id string for one bounded organism run."""

    return f"trace:{uuid4().hex}"


def stable_output_contract_id(
    contract: OutputContract | dict[str, Any] | None,
) -> str | None:
    """Derive one stable id for semantically identical output contracts."""

    if contract is None:
        return None
    if isinstance(contract, OutputContract):
        payload = contract.model_dump(mode="json", exclude_none=True)
    elif isinstance(contract, dict):
        payload = dict(contract)
    else:
        return None
    normalized = {
        "definition_of_done": str(payload.get("definition_of_done") or "").strip(),
        "expected_return_shape": str(payload.get("expected_return_shape") or "").strip(),
        "output_schema": payload.get("output_schema"),
    }
    if not any(
        [
            normalized["definition_of_done"],
            normalized["expected_return_shape"],
            normalized["output_schema"],
        ]
    ):
        return None
    blob = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return f"contract:{digest}"


def organism_event_context(
    *,
    metadata: dict[str, Any] | None = None,
    output_contract: OutputContract | dict[str, Any] | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """Flatten the reusable execution metadata onto event payloads."""

    source = dict(metadata or {})
    sender = dict(source.get("sender") or {})
    recipient = dict(source.get("recipient") or {})
    resolved_worker_id = _clean_text(worker_id or source.get("worker_id"))
    organism_id = _clean_text(
        source.get("organism_id")
        or recipient.get("organism_id")
        or sender.get("organism_id")
    )
    organ_id = _clean_text(
        source.get("organ_id")
        or recipient.get("organ_id")
        or sender.get("organ_id")
    )
    lane = _clean_text(
        resolved_worker_id
        or source.get("parallel_lane")
        or recipient.get("cell_id")
        or source.get("organism_stage")
    )
    return _compact_dict(
        {
            "trace_id": _clean_text(source.get("trace_id")),
            "root_task_id": _clean_text(source.get("root_task_id")),
            "handoff_packet_id": _clean_text(source.get("handoff_packet_id")),
            "agent_id": _clean_text(source.get("agent_id")),
            "agent_session_id": _clean_text(source.get("agent_session_id")),
            "agent_message_id": _clean_text(source.get("agent_message_id")),
            "agent_message_index": source.get("agent_message_index"),
            "worker_session_id": _clean_text(source.get("worker_session_id")),
            "durable_mode": (
                bool(source.get("durable_mode"))
                if source.get("durable_mode") is not None
                else None
            ),
            "organism_id": organism_id,
            "organ_id": organ_id,
            "organ_kind": _clean_text(source.get("organ_kind")),
            "organism_stage": _clean_text(source.get("organism_stage")),
            "sender_cell_id": _clean_text(sender.get("cell_id")),
            "recipient_cell_id": _clean_text(recipient.get("cell_id")),
            "contract_id": stable_output_contract_id(output_contract),
            "parallel_lane": lane,
            "worker_id": resolved_worker_id,
        }
    )


class OrganismLogContext(BaseModel):
    """Stable envelope fields carried by one organism-log stream."""

    product: str = ""
    stream_kind: Literal["bounded_run", "control_plane", "run"] = "run"
    session_id: str = ""
    turn_id: str = ""
    task_id: str = ""
    trace_id: str = ""
    organism_id: str = ""
    organ_id: str = ""


class OrganismLogRow(BaseModel):
    """Normalized read-side row for ``organism_log_v1`` consumers."""

    schema_version: str = ORGANISM_LOG_SCHEMA_VERSION
    product: str = ""
    stream_kind: str = ""
    session_id: str = ""
    turn_id: str = ""
    task_id: str = ""
    trace_id: str = ""
    organism_id: str = ""
    organ_id: str = ""
    record_id: str = ""
    sequence: int = 0
    timestamp: str = ""
    record_kind: Literal["event", "span"] = "event"
    span_id: str = ""
    parent_span_id: str = ""
    span_kind: str = ""
    parallel_lane: str = ""
    event: str = ""
    event_family: str = ""
    status: str = ""
    summary: str = ""
    contract_id: str = ""
    worker_id: str = ""
    tool_id: str = ""
    tool_call_id: str = ""
    model_call_id: str = ""
    attempt: int | None = None
    duration_ms: int | None = None
    wait_reason: str = ""
    blocked_by: list[str] = Field(default_factory=list)
    start_timestamp: str = ""
    end_timestamp: str = ""
    start_record_id: str = ""
    end_record_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class OrganismLogProjection(BaseModel):
    """Normalized projection over one organism log."""

    events: list[OrganismLogRow] = Field(default_factory=list)
    spans: list[OrganismLogRow] = Field(default_factory=list)
    slowest_spans: list[OrganismLogRow] = Field(default_factory=list)
    blocking_spans: list[OrganismLogRow] = Field(default_factory=list)


def _row_event_family(event: str) -> str:
    text = str(event or "").strip()
    if text.startswith("agent."):
        return "agent"
    if text.startswith("contract."):
        return "contract"
    if text.startswith("run.log."):
        return "run_log"
    if text.startswith("provider.build."):
        return "provider_build"
    if text.startswith("tool."):
        return "tool"
    if text.startswith("model."):
        return "model"
    if text.startswith("stage."):
        return "stage"
    if text.startswith("organism."):
        return "organism"
    if text.startswith("attempt."):
        return "attempt"
    if text.startswith("status."):
        return "status"
    if text.startswith("orchestrator."):
        return "orchestrator"
    if text.startswith("interactive."):
        return "interactive"
    if text.startswith("session."):
        return "session"
    if text.startswith("product."):
        return "product"
    if text.startswith("cli."):
        return "cli"
    if text.startswith("research."):
        return "research"
    if text.startswith("code."):
        return "code"
    return text.split(".", 1)[0].replace(".", "_")


def _row_status(raw: dict[str, Any]) -> str:
    explicit = _clean_text(raw.get("status"))
    if explicit:
        return explicit
    event = str(raw.get("event") or "").strip()
    if event.endswith(".started") or event.endswith(".requested"):
        return "started"
    if event.endswith(".completed") or event.endswith(".responded"):
        return "completed"
    if event.endswith(".failed"):
        return "failed"
    if event.endswith(".denied"):
        return "denied"
    return ""


def _row_summary(raw: dict[str, Any]) -> str:
    if str(raw.get("event") or "").startswith("contract."):
        errors = [str(item).strip() for item in list(raw.get("errors") or []) if str(item).strip()]
        if errors:
            return errors[0]
        status = _clean_text(raw.get("status"))
        if status:
            return status
    for key in (
        "message",
        "summary",
        "detail",
        "public_response",
        "clarifying_question",
        "question",
        "plan_summary",
        "comparison_note",
        "repair_brief",
        "objective",
        "task",
        "label",
        "error",
    ):
        text = _clean_text(raw.get(key))
        if text:
            return text
    if str(raw.get("event") or "").startswith("tool."):
        tool_id = _clean_text(raw.get("tool_id")) or "tool"
        arguments = raw.get("arguments")
        if arguments:
            return f"{tool_id} {json.dumps(arguments, sort_keys=True, default=str)[:160]}"
        return tool_id
    return ""


def _normalize_event_row(raw: dict[str, Any]) -> OrganismLogRow:
    sequence = int(raw.get("sequence") or 0)
    span_kind = _clean_text(raw.get("span_kind"))
    span_id = _clean_text(raw.get("span_id"))
    tool_call_id = _clean_text(raw.get("tool_call_id"))
    model_call_id = _clean_text(raw.get("model_call_id"))
    if span_kind == "tool_call" and not tool_call_id:
        tool_call_id = span_id or ""
    if span_kind == "model_call" and not model_call_id:
        model_call_id = span_id or ""
    return OrganismLogRow(
        schema_version=str(raw.get("schema") or ORGANISM_LOG_SCHEMA_VERSION),
        product=str(raw.get("product") or ""),
        stream_kind=str(raw.get("stream") or raw.get("stream_kind") or ""),
        session_id=str(raw.get("session_id") or ""),
        turn_id=str(raw.get("turn_id") or ""),
        task_id=str(raw.get("task_id") or ""),
        trace_id=str(raw.get("trace_id") or ""),
        organism_id=str(raw.get("organism_id") or ""),
        organ_id=str(raw.get("organ_id") or ""),
        record_id=f"event:{sequence}",
        sequence=sequence,
        timestamp=str(raw.get("timestamp") or ""),
        record_kind="event",
        span_id=span_id or "",
        parent_span_id=str(raw.get("parent_span_id") or ""),
        span_kind=span_kind or "",
        parallel_lane=str(raw.get("parallel_lane") or ""),
        event=str(raw.get("event") or ""),
        event_family=_row_event_family(str(raw.get("event") or "")),
        status=_row_status(raw),
        summary=_row_summary(raw),
        contract_id=str(raw.get("contract_id") or ""),
        worker_id=str(raw.get("worker_id") or ""),
        tool_id=str(raw.get("tool_id") or ""),
        tool_call_id=tool_call_id or "",
        model_call_id=model_call_id or "",
        attempt=(
            int(raw.get("attempt"))
            if raw.get("attempt") is not None and str(raw.get("attempt")).strip()
            else None
        ),
        duration_ms=(
            int(raw.get("duration_ms"))
            if raw.get("duration_ms") is not None and str(raw.get("duration_ms")).strip()
            else None
        ),
        wait_reason=str(raw.get("error") or raw.get("wait_reason") or ""),
        blocked_by=[
            str(item).strip()
            for item in list(raw.get("blocked_by") or [])
            if str(item).strip()
        ],
        payload=dict(raw),
    )


def _normalize_span_row(projected: dict[str, Any], *, raw_rows: list[dict[str, Any]]) -> OrganismLogRow:
    span_id = str(projected.get("span_id") or "")
    raw_row_by_sequence = {
        int(row.get("sequence") or 0): row
        for row in raw_rows
        if int(row.get("sequence") or 0) > 0
    }
    start_sequence = int(projected.get("start_sequence") or 0)
    end_sequence = int(projected.get("end_sequence") or 0)
    started_row = next(
        (
            row
            for row in raw_rows
            if str(row.get("span_id") or "") == span_id and str(row.get("row_kind") or "") == "span_start"
        ),
        {},
    )
    if not started_row and start_sequence:
        started_row = dict(raw_row_by_sequence.get(start_sequence) or {})
    ended_row = next(
        (
            row
            for row in raw_rows
            if str(row.get("span_id") or "") == span_id and str(row.get("row_kind") or "") == "span_end"
        ),
        {},
    )
    if not ended_row and end_sequence:
        ended_row = dict(raw_row_by_sequence.get(end_sequence) or {})
    if not started_row and ended_row:
        started_row = dict(ended_row)
    if not ended_row and started_row:
        ended_row = dict(started_row)
    end_sequence = int(ended_row.get("sequence") or started_row.get("sequence") or 0)
    span_kind = str(projected.get("span_kind") or started_row.get("span_kind") or "")
    tool_id = str(projected.get("tool_id") or started_row.get("tool_id") or "")
    worker_id = str(projected.get("worker_id") or started_row.get("worker_id") or "")
    parallel_lane = str(
        projected.get("parallel_lane") or started_row.get("parallel_lane") or ""
    )
    tool_call_id = span_id if span_kind == "tool_call" else ""
    model_call_id = span_id if span_kind == "model_call" else ""
    return OrganismLogRow(
        schema_version=str(started_row.get("schema") or ORGANISM_LOG_SCHEMA_VERSION),
        product=str(projected.get("product") or started_row.get("product") or ""),
        stream_kind=str(projected.get("stream") or started_row.get("stream") or started_row.get("stream_kind") or ""),
        session_id=str(projected.get("session_id") or started_row.get("session_id") or ""),
        turn_id=str(projected.get("turn_id") or started_row.get("turn_id") or ""),
        task_id=str(projected.get("task_id") or started_row.get("task_id") or ""),
        trace_id=str(projected.get("trace_id") or started_row.get("trace_id") or ""),
        organism_id=str(projected.get("organism_id") or started_row.get("organism_id") or ""),
        organ_id=str(projected.get("organ_id") or started_row.get("organ_id") or ""),
        record_id=f"span:{end_sequence}",
        sequence=end_sequence,
        timestamp=str(projected.get("ended_at") or started_row.get("timestamp") or ""),
        record_kind="span",
        span_id=span_id,
        parent_span_id=str(projected.get("parent_span_id") or ""),
        span_kind=span_kind,
        parallel_lane=parallel_lane,
        event=f"{_row_event_family(str(projected.get('event') or started_row.get('event') or 'span'))}.span",
        event_family=_row_event_family(str(projected.get("event") or started_row.get("event") or "span")),
        status=str(projected.get("status") or ""),
        summary=_row_summary(ended_row or started_row or {}),
        contract_id=str(projected.get("contract_id") or started_row.get("contract_id") or ""),
        worker_id=worker_id,
        tool_id=tool_id,
        tool_call_id=tool_call_id,
        model_call_id=model_call_id,
        attempt=(
            int(started_row.get("attempt"))
            if started_row.get("attempt") is not None and str(started_row.get("attempt")).strip()
            else None
        ),
        duration_ms=(
            int(projected.get("duration_ms"))
            if projected.get("duration_ms") is not None and str(projected.get("duration_ms")).strip()
            else None
        ),
        wait_reason=str(ended_row.get("error") or ""),
        blocked_by=[
            str(item).strip()
            for item in list(projected.get("blocked_by") or started_row.get("blocked_by") or [])
            if str(item).strip()
        ],
        start_timestamp=str(projected.get("started_at") or ""),
        end_timestamp=str(projected.get("ended_at") or ""),
        start_record_id=(
            f"event:{int(started_row.get('sequence') or 0)}" if started_row else ""
        ),
        end_record_id=(
            f"event:{int(ended_row.get('sequence') or 0)}" if ended_row else ""
        ),
        payload={
            "projected_span": dict(projected),
            "start_row": dict(started_row),
            "end_row": dict(ended_row),
        },
    )

class OrganismLogWriter:
    """Append-only JSONL writer for the shared ``organism_log_v1`` contract."""

    def __init__(
        self,
        *,
        path: Path,
        stream: str = "run",
        base_context: dict[str, Any] | None = None,
        context: OrganismLogContext | dict[str, Any] | None = None,
    ) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        resolved_context = _compact_dict(dict(base_context or {}))
        if context is not None:
            context_payload = (
                context.model_dump(mode="json")
                if isinstance(context, OrganismLogContext)
                else dict(context)
            )
            if not _clean_text(stream):
                stream = str(context_payload.get("stream_kind") or "")
            resolved_context.update(_compact_dict(context_payload))
        resolved_stream = (
            _clean_text(resolved_context.pop("stream_kind", None))
            or _clean_text(stream)
            or "run"
        )
        self._stream = resolved_stream
        self._base_context = resolved_context
        self._open_spans: dict[str, dict[str, Any]] = {}
        self._sequence = 0
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as existing:
                self._sequence = sum(1 for _line in existing)
        self._handle = self.path.open("a", encoding="utf-8")

    def update_context(self, **payload: Any) -> None:
        self._base_context.update(_compact_dict(dict(payload)))

    def emit(self, event: dict[str, Any]) -> dict[str, Any]:
        raw = dict(event or {})
        timestamp = str(raw.get("timestamp") or _utcnow_iso())
        row = _compact_dict(
            {
                "schema": ORGANISM_LOG_SCHEMA,
                "timestamp": timestamp,
                "stream": self._stream,
                **self._base_context,
                **raw,
            }
        )
        row.setdefault("event", "event.unknown")
        row.update(self._classify_event(row))
        self._write(row)
        return row

    def emit_stage_records(self, stage_records: Iterable[dict[str, Any]]) -> None:
        for record in stage_records:
            payload = dict(record or {})
            packet_id = _clean_text(payload.get("packet_id"))
            row = _compact_dict(
                {
                    "schema": ORGANISM_LOG_SCHEMA,
                    "timestamp": _utcnow_iso(),
                    "stream": self._stream,
                    **self._base_context,
                    **payload,
                    "event": "organism.stage.record",
                    "row_kind": "stage_summary",
                    "span_kind": "organism_stage",
                    "span_id": (
                        f"stage:{self._base_context.get('trace_id') or self._base_context.get('task_id') or 'run'}:"
                        f"{payload.get('attempt') or 0}:{payload.get('stage') or 'unknown'}"
                    ),
                    "parent_span_id": (
                        _prefixed_id("packet", packet_id)
                        if packet_id
                        else None
                    ),
                    "packet_id": packet_id,
                }
            )
            self._write(row)

    def emit_trace_rows(self, trace_rows: Iterable[dict[str, Any]]) -> None:
        for trace in trace_rows:
            payload = dict(trace or {})
            kind = str(payload.get("kind") or "").strip()
            timestamp = (
                str(payload.get("created_at") or "")
                or str(payload.get("emitted_at") or "")
                or _utcnow_iso()
            )
            if kind == "handoff":
                packet_id = _clean_text(payload.get("message_id"))
                parent_packet_id = _clean_text(payload.get("parent_packet_id"))
                row = _compact_dict(
                    {
                        "schema": ORGANISM_LOG_SCHEMA,
                        "timestamp": timestamp,
                        "stream": self._stream,
                        **self._base_context,
                        **payload,
                        "event": "trace.handoff",
                        "row_kind": "trace_handoff",
                        "span_kind": "cell_handoff",
                        "span_id": _prefixed_id("packet", packet_id),
                        "parent_span_id": (
                            _prefixed_id("packet", parent_packet_id)
                            if parent_packet_id
                            else None
                        ),
                        "packet_id": packet_id,
                        "parallel_lane": _clean_text(
                            payload.get("recipient_cell_id")
                            or payload.get("sender_cell_id")
                        ),
                    }
                )
            else:
                signal_id = _clean_text(payload.get("message_id"))
                related_packet_id = _clean_text(payload.get("related_packet_id"))
                row = _compact_dict(
                    {
                        "schema": ORGANISM_LOG_SCHEMA,
                        "timestamp": timestamp,
                        "stream": self._stream,
                        **self._base_context,
                        **payload,
                        "event": "trace.signal",
                        "row_kind": "trace_signal",
                        "span_kind": "supervisory_signal",
                        "span_id": _prefixed_id("signal", signal_id),
                        "parent_span_id": (
                            _prefixed_id("packet", related_packet_id)
                            if related_packet_id
                            else None
                        ),
                        "signal_id": signal_id,
                        "parallel_lane": _clean_text(payload.get("source_cell_id")),
                    }
                )
            self._write(row)

    def close(self) -> None:
        self._handle.close()

    def _write(self, payload: dict[str, Any]) -> None:
        self._sequence += 1
        row = {
            **payload,
            "sequence": self._sequence,
        }
        self._handle.write(
            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        )
        self._handle.write("\n")
        self._handle.flush()

    def _classify_event(self, row: dict[str, Any]) -> dict[str, Any]:
        event = str(row.get("event") or "").strip()
        if event == "run.log.started":
            return self._start_span(
                row,
                span_kind="run",
                span_id=self._run_span_id(row),
            )
        if event in {"run.log.completed", "run.log.failed"}:
            return self._end_span(
                row,
                span_kind="run",
                span_id=self._run_span_id(row),
                status=_clean_text(row.get("status") or event.rsplit(".", 1)[-1]),
            )
        if event == "agent.session.created":
            return self._start_span(
                row,
                span_kind="durable_agent_session",
                span_id=self._agent_session_span_id(row),
                parent_span_id=self._run_parent(row),
            )
        if event == "agent.session.closed":
            return self._end_span(
                row,
                span_kind="durable_agent_session",
                span_id=self._agent_session_span_id(row),
                parent_span_id=self._run_parent(row),
                status="closed",
            )
        if event in {
            "agent.mailbox.enqueued",
            "agent.mailbox.started",
            "agent.mailbox.completed",
            "agent.mailbox.failed",
        }:
            return _compact_dict(
                {
                    "row_kind": "event",
                    "parent_span_id": self._agent_session_span_id(row),
                }
            )
        if event == "agent.background.started":
            return self._start_span(
                row,
                span_kind="durable_background_task",
                span_id=self._background_span_id(row),
                parent_span_id=self._agent_session_span_id(row),
            )
        if event in {
            "agent.background.completed",
            "agent.background.failed",
            "agent.background.cancelled",
        }:
            return self._end_span(
                row,
                span_kind="durable_background_task",
                span_id=self._background_span_id(row),
                parent_span_id=self._agent_session_span_id(row),
                status=_clean_text(row.get("status") or event.rsplit(".", 1)[-1]),
            )
        if event == "organism.started":
            return self._start_span(
                row,
                span_kind="organism",
                span_id=f"organism:{row.get('trace_id') or row.get('organism_id') or row.get('task_id') or 'active'}",
                parent_span_id=(
                    self._run_parent(row)
                ),
            )
        if event in {"organism.completed", "organism.failed"}:
            return self._end_span(
                row,
                span_kind="organism",
                span_id=f"organism:{row.get('trace_id') or row.get('organism_id') or row.get('task_id') or 'active'}",
                parent_span_id=(
                    self._run_parent(row)
                ),
                status=_clean_text(row.get("status") or event.rsplit(".", 1)[-1]),
            )
        if event == "attempt.started":
            return self._start_span(
                row,
                span_kind="attempt",
                span_id=(
                    f"attempt:{row.get('trace_id') or row.get('task_id') or 'active'}:"
                    f"{row.get('attempt') or 0}"
                ),
                parent_span_id=(
                    f"organism:{row.get('trace_id') or row.get('organism_id') or row.get('task_id') or 'active'}"
                ),
            )
        if event == "stage.started":
            return self._start_span(
                row,
                span_kind="organism_stage",
                span_id=self._stage_span_id(row),
                parent_span_id=self._attempt_span_id(row),
            )
        if event == "stage.completed":
            return self._end_span(
                row,
                span_kind="organism_stage",
                span_id=self._stage_span_id(row),
                parent_span_id=self._attempt_span_id(row),
                status=_clean_text(row.get("status")),
            )
        if event == "worker.started":
            return self._start_span(
                row,
                span_kind="worker_execution",
                span_id=self._worker_span_id(row),
                parent_span_id=self._packet_parent(row),
            )
        if event in {"worker.completed", "worker.failed"}:
            return self._end_span(
                row,
                span_kind="worker_execution",
                span_id=self._worker_span_id(row),
                parent_span_id=self._packet_parent(row),
                status=_clean_text(row.get("status") or event.rsplit(".", 1)[-1]),
            )
        if event == "contract.validation.started":
            return self._start_span(
                row,
                span_kind="output_contract_validation",
                span_id=self._contract_validation_span_id(row),
                parent_span_id=self._contract_validation_parent(row),
            )
        if event == "contract.validation.completed":
            return self._end_span(
                row,
                span_kind="output_contract_validation",
                span_id=self._contract_validation_span_id(row),
                parent_span_id=self._contract_validation_parent(row),
                status=_clean_text(row.get("status") or "completed"),
            )
        if event == "contract.repair.started":
            return self._start_span(
                row,
                span_kind="output_contract_repair",
                span_id=self._contract_repair_span_id(row),
                parent_span_id=self._worker_span_id(row),
            )
        if event in {"contract.repair.completed", "contract.repair.failed"}:
            return self._end_span(
                row,
                span_kind="output_contract_repair",
                span_id=self._contract_repair_span_id(row),
                parent_span_id=self._worker_span_id(row),
                status=_clean_text(row.get("status") or event.rsplit(".", 1)[-1]),
            )
        if event == "model.requested":
            blocked_by = [
                str(item).strip()
                for item in list(row.get("blocked_by_tool_call_ids") or [])
                if str(item).strip()
            ]
            return self._start_span(
                row,
                span_kind="model_call",
                span_id=self._model_span_id(row),
                parent_span_id=self._packet_parent(row),
                blocked_by=blocked_by or None,
            )
        if event in {"model.responded", "model.timeout", "model.provider_prompt_rejected"}:
            return self._end_span(
                row,
                span_kind="model_call",
                span_id=self._model_span_id(row),
                parent_span_id=self._packet_parent(row),
                status=_clean_text(row.get("finish_reason") or event.rsplit(".", 1)[-1]),
            )
        if event == "tool.started":
            return self._start_span(
                row,
                span_kind="tool_call",
                span_id=self._tool_span_id(row),
                parent_span_id=_clean_text(row.get("parent_model_call_id"))
                or self._packet_parent(row),
            )
        if event in {"tool.completed", "tool.failed", "tool.denied"}:
            return self._end_span(
                row,
                span_kind="tool_call",
                span_id=self._tool_span_id(row),
                parent_span_id=_clean_text(row.get("parent_model_call_id"))
                or self._packet_parent(row),
                status=_clean_text(row.get("status") or event.rsplit(".", 1)[-1]),
            )
        if event.endswith(".started"):
            base = event.rsplit(".", 1)[0]
            return self._start_span(
                row,
                span_kind="control_stage",
                span_id=self._generic_span_id(base, row),
            )
        if event.endswith(".completed") or event.endswith(".failed"):
            base = event.rsplit(".", 1)[0]
            return self._end_span(
                row,
                span_kind="control_stage",
                span_id=self._generic_span_id(base, row),
                status=_clean_text(row.get("status") or event.rsplit(".", 1)[-1]),
            )
        return {
            "row_kind": "event",
            "span_kind": None,
            "span_id": None,
            "parent_span_id": self._packet_parent(row),
        }

    def _start_span(
        self,
        row: dict[str, Any],
        *,
        span_kind: str,
        span_id: str,
        parent_span_id: str | None = None,
        status: str | None = None,
        blocked_by: list[str] | None = None,
    ) -> dict[str, Any]:
        self._open_spans[span_id] = {
            "timestamp": row.get("timestamp"),
            "parent_span_id": parent_span_id,
            "span_kind": span_kind,
        }
        return _compact_dict(
            {
                "row_kind": "span_start",
                "span_kind": span_kind,
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "status": status,
                "blocked_by": blocked_by,
            }
        )

    def _end_span(
        self,
        row: dict[str, Any],
        *,
        span_kind: str,
        span_id: str,
        parent_span_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        open_span = self._open_spans.pop(span_id, {})
        started_at = open_span.get("timestamp")
        resolved_parent = parent_span_id or open_span.get("parent_span_id")
        return _compact_dict(
            {
                "row_kind": "span_end",
                "span_kind": span_kind,
                "span_id": span_id,
                "parent_span_id": resolved_parent,
                "status": status,
                "duration_ms": _duration_ms(started_at, row.get("timestamp")),
            }
        )

    @staticmethod
    def _run_span_id(row: dict[str, Any]) -> str:
        return f"run:{row.get('task_id') or row.get('trace_id') or 'active'}"

    @classmethod
    def _run_parent(cls, row: dict[str, Any]) -> str | None:
        if _clean_text(row.get("task_id")) or _clean_text(row.get("trace_id")):
            return cls._run_span_id(row)
        return None

    @staticmethod
    def _agent_session_span_id(row: dict[str, Any]) -> str | None:
        session_id = _clean_text(row.get("agent_session_id"))
        if not session_id:
            return None
        return f"agent-session:{session_id}"

    @classmethod
    def _mailbox_wait_span_id(cls, row: dict[str, Any]) -> str | None:
        session_id = _clean_text(row.get("agent_session_id"))
        message_id = _clean_text(row.get("message_id") or row.get("agent_message_id"))
        if not session_id or not message_id:
            return None
        return f"mailbox-wait:{session_id}:{message_id}"

    @classmethod
    def _mailbox_turn_span_id(cls, row: dict[str, Any]) -> str | None:
        session_id = _clean_text(row.get("agent_session_id"))
        message_id = _clean_text(row.get("message_id") or row.get("agent_message_id"))
        if not session_id or not message_id:
            return None
        return f"mailbox-turn:{session_id}:{message_id}"

    @staticmethod
    def _background_span_id(row: dict[str, Any]) -> str | None:
        session_id = _clean_text(row.get("agent_session_id"))
        task_id = _clean_text(row.get("task_id"))
        if not session_id or not task_id:
            return None
        return f"background-task:{session_id}:{task_id}"

    @classmethod
    def _packet_parent(cls, row: dict[str, Any]) -> str | None:
        packet_id = _clean_text(row.get("handoff_packet_id"))
        if not packet_id:
            return cls._mailbox_turn_span_id(row)
        return _prefixed_id("packet", packet_id)

    @staticmethod
    def _execution_scope_id(row: dict[str, Any]) -> str:
        packet_id = _clean_text(row.get("handoff_packet_id"))
        if packet_id:
            return packet_id
        message_id = _clean_text(row.get("agent_message_id"))
        if message_id:
            return message_id
        return str(row.get("trace_id") or row.get("task_id") or "active")

    @staticmethod
    def _attempt_span_id(row: dict[str, Any]) -> str:
        return (
            f"attempt:{row.get('trace_id') or row.get('task_id') or 'active'}:"
            f"{row.get('attempt') or 0}"
        )

    @staticmethod
    def _stage_span_id(row: dict[str, Any]) -> str:
        return (
            f"stage:{row.get('trace_id') or row.get('task_id') or 'active'}:"
            f"{row.get('attempt') or 0}:{row.get('stage') or 'unknown'}"
        )

    @staticmethod
    def _worker_span_id(row: dict[str, Any]) -> str:
        packet_id = _clean_text(row.get("handoff_packet_id"))
        if packet_id:
            return f"worker:{packet_id}"
        return (
            f"worker:{OrganismLogWriter._execution_scope_id(row)}:"
            f"{row.get('worker_id') or 'worker'}:{row.get('contract_id') or 'contract'}"
        )

    @staticmethod
    def _model_span_id(row: dict[str, Any]) -> str:
        explicit = _clean_text(row.get("model_call_id"))
        if explicit:
            return explicit
        return (
            f"model-call:{OrganismLogWriter._execution_scope_id(row)}:"
            f"{row.get('worker_id') or 'worker'}:{row.get('round') or 1}"
        )

    @staticmethod
    def _tool_span_id(row: dict[str, Any]) -> str:
        explicit = _clean_text(row.get("tool_call_id"))
        if explicit:
            return explicit
        return (
            f"tool-call:{OrganismLogWriter._execution_scope_id(row)}:"
            f"{row.get('worker_id') or 'worker'}:{row.get('tool_id') or 'tool'}"
        )

    @staticmethod
    def _contract_validation_span_id(row: dict[str, Any]) -> str:
        phase = _clean_text(row.get("validation_phase")) or "initial"
        repair_round = int(row.get("repair_round") or 0)
        return (
            f"contract-validation:{OrganismLogWriter._execution_scope_id(row)}:"
            f"{row.get('worker_id') or 'worker'}:{row.get('contract_id') or 'contract'}:"
            f"{phase}:{repair_round}"
        )

    @staticmethod
    def _contract_repair_span_id(row: dict[str, Any]) -> str:
        repair_round = int(row.get("repair_round") or 1)
        return (
            f"contract-repair:{OrganismLogWriter._execution_scope_id(row)}:"
            f"{row.get('worker_id') or 'worker'}:{row.get('contract_id') or 'contract'}:"
            f"{repair_round}"
        )

    @classmethod
    def _contract_validation_parent(cls, row: dict[str, Any]) -> str:
        phase = _clean_text(row.get("validation_phase")) or "initial"
        if phase == "repair":
            return cls._contract_repair_span_id(row)
        return cls._worker_span_id(row)

    @staticmethod
    def _generic_span_id(base: str, row: dict[str, Any]) -> str:
        suffix = (
            _clean_text(row.get("task_id"))
            or _clean_text(row.get("trace_id"))
            or _clean_text(row.get("turn_number"))
            or _clean_text(row.get("worker_id"))
            or "active"
        )
        return f"{base}:{suffix}"


def read_organism_log(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def readable_organism_log_v1_rows(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the readable ``organism_log_v1`` suffix from a mixed JSONL file."""

    raw_rows = [dict(row) for row in rows]
    if not raw_rows:
        return []
    last_legacy_index = max(
        (
            index
            for index, row in enumerate(raw_rows)
            if str(row.get("schema") or "").strip() != ORGANISM_LOG_SCHEMA
        ),
        default=-1,
    )
    readable = raw_rows[last_legacy_index + 1 :]
    if not readable:
        return []
    if any(str(row.get("schema") or "").strip() != ORGANISM_LOG_SCHEMA for row in readable):
        return []
    return readable


def write_organism_log(path: str | Path, rows: Iterable[dict[str, Any]]) -> Path:
    """Write raw ``organism_log_v1`` rows to one JSONL file."""

    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8") as handle:
        for sequence, raw_row in enumerate(rows, start=1):
            row = _compact_dict(dict(raw_row))
            row["schema"] = str(row.get("schema") or ORGANISM_LOG_SCHEMA)
            row["sequence"] = sequence
            if not _clean_text(row.get("stream")):
                row["stream"] = str(row.get("stream_kind") or "run")
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            )
            handle.write("\n")
    return resolved


def project_organism_spans(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct closed spans from ``organism_log_v1`` rows."""

    open_spans: dict[str, dict[str, Any]] = {}
    projected: list[dict[str, Any]] = []
    open_mailbox_waits: dict[str, dict[str, Any]] = {}
    open_mailbox_turns: dict[str, dict[str, Any]] = {}
    active_mailbox_turns: dict[str, str] = {}
    sorted_rows = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            str(row.get("timestamp") or ""),
            int(row.get("sequence") or 0),
        ),
    )
    for row in sorted_rows:
        span_id = _clean_text(row.get("span_id"))
        row_kind = str(row.get("row_kind") or "").strip()
        if row_kind in {"trace_handoff", "trace_signal"} and span_id:
            projected.append(
                _compact_dict(
                    {
                        "span_id": span_id,
                        "span_kind": _clean_text(row.get("span_kind")),
                        "event": _clean_text(row.get("event")),
                        "started_at": row.get("timestamp"),
                        "ended_at": row.get("timestamp"),
                        "duration_ms": 0,
                        "parent_span_id": _clean_text(row.get("parent_span_id")),
                        "parallel_lane": _clean_text(row.get("parallel_lane")),
                        "status": _clean_text(row.get("status") or "observed"),
                        "trace_id": _clean_text(row.get("trace_id")),
                        "task_id": _clean_text(row.get("task_id")),
                        "worker_id": _clean_text(row.get("worker_id")),
                        "tool_id": _clean_text(row.get("tool_id")),
                        "model": _clean_text(row.get("model")),
                        "contract_id": _clean_text(row.get("contract_id")),
                        "blocked_by": list(row.get("blocked_by") or []),
                        "start_sequence": int(row.get("sequence") or 0),
                        "end_sequence": int(row.get("sequence") or 0),
                    }
                )
            )
            continue
        event = str(row.get("event") or "").strip()
        mailbox_session_id = _clean_text(row.get("agent_session_id"))
        if event == "agent.mailbox.enqueued":
            wait_span_id = OrganismLogWriter._mailbox_wait_span_id(row)
            if wait_span_id:
                blocker_span_id = (
                    active_mailbox_turns.get(mailbox_session_id or "")
                    if mailbox_session_id
                    else None
                )
                open_mailbox_waits[wait_span_id] = {
                    "start_row": dict(row),
                    "blocked_by": [blocker_span_id] if blocker_span_id else [],
                }
            continue
        if event == "agent.mailbox.started":
            wait_span_id = OrganismLogWriter._mailbox_wait_span_id(row)
            wait_state = open_mailbox_waits.pop(wait_span_id, None) if wait_span_id else None
            if wait_span_id and wait_state is not None:
                started_row = dict(wait_state.get("start_row") or {})
                projected.append(
                    _compact_dict(
                        {
                            "span_id": wait_span_id,
                            "span_kind": "durable_mailbox_wait",
                            "event": "agent.mailbox.wait",
                            "started_at": started_row.get("timestamp"),
                            "ended_at": row.get("timestamp"),
                            "duration_ms": _duration_ms(
                                started_row.get("timestamp"),
                                row.get("timestamp"),
                            ),
                            "parent_span_id": OrganismLogWriter._agent_session_span_id(row),
                            "parallel_lane": _clean_text(
                                row.get("agent_id") or started_row.get("agent_id")
                            ),
                            "status": "completed",
                            "trace_id": _clean_text(row.get("trace_id") or started_row.get("trace_id")),
                            "task_id": _clean_text(row.get("task_id") or started_row.get("task_id")),
                            "worker_id": _clean_text(
                                row.get("worker_id") or started_row.get("worker_id")
                            ),
                            "contract_id": _clean_text(
                                row.get("contract_id") or started_row.get("contract_id")
                            ),
                            "blocked_by": list(wait_state.get("blocked_by") or []),
                            "start_sequence": int(started_row.get("sequence") or 0),
                            "end_sequence": int(row.get("sequence") or 0),
                        }
                    )
                )
            turn_span_id = OrganismLogWriter._mailbox_turn_span_id(row)
            if turn_span_id:
                open_mailbox_turns[turn_span_id] = {"start_row": dict(row)}
                if mailbox_session_id:
                    active_mailbox_turns[mailbox_session_id] = turn_span_id
            continue
        if event in {"agent.mailbox.completed", "agent.mailbox.failed"}:
            turn_span_id = OrganismLogWriter._mailbox_turn_span_id(row)
            turn_state = open_mailbox_turns.pop(turn_span_id, None) if turn_span_id else None
            if turn_span_id and turn_state is not None:
                started_row = dict(turn_state.get("start_row") or {})
                projected.append(
                    _compact_dict(
                        {
                            "span_id": turn_span_id,
                            "span_kind": "durable_mailbox_turn",
                            "event": "agent.mailbox.turn",
                            "started_at": started_row.get("timestamp"),
                            "ended_at": row.get("timestamp"),
                            "duration_ms": (
                                row.get("duration_ms")
                                if row.get("duration_ms") is not None
                                else _duration_ms(started_row.get("timestamp"), row.get("timestamp"))
                            ),
                            "parent_span_id": OrganismLogWriter._agent_session_span_id(row),
                            "parallel_lane": _clean_text(
                                row.get("agent_id") or started_row.get("agent_id")
                            ),
                            "status": _clean_text(
                                row.get("status") or event.rsplit(".", 1)[-1]
                            ),
                            "trace_id": _clean_text(row.get("trace_id") or started_row.get("trace_id")),
                            "task_id": _clean_text(row.get("task_id") or started_row.get("task_id")),
                            "worker_id": _clean_text(
                                row.get("worker_id") or started_row.get("worker_id")
                            ),
                            "contract_id": _clean_text(
                                row.get("contract_id") or started_row.get("contract_id")
                            ),
                            "blocked_by": list(
                                row.get("blocked_by")
                                or started_row.get("blocked_by")
                                or []
                            ),
                            "start_sequence": int(started_row.get("sequence") or 0),
                            "end_sequence": int(row.get("sequence") or 0),
                        }
                    )
                )
            if (
                mailbox_session_id
                and active_mailbox_turns.get(mailbox_session_id) == turn_span_id
            ):
                active_mailbox_turns.pop(mailbox_session_id, None)
            continue
        if not span_id:
            continue
        if row_kind == "span_start":
            open_spans[span_id] = dict(row)
            continue
        if row_kind != "span_end":
            continue
        started = open_spans.pop(span_id, None)
        if started is None:
            continue
        projected.append(
            _compact_dict(
                {
                    "span_id": span_id,
                    "span_kind": _clean_text(row.get("span_kind") or started.get("span_kind")),
                    "event": _clean_text(started.get("event")),
                    "started_at": started.get("timestamp"),
                    "ended_at": row.get("timestamp"),
                    "duration_ms": (
                        row.get("duration_ms")
                        if row.get("duration_ms") is not None
                        else _duration_ms(started.get("timestamp"), row.get("timestamp"))
                    ),
                    "parent_span_id": _clean_text(
                        row.get("parent_span_id") or started.get("parent_span_id")
                    ),
                    "parallel_lane": _clean_text(
                        row.get("parallel_lane") or started.get("parallel_lane")
                    ),
                    "status": _clean_text(row.get("status")),
                    "trace_id": _clean_text(row.get("trace_id") or started.get("trace_id")),
                    "task_id": _clean_text(row.get("task_id") or started.get("task_id")),
                    "worker_id": _clean_text(
                        row.get("worker_id") or started.get("worker_id")
                    ),
                    "tool_id": _clean_text(row.get("tool_id") or started.get("tool_id")),
                    "model": _clean_text(row.get("model") or started.get("model")),
                    "contract_id": _clean_text(
                        row.get("contract_id") or started.get("contract_id")
                    ),
                    "blocked_by": list(
                        row.get("blocked_by")
                        or started.get("blocked_by")
                        or []
                    ),
                    "start_sequence": int(started.get("sequence") or 0),
                    "end_sequence": int(row.get("sequence") or 0),
                }
            )
        )
    for wait_span_id, state in open_mailbox_waits.items():
        started = dict(state.get("start_row") or {})
        projected.append(
            _compact_dict(
                {
                    "span_id": wait_span_id,
                    "span_kind": "durable_mailbox_wait",
                    "event": "agent.mailbox.wait",
                    "started_at": started.get("timestamp"),
                    "ended_at": None,
                    "duration_ms": None,
                    "parent_span_id": OrganismLogWriter._agent_session_span_id(started),
                    "parallel_lane": _clean_text(started.get("agent_id")),
                    "status": "waiting",
                    "trace_id": _clean_text(started.get("trace_id")),
                    "task_id": _clean_text(started.get("task_id")),
                    "worker_id": _clean_text(started.get("worker_id")),
                    "contract_id": _clean_text(started.get("contract_id")),
                    "blocked_by": list(state.get("blocked_by") or []),
                    "start_sequence": int(started.get("sequence") or 0),
                }
            )
        )
    for turn_span_id, state in open_mailbox_turns.items():
        started = dict(state.get("start_row") or {})
        projected.append(
            _compact_dict(
                {
                    "span_id": turn_span_id,
                    "span_kind": "durable_mailbox_turn",
                    "event": "agent.mailbox.turn",
                    "started_at": started.get("timestamp"),
                    "ended_at": None,
                    "duration_ms": None,
                    "parent_span_id": OrganismLogWriter._agent_session_span_id(started),
                    "parallel_lane": _clean_text(started.get("agent_id")),
                    "status": "running",
                    "trace_id": _clean_text(started.get("trace_id")),
                    "task_id": _clean_text(started.get("task_id")),
                    "worker_id": _clean_text(started.get("worker_id")),
                    "contract_id": _clean_text(started.get("contract_id")),
                    "blocked_by": list(started.get("blocked_by") or []),
                    "start_sequence": int(started.get("sequence") or 0),
                }
            )
        )
    for span_id, started in open_spans.items():
        projected.append(
            _compact_dict(
                {
                    "span_id": span_id,
                    "span_kind": _clean_text(started.get("span_kind")),
                    "event": _clean_text(started.get("event")),
                    "started_at": started.get("timestamp"),
                    "ended_at": None,
                    "duration_ms": None,
                    "parent_span_id": _clean_text(started.get("parent_span_id")),
                    "parallel_lane": _clean_text(started.get("parallel_lane")),
                    "status": _clean_text(started.get("status")),
                    "trace_id": _clean_text(started.get("trace_id")),
                    "task_id": _clean_text(started.get("task_id")),
                    "worker_id": _clean_text(started.get("worker_id")),
                    "tool_id": _clean_text(started.get("tool_id")),
                    "model": _clean_text(started.get("model")),
                    "contract_id": _clean_text(started.get("contract_id")),
                    "blocked_by": list(started.get("blocked_by") or []),
                    "start_sequence": int(started.get("sequence") or 0),
                }
            )
        )
    return projected


def normalize_organism_log_rows(raw_rows: Iterable[dict[str, Any]]) -> list[OrganismLogRow]:
    """Normalize raw ``organism_log_v1`` rows into event + projected span rows."""

    raw_rows = [dict(row) for row in raw_rows]
    event_rows = [_normalize_event_row(row) for row in raw_rows]
    span_rows = [
        _normalize_span_row(span, raw_rows=raw_rows)
        for span in project_organism_spans(raw_rows)
    ]
    return sorted(
        [*event_rows, *span_rows],
        key=lambda row: (int(row.sequence or 0), 0 if row.record_kind == "event" else 1),
    )


def read_organism_log_rows(path: str | Path) -> list[OrganismLogRow]:
    raw_rows = readable_organism_log_v1_rows(read_organism_log(path))
    return normalize_organism_log_rows(raw_rows)


def project_organism_log(
    rows: Iterable[OrganismLogRow | dict[str, Any]],
    *,
    limit: int = 5,
) -> OrganismLogProjection:
    normalized: list[OrganismLogRow] = []
    for row in rows:
        if isinstance(row, OrganismLogRow):
            normalized.append(row)
            continue
        payload = dict(row)
        if str(payload.get("record_kind") or "") == "span":
            normalized.append(OrganismLogRow.model_validate(payload))
        else:
            normalized.append(_normalize_event_row(payload))
    events = [row for row in normalized if row.record_kind == "event"]
    spans = [row for row in normalized if row.record_kind == "span"]
    slowest_spans = sorted(
        [row for row in spans if row.duration_ms is not None],
        key=lambda row: int(row.duration_ms or 0),
        reverse=True,
    )[: max(1, int(limit))]
    blocking_spans = [
        row
        for row in spans
        if row.status in {"failed", "denied", "cancelled", "blocked"}
        or bool(_clean_text(row.wait_reason))
    ][: max(1, int(limit))]
    return OrganismLogProjection(
        events=events,
        spans=spans,
        slowest_spans=slowest_spans,
        blocking_spans=blocking_spans,
    )


def summarize_organism_log_rows(
    rows: Iterable[OrganismLogRow | dict[str, Any]],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    projection = project_organism_log(rows, limit=limit)
    return {
        "schema_version": ORGANISM_LOG_SCHEMA_VERSION,
        "row_count": len(projection.events) + len(projection.spans),
        "event_count": len(projection.events),
        "span_count": len(projection.spans),
        "slowest_spans": [
            {
                "span_id": row.span_id,
                "event_family": row.event_family,
                "summary": row.summary,
                "duration_ms": row.duration_ms,
            }
            for row in projection.slowest_spans
        ],
        "blocking_spans": [
            {
                "span_id": row.span_id,
                "event_family": row.event_family,
                "status": row.status,
                "summary": row.summary,
                "wait_reason": row.wait_reason,
            }
            for row in projection.blocking_spans
        ],
    }


def summarize_organism_log(path: str | Path, *, limit: int = 5) -> dict[str, Any]:
    rows = read_organism_log_rows(path)
    return summarize_organism_log_rows(rows, limit=limit)


__all__ = [
    "ORGANISM_LOG_SCHEMA",
    "ORGANISM_LOG_SCHEMA_VERSION",
    "normalize_organism_log_rows",
    "OrganismLogContext",
    "OrganismLogProjection",
    "OrganismLogRow",
    "OrganismLogWriter",
    "new_trace_id",
    "organism_event_context",
    "project_organism_log",
    "project_organism_spans",
    "read_organism_log",
    "read_organism_log_rows",
    "summarize_organism_log_rows",
    "stable_output_contract_id",
    "summarize_organism_log",
    "write_organism_log",
]
