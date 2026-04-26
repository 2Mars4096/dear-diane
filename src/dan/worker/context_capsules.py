"""Typed context capsules for incremental artifact passing between agents."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Sequence

from pydantic import BaseModel, Field

CapsuleKind = Literal[
    "file_context",
    "directory_context",
    "test_result",
    "shell_output",
    "web_evidence",
    "browser_state",
    "implementation_delta",
    "workspace_state",
    "blocker",
    "decision",
    "validation_result",
    "handoff_brief",
]

ArtifactState = Literal[
    "provisional",
    "useful_for_downstream",
    "validated",
    "invalidated",
    "superseded",
]

RawRefKind = Literal[
    "tool_result",
    "file",
    "url",
    "command",
    "event",
    "log",
    "screenshot",
    "artifact",
]

_DEFAULT_TEXT_LIMIT = 1200
_SPAN_TEXT_LIMIT = 900
_MAX_EVIDENCE_SPANS = 6
_TEST_COMMAND_RE = re.compile(
    r"\b(pytest|unittest|tox|nox|npm\s+test|pnpm\s+test|yarn\s+test|vitest|jest|cargo\s+test|go\s+test|mvn\s+test|gradle\s+test)\b",
    re.IGNORECASE,
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    blob = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"{prefix}:{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]}"


def _compact_text(value: Any, *, limit: int = _DEFAULT_TEXT_LIMIT) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    head_limit = max(limit // 2 - 36, 0)
    tail_limit = max(limit - head_limit - 72, 0)
    omitted = max(len(text) - head_limit - tail_limit, 0)
    return (
        text[:head_limit]
        + f"\n...[context capsule omitted {omitted} chars]...\n"
        + text[-tail_limit:]
    )


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _dedupe_text(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class RawRef(BaseModel):
    """Pointer to full-fidelity evidence outside the capsule."""

    ref_id: str = ""
    kind: RawRefKind = "tool_result"
    uri: str = ""
    path: str = ""
    url: str = ""
    command: str = ""
    event_ids: list[str] = Field(default_factory=list)
    tool_call_id: str = ""
    model_call_id: str = ""
    line_start: int | None = None
    line_end: int | None = None
    description: str = ""


class EvidenceSpan(BaseModel):
    """Small exact retained evidence carried in the active context packet."""

    kind: str = "text"
    text: str = ""
    path: str = ""
    url: str = ""
    title: str = ""
    line_start: int | None = None
    line_end: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextCapsule(BaseModel):
    """Bounded, provenance-backed retained context from a tool or worker output."""

    capsule_id: str
    kind: CapsuleKind
    source_task_id: str = ""
    source_worker_id: str = ""
    source_trace_id: str = ""
    source_event_ids: list[str] = Field(default_factory=list)
    raw_refs: list[RawRef] = Field(default_factory=list)
    summary: str = ""
    retained_evidence: list[EvidenceSpan] = Field(default_factory=list)
    relevance: str = ""
    confidence: float = 0.75
    artifact_state: ArtifactState = "useful_for_downstream"
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    unlocks: list[str] = Field(default_factory=list)
    invalidates: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utcnow_iso)


class ReadinessSignal(BaseModel):
    """A scheduler-facing signal that one or more capsules can unlock work."""

    readiness_id: str
    source_task_id: str = ""
    source_worker_id: str = ""
    capsule_ids: list[str] = Field(default_factory=list)
    ready_for_downstream: bool = False
    predicate: str = ""
    summary: str = ""
    blockers: list[str] = Field(default_factory=list)
    downstream_task_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utcnow_iso)


class ContextPacket(BaseModel):
    """Bounded context bundle passed to a downstream agent."""

    packet_id: str
    target_task_id: str = ""
    capsules: list[ContextCapsule] = Field(default_factory=list)
    raw_refs: list[RawRef] = Field(default_factory=list)
    retained_evidence: list[EvidenceSpan] = Field(default_factory=list)
    omitted_capsule_ids: list[str] = Field(default_factory=list)
    total_retained_chars: int = 0
    created_at: str = Field(default_factory=_utcnow_iso)


def build_tool_context_capsules(
    tool_record: dict[str, Any],
    *,
    source_task_id: str = "",
    source_worker_id: str = "",
    source_trace_id: str = "",
    source_event_ids: Sequence[str] | None = None,
) -> list[ContextCapsule]:
    """Derive bounded deterministic capsules from one local tool result."""

    tool_id = _clean_text(tool_record.get("tool_id"))
    if not tool_id:
        return []
    ok = bool(tool_record.get("ok"))
    arguments = dict(tool_record.get("arguments") or {})
    result = tool_record.get("result")
    error = _clean_text(tool_record.get("error"))
    event_ids = _dedupe_text(source_event_ids or [])

    if not ok:
        return [
            _make_capsule(
                tool_record,
                kind="blocker",
                summary=f"{tool_id} failed: {error or 'unknown error'}",
                retained_evidence=[
                    EvidenceSpan(
                        kind="error",
                        text=_compact_text(error or "unknown tool failure", limit=_SPAN_TEXT_LIMIT),
                    )
                ],
                relevance="Tool failure may block or redirect downstream work.",
                confidence=0.8,
                artifact_state="provisional",
                open_questions=["Determine whether this failure is transient or requires a different tool/path."],
                unlocks=[f"tool_failure:{tool_id}"],
                source_task_id=source_task_id,
                source_worker_id=source_worker_id,
                source_trace_id=source_trace_id,
                source_event_ids=event_ids,
            )
        ]

    if tool_id == "file_read":
        return _file_read_capsules(
            tool_record,
            arguments=arguments,
            result=dict(result or {}) if isinstance(result, dict) else {},
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=event_ids,
        )
    if tool_id == "list_directory":
        return _list_directory_capsules(
            tool_record,
            arguments=arguments,
            result=dict(result or {}) if isinstance(result, dict) else {},
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=event_ids,
        )
    if tool_id == "workspace_check":
        return _workspace_check_capsules(
            tool_record,
            arguments=arguments,
            result=dict(result or {}) if isinstance(result, dict) else {},
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=event_ids,
        )
    if tool_id == "shell_command":
        return _shell_capsules(
            tool_record,
            arguments=arguments,
            result=dict(result or {}) if isinstance(result, dict) else {},
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=event_ids,
        )
    if tool_id in {"web_search", "web_fetch", "http_request"}:
        return _web_capsules(
            tool_record,
            arguments=arguments,
            result=dict(result or {}) if isinstance(result, dict) else {},
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=event_ids,
        )
    if tool_id in {"git_diff", "git_status", "git_log"}:
        return _git_capsules(
            tool_record,
            arguments=arguments,
            result=dict(result or {}) if isinstance(result, dict) else {},
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=event_ids,
        )
    if tool_id in {"file_write", "file_edit", "file_copy", "file_move", "file_delete"}:
        return _mutation_capsules(
            tool_record,
            arguments=arguments,
            result=dict(result or {}) if isinstance(result, dict) else {},
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=event_ids,
        )
    if tool_id.startswith("browser_"):
        return _browser_capsules(
            tool_record,
            arguments=arguments,
            result=result if isinstance(result, dict) else {"result": result},
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=event_ids,
        )
    return []


def assemble_context_packet(
    capsules: Sequence[ContextCapsule | dict[str, Any]],
    *,
    target_task_id: str = "",
    max_capsules: int = 12,
    max_evidence_chars: int = 5000,
    include_kinds: set[str] | None = None,
) -> ContextPacket:
    """Build a bounded downstream context packet from retained capsules."""

    normalized: list[ContextCapsule] = []
    for capsule in capsules:
        parsed = (
            capsule
            if isinstance(capsule, ContextCapsule)
            else ContextCapsule.model_validate(capsule)
        )
        if include_kinds is not None and parsed.kind not in include_kinds:
            continue
        normalized.append(parsed)

    selected = normalized[: max(max_capsules, 0)]
    omitted = [capsule.capsule_id for capsule in normalized[len(selected) :]]
    raw_refs: list[RawRef] = []
    evidence: list[EvidenceSpan] = []
    retained_chars = 0
    raw_ref_keys: set[tuple[str, str, str]] = set()
    for capsule in selected:
        for ref in capsule.raw_refs:
            key = (ref.kind, ref.path or ref.url or ref.command or ref.uri, ref.tool_call_id)
            if key in raw_ref_keys:
                continue
            raw_ref_keys.add(key)
            raw_refs.append(ref)
        for span in capsule.retained_evidence:
            if retained_chars >= max_evidence_chars:
                break
            remaining = max(max_evidence_chars - retained_chars, 0)
            text = _compact_text(span.text, limit=min(len(span.text), remaining))
            if len(text) > remaining:
                text = text[:remaining]
            retained_chars += len(text)
            evidence.append(span.model_copy(update={"text": text}))

    packet_id = _stable_id(
        "ctxpkt",
        {
            "target_task_id": target_task_id,
            "capsules": [capsule.capsule_id for capsule in selected],
            "omitted": omitted,
        },
    )
    return ContextPacket(
        packet_id=packet_id,
        target_task_id=target_task_id,
        capsules=selected,
        raw_refs=raw_refs,
        retained_evidence=evidence,
        omitted_capsule_ids=omitted,
        total_retained_chars=retained_chars,
    )


def readiness_signal_from_capsules(
    capsules: Sequence[ContextCapsule | dict[str, Any]],
    *,
    source_task_id: str = "",
    source_worker_id: str = "",
    predicate: str = "capsules_available",
    downstream_task_ids: Sequence[str] | None = None,
) -> ReadinessSignal:
    normalized = [
        capsule if isinstance(capsule, ContextCapsule) else ContextCapsule.model_validate(capsule)
        for capsule in capsules
    ]
    blockers = [
        capsule.summary
        for capsule in normalized
        if capsule.kind == "blocker" and capsule.summary
    ]
    useful = [
        capsule
        for capsule in normalized
        if capsule.artifact_state in {"useful_for_downstream", "validated"}
        and capsule.kind != "blocker"
    ]
    summary = "; ".join(capsule.summary for capsule in useful[:3] if capsule.summary)
    readiness_id = _stable_id(
        "ready",
        {
            "predicate": predicate,
            "capsules": [capsule.capsule_id for capsule in normalized],
            "downstream": list(downstream_task_ids or []),
        },
    )
    return ReadinessSignal(
        readiness_id=readiness_id,
        source_task_id=source_task_id,
        source_worker_id=source_worker_id,
        capsule_ids=[capsule.capsule_id for capsule in normalized],
        ready_for_downstream=bool(useful) and not blockers,
        predicate=predicate,
        summary=summary or ("Blocked: " + "; ".join(blockers[:2]) if blockers else ""),
        blockers=blockers,
        downstream_task_ids=_dedupe_text(downstream_task_ids or []),
    )


def _make_capsule(
    tool_record: dict[str, Any],
    *,
    kind: CapsuleKind,
    summary: str,
    retained_evidence: list[EvidenceSpan] | None = None,
    raw_refs: list[RawRef] | None = None,
    relevance: str,
    confidence: float = 0.75,
    artifact_state: ArtifactState = "useful_for_downstream",
    assumptions: list[str] | None = None,
    open_questions: list[str] | None = None,
    unlocks: list[str] | None = None,
    invalidates: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    source_task_id: str = "",
    source_worker_id: str = "",
    source_trace_id: str = "",
    source_event_ids: Sequence[str] | None = None,
) -> ContextCapsule:
    tool_id = _clean_text(tool_record.get("tool_id"))
    tool_call_id = _clean_text(tool_record.get("tool_call_id"))
    model_call_id = _clean_text(tool_record.get("model_call_id"))
    payload = {
        "kind": kind,
        "tool_id": tool_id,
        "tool_call_id": tool_call_id,
        "model_call_id": model_call_id,
        "summary": summary,
        "source_task_id": source_task_id,
        "source_worker_id": source_worker_id,
        "source_trace_id": source_trace_id,
    }
    return ContextCapsule(
        capsule_id=_stable_id("ctxcap", payload),
        kind=kind,
        source_task_id=source_task_id,
        source_worker_id=source_worker_id,
        source_trace_id=source_trace_id,
        source_event_ids=_dedupe_text(source_event_ids or []),
        raw_refs=raw_refs
        or [
            RawRef(
                ref_id=_stable_id(
                    "raw",
                    {
                        "tool_id": tool_id,
                        "tool_call_id": tool_call_id,
                        "model_call_id": model_call_id,
                    },
                ),
                kind="tool_result",
                event_ids=_dedupe_text(source_event_ids or []),
                tool_call_id=tool_call_id,
                model_call_id=model_call_id,
                description=f"Full `{tool_id}` tool result",
            )
        ],
        summary=summary,
        retained_evidence=retained_evidence or [],
        relevance=relevance,
        confidence=max(0.0, min(float(confidence), 1.0)),
        artifact_state=artifact_state,
        assumptions=assumptions or [],
        open_questions=open_questions or [],
        unlocks=_dedupe_text(unlocks or []),
        invalidates=_dedupe_text(invalidates or []),
        metadata={"tool_id": tool_id, **dict(metadata or {})},
    )


def _raw_ref_for_tool(
    tool_record: dict[str, Any],
    *,
    kind: RawRefKind = "tool_result",
    path: str = "",
    url: str = "",
    command: str = "",
    line_start: int | None = None,
    line_end: int | None = None,
    description: str = "",
    source_event_ids: Sequence[str] | None = None,
) -> RawRef:
    tool_id = _clean_text(tool_record.get("tool_id"))
    tool_call_id = _clean_text(tool_record.get("tool_call_id"))
    model_call_id = _clean_text(tool_record.get("model_call_id"))
    return RawRef(
        ref_id=_stable_id(
            "raw",
            {
                "tool_id": tool_id,
                "tool_call_id": tool_call_id,
                "path": path,
                "url": url,
                "command": command,
                "line_start": line_start,
                "line_end": line_end,
            },
        ),
        kind=kind,
        path=path,
        url=url,
        command=command,
        event_ids=_dedupe_text(source_event_ids or []),
        tool_call_id=tool_call_id,
        model_call_id=model_call_id,
        line_start=line_start,
        line_end=line_end,
        description=description,
    )


def _file_read_capsules(
    tool_record: dict[str, Any],
    *,
    arguments: dict[str, Any],
    result: dict[str, Any],
    source_task_id: str,
    source_worker_id: str,
    source_trace_id: str,
    source_event_ids: Sequence[str],
) -> list[ContextCapsule]:
    path = _first_non_empty(result.get("path"), arguments.get("path"), arguments.get("file_path"))
    start_line = _int_or_none(arguments.get("start_line"))
    end_line = _int_or_none(arguments.get("end_line"))
    content = str(result.get("content") or "")
    line_count = _int_or_none(result.get("line_count"))
    summary = f"Read `{path or 'unknown file'}`"
    if start_line or end_line:
        summary += f" lines {start_line or 1}-{end_line or 'end'}"
    if line_count is not None:
        summary += f" ({line_count} returned lines)"
    raw_ref = _raw_ref_for_tool(
        tool_record,
        kind="file",
        path=path,
        line_start=start_line,
        line_end=end_line,
        description="Full file content is available through the original tool result or a targeted file_read.",
        source_event_ids=source_event_ids,
    )
    return [
        _make_capsule(
            tool_record,
            kind="file_context",
            summary=summary,
            retained_evidence=[
                EvidenceSpan(
                    kind="file_excerpt",
                    text=_compact_text(content, limit=_SPAN_TEXT_LIMIT),
                    path=path,
                    line_start=start_line,
                    line_end=end_line,
                    metadata={
                        "line_count": line_count,
                        "size": result.get("size"),
                    },
                )
            ]
            if content
            else [],
            raw_refs=[raw_ref],
            relevance="File content was inspected and may ground downstream code, research, or validation work.",
            confidence=0.9,
            unlocks=[f"file_context:{path}"] if path else ["file_context"],
            metadata={"line_count": line_count, "size": result.get("size")},
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=source_event_ids,
        )
    ]


def _list_directory_capsules(
    tool_record: dict[str, Any],
    *,
    arguments: dict[str, Any],
    result: dict[str, Any],
    source_task_id: str,
    source_worker_id: str,
    source_trace_id: str,
    source_event_ids: Sequence[str],
) -> list[ContextCapsule]:
    path = _first_non_empty(arguments.get("path"), ".")
    entries = [entry for entry in result.get("entries") or [] if isinstance(entry, dict)]
    lines = [
        f"{entry.get('type') or 'entry'} {entry.get('path') or entry.get('name') or ''}".strip()
        for entry in entries[:50]
    ]
    summary = f"Listed `{path}`: {result.get('count', len(entries))} entries"
    if result.get("truncated"):
        summary += " (truncated)"
    return [
        _make_capsule(
            tool_record,
            kind="directory_context",
            summary=summary,
            retained_evidence=[
                EvidenceSpan(
                    kind="directory_entries",
                    text=_compact_text("\n".join(lines), limit=_SPAN_TEXT_LIMIT),
                    path=path,
                    metadata={
                        "count": result.get("count"),
                        "total_count": result.get("total_count"),
                        "truncated": result.get("truncated"),
                        "next_start_after": result.get("next_start_after"),
                    },
                )
            ]
            if lines
            else [],
            raw_refs=[
                _raw_ref_for_tool(
                    tool_record,
                    kind="tool_result",
                    path=path,
                    description="Full directory listing page is available in the tool result.",
                    source_event_ids=source_event_ids,
                )
            ],
            relevance="Directory inventory can route downstream file-selection or artifact checks.",
            confidence=0.85,
            unlocks=[f"directory_context:{path}"],
            metadata={
                "count": result.get("count"),
                "total_count": result.get("total_count"),
                "truncated": result.get("truncated"),
            },
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=source_event_ids,
        )
    ]


def _shell_capsules(
    tool_record: dict[str, Any],
    *,
    arguments: dict[str, Any],
    result: dict[str, Any],
    source_task_id: str,
    source_worker_id: str,
    source_trace_id: str,
    source_event_ids: Sequence[str],
) -> list[ContextCapsule]:
    command = _clean_text(arguments.get("command"))
    exit_code = _int_or_none(result.get("exit_code"))
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    is_test = bool(_TEST_COMMAND_RE.search(command))
    kind: CapsuleKind = "test_result" if is_test else "shell_output"
    status = "passed" if exit_code == 0 else "failed"
    summary = f"Command `{command or 'unknown'}` {status}"
    if exit_code is not None:
        summary += f" with exit code {exit_code}"
    output = "\n".join(part for part in [stdout, stderr] if part)
    return [
        _make_capsule(
            tool_record,
            kind=kind,
            summary=summary,
            retained_evidence=[
                EvidenceSpan(
                    kind="command_output",
                    text=_compact_text(output, limit=_SPAN_TEXT_LIMIT),
                    metadata={"exit_code": exit_code},
                )
            ]
            if output
            else [],
            raw_refs=[
                _raw_ref_for_tool(
                    tool_record,
                    kind="command",
                    command=command,
                    description="Full shell command output is available in the tool result.",
                    source_event_ids=source_event_ids,
                )
            ],
            relevance=(
                "Test output can unlock validation or repair decisions."
                if is_test
                else "Command output may contain diagnostics for downstream work."
            ),
            confidence=0.9 if exit_code == 0 else 0.7,
            artifact_state="useful_for_downstream" if exit_code == 0 else "provisional",
            open_questions=[] if exit_code == 0 else ["Inspect failing command output before treating downstream work as validated."],
            unlocks=[
                "validation:test_passed" if is_test and exit_code == 0 else f"command:{status}"
            ],
            metadata={"exit_code": exit_code, "is_test_command": is_test},
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=source_event_ids,
        )
    ]


def _workspace_check_capsules(
    tool_record: dict[str, Any],
    *,
    arguments: dict[str, Any],
    result: dict[str, Any],
    source_task_id: str,
    source_worker_id: str,
    source_trace_id: str,
    source_event_ids: Sequence[str],
) -> list[ContextCapsule]:
    check = _first_non_empty(result.get("check"), arguments.get("check"))
    path = _first_non_empty(result.get("path"), arguments.get("path"))
    passed = result.get("passed")
    if passed is True:
        status = "passed"
        artifact_state: ArtifactState = "validated"
        confidence = 0.95
    elif passed is False:
        status = "failed"
        artifact_state = "invalidated"
        confidence = 0.9
    else:
        status = "completed"
        artifact_state = "useful_for_downstream"
        confidence = 0.85
    summary = f"workspace_check {check or 'check'} {status}"
    if path:
        summary += f" for `{path}`"
    retained = _compact_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
        limit=_SPAN_TEXT_LIMIT,
    )
    return [
        _make_capsule(
            tool_record,
            kind="validation_result",
            summary=summary,
            retained_evidence=[
                EvidenceSpan(
                    kind="workspace_check",
                    text=retained,
                    path=path,
                    metadata={
                        "check": check,
                        "passed": passed,
                        "size": result.get("size"),
                    },
                )
            ],
            raw_refs=[
                _raw_ref_for_tool(
                    tool_record,
                    kind="file" if path else "tool_result",
                    path=path,
                    description="Full deterministic workspace check result is available in the tool result.",
                    source_event_ids=source_event_ids,
                )
            ],
            relevance="Deterministic workspace checks can unlock validation, repair, or aggregation decisions without shell execution.",
            confidence=confidence,
            artifact_state=artifact_state,
            open_questions=[] if passed is not False else ["Resolve the deterministic workspace check failure before treating the artifact as validated."],
            unlocks=[f"workspace_check:{check}", "validation:deterministic_check_passed"]
            if passed is True
            else [f"workspace_check:{check}"],
            invalidates=[f"workspace_check:{check}"] if passed is False else [],
            metadata={
                "check": check,
                "passed": passed,
                "path": path,
            },
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=source_event_ids,
        )
    ]


def _web_capsules(
    tool_record: dict[str, Any],
    *,
    arguments: dict[str, Any],
    result: dict[str, Any],
    source_task_id: str,
    source_worker_id: str,
    source_trace_id: str,
    source_event_ids: Sequence[str],
) -> list[ContextCapsule]:
    query = _first_non_empty(arguments.get("query"), arguments.get("url"))
    results = [item for item in result.get("results") or [] if isinstance(item, dict)]
    fetched = [item for item in result.get("fetched_results") or [] if isinstance(item, dict)]
    spans: list[EvidenceSpan] = []
    raw_refs: list[RawRef] = []
    for item in (results + fetched)[:_MAX_EVIDENCE_SPANS]:
        url = _clean_text(item.get("url"))
        title = _clean_text(item.get("title")) or url
        text = _first_non_empty(item.get("snippet"), item.get("content"), item.get("text"), item.get("excerpt"))
        if text:
            spans.append(
                EvidenceSpan(
                    kind="web_excerpt",
                    title=title,
                    url=url,
                    text=_compact_text(text, limit=_SPAN_TEXT_LIMIT // 2),
                    metadata={
                        "provider": item.get("provider") or result.get("provider"),
                        "authority_tier": item.get("authority_tier"),
                    },
                )
            )
        if url:
            raw_refs.append(
                _raw_ref_for_tool(
                    tool_record,
                    kind="url",
                    url=url,
                    description=f"Web evidence source: {title}",
                    source_event_ids=source_event_ids,
                )
            )
    summary = f"Web evidence for `{query or 'query'}`: {result.get('count', len(results))} results"
    return [
        _make_capsule(
            tool_record,
            kind="web_evidence",
            summary=summary,
            retained_evidence=spans,
            raw_refs=raw_refs
            or [
                _raw_ref_for_tool(
                    tool_record,
                    kind="tool_result",
                    description="Full web search result is available in the tool result.",
                    source_event_ids=source_event_ids,
                )
            ],
            relevance="Web evidence can ground downstream research, verification, or citation tasks.",
            confidence=0.75,
            unlocks=["web_evidence"],
            metadata={
                "query": query,
                "provider": result.get("provider"),
                "count": result.get("count"),
                "grounded_result_count": result.get("grounded_result_count"),
            },
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=source_event_ids,
        )
    ]


def _git_capsules(
    tool_record: dict[str, Any],
    *,
    arguments: dict[str, Any],
    result: dict[str, Any],
    source_task_id: str,
    source_worker_id: str,
    source_trace_id: str,
    source_event_ids: Sequence[str],
) -> list[ContextCapsule]:
    tool_id = _clean_text(tool_record.get("tool_id"))
    if tool_id == "git_diff":
        summary = (
            f"Git diff: {result.get('files_changed', 0)} files, "
            f"+{result.get('additions', 0)} -{result.get('deletions', 0)}"
        )
        evidence_text = _compact_text(result.get("diff_text") or "", limit=_SPAN_TEXT_LIMIT)
        kind: CapsuleKind = "implementation_delta"
    else:
        summary = f"{tool_id}: branch {result.get('branch') or ''}".strip()
        status_parts = []
        for key in ("modified", "staged", "untracked"):
            values = result.get(key) or []
            if values:
                status_parts.append(f"{key}: " + ", ".join(str(item) for item in values[:12]))
        evidence_text = _compact_text("\n".join(status_parts), limit=_SPAN_TEXT_LIMIT)
        kind = "workspace_state"
    return [
        _make_capsule(
            tool_record,
            kind=kind,
            summary=summary,
            retained_evidence=[
                EvidenceSpan(
                    kind=tool_id,
                    text=evidence_text,
                    path=_clean_text(arguments.get("path")),
                    metadata={key: value for key, value in result.items() if key != "diff_text"},
                )
            ]
            if evidence_text
            else [],
            raw_refs=[
                _raw_ref_for_tool(
                    tool_record,
                    kind="tool_result",
                    path=_clean_text(arguments.get("path")),
                    description=f"Full `{tool_id}` result is available in the tool result.",
                    source_event_ids=source_event_ids,
                )
            ],
            relevance="Git state can unlock merge, validation, or changed-file routing decisions.",
            confidence=0.9,
            unlocks=[kind],
            metadata={key: value for key, value in result.items() if key != "diff_text"},
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=source_event_ids,
        )
    ]


def _mutation_capsules(
    tool_record: dict[str, Any],
    *,
    arguments: dict[str, Any],
    result: dict[str, Any],
    source_task_id: str,
    source_worker_id: str,
    source_trace_id: str,
    source_event_ids: Sequence[str],
) -> list[ContextCapsule]:
    tool_id = _clean_text(tool_record.get("tool_id"))
    path = _first_non_empty(result.get("path"), arguments.get("path"), arguments.get("source_path"))
    mode = _first_non_empty(result.get("mode"), arguments.get("mode"))
    summary = f"{tool_id} changed `{path or 'workspace'}`"
    if mode:
        summary += f" using {mode}"
    return [
        _make_capsule(
            tool_record,
            kind="implementation_delta",
            summary=summary,
            retained_evidence=[
                EvidenceSpan(
                    kind="mutation_result",
                    text=_compact_text(
                        json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
                        limit=_SPAN_TEXT_LIMIT,
                    ),
                    path=path,
                    metadata={"mode": mode},
                )
            ],
            raw_refs=[
                _raw_ref_for_tool(
                    tool_record,
                    kind="file" if path else "tool_result",
                    path=path,
                    description="Full mutation result is available in the tool result; file content can be re-read from the workspace.",
                    source_event_ids=source_event_ids,
                )
            ],
            relevance="Workspace mutation can unlock downstream validation or dependent implementation work.",
            confidence=0.85,
            unlocks=["implementation_delta", f"file_changed:{path}" if path else "workspace_changed"],
            metadata={"mode": mode, **{key: value for key, value in result.items() if key != "content"}},
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=source_event_ids,
        )
    ]


def _browser_capsules(
    tool_record: dict[str, Any],
    *,
    arguments: dict[str, Any],
    result: dict[str, Any],
    source_task_id: str,
    source_worker_id: str,
    source_trace_id: str,
    source_event_ids: Sequence[str],
) -> list[ContextCapsule]:
    tool_id = _clean_text(tool_record.get("tool_id"))
    url = _first_non_empty(result.get("url"), arguments.get("url"))
    summary = f"{tool_id} captured browser state"
    if url:
        summary += f" at {url}"
    retained = _compact_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
        limit=_SPAN_TEXT_LIMIT,
    )
    return [
        _make_capsule(
            tool_record,
            kind="browser_state",
            summary=summary,
            retained_evidence=[
                EvidenceSpan(
                    kind="browser_state",
                    url=url,
                    text=retained,
                )
            ],
            raw_refs=[
                _raw_ref_for_tool(
                    tool_record,
                    kind="url" if url else "tool_result",
                    url=url,
                    description="Full browser tool result is available in the tool result.",
                    source_event_ids=source_event_ids,
                )
            ],
            relevance="Browser state can unlock UI/operator follow-up work.",
            confidence=0.75,
            unlocks=["browser_state"],
            source_task_id=source_task_id,
            source_worker_id=source_worker_id,
            source_trace_id=source_trace_id,
            source_event_ids=source_event_ids,
        )
    ]


__all__ = [
    "ArtifactState",
    "CapsuleKind",
    "ContextCapsule",
    "ContextPacket",
    "EvidenceSpan",
    "RawRef",
    "ReadinessSignal",
    "assemble_context_packet",
    "build_tool_context_capsules",
    "readiness_signal_from_capsules",
]
