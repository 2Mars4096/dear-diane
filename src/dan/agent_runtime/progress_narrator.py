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
from typing import Any, Callable, Mapping, Sequence


NARRATOR_READ_ONLY = "narrator read-only"
EXECUTOR_READ_ONLY = "executor read-only"
EXECUTOR_WRITE = "executor write"
CLARIFICATION = "clarification"

def tokenize_intent_text(text: str) -> tuple[str, ...]:
    """Return lowercase word tokens for non-routing local text utilities."""

    normalized = str(text or "").lower().replace("’", "'")
    normalized = normalized.replace("what's", "what is").replace("whats", "what is")
    tokens: list[str] = []
    current: list[str] = []
    for char in normalized:
        if char.isalnum():
            current.append(char)
            continue
        if current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _intent_command(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return ""
    return stripped.split(maxsplit=1)[0].lower()


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


def _heartbeat_visible_step(snapshot: RunNarratorSnapshot) -> str:
    phase = str(snapshot.phase or "").strip().lower()
    current = _clean_progress_line(snapshot.current_step) or _clip(snapshot.current_step, limit=120)
    if current.lower().startswith(("still running:", "still working", "waiting on ")):
        current = ""
    if current:
        return current
    if "model" in phase:
        return "waiting for the model response"
    if "valid" in phase:
        return "validation is still running"
    if "repair" in phase or "retry" in phase:
        return "the repair pass is still running"
    if "tool" in phase:
        return "waiting for the active tool to finish"
    if phase:
        return f"{phase.replace('_', ' ')} is still running"
    return ""


def _is_next_step_question(text: str) -> bool:
    """Detect next-step phrasing for deterministic narration only, not routing."""

    tokens = tokenize_intent_text(text)
    phrases = (
        ("what", "is", "next"),
        ("what", "now"),
        ("next", "step"),
        ("next", "expected"),
        ("where", "next"),
        ("what", "shall", "we", "do", "next"),
        ("what", "should", "we", "do", "next"),
        ("what", "can", "we", "do", "next"),
    )
    for phrase in phrases:
        width = len(phrase)
        if width > len(tokens):
            continue
        for index in range(len(tokens) - width + 1):
            if tuple(tokens[index : index + width]) == phrase:
                return True
    return False


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
    """Classify only explicit command turns without keyword or regex routing.

    Free-text requests should go through ``route_agent_turn_intent_with_model``.
    This function intentionally refuses to guess natural-language intent when a
    model router is unavailable.
    """

    stripped = str(text or "").strip()
    if not stripped:
        return AgentTurnIntentDecision(
            lane=CLARIFICATION,
            confidence=0.0,
            rationale="empty input",
            clarification="What should Super DAN do?",
        )
    command = _intent_command(stripped)
    if command in {"/progress", "/last", "/narrate"}:
        return AgentTurnIntentDecision(
            lane=NARRATOR_READ_ONLY,
            confidence=1.0,
            rationale="explicit progress command",
        )
    return AgentTurnIntentDecision(
        lane=CLARIFICATION,
        confidence=0.35,
        rationale="free-text routing requires model-assisted intent classification",
        clarification=(
            "I need the model router to decide whether this is progress, read-only inspection, "
            "or workspace-changing work."
        ),
    )


def _first_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from model text without regex."""

    raw = str(text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    start = raw.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(raw)):
            char = raw[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(raw[start : index + 1])
                    break
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _normalize_model_route_lane(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _lane_from_model_route_text(value: Any) -> str:
    lane_text = _normalize_model_route_lane(value)
    if lane_text in {"narrator", "narrator_read_only", "progress", "status"}:
        return NARRATOR_READ_ONLY
    if lane_text in {"executor_read_only", "read_only", "readonly"}:
        return EXECUTOR_READ_ONLY
    if lane_text in {"executor_write", "write", "workspace_write"}:
        return EXECUTOR_WRITE
    if lane_text == CLARIFICATION:
        return CLARIFICATION
    return ""


def _model_route_payload_issue(payload: Mapping[str, Any] | None) -> str:
    if payload is None:
        return "no JSON object was found"
    lane_value = payload.get("lane") or payload.get("route")
    if not str(lane_value or "").strip():
        return "the route object is missing lane"
    if not _lane_from_model_route_text(lane_value):
        return f"unsupported lane: {lane_value}"
    return ""


def _decision_from_model_route_payload(payload: Mapping[str, Any]) -> AgentTurnIntentDecision:
    lane = _lane_from_model_route_text(payload.get("lane") or payload.get("route"))
    effort = str(payload.get("complexity") or payload.get("executor_effort") or "complex").strip().lower()
    if effort not in {"simple", "complex"}:
        effort = "complex"
    rationale = _clip(payload.get("rationale") or payload.get("reason") or "model-assisted route", limit=220)
    confidence = _coerce_confidence(payload.get("confidence"))
    clarification = _clip(payload.get("clarification") or "", limit=1200)
    if lane == NARRATOR_READ_ONLY:
        return AgentTurnIntentDecision(
            lane=NARRATOR_READ_ONLY,
            confidence=confidence or 0.7,
            rationale=rationale,
        )
    if lane == EXECUTOR_READ_ONLY:
        return AgentTurnIntentDecision(
            lane=EXECUTOR_READ_ONLY,
            confidence=confidence or 0.7,
            rationale=rationale,
            executor_effort=effort,
        )
    if lane == EXECUTOR_WRITE:
        return AgentTurnIntentDecision(
            lane=EXECUTOR_WRITE,
            confidence=confidence or 0.7,
            rationale=rationale,
            executor_effort=effort,
        )
    return AgentTurnIntentDecision(
        lane=CLARIFICATION,
        confidence=confidence,
        rationale=rationale or "model route requested clarification",
        clarification=clarification
        or "Should this be progress/status, read-only workspace inspection, or workspace-changing work?",
    )


def _router_transcript_lines(transcript_tail: Sequence[Any]) -> list[str]:
    lines: list[str] = []
    for item in transcript_tail[-6:]:
        if isinstance(item, Mapping):
            role = str(item.get("role") or "").strip()
            text = str(item.get("text") or "").strip()
        else:
            role = str(getattr(item, "role", "") or "").strip()
            text = str(getattr(item, "text", "") or "").strip()
        if not text:
            continue
        lines.append(f"{role or 'message'}: {_clip(text, limit=500)}")
    return lines


def _agent_turn_router_messages(
    *,
    text: str,
    transcript_tail: Sequence[Any] = (),
    selected_skills: Sequence[str] = (),
    surface: str = "super-tui",
) -> list[dict[str, str]]:
    transcript = "\n".join(_router_transcript_lines(transcript_tail)) or "(none)"
    skills = ", ".join(str(skill).strip() for skill in selected_skills if str(skill).strip()) or "(none)"
    return [
        {
            "role": "system",
            "content": (
                "Route the user's Super DAN turn by meaning and recent context, not by keywords. "
                "Return exactly one JSON object with keys lane, complexity, confidence, rationale, clarification. "
                "lane is narrator_read_only for current/recent run status only, executor_read_only for workspace inspection with no file changes, "
                "executor_write for edits/execution/generated artifacts, or clarification when choosing would be unsafe. "
                "For ordinary workspace-changing work, the user's request is authorization to select executor_write; "
                "do not ask for a second confirmation merely because files may change, commands may run, or an analysis will create artifacts. "
                "Use clarification sparingly. Follow-up questions about progress, outcome, what remains, or the next step should usually be narrator_read_only "
                "when the recent transcript gives enough session context. "
                "Use clarification only when the goal, target, or capability boundary is genuinely missing or unsafe to choose. "
                "complexity is simple only for a bounded single-source read or exact single-file operation; otherwise complex. "
                "Leave clarification empty unless lane is clarification."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Surface: {surface}\n"
                f"Selected skills: {skills}\n"
                f"Recent visible transcript:\n{transcript}\n\n"
                f"User turn:\n{text}\n\n"
                "Route this turn now."
            ),
        },
    ]


def _agent_turn_router_repair_messages(
    *,
    text: str,
    bad_response: str,
    issue: str,
    transcript_tail: Sequence[Any] = (),
    selected_skills: Sequence[str] = (),
    surface: str = "super-tui",
) -> list[dict[str, str]]:
    transcript = "\n".join(_router_transcript_lines(transcript_tail)) or "(none)"
    skills = ", ".join(str(skill).strip() for skill in selected_skills if str(skill).strip()) or "(none)"
    return [
        {
            "role": "system",
            "content": (
                "Repair a Super DAN routing response. Decide by meaning and recent context, not by keyword matching. "
                "Return only one JSON object with this exact shape: "
                '{"lane":"narrator_read_only|executor_read_only|executor_write|clarification",'
                '"complexity":"simple|complex","confidence":0.0,"rationale":"short reason","clarification":""}. '
                "Use narrator_read_only only for current/recent run status. Use executor_read_only for inspection or answers that should not change files. "
                "Use executor_write when the request needs edits, generated artifacts, command execution, data processing, or other workspace-changing work. "
                "For ordinary workspace-changing work, the user's request is authorization to select executor_write; "
                "do not ask for a second confirmation merely because files may change, commands may run, or an analysis will create artifacts. "
                "Use clarification only when the goal, target, or capability boundary is genuinely missing or unsafe to choose."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Surface: {surface}\n"
                f"Selected skills: {skills}\n"
                f"Recent visible transcript:\n{transcript}\n\n"
                f"User turn:\n{text}\n\n"
                f"Previous router output was invalid because: {issue}\n"
                f"Previous router output:\n{_clip(bad_response, limit=900)}\n\n"
                "Repair the route now. Return JSON only."
            ),
        },
    ]


async def route_agent_turn_intent_with_model(
    provider: Any,
    text: str,
    *,
    model: str,
    transcript_tail: Sequence[Any] = (),
    selected_skills: Sequence[str] = (),
    surface: str = "super-tui",
) -> AgentTurnIntentDecision:
    """Route a free-text turn through a model-authored structured decision."""

    response = await provider.complete(
        messages=_agent_turn_router_messages(
            text=text,
            transcript_tail=transcript_tail,
            selected_skills=selected_skills,
            surface=surface,
        ),
        model=model,
        temperature=0.0,
        max_tokens=300,
    )
    raw_text = getattr(response, "text", "")
    payload = _first_json_object(raw_text)
    issue = _model_route_payload_issue(payload)
    if issue:
        repair_response = await provider.complete(
            messages=_agent_turn_router_repair_messages(
                text=text,
                bad_response=raw_text,
                issue=issue,
                transcript_tail=transcript_tail,
                selected_skills=selected_skills,
                surface=surface,
            ),
            model=model,
            temperature=0.0,
            max_tokens=300,
        )
        repair_payload = _first_json_object(getattr(repair_response, "text", ""))
        repair_issue = _model_route_payload_issue(repair_payload)
        if not repair_issue and repair_payload is not None:
            payload = repair_payload
        else:
            return AgentTurnIntentDecision(
                lane=CLARIFICATION,
                confidence=0.0,
                rationale=f"model router did not return a usable structured route: {repair_issue or issue}",
                clarification="I could not route that request safely. Please say whether this should read, write, or report progress.",
            )
    if _lane_from_model_route_text(payload.get("lane") or payload.get("route")) == CLARIFICATION:
        review_response = await provider.complete(
            messages=_agent_turn_router_repair_messages(
                text=text,
                bad_response=json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                issue=(
                    "the route selected clarification; verify that clarification is truly necessary "
                    "and not only asking for extra permission to perform requested workspace-changing work"
                ),
                transcript_tail=transcript_tail,
                selected_skills=selected_skills,
                surface=surface,
            ),
            model=model,
            temperature=0.0,
            max_tokens=300,
        )
        review_payload = _first_json_object(getattr(review_response, "text", ""))
        review_issue = _model_route_payload_issue(review_payload)
        if not review_issue and review_payload is not None:
            payload = review_payload
    return _decision_from_model_route_payload(payload)


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


@dataclass(frozen=True)
class NarratorReport:
    """Human report emitted by the passive narrator sidecar.

    Reports are generated from sanitized run snapshots/events. They are not
    tool calls and they do not grant the narrator read, write, shell, approval,
    or steering authority.
    """

    kind: str
    text: str
    relation_to_request: str = ""
    done: tuple[str, ...] = ()
    remaining: str = ""
    next: str = ""
    confidence: float = 0.8
    stale: bool = False
    verbosity: str = "compact"
    source_snapshot_id: str = ""
    created_at: str = field(default_factory=_now_iso)

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "relation_to_request": self.relation_to_request,
            "done": list(self.done),
            "remaining": self.remaining,
            "next": self.next,
            "confidence": self.confidence,
            "stale": self.stale,
            "verbosity": self.verbosity,
            "source_snapshot_id": self.source_snapshot_id,
            "created_at": self.created_at,
        }


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


def build_narrator_report_event(report: NarratorReport) -> dict[str, Any]:
    """Return a normalized event row for a passive narrator report."""

    payload = report.to_payload()
    return {
        "event": f"narrator.{report.kind}",
        "report": payload,
        **payload,
    }


def _request_subject(snapshot: RunNarratorSnapshot) -> str:
    objective = _clip(snapshot.objective, limit=120)
    return objective or "the request"


def _changed_summary(snapshot: RunNarratorSnapshot, *, limit: int = 2) -> tuple[str, ...]:
    return tuple(
        _display_path(path, snapshot.workspace)
        for path in snapshot.changed_files[-limit:]
        if str(path or "").strip()
    )


def _done_items(snapshot: RunNarratorSnapshot) -> tuple[str, ...]:
    items: list[str] = []
    changed = _changed_summary(snapshot, limit=3)
    if changed:
        items.append("changed " + ", ".join(changed))
    if snapshot.validation and snapshot.validation != "running":
        score = f" ({snapshot.validation_score})" if snapshot.validation_score else ""
        items.append(f"validation {snapshot.validation}{score}")
    latest = _latest_clean_progress((*snapshot.activity, *snapshot.results))
    if latest and latest.rstrip(".").lower() not in {"done", "run completed"}:
        items.append(latest.rstrip("."))
    return _compact_list(items, limit=4)


def deterministic_narrator_report(
    snapshot: RunNarratorSnapshot,
    *,
    trigger: str,
    event_name: str = "",
    event_payload: Mapping[str, Any] | None = None,
) -> NarratorReport | None:
    """Build a passive narrator report from a sanitized snapshot/event.

    This function is deliberately deterministic and side-effect free. It never
    reads files, calls tools, or mutates state.
    """

    trigger = str(trigger or "").strip().lower()
    event_name = str(event_name or "").strip()
    event_payload = event_payload or {}
    subject = _request_subject(snapshot)
    done = _done_items(snapshot)
    next_step = _next_step_line(snapshot)
    relation = f"Report is grounded in the current run state for: {subject}"
    kind = trigger if trigger in {
        "opening",
        "progress",
        "checkpoint",
        "uncertainty",
        "drift",
        "blocker",
        "final",
        "heartbeat",
    } else "progress"
    text = ""
    remaining = ""

    if kind == "opening":
        return None
    elif kind == "heartbeat":
        elapsed = event_payload.get("elapsed_seconds") or snapshot.elapsed_seconds
        elapsed_text = _format_elapsed(float(elapsed or 0.0))
        current = _heartbeat_visible_step(snapshot)
        if current:
            text = f"Still working after {elapsed_text}. Current visible step: {current}."
        else:
            text = f"Still working after {elapsed_text}. No newer executor event is visible yet."
        remaining = next_step
    elif kind == "final":
        status = str(snapshot.status or "finished").strip().lower()
        if status in {"completed", "done"}:
            summary = "; ".join(done)
            if summary:
                text = f"Run finished successfully: {summary}."
            else:
                text = "Run finished successfully."
            remaining = "No blocker is visible in the final snapshot."
        elif status in {"failed", "blocked", "stopped"}:
            blocker = "; ".join(_clip(item, limit=120) for item in snapshot.blockers[-3:]) or "the run did not complete cleanly"
            text = f"The run did not complete cleanly. Status: {status}; blocker: {blocker}."
            remaining = "A follow-up or repair pass is needed."
        else:
            text = f"The run finished with status {status or 'unknown'}."
            remaining = next_step
    elif kind == "blocker":
        blocker = "; ".join(_clip(item, limit=120) for item in snapshot.blockers[-3:]) or _clip(snapshot.current_step, limit=120)
        text = f"A blocker is visible for {subject}: {blocker}."
        remaining = "The executor needs a repair, retry, or user decision before this can finish cleanly."
    elif kind == "checkpoint":
        if event_name == "live.validation.started":
            text = f"Validation is checking whether the current work satisfies {subject}."
            remaining = "The next visible result should be validation passed, validation failed, or queued repair work."
        elif event_name in {"live.validation.completed", "live.validation.model_completed"}:
            score = f" ({snapshot.validation_score})" if snapshot.validation_score else ""
            if snapshot.validation == "passed":
                text = f"Validation passed{score}; the run is close to a clean finish for {subject}."
                remaining = "The executor should finish or hand off any queued follow-up."
            else:
                text = f"Validation failed{score}; the executor needs to repair the visible gaps for {subject}."
                remaining = "A repair or follow-up validation step remains."
        elif event_name == "super.hook.packet_enqueued" or snapshot.queued_work:
            text = f"Follow-up work was queued for {subject}: {_clip(snapshot.queued_work, limit=120)}."
            remaining = next_step
        elif _changed_summary(snapshot):
            text = f"Changed {', '.join(_changed_summary(snapshot))} for {subject}."
            remaining = "The next step is to validate or continue from that change."
        else:
            summary = "; ".join(done) if done else _clip(snapshot.current_step, limit=120)
            text = f"Checkpoint for {subject}: {summary or 'the run advanced'}."
            remaining = next_step
    else:
        if event_name == "model.requested":
            text = f"Thinking through the next step for {subject}."
            remaining = "It should either call a tool, validate, or prepare a response."
        elif event_name == "tool.started":
            tool_id = str(event_payload.get("tool_id") or "").strip()
            if tool_id in {"file_read", "list_directory", "workspace_check"}:
                text = f"Checking the relevant workspace context for {subject}."
            elif tool_id in {"file_write", "file_edit"}:
                text = f"Preparing a workspace change for {subject}."
            elif tool_id == "shell_command":
                text = f"Using a terminal command because it is the direct way to advance {subject}."
            else:
                text = f"Running a project tool for {subject}."
            remaining = next_step
        elif event_name == "tool.completed":
            tool_id = str(event_payload.get("tool_id") or "").strip()
            if tool_id in {"file_read", "list_directory", "workspace_check"}:
                text = f"Relevant context is available for {subject}; moving toward an answer."
            elif tool_id in {"file_write", "file_edit"}:
                changed = ", ".join(_changed_summary(snapshot)) or "workspace files"
                text = f"A workspace change landed for {subject}: {changed}."
            elif tool_id == "shell_command":
                text = f"The terminal command finished; using that result for {subject}."
            else:
                text = f"A project tool finished for {subject}."
            remaining = next_step
        elif event_name == "super.heartbeat":
            return deterministic_narrator_report(
                snapshot,
                trigger="heartbeat",
                event_name=event_name,
                event_payload=event_payload,
            )
        else:
            current = _clean_progress_line(snapshot.current_step) or _clip(snapshot.current_step, limit=120)
            text = f"The run is progressing on {subject}: {current or snapshot.phase}."
            remaining = next_step

    if not text:
        return None
    return NarratorReport(
        kind=kind,
        text=_clip(text, limit=240),
        relation_to_request=relation,
        done=done,
        remaining=_clip(remaining, limit=180),
        next=_clip(next_step, limit=180),
        confidence=0.78 if snapshot.has_run_context else 0.58,
        source_snapshot_id=snapshot.snapshot_id,
    )


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
