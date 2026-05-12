"""Core progress narrator contracts.

The narrator lane is intentionally snapshot-only. It explains already-known
run state for status/progress questions, but it never receives workspace tools
or mutation capabilities.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Sequence


NARRATOR_READ_ONLY = "narrator read-only"
EXECUTOR_READ_ONLY = "executor read-only"
EXECUTOR_WRITE = "executor write"
CLARIFICATION = "clarification"

_WRITE_INTENT_RE = re.compile(
    r"\b(?:fix|add|update|create|touch|refactor|implement|delete|remove|write|build|generate|patch|modify|change|"
    r"edit|scaffold|install|rename|move|copy|migrate|continue|proceed|capture|collect|run|execute)\b",
    flags=re.IGNORECASE,
)
_READ_INTENT_RE = re.compile(
    r"\b(?:show|list|read|view|open|display|explain|summari[sz]e|find|search|check|inspect|review|what|where|"
    r"which|why|how|tell)\b",
    flags=re.IGNORECASE,
)
_PROGRESS_INTENT_RE = re.compile(
    r"\b(?:current|latest|recent|last|active|running|progress|status|state|phase|step|elapsed|working|work|"
    r"happening|going on|where are we|what now|what's next|next expected)\b",
    flags=re.IGNORECASE,
)
_STATUS_PHRASE_RE = re.compile(
    r"(?:what(?:'s| is)\s+(?:happening|going on|the status|the progress)|"
    r"where\s+are\s+we|"
    r"tell\s+me\s+(?:the\s+)?(?:current\s+)?(?:progress|status)|"
    r"current\s+(?:progress|status)|"
    r"(?:show|summari[sz]e|explain)\s+(?:the\s+)?(?:current\s+)?(?:progress|status|run state)|"
    r"/(?:progress|last|narrate)\b)",
    flags=re.IGNORECASE,
)
_NEXT_STEP_RE = re.compile(
    r"\b(?:what(?:'s| is)?\s+next|next\s+step|what\s+now|next\s+expected|where\s+next|whats\s+next)\b",
    flags=re.IGNORECASE,
)
_PATH_HINT_RE = re.compile(
    r"(?<![\w$])(?:~|/|\./|\.\./|\.dan-super/|docs/|src/|tests/|apps/|website/|data/|raw/|figures/|tables/|logs/|beamer/)"
    r"[^\s,;:)]+"
    r"|(?<![\w$])[\w.-]+\.(?:py|md|jsonl?|html|css|js|ts|tsx|txt|csv|parquet|tex|pdf|png|jpg|jpeg|svg)(?![\w])"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clip(value: Any, *, limit: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _compact_list(values: Sequence[Any], *, limit: int = 8) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _display_path(path: str, workspace: str = "") -> str:
    text = str(path or "").strip()
    root = str(workspace or "").strip().rstrip("/")
    if root and text.startswith(root + "/"):
        return text[len(root) + 1 :]
    return text


def _clean_progress_line(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\s*-\s*", "", text)
    if not text:
        return ""
    if text.startswith("Progress summary requested") or text.startswith("Narrator "):
        return ""
    if "Workspace context checked" in text:
        match = re.search(r"\((\d+\s+items?)\)", text)
        suffix = f" ({match.group(1)})" if match else ""
        return f"workspace context checked{suffix}"
    if "Terminal command finished" in text:
        return _clip(text, limit=140)
    if text.startswith("Model provider ready"):
        return "model provider ready"
    if text.startswith("Validation "):
        return text.rstrip(".")
    if text.startswith("File changed:"):
        return text
    if "{" in text or "}" in text or "result=" in text or "arguments=" in text:
        return ""
    return _clip(text, limit=150)


def _latest_clean_progress(values: Sequence[str]) -> str:
    for value in reversed(values):
        cleaned = _clean_progress_line(value)
        if cleaned:
            return cleaned
    return ""


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _is_next_step_question(text: str) -> bool:
    return bool(_NEXT_STEP_RE.search(str(text or "")))


def _status_line(snapshot: RunNarratorSnapshot) -> str:
    state = snapshot.status or "unknown"
    phase = snapshot.phase or "unknown"
    elapsed = f" after {_format_elapsed(snapshot.elapsed_seconds)}" if float(snapshot.elapsed_seconds or 0.0) >= 1.0 else ""
    if state in {"completed", "done"}:
        return f"Status: complete{elapsed}; final phase was {phase}."
    if state in {"failed", "blocked", "stopped"}:
        return f"Status: {state}{elapsed}; phase is {phase}."
    return f"Status: {state}{elapsed}; phase is {phase}."


def _next_step_line(snapshot: RunNarratorSnapshot) -> str:
    state = str(snapshot.status or "").strip().lower()
    phase = str(snapshot.phase or "").strip().lower()
    queued = _clip(snapshot.queued_work, limit=120)
    current = _clean_progress_line(snapshot.current_step) or _clip(snapshot.current_step, limit=120)
    if snapshot.blockers:
        return f"Next: address the visible blocker: {_clip(snapshot.blockers[-1], limit=120)}."
    if state in {"completed", "done"}:
        return "Next: the run is already complete; review the changed files or start a new request."
    if state in {"failed", "blocked", "stopped"}:
        return "Next: inspect the failure/blocker and run a repair or follow-up request."
    if queued:
        return f"Next: queued {queued} should run after the current phase."
    if "valid" in phase or "valid" in current.lower():
        return "Next: validation should finish; if it passes the run can complete, otherwise repair/follow-up work will be queued."
    if "model" in phase:
        return "Next: the model is choosing whether to call a tool, validate, or finish from the current workspace state."
    if "tool" in phase:
        return "Next: the active tool needs to finish, then the run will decide whether to validate or continue."
    if current:
        return f"Next: continue the current step: {current}."
    return "Next: wait for the next run event; no more specific next action is visible in the snapshot."


@dataclass(frozen=True)
class AgentTurnIntentDecision:
    """Three-lane front-door decision shared by surfaces."""

    lane: str
    confidence: float
    rationale: str
    executor_effort: str = ""
    clarification: str = ""

    @property
    def needs_clarification(self) -> bool:
        return self.lane == CLARIFICATION or bool(self.clarification)

    @property
    def is_narrator(self) -> bool:
        return self.lane == NARRATOR_READ_ONLY


def classify_agent_turn_intent(text: str) -> AgentTurnIntentDecision:
    """Classify a surface turn into narrator/read-only/write executor lanes.

    The classifier is deliberately conservative and lexical. Model-assisted
    routing can replace this later, but the capability boundary should stay the
    same: narrator requests answer from state snapshots, executor read-only may
    inspect workspace context, and executor write may mutate.
    """

    stripped = str(text or "").strip()
    if not stripped:
        return AgentTurnIntentDecision(
            lane=CLARIFICATION,
            confidence=0.0,
            rationale="empty input",
            clarification="What should Super DAN do?",
        )
    lowered = stripped.lower()
    has_write = bool(_WRITE_INTENT_RE.search(lowered))
    has_read = bool(_READ_INTENT_RE.search(lowered))
    has_path = bool(_PATH_HINT_RE.search(stripped))
    is_progress_command = lowered.startswith(("/progress", "/last", "/narrate"))
    has_progress = bool(_PROGRESS_INTENT_RE.search(lowered) or _STATUS_PHRASE_RE.search(lowered))

    if has_write:
        return AgentTurnIntentDecision(
            lane=EXECUTOR_WRITE,
            confidence=0.84,
            rationale="mutation or execution intent detected",
            executor_effort="complex",
        )
    if has_progress and (not has_path or is_progress_command):
        return AgentTurnIntentDecision(
            lane=NARRATOR_READ_ONLY,
            confidence=0.86,
            rationale="progress/status question over current or recent run state",
        )
    if has_read:
        effort = "complex" if re.search(r"\b(?:summari[sz]e|explain|review|search|find|workspace|project|all)\b", lowered) else "simple"
        return AgentTurnIntentDecision(
            lane=EXECUTOR_READ_ONLY,
            confidence=0.78,
            rationale="workspace inspection or answer request detected",
            executor_effort=effort,
        )
    return AgentTurnIntentDecision(
        lane=CLARIFICATION,
        confidence=0.35,
        rationale="no clear narrator, read-only executor, or write executor intent",
        clarification="Should this be a progress/status answer, a read-only workspace answer, or workspace-changing work?",
    )


@dataclass(frozen=True)
class TranscriptRef:
    role: str
    text: str
    created_at: str = ""

    def to_payload(self) -> dict[str, str]:
        return {
            "role": self.role,
            "text": self.text,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class RunNarratorSnapshot:
    """Snapshot passed to the narrator lane.

    It should be assembled from run state, event streams, queue state, and
    transcript state. It should not require arbitrary file reads.
    """

    snapshot_version: str = "run_narrator_snapshot_v1"
    snapshot_id: str = ""
    created_at: str = field(default_factory=_now_iso)
    run_id: str = ""
    task_id: str = ""
    objective: str = ""
    workspace: str = ""
    status: str = "idle"
    phase: str = "waiting"
    current_step: str = ""
    elapsed_seconds: float = 0.0
    model: str = ""
    mode_line: str = ""
    recent_events: tuple[str, ...] = ()
    activity: tuple[str, ...] = ()
    results: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    validation: str = ""
    validation_score: str = ""
    blockers: tuple[str, ...] = ()
    queued_work: str = ""
    trace_refs: tuple[str, ...] = ()
    transcript_tail: tuple[TranscriptRef, ...] = ()
    source_event_count: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "snapshot_version": self.snapshot_version,
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "objective": self.objective,
            "workspace": self.workspace,
            "status": self.status,
            "phase": self.phase,
            "current_step": self.current_step,
            "elapsed_seconds": round(float(self.elapsed_seconds or 0.0), 3),
            "model": self.model,
            "mode_line": self.mode_line,
            "recent_events": list(self.recent_events),
            "activity": list(self.activity),
            "results": list(self.results),
            "changed_files": list(self.changed_files),
            "artifacts": list(self.artifacts),
            "validation": self.validation,
            "validation_score": self.validation_score,
            "blockers": list(self.blockers),
            "queued_work": self.queued_work,
            "trace_refs": list(self.trace_refs),
            "transcript_tail": [item.to_payload() for item in self.transcript_tail],
            "source_event_count": int(self.source_event_count or 0),
        }

    def to_prompt_text(self) -> str:
        return json.dumps(self.to_payload(), sort_keys=True, ensure_ascii=True, indent=2)

    @property
    def has_run_context(self) -> bool:
        return bool(
            self.run_id
            or self.task_id
            or self.objective
            or self.recent_events
            or self.activity
            or self.results
            or self.changed_files
            or self.artifacts
            or self.trace_refs
        )


@dataclass(frozen=True)
class NarratorRequest:
    question: str
    snapshot: RunNarratorSnapshot
    request_id: str = ""
    surface: str = ""
    style: str = "compact"
    verbosity: str = "normal"
    max_tokens: int = 700
    created_at: str = field(default_factory=_now_iso)

    def to_prompt_text(self) -> str:
        return (
            f"User question: {self.question}\n"
            f"Surface: {self.surface or 'unknown'}\n"
            f"Style: {self.style}; verbosity: {self.verbosity}\n\n"
            "Run snapshot:\n"
            f"{self.snapshot.to_prompt_text()}"
        )


@dataclass(frozen=True)
class NarratorResponse:
    request_id: str
    status: str
    text: str
    created_at: str = field(default_factory=_now_iso)
    cited_refs: tuple[str, ...] = ()
    confidence: float = 1.0
    stale: bool = False
    fallback_used: bool = False
    failure: str = ""
    snapshot_id: str = ""

    def is_stale_against(self, snapshot: RunNarratorSnapshot) -> bool:
        return bool(self.snapshot_id and snapshot.snapshot_id and self.snapshot_id != snapshot.snapshot_id)


NarratorEventCallback = Callable[[dict[str, Any]], None]
NarratorSnapshotGetter = Callable[[], RunNarratorSnapshot]


def _emit_narrator_event(
    callback: NarratorEventCallback | None,
    event_type: str,
    *,
    request: NarratorRequest | None = None,
    response: NarratorResponse | None = None,
    text: str = "",
    error: str = "",
) -> None:
    if callback is None:
        return
    callback(
        build_narrator_event(
            event_type,
            request=request,
            response=response,
            text=text,
            error=error,
        )
    )


@dataclass
class NarratorJobHandle:
    """Cancellable handle for one in-flight narrator request."""

    request: NarratorRequest
    task: asyncio.Task[NarratorResponse]

    def cancel(self) -> bool:
        return self.task.cancel()

    def done(self) -> bool:
        return self.task.done()

    async def wait(self) -> NarratorResponse:
        return await self.task


def build_narrator_event(
    event_type: str,
    *,
    request: NarratorRequest | None = None,
    response: NarratorResponse | None = None,
    text: str = "",
    error: str = "",
) -> dict[str, Any]:
    """Return a normalized narrator event row for surface subscribers."""

    snapshot = request.snapshot if request is not None else None
    if snapshot is None and response is not None:
        snapshot_id = response.snapshot_id
    else:
        snapshot_id = snapshot.snapshot_id if snapshot is not None else ""
    return {
        "event": event_type,
        "request_id": request.request_id if request is not None else response.request_id if response is not None else "",
        "snapshot_id": snapshot_id,
        "snapshot_version": snapshot.snapshot_version if snapshot is not None else "",
        "surface": request.surface if request is not None else "",
        "status": response.status if response is not None else "",
        "text": text or (response.text if response is not None else ""),
        "error": error or (response.failure if response is not None else ""),
        "created_at": _now_iso(),
    }


def deterministic_narrator_response(request: NarratorRequest) -> NarratorResponse:
    """Build a compact answer from the snapshot without a model provider."""

    snapshot = request.snapshot
    lines: list[str] = []
    wants_next_step = _is_next_step_question(request.question)
    if not snapshot.has_run_context:
        transcript_hint = ""
        if snapshot.transcript_tail:
            latest = snapshot.transcript_tail[-1]
            transcript_hint = f" Latest transcript entry: {latest.role}: {_clip(latest.text, limit=120)}"
        if wants_next_step:
            lines.append("Next: no active or recent run is available, so there is no visible next step yet.")
        else:
            lines.append("Status: no active or recent run is available in this session snapshot." + transcript_hint)
    else:
        if wants_next_step:
            lines.append(_next_step_line(snapshot))
            lines.append(_status_line(snapshot))
        else:
            lines.append(_status_line(snapshot))
            if str(snapshot.status or "").strip().lower() in {"running", "queued"}:
                lines.append(_next_step_line(snapshot))
        latest_completed = _latest_clean_progress((*snapshot.activity, *snapshot.results))
        if latest_completed and latest_completed.rstrip(".").lower() not in {"done", "run completed"}:
            lines.append(f"Last completed: {latest_completed.rstrip('.')}.")
        current_step = _clean_progress_line(snapshot.current_step) or _clip(snapshot.current_step, limit=150)
        if current_step and not wants_next_step:
            lines.append(f"Current work: {current_step}.")
        elif str(snapshot.status or "").strip().lower() == "running" and not wants_next_step:
            lines.append("Current work: waiting for the next event from the active run.")
        if snapshot.changed_files and not wants_next_step:
            lines.append(
                "Changed: "
                + ", ".join(
                    _clip(_display_path(path, snapshot.workspace), limit=80)
                    for path in snapshot.changed_files[-4:]
                )
                + "."
            )
        elif snapshot.artifacts and not wants_next_step:
            lines.append(
                "Artifacts: "
                + ", ".join(
                    _clip(_display_path(path, snapshot.workspace), limit=80)
                    for path in snapshot.artifacts[-4:]
                )
                + "."
            )
        if snapshot.validation:
            score = f" ({snapshot.validation_score})" if snapshot.validation_score else ""
            lines.append(f"Validation: {snapshot.validation}{score}.")
        if snapshot.blockers:
            lines.append(
                "Blockers: " + "; ".join(_clip(item, limit=120) for item in snapshot.blockers[-3:])
            )
        elif not wants_next_step:
            lines.append("Blockers: none visible in the current snapshot.")
        if snapshot.queued_work and not wants_next_step:
            lines.append(f"Queued: {_clip(snapshot.queued_work, limit=160)}.")

    return NarratorResponse(
        request_id=request.request_id,
        status="fallback",
        text="\n".join(lines),
        cited_refs=_compact_list(snapshot.trace_refs, limit=4),
        confidence=0.72 if snapshot.has_run_context else 0.55,
        fallback_used=True,
        snapshot_id=snapshot.snapshot_id,
    )


def narrator_system_prompt() -> str:
    return (
        "You are the progress narrator for DAN. Answer only from the provided run snapshot. "
        "Do not claim to inspect files, run commands, mutate state, steer execution, or access hidden context. "
        "If the snapshot is missing information, say what is unknown. Answer the user's exact question first. "
        "For next-step questions, start with a short Next line. For progress/status questions, start with a short Status line. "
        "Use at most 4 short plain lines. Prefer labels like Next, Status, Last completed, Validation, and Blockers. "
        "Do not include trace paths, event-log paths, Markdown headings, bold text, or raw JSON/tool payloads unless the user explicitly asks for logs."
    )


def narrator_messages(request: NarratorRequest) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": narrator_system_prompt()},
        {"role": "user", "content": request.to_prompt_text()},
    ]


async def generate_narrator_response(
    provider: Any,
    request: NarratorRequest,
    *,
    model: str,
) -> NarratorResponse:
    """Ask an LLM to narrate a snapshot, falling back deterministically.

    This function intentionally passes no tools to the provider.
    """

    fallback = deterministic_narrator_response(request)
    if provider is None:
        return fallback
    try:
        result = await provider.complete(
            messages=narrator_messages(request),
            model=model,
            temperature=0.2,
            max_tokens=request.max_tokens,
        )
    except Exception as exc:
        return NarratorResponse(
            request_id=request.request_id,
            status="failed",
            text=fallback.text,
            cited_refs=fallback.cited_refs,
            confidence=fallback.confidence,
            fallback_used=True,
            failure=str(exc),
            snapshot_id=request.snapshot.snapshot_id,
        )
    text = str(getattr(result, "text", "") or "").strip()
    if not text:
        return fallback
    return NarratorResponse(
        request_id=request.request_id,
        status="completed",
        text=text,
        cited_refs=_compact_list(request.snapshot.trace_refs, limit=4),
        confidence=0.86,
        snapshot_id=request.snapshot.snapshot_id,
    )


async def run_narrator_job(
    provider: Any,
    request: NarratorRequest,
    *,
    model: str,
    latest_snapshot_getter: NarratorSnapshotGetter | None = None,
    event_callback: NarratorEventCallback | None = None,
) -> NarratorResponse:
    """Run one narrator request as a cancellable async job."""

    _emit_narrator_event(event_callback, "narrator.requested", request=request)
    _emit_narrator_event(event_callback, "narrator.started", request=request)
    try:
        response = await generate_narrator_response(provider, request, model=model)
    except asyncio.CancelledError:
        _emit_narrator_event(
            event_callback,
            "narrator.failed",
            request=request,
            error="cancelled",
        )
        raise

    latest_snapshot = latest_snapshot_getter() if latest_snapshot_getter is not None else request.snapshot
    if response.is_stale_against(latest_snapshot):
        stale_response = replace(response, status="stale", stale=True)
        _emit_narrator_event(
            event_callback,
            "narrator.stale",
            request=request,
            response=stale_response,
        )
        return stale_response

    if response.status == "failed":
        _emit_narrator_event(
            event_callback,
            "narrator.failed",
            request=request,
            response=response,
            error=response.failure,
        )
    else:
        _emit_narrator_event(
            event_callback,
            "narrator.completed",
            request=request,
            response=response,
        )
    return response


def start_narrator_job(
    provider: Any,
    request: NarratorRequest,
    *,
    model: str,
    latest_snapshot_getter: NarratorSnapshotGetter | None = None,
    event_callback: NarratorEventCallback | None = None,
    previous: NarratorJobHandle | None = None,
    cancel_previous: bool = True,
) -> NarratorJobHandle:
    """Start a narrator job on the current event loop.

    Surfaces can pass the previous handle to debounce repeated progress
    questions; the old task is cancelled before the new one starts.
    """

    if cancel_previous and previous is not None and not previous.done():
        previous.cancel()
    task = asyncio.create_task(
        run_narrator_job(
            provider,
            request,
            model=model,
            latest_snapshot_getter=latest_snapshot_getter,
            event_callback=event_callback,
        )
    )
    return NarratorJobHandle(request=request, task=task)
