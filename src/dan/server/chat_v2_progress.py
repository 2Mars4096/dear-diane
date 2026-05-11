"""Surface progress rendering for Chat/Agent V2 events."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from dan.server.chat_v2 import (
    AgentRunEvent,
    SurfaceUpdateHandle,
    TaskSnapshot,
    format_token_usage,
    merge_token_usage,
    normalize_token_usage,
)


_TERMINAL_EVENT_TYPES = {"completed", "failed", "blocked", "stopped"}
_WEB_TOOL_HINTS = ("web", "search", "fetch", "http")
_READ_TOOL_IDS = {
    "file_read",
    "list_directory",
    "workspace_check",
    "git_status",
    "git_diff",
    "git_log",
    "pdf_read",
    "csv_read",
    "spreadsheet_read",
}
_WRITE_TOOL_IDS = {
    "file_write",
    "file_edit",
    "file_copy",
    "file_move",
    "file_delete",
}
_COMMAND_TOOL_IDS = {
    "shell_command",
    "run_python",
    "python_eval",
    "screenshot",
}
_MEDIA_TOOL_IDS = {
    "image_describe",
    "audio_transcribe",
}


class SurfaceDeliveryRecord(BaseModel):
    surface_type: str
    native_message_id: str | None = None
    text: str
    delivered: bool
    used_edit: bool = False
    error: str = ""


class AgentProgressSnapshot(BaseModel):
    """Current surface-facing projection of a normalized Agent event stream."""

    run_id: str = ""
    phase: str = "Processing"
    detail: str = "waiting for backend events"
    latest_summary: str = ""
    latest_event_type: str = ""
    latest_source_event_type: str = ""
    latest_tool_id: str = ""
    event_count: int = 0
    elapsed_seconds: float = 0.0
    quiet_seconds: float = 0.0
    terminal: bool = False
    terminal_event_type: str = ""
    token_usage: dict[str, int] = Field(default_factory=dict)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)


class AgentProgressStateMachine:
    """Translate real Agent/Super DAN events into one live progress state.

    This is intentionally a surface layer: it never invents backend steps or
    prompt text.  It only remembers the latest normalized event, its raw source
    event type, and small bits of payload metadata such as tool id and token
    usage so a Telegram/frontend sink can render quiet-period heartbeats.
    """

    def __init__(
        self,
        *,
        run_id: str = "",
        clock: Any = time.monotonic,
    ) -> None:
        self.run_id = str(run_id or "").strip()
        self._clock = clock
        now = float(self._clock())
        self._started_at = now
        self._last_event_at = now
        self._phase = "Processing"
        self._detail = "waiting for backend events"
        self._latest_summary = ""
        self._latest_event_type = ""
        self._latest_source_event_type = ""
        self._latest_tool_id = ""
        self._event_count = 0
        self._terminal_event_type = ""
        self._token_usage: dict[str, int] = {}
        self._artifact_refs: list[dict[str, Any]] = []
        self._saw_tool = False
        self._saw_artifact = False
        self._saw_validation = False

    @property
    def terminal(self) -> bool:
        return bool(self._terminal_event_type)

    def observe(
        self,
        event: AgentRunEvent | Mapping[str, Any],
    ) -> AgentProgressSnapshot:
        agent_event = _coerce_agent_event(event)
        now = float(self._clock())
        self._last_event_at = now
        self._event_count += 1
        if agent_event.run_id and not self.run_id:
            self.run_id = str(agent_event.run_id)
        self._latest_event_type = str(agent_event.type)
        self._latest_source_event_type = str(agent_event.source_event_type or "")
        summary = _progress_summary(
            str(agent_event.summary or "").strip(),
            self._latest_source_event_type.lower(),
        )
        if summary:
            self._latest_summary = summary
        if agent_event.artifact_refs:
            self._artifact_refs = [
                dict(item)
                for item in agent_event.artifact_refs
                if isinstance(item, Mapping)
            ]
            self._saw_artifact = True

        usage_total = normalize_token_usage(agent_event.token_usage_total)
        usage_delta = normalize_token_usage(agent_event.token_usage_delta)
        if usage_total:
            self._token_usage = usage_total
        elif usage_delta:
            self._token_usage = merge_token_usage(self._token_usage, usage_delta)

        phase, detail, tool_id = self._phase_for_event(agent_event)
        self._phase = phase
        self._detail = detail
        if tool_id:
            self._latest_tool_id = tool_id
        if agent_event.type == "tool_used":
            self._saw_tool = True
        if agent_event.type == "artifact_changed":
            self._saw_artifact = True
        if phase == "Validating":
            self._saw_validation = True
        if agent_event.type in _TERMINAL_EVENT_TYPES:
            self._terminal_event_type = agent_event.type
        return self.snapshot(now=now)

    def snapshot(self, *, now: float | None = None) -> AgentProgressSnapshot:
        current = float(self._clock()) if now is None else float(now)
        return AgentProgressSnapshot(
            run_id=self.run_id,
            phase=self._phase,
            detail=self._detail,
            latest_summary=self._latest_summary,
            latest_event_type=self._latest_event_type,
            latest_source_event_type=self._latest_source_event_type,
            latest_tool_id=self._latest_tool_id,
            event_count=self._event_count,
            elapsed_seconds=max(0.0, current - self._started_at),
            quiet_seconds=max(0.0, current - self._last_event_at),
            terminal=self.terminal,
            terminal_event_type=self._terminal_event_type,
            token_usage=dict(self._token_usage),
            artifact_refs=[dict(item) for item in self._artifact_refs],
        )

    def render_status(
        self,
        *,
        heartbeat: bool = False,
        include_run_id: bool = False,
        now: float | None = None,
    ) -> str:
        return render_agent_progress_snapshot(
            self.snapshot(now=now),
            heartbeat=heartbeat,
            include_run_id=include_run_id,
        )

    def _phase_for_event(self, event: AgentRunEvent) -> tuple[str, str, str]:
        event_type = str(event.type)
        source = str(event.source_event_type or "").strip()
        source_lower = source.lower()
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        summary = _progress_summary(str(event.summary or "").strip(), source_lower)

        if event_type == "accepted":
            return "Got it", summary or "accepted Agent task", ""
        if event_type == "queued":
            return "Queued", summary or "waiting for the Agent lane", ""
        if event_type in {"queue_item_added", "queue_item_injected"}:
            return "Queued follow-up", summary or "stored for the active Agent run", ""
        if event_type == "planned":
            detail = summary or _stage_detail(source_lower) or "planning backend work"
            return "Processing", detail, ""
        if event_type == "worker_started":
            if "validation" in source_lower:
                return "Validating", summary or "checking the result", ""
            if "repair" in source_lower:
                return "Repairing", summary or "addressing validation feedback", ""
            return "Processing", summary or "backend worker is running", ""
        if event_type == "model_text_delta":
            return self._model_phase(payload, summary=summary, source=source_lower)
        if event_type == "tool_used":
            tool_id = _extract_tool_id(payload)
            phase, detail = _tool_progress(tool_id, source_lower, payload, summary=summary)
            return phase, detail, tool_id
        if event_type == "artifact_changed":
            if source_lower.startswith("super.hook."):
                return "Updating queues", summary or "projecting backend hook state", ""
            return "Updating artifacts", summary or _artifact_detail(event.artifact_refs), ""
        if event_type == "validation_started":
            return "Validating", summary or "checking the result", ""
        if event_type == "repair_started":
            return "Repairing", summary or "addressing validation feedback", ""
        if event_type == "branch_created":
            return "Branching", summary or "created a sibling task branch", ""
        if event_type == "needs_input":
            return "Needs input", summary or "waiting for user input", ""
        if event_type == "token_usage_recorded":
            usage_text = format_token_usage(event.token_usage_delta)
            detail = summary or f"model round usage: {usage_text}"
            return "Model round complete", detail, ""
        if event_type == "completed":
            return "Done", summary or "Agent run completed", ""
        if event_type == "failed":
            return "Failed", summary or "Agent run failed", ""
        if event_type == "blocked":
            return "Blocked", summary or "Agent run is blocked", ""
        if event_type == "stopped":
            return "Stopped", summary or "Agent run stopped", ""
        return "Processing", summary or event_type.replace("_", " "), ""

    def _model_phase(
        self,
        payload: Mapping[str, Any],
        *,
        summary: str,
        source: str,
    ) -> tuple[str, str, str]:
        tool_count = _coerce_int(payload.get("tool_count"))
        round_id = str(payload.get("round") or "").strip()
        suffix = f" round {round_id}" if round_id else ""
        if source.startswith("model.context_length_retry"):
            return "Repairing context", summary or "retrying with a smaller prompt", ""
        if tool_count == 0 and (self._saw_tool or self._saw_artifact or self._saw_validation):
            return "Summarizing", summary or f"asking the model to synthesize{suffix}", ""
        return "Thinking", summary or f"asking the model for the next step{suffix}", ""


def render_agent_progress_snapshot(
    snapshot: AgentProgressSnapshot,
    *,
    heartbeat: bool = False,
    include_run_id: bool = False,
) -> str:
    """Render a compact live status from an Agent progress snapshot."""

    detail = snapshot.detail or snapshot.latest_summary or snapshot.run_id or "working"
    lines = [f"{snapshot.phase}: {detail}"]
    if heartbeat and not snapshot.terminal:
        lines.append(f"Elapsed: {_format_elapsed(snapshot.elapsed_seconds)}")
        if snapshot.event_count:
            lines.append(f"Last backend event: {_format_elapsed(snapshot.quiet_seconds)} ago")
        else:
            lines.append(f"Waiting for first backend event: {_format_elapsed(snapshot.elapsed_seconds)}")
    if include_run_id and snapshot.run_id:
        lines.append(f"Run: {snapshot.run_id}")
    usage_text = format_token_usage(snapshot.token_usage)
    if snapshot.terminal and usage_text != "unavailable":
        lines.append("")
        lines.append(f"Tokens: {usage_text}")
    if snapshot.terminal and snapshot.terminal_event_type == "completed" and snapshot.artifact_refs:
        paths = [
            str(item.get("path") or "").strip()
            for item in snapshot.artifact_refs
            if isinstance(item, Mapping) and str(item.get("path") or "").strip()
        ]
        if paths:
            lines.append("")
            lines.append("Artifacts:")
            lines.extend(f"- {path}" for path in paths[:8])
    return "\n".join(line for line in lines if line).strip()


def _coerce_agent_event(event: AgentRunEvent | Mapping[str, Any]) -> AgentRunEvent:
    if isinstance(event, AgentRunEvent):
        return event
    return AgentRunEvent.model_validate(dict(event))


def _stage_detail(source_lower: str) -> str:
    if not source_lower.startswith("stage."):
        return ""
    stage = source_lower.split(".", 1)[1].replace("_", " ").replace("-", " ")
    return stage.strip() or ""


def _progress_summary(summary: str, source_lower: str) -> str:
    clean = str(summary or "").strip()
    lower = clean.lower()
    if not clean:
        return ""
    if source_lower and lower == source_lower:
        return ""
    if lower.startswith(
        (
            "tool.",
            "model.",
            "live.",
            "stage.",
            "worker.",
            "run.log.",
            "contract.",
            "super.",
            "organism.",
        )
    ):
        return ""
    return clean


def _extract_tool_id(payload: Mapping[str, Any]) -> str:
    for key in ("tool_id", "tool", "tool_name", "name"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        value = str(metadata.get("id") or metadata.get("name") or "").strip()
        if value:
            return value
    return "tool"


def _tool_progress(
    tool_id: str,
    source_lower: str,
    payload: Mapping[str, Any],
    *,
    summary: str,
) -> tuple[str, str]:
    normalized = tool_id.lower().replace("-", "_")
    completed = source_lower.endswith(".completed")
    failed = source_lower.endswith(".failed") or source_lower.endswith(".denied")
    if failed:
        phase = "Recovering"
    elif _is_web_tool(normalized):
        phase = "Fetched web evidence" if completed else "Fetching web evidence"
    elif normalized in _WRITE_TOOL_IDS or "write" in normalized or "edit" in normalized:
        phase = "Edited files" if completed else "Editing files"
    elif normalized in _READ_TOOL_IDS or "read" in normalized or "git_" in normalized:
        phase = "Read workspace" if completed else "Reading workspace"
    elif normalized in _COMMAND_TOOL_IDS or "shell" in normalized or "command" in normalized:
        phase = "Ran command" if completed else "Running command"
    elif normalized in _MEDIA_TOOL_IDS or "image" in normalized or "audio" in normalized:
        phase = "Processed media" if completed else "Processing media"
    else:
        phase = "Used tool" if completed else "Using tool"

    if summary and not summary.lower().startswith(("tool.", "live.", "model.")):
        return phase, summary
    target = _tool_target(payload)
    detail = tool_id if not target else f"{tool_id}: {target}"
    return phase, detail


def _is_web_tool(normalized_tool_id: str) -> bool:
    return any(hint in normalized_tool_id for hint in _WEB_TOOL_HINTS)


def _tool_target(payload: Mapping[str, Any]) -> str:
    args = payload.get("arguments")
    if not isinstance(args, Mapping):
        args = payload.get("args")
    if not isinstance(args, Mapping):
        args = payload.get("input")
    if not isinstance(args, Mapping):
        args = {}
    for key in (
        "query",
        "url",
        "path",
        "file_path",
        "directory",
        "working_directory",
        "command",
        "check",
    ):
        value = str(args.get(key) or "").strip()
        if value:
            return _clip_progress_text(value, limit=96)
    return ""


def _artifact_detail(artifact_refs: list[dict[str, Any]]) -> str:
    paths = [
        str(item.get("path") or "").strip()
        for item in artifact_refs
        if isinstance(item, Mapping) and str(item.get("path") or "").strip()
    ]
    if not paths:
        return "updating output artifacts"
    if len(paths) == 1:
        return f"updated {paths[0]}"
    return f"updated {len(paths)} artifacts"


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(round(float(seconds))))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _clip_progress_text(text: str, *, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)].rstrip() + "..."


def render_agent_event_for_surface(
    event: AgentRunEvent,
    *,
    snapshot: TaskSnapshot | None = None,
) -> str:
    """Render a compact, surface-neutral progress line."""

    status = (snapshot.status if snapshot is not None else "") or ""
    queue_position = snapshot.queue_position if snapshot is not None else None
    if event.type == "accepted":
        return event.summary or "Accepted. I am starting the Agent task."
    if event.type == "queued":
        if queue_position is not None:
            return f"Queued at position {queue_position}."
        return event.summary or "Queued."
    if event.type == "queue_item_added":
        lane = str(event.payload.get("lane") or "").replace("_", "-")
        position = event.payload.get("queue_position") or queue_position
        if lane and position:
            return f"Queued in {lane} lane at position {position}."
        return event.summary or "Queued follow-up."
    if event.type == "planned":
        return event.summary or "Plan ready."
    if event.type == "worker_started":
        return event.summary or "Worker started."
    if event.type == "model_text_delta":
        return event.summary or "Working."
    if event.type == "tool_used":
        return event.summary or "Used a tool."
    if event.type == "artifact_changed":
        return event.summary or "Updated an artifact."
    if event.type == "validation_started":
        return event.summary or "Validating the result."
    if event.type == "repair_started":
        return event.summary or "Repair pass started."
    if event.type == "branch_created":
        return event.summary or "Created a branch."
    if event.type == "needs_input":
        return event.summary or "I need input before continuing."
    if event.type == "token_usage_recorded":
        delta = format_token_usage(event.token_usage_delta)
        total = format_token_usage(event.token_usage_total)
        if total != "unavailable":
            return f"Tokens: {delta} (run total: {total})."
        return f"Tokens: {delta}."
    if event.type == "completed":
        base = event.summary or "Done."
        usage = (
            snapshot.token_usage
            if snapshot is not None and snapshot.token_usage
            else event.token_usage_total
        )
        usage_text = format_token_usage(usage)
        if usage_text != "unavailable":
            return f"{base}\nTokens: {usage_text}"
        return base
    if event.type == "failed":
        return event.summary or "Failed."
    if event.type == "blocked":
        return event.summary or "Blocked."
    if event.type == "stopped":
        return event.summary or "Stopped."
    return event.summary or status or event.type


class TelegramProgressSink:
    """Deliver normalized Agent events through a Telegram adapter.

    The adapter only needs a `send_or_edit(chat_id, text, message_id=None,
    reply_to=None, thread_id=None)` coroutine, matching the existing Telegram
    adapter surface.
    """

    def __init__(
        self,
        adapter: Any,
        *,
        min_edit_interval_seconds: float = 0.0,
        clock: Any = time.monotonic,
    ) -> None:
        self.adapter = adapter
        self.min_edit_interval_seconds = max(0.0, float(min_edit_interval_seconds))
        self._clock = clock
        self._last_edit_at: dict[str, float] = {}

    async def deliver(
        self,
        event: AgentRunEvent,
        handle: SurfaceUpdateHandle,
        *,
        snapshot: TaskSnapshot | None = None,
        progress_message_id: str | int | None = None,
    ) -> SurfaceDeliveryRecord:
        text = render_agent_event_for_surface(event, snapshot=snapshot)
        chat_id = _int_or_none(handle.native_chat_id)
        thread_id = _int_or_none(handle.native_thread_id)
        reply_to = _int_or_none(
            handle.reply_to_message_id or handle.native_message_id
        )
        if chat_id is None:
            return SurfaceDeliveryRecord(
                surface_type="telegram",
                text=text,
                delivered=False,
                error="missing native_chat_id",
            )

        edit_target = _int_or_none(progress_message_id)
        should_edit = bool(handle.supports_edit and edit_target is not None)
        if should_edit and self._edit_is_throttled(handle, edit_target):
            return SurfaceDeliveryRecord(
                surface_type="telegram",
                native_message_id=str(edit_target),
                text=text,
                delivered=False,
                used_edit=True,
                error="throttled",
            )

        try:
            message_id = await self.adapter.send_or_edit(
                chat_id,
                text,
                edit_target if should_edit else None,
                reply_to=None if should_edit else reply_to,
                thread_id=thread_id,
            )
        except Exception as exc:
            if not should_edit:
                return SurfaceDeliveryRecord(
                    surface_type="telegram",
                    text=text,
                    delivered=False,
                    error=str(exc),
                )
            try:
                message_id = await self.adapter.send_or_edit(
                    chat_id,
                    text,
                    None,
                    reply_to=reply_to,
                    thread_id=thread_id,
                )
                should_edit = False
            except Exception as fallback_exc:
                return SurfaceDeliveryRecord(
                    surface_type="telegram",
                    native_message_id=str(edit_target),
                    text=text,
                    delivered=False,
                    used_edit=True,
                    error=str(fallback_exc),
                )

        if message_id is None and should_edit:
            message_id = await self.adapter.send_or_edit(
                chat_id,
                text,
                None,
                reply_to=reply_to,
                thread_id=thread_id,
            )
            should_edit = False

        if should_edit and edit_target is not None:
            self._last_edit_at[self._edit_key(handle, edit_target)] = self._clock()
        return SurfaceDeliveryRecord(
            surface_type="telegram",
            native_message_id=str(message_id) if message_id is not None else None,
            text=text,
            delivered=message_id is not None,
            used_edit=should_edit,
        )

    def _edit_is_throttled(
        self,
        handle: SurfaceUpdateHandle,
        message_id: int,
    ) -> bool:
        if self.min_edit_interval_seconds <= 0:
            return False
        key = self._edit_key(handle, message_id)
        last = self._last_edit_at.get(key)
        return last is not None and (
            self._clock() - last < self.min_edit_interval_seconds
        )

    @staticmethod
    def _edit_key(handle: SurfaceUpdateHandle, message_id: int) -> str:
        return f"{handle.native_chat_id or ''}:{message_id}"


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None
