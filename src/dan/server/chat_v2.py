"""V2 chat/agent control-plane ingress contracts.

This module is intentionally transport-neutral. Legacy chat requests, Telegram
updates, frontend turns, and future messaging surfaces should normalize into
these contracts before the turn is routed to ChatManagerV2 or Agent runs.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.notes import enrich_notes_surface_context
from dan.workspace_roots import normalize_workspace_root as normalize_workspace_root_path


AttachmentKind = Literal[
    "image",
    "figure",
    "pdf",
    "document",
    "audio",
    "video",
    "data",
    "unknown",
]

TriageAction = Literal[
    "chat_response",
    "agent_suggested",
    "agent_requested",
    "append_to_active_run",
    "continue_after_current",
    "control_command",
    "ignored",
]

AgentRunCommandType = Literal[
    "start",
    "append_followup",
    "continue_after_current",
    "approve",
    "deny",
    "status",
    "pause",
    "stop",
    "cancel",
    "retry",
    "branch_from",
    "reprioritize",
    "resume",
]

AgentRunEventType = Literal[
    "accepted",
    "queued",
    "waiting_dependency",
    "background_run_started",
    "queue_item_added",
    "queue_item_injected",
    "queue_item_completed",
    "queue_item_requeued",
    "status_reported",
    "pause_requested",
    "stop_requested",
    "planned",
    "worker_started",
    "model_text_delta",
    "tool_used",
    "artifact_changed",
    "validation_started",
    "repair_started",
    "branch_created",
    "needs_input",
    "token_usage_recorded",
    "completed",
    "failed",
    "blocked",
    "paused",
    "stopped",
]


class AttachmentRef(BaseModel):
    """A structured file/media reference attached to a surface turn."""

    id: str
    kind: AttachmentKind = "unknown"
    source_surface: str = ""
    native_file_id: str | None = None
    native_message_id: str | None = None
    mime_type: str | None = None
    local_path: str | None = None
    display_name: str | None = None
    caption: str = ""
    size_bytes: int | None = None
    checksum: str | None = None
    created_at: str | None = None
    retention_policy: str = "default"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SurfaceUpdateHandle(BaseModel):
    """Opaque surface delivery handle used by progress sinks."""

    surface_type: str = ""
    surface_id: str = ""
    native_chat_id: str | None = None
    native_thread_id: str | None = None
    native_message_id: str | None = None
    reply_to_message_id: str | None = None
    supports_edit: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class SurfaceTurn(BaseModel):
    """Canonical V2 intake object for any user turn."""

    id: str
    text: str
    workspace_root: str = ""
    workspace_id: str = ""
    surface_type: str = ""
    surface_id: str = ""
    surface: str = ""
    session_id: str = ""
    thread_id: str = ""
    user_id: str | None = None
    username: str | None = None
    native_chat_id: str | None = None
    native_thread_id: str | None = None
    native_message_id: str | None = None
    reply_to_message_id: str | None = None
    reply_to_text: str | None = None
    privacy_scope: Literal["private", "shared", "unknown"] = "unknown"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    attachments: list[AttachmentRef] = Field(default_factory=list)
    update_handle: SurfaceUpdateHandle | None = None
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunCommand(BaseModel):
    """Command from triage/control plane into an Agent run."""

    command: AgentRunCommandType
    task_id: str | None = None
    run_id: str | None = None
    surface_turn_id: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentRunEvent(BaseModel):
    """Normalized Agent event emitted from organism/backend execution."""

    type: AgentRunEventType
    run_id: str | None = None
    task_id: str | None = None
    summary: str = ""
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    source_event_id: str | None = None
    source_event_type: str | None = None
    source_event_path: str | None = None
    token_usage_delta: dict[str, int] = Field(default_factory=dict)
    token_usage_total: dict[str, int] = Field(default_factory=dict)
    token_usage_round: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskSnapshot(BaseModel):
    """Stable task projection for chat, Telegram, and frontend status views."""

    task_id: str
    thread_id: str = ""
    status: str = "unknown"
    phase: str = ""
    queue_position: int | None = None
    latest_progress: str = ""
    latest_artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    blocker: str = ""
    trace_refs: list[str] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    latest_token_usage_round: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TriageDecisionRecord(BaseModel):
    """Deterministic first-pass decision from a normalized surface turn."""

    action: TriageAction
    reason: str = ""
    task_binding: Literal[
        "none",
        "new_task",
        "existing_task",
        "append",
        "continue_after_current",
        "branch",
        "bypass",
    ] = "none"
    topic_key: str = ""
    queue_key: str = ""
    control_command: str | None = None
    task_family_hints: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class V2BridgeContext(BaseModel):
    """Compatibility payload injected while legacy surfaces point at V2."""

    surface_turn: SurfaceTurn
    triage_decision: TriageDecisionRecord
    selected_control_plane: Literal["v2"] = "v2"
    legacy_bridge: bool = True
    delegated_to: str = "/api/chat/message"


def build_v2_bridge_context(
    req: Any,
    *,
    selected_control_plane: Literal["v2"] = "v2",
) -> dict[str, Any]:
    """Build the V2 ingress/triage compatibility payload for a legacy request."""

    surface_turn = build_surface_turn_from_chat_request(req)
    decision = triage_surface_turn(surface_turn, requested_mode=getattr(req, "mode", "auto"))
    return V2BridgeContext(
        surface_turn=surface_turn,
        triage_decision=decision,
        selected_control_plane=selected_control_plane,
    ).model_dump(mode="json")


def summarize_v2_bridge_context(context: dict[str, Any] | None) -> dict[str, Any]:
    """Return a compact public summary safe to include in chat responses."""

    if not isinstance(context, dict):
        return {}
    surface_turn = context.get("surface_turn")
    decision = context.get("triage_decision")
    surface_turn = surface_turn if isinstance(surface_turn, dict) else {}
    decision = decision if isinstance(decision, dict) else {}
    attachments = surface_turn.get("attachments") if isinstance(surface_turn, dict) else []
    return {
        "surface_turn_id": str(surface_turn.get("id") or ""),
        "triage_action": str(decision.get("action") or ""),
        "task_binding": str(decision.get("task_binding") or ""),
        "topic_key": str(decision.get("topic_key") or ""),
        "queue_key": str(decision.get("queue_key") or ""),
        "workspace_root": str(surface_turn.get("workspace_root") or ""),
        "workspace_id": str(surface_turn.get("workspace_id") or ""),
        "attachment_count": len(attachments) if isinstance(attachments, list) else 0,
        "legacy_bridge": bool(context.get("legacy_bridge", True)),
        "delegated_to": str(context.get("delegated_to") or "/api/chat/message"),
    }


def legacy_request_with_v2_context(req: Any) -> Any:
    """Return a copy of a legacy chat request forced through the V2 bridge."""

    bridge_context = build_v2_bridge_context(req)
    surface_context = dict(getattr(req, "surface_context", None) or {})
    surface_context["v2_control_plane"] = bridge_context
    if hasattr(req, "model_copy"):
        return req.model_copy(
            update={
                "control_plane_mode": "v2",
                "surface_context": surface_context,
            }
        )
    req.control_plane_mode = "v2"
    req.surface_context = surface_context
    return req


def build_surface_turn_from_chat_request(req: Any) -> SurfaceTurn:
    """Normalize the legacy `ChatMessageRequest` shape into `SurfaceTurn`."""

    raw_surface_context = dict(getattr(req, "surface_context", None) or {})
    surface_context = enrich_notes_surface_context(
        raw_surface_context,
        workspace_root=(
            getattr(req, "workspace_root", None)
            or raw_surface_context.get("workspace_root")
            or raw_surface_context.get("workspace_path")
            or raw_surface_context.get("cwd")
        ),
    )
    native = _native_surface_metadata(surface_context)
    surface_type = _clean(getattr(req, "surface_type", "") or native.get("surface_type"))
    surface_id = _clean(getattr(req, "surface_id", "") or native.get("surface_id"))
    surface = _clean(getattr(req, "surface", ""))
    if not surface and surface_type and surface_id:
        surface = f"{surface_type}:{surface_id}"
    session_id = _clean(getattr(req, "session_id", "") or getattr(req, "thread_id", ""))
    thread_id = _clean(getattr(req, "thread_id", "") or session_id)
    text = str(getattr(req, "message", "") or "")

    native_chat_id = _string_or_none(
        _first_present(native, surface_context, "chat_id", "native_chat_id", "external_id")
    )
    native_thread_id = _string_or_none(
        _first_present(native, surface_context, "thread_id", "message_thread_id", "native_thread_id")
    )
    native_message_id = _string_or_none(
        _first_present(native, surface_context, "message_id", "native_message_id")
    )
    reply_to_message_id = _string_or_none(
        _first_present(native, surface_context, "reply_to_message_id")
    )
    reply_to_text = _string_or_none(_first_present(native, surface_context, "reply_to_text"))
    user_id = _string_or_none(_first_present(native, surface_context, "from_user_id", "user_id"))
    username = _string_or_none(
        _first_present(native, surface_context, "from_user_username", "username")
    )
    privacy_scope = _privacy_scope(surface_context, native)
    workspace_root, workspace_id, workspace_source = _workspace_binding(req, surface_context, native)
    conversation = surface_context.get("conversation")
    if not isinstance(conversation, dict):
        conversation = {}
    conversation_lane_key = _clean(conversation.get("lane_key"))
    surface_topic_key = _surface_topic_key_from_parts(
        privacy_scope=privacy_scope,
        surface=surface or surface_type,
        user=user_id or username or "user",
        topic=conversation_lane_key or native_thread_id or thread_id or session_id or "default",
    )
    attachments = build_attachment_refs_from_chat_request(req, surface=surface or surface_type)
    update_handle = SurfaceUpdateHandle(
        surface_type=surface_type,
        surface_id=surface_id,
        native_chat_id=native_chat_id,
        native_thread_id=native_thread_id,
        native_message_id=native_message_id,
        reply_to_message_id=reply_to_message_id,
        supports_edit=bool(surface_context.get("supports_message_edit", True)),
        metadata={
            key: value
            for key, value in native.items()
            if key
            in {
                "chat_type",
                "ingress_dedup_key",
                "sender_chat_id",
                "sender_chat_username",
            }
        },
    )
    turn_id = _stable_id(
        "surface-turn",
        workspace_id,
        workspace_root,
        surface or surface_type,
        session_id,
        native_chat_id or "",
        native_thread_id or "",
        native_message_id or "",
        text,
        "|".join(ref.id for ref in attachments),
    )
    return SurfaceTurn(
        id=turn_id,
        text=text,
        workspace_root=workspace_root,
        workspace_id=workspace_id,
        surface_type=surface_type,
        surface_id=surface_id,
        surface=surface,
        session_id=session_id,
        thread_id=thread_id,
        user_id=user_id,
        username=username,
        native_chat_id=native_chat_id,
        native_thread_id=native_thread_id,
        native_message_id=native_message_id,
        reply_to_message_id=reply_to_message_id,
        reply_to_text=reply_to_text,
        privacy_scope=privacy_scope,
        attachments=attachments,
        update_handle=update_handle,
        capabilities=_surface_capabilities(surface_type, surface_context),
        metadata={
            "workflow_id": _clean(getattr(req, "workflow_id", "")),
            "requested_mode": _clean(getattr(req, "mode", "")),
            "legacy_thread_id": _clean(getattr(req, "thread_id", "")),
            "queue_action": _clean(surface_context.get("queue_action")),
            "task_id": _clean(surface_context.get("task_id")),
            "workspace_source": workspace_source,
            "surface_topic_key": surface_topic_key,
            "history": _bounded_chat_history(getattr(req, "history", [])),
            "reply_context": _reply_context(surface_context, native),
            "surface_context": _agent_surface_context(surface_context),
        },
    )


def normalize_token_usage(raw: Any) -> dict[str, int]:
    """Return canonical integer token usage while preserving provider extras."""

    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        try:
            normalized[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    if not normalized:
        return {}
    prompt = int(
        normalized.get(
            "prompt_tokens",
            normalized.get("input_tokens", normalized.get("prompt", 0)),
        )
        or 0
    )
    completion = int(
        normalized.get(
            "completion_tokens",
            normalized.get("output_tokens", normalized.get("completion", 0)),
        )
        or 0
    )
    total = int(normalized.get("total_tokens", prompt + completion) or (prompt + completion))
    normalized["prompt_tokens"] = prompt
    normalized["completion_tokens"] = completion
    normalized["total_tokens"] = total
    return normalized


def merge_token_usage(*usage_maps: Any) -> dict[str, int]:
    """Add provider token usage maps together."""

    merged: dict[str, int] = {}
    for usage_map in usage_maps:
        for key, value in normalize_token_usage(usage_map).items():
            merged[key] = int(merged.get(key, 0) or 0) + int(value)
    return merged


def format_token_usage(raw: Any) -> str:
    """Compact token usage string for Telegram, CLI, and debug views."""

    usage = normalize_token_usage(raw)
    if not usage:
        return "unavailable"
    parts = [
        f"prompt={usage.get('prompt_tokens', 0)}",
        f"completion={usage.get('completion_tokens', 0)}",
        f"total={usage.get('total_tokens', 0)}",
    ]
    if usage.get("cached_input_tokens"):
        parts.append(f"cached_input={usage['cached_input_tokens']}")
    if usage.get("cache_write_tokens"):
        parts.append(f"cache_write={usage['cache_write_tokens']}")
    return ", ".join(parts)


def build_attachment_refs_from_chat_request(
    req: Any,
    *,
    surface: str = "",
) -> list[AttachmentRef]:
    """Collect structured attachments from legacy request fields."""

    refs: list[AttachmentRef] = []
    seen: set[str] = set()
    now = datetime.now(timezone.utc).isoformat()

    primary_path = _clean(getattr(req, "attachment_path", ""))
    if primary_path:
        refs.append(
            _attachment_ref_from_parts(
                source_surface=surface,
                path=primary_path,
                name=Path(primary_path).name,
                caption="",
                kind=None,
                created_at=now,
            )
        )

    surface_context = dict(getattr(req, "surface_context", None) or {})
    raw_attachments = surface_context.get("appended_attachments")
    if isinstance(raw_attachments, list):
        for item in raw_attachments:
            if not isinstance(item, dict):
                continue
            refs.append(
                _attachment_ref_from_parts(
                    source_surface=surface,
                    path=_clean(item.get("path")),
                    name=_clean(item.get("name")) or None,
                    caption=_clean(item.get("caption")),
                    kind=_normalize_kind(item.get("kind")),
                    native_file_id=_string_or_none(item.get("native_file_id") or item.get("file_id")),
                    native_message_id=_string_or_none(item.get("native_message_id") or item.get("message_id")),
                    mime_type=_string_or_none(item.get("mime_type")),
                    size_bytes=_int_or_none(item.get("size_bytes") or item.get("size")),
                    checksum=_string_or_none(item.get("checksum")),
                    created_at=now,
                    metadata={k: v for k, v in item.items() if k not in _ATTACHMENT_KNOWN_KEYS},
                )
            )

    legacy = _legacy_attachment_from_text(str(getattr(req, "message", "") or ""))
    if legacy is not None:
        refs.append(
            _attachment_ref_from_parts(
                source_surface=surface,
                path=legacy["path"],
                name=Path(legacy["path"]).name,
                caption=legacy.get("caption", ""),
                kind=legacy.get("kind"),
                created_at=now,
                metadata={"source": "legacy_text_marker"},
            )
        )

    deduped: list[AttachmentRef] = []
    for ref in refs:
        key = ref.checksum or ref.native_file_id or ref.local_path or ref.id
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


def triage_surface_turn(
    turn: SurfaceTurn,
    *,
    requested_mode: str = "auto",
) -> TriageDecisionRecord:
    """Deterministic first-pass V2 routing.

    This is deliberately conservative. It gives the control plane a typed
    routing object while later slices can replace the classifier behind this
    contract.
    """

    message = " ".join(turn.text.split())
    lower = message.lower()
    requested = str(requested_mode or "auto").strip().lower()
    topic_key = _topic_key(turn)
    hints = _task_family_hints(lower, requested, turn.attachments)

    command = _control_command(lower)
    if command:
        return TriageDecisionRecord(
            action="control_command",
            reason=f"recognized {command} control command",
            task_binding="bypass",
            topic_key=topic_key,
            queue_key="control:bypass",
            control_command=command,
            task_family_hints=hints,
        )

    queue_action = str(turn.metadata.get("queue_action") or "").strip().lower()
    explicit_lane = _explicit_queue_lane(lower, queue_action)
    if explicit_lane == "append":
        return TriageDecisionRecord(
            action="append_to_active_run",
            reason="explicit checkpoint append requested",
            task_binding="append",
            topic_key=topic_key,
            queue_key=f"task:{topic_key}:append",
            task_family_hints=hints,
        )
    if explicit_lane == "continue_after_current":
        return TriageDecisionRecord(
            action="continue_after_current",
            reason="explicit continue-after-current lane requested",
            task_binding="continue_after_current",
            topic_key=topic_key,
            queue_key=f"task:{topic_key}:continue",
            task_family_hints=hints,
        )

    if requested in {"agent", "build", "mutate"}:
        return TriageDecisionRecord(
            action="agent_requested",
            reason=f"requested mode `{requested}` requires bounded Agent execution",
            task_binding="new_task",
            topic_key=topic_key,
            queue_key=f"task:{topic_key}",
            task_family_hints=hints,
            requires_confirmation=turn.privacy_scope == "shared" and requested in {"build", "mutate"},
        )

    if turn.reply_to_message_id and _looks_like_followup(lower):
        return TriageDecisionRecord(
            action="agent_suggested",
            reason="reply/follow-up evidence points at an active task, but queue lane requires explicit user choice",
            task_binding="existing_task",
            topic_key=topic_key,
            queue_key=f"task:{topic_key}:needs-lane",
            task_family_hints=hints,
            requires_confirmation=True,
        )

    if _agent_suggestion_needed(lower, turn.attachments):
        return TriageDecisionRecord(
            action="agent_suggested",
            reason="turn looks long-running, mutating, or attachment-heavy",
            task_binding="new_task",
            topic_key=topic_key,
            queue_key=f"task:{topic_key}",
            task_family_hints=hints,
            requires_confirmation=True,
        )

    return TriageDecisionRecord(
        action="chat_response",
        reason="default lightweight chat turn",
        task_binding="none",
        topic_key=topic_key,
        queue_key=f"chat:{topic_key}",
        task_family_hints=hints,
    )


def _attachment_ref_from_parts(
    *,
    source_surface: str,
    path: str = "",
    name: str | None = None,
    caption: str = "",
    kind: AttachmentKind | str | None = None,
    native_file_id: str | None = None,
    native_message_id: str | None = None,
    mime_type: str | None = None,
    size_bytes: int | None = None,
    checksum: str | None = None,
    created_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AttachmentRef:
    resolved_kind = _normalize_kind(kind) or _kind_from_path(path)
    ref_id = _stable_id(
        "attachment",
        source_surface,
        native_file_id or "",
        native_message_id or "",
        path,
        name or "",
        caption,
    )
    return AttachmentRef(
        id=ref_id,
        kind=resolved_kind,
        source_surface=source_surface,
        native_file_id=native_file_id,
        native_message_id=native_message_id,
        mime_type=mime_type,
        local_path=path or None,
        display_name=name,
        caption=caption,
        size_bytes=size_bytes,
        checksum=checksum,
        created_at=created_at,
        metadata=dict(metadata or {}),
    )


_ATTACHMENT_KNOWN_KEYS = {
    "path",
    "name",
    "caption",
    "kind",
    "native_file_id",
    "file_id",
    "native_message_id",
    "message_id",
    "mime_type",
    "size_bytes",
    "size",
    "checksum",
}


def _native_surface_metadata(surface_context: dict[str, Any]) -> dict[str, Any]:
    for key in ("native", "telegram", "message_context", "surface_turn"):
        value = surface_context.get(key)
        if isinstance(value, dict):
            return dict(value)
    adapter = surface_context.get("adapter")
    if isinstance(adapter, dict):
        return dict(adapter)
    return {}


def _surface_capabilities(surface_type: str, surface_context: dict[str, Any]) -> list[str]:
    surface_type = surface_type.lower()
    defaults: list[str] = []
    if surface_type == "telegram":
        defaults = ["message_edit", "threaded_replies", "media_download"]
    elif surface_type in {"whatsapp", "whatsapp-web"}:
        defaults = ["media_download"]
    elif surface_type in {"web", "editor", "cli"}:
        defaults = ["streaming", "file_upload"]

    raw = surface_context.get("capabilities")
    if isinstance(raw, list):
        return list(
            dict.fromkeys(
                [
                    *[str(item) for item in raw if str(item).strip()],
                    *defaults,
                ]
            )
        )
    return defaults


def _privacy_scope(
    surface_context: dict[str, Any],
    native: dict[str, Any],
) -> Literal["private", "shared", "unknown"]:
    explicit = str(surface_context.get("privacy_scope") or native.get("privacy_scope") or "").lower()
    if explicit in {"private", "shared"}:
        return explicit  # type: ignore[return-value]
    chat_type = str(native.get("chat_type") or surface_context.get("chat_type") or "").lower()
    if chat_type == "private":
        return "private"
    if chat_type in {"group", "supergroup", "channel", "forum"}:
        return "shared"
    return "unknown"


def _workspace_binding(
    req: Any,
    surface_context: dict[str, Any],
    native: dict[str, Any],
) -> tuple[str, str, str]:
    """Resolve the V2 workspace binding for one surface session."""

    workspace_payload = surface_context.get("workspace")
    workspace_dict = workspace_payload if isinstance(workspace_payload, dict) else {}
    candidate = _first_workspace_value(
        getattr(req, "workspace_root", None),
        getattr(req, "workspace", None),
        surface_context.get("workspace_root"),
        surface_context.get("workspace_path"),
        workspace_payload,
        surface_context.get("working_directory"),
        surface_context.get("cwd"),
        workspace_dict.get("root"),
        workspace_dict.get("path"),
        native.get("workspace_root"),
        native.get("workspace_path"),
    )
    source = "explicit" if candidate else ""
    if not candidate:
        candidate = _workspace_path_from_message_text(str(getattr(req, "message", "") or ""))
        source = "message_path" if candidate else "default_home"
    raw_root = candidate or "~"
    workspace_root = _normalize_workspace_root(raw_root)
    explicit_id = _first_workspace_value(
        surface_context.get("workspace_id"),
        workspace_dict.get("id"),
        native.get("workspace_id"),
    )
    workspace_id = explicit_id or workspace_root
    return workspace_root, workspace_id, source


def _first_workspace_value(*values: Any) -> str:
    for value in values:
        if isinstance(value, dict):
            continue
        text = _clean(value)
        if text:
            return text
    return ""


def _normalize_workspace_root(value: str) -> str:
    raw = _clean(value) or "~"
    try:
        return str(normalize_workspace_root_path(raw))
    except Exception:
        try:
            return str(Path(raw).expanduser())
        except Exception:
            return str(Path.home())


def _workspace_path_from_message_text(text: str) -> str:
    """Infer a workspace from explicit natural-language path mentions.

    This intentionally requires path-ish wording so attachment markers or random
    file references do not silently change the workspace.
    """

    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    cue = r"(?:workspace|workdir|cwd|repo|repository|project|folder|directory|dir|path)"
    relation = r"(?:\s*(?:is|=|:|,|at|to|under|inside|in))*\s+"
    path = r"(?P<path>(?:~|\$HOME|/)[^\s\"'`<>]+)"
    patterns = [
        re.compile(cue + relation + path, re.IGNORECASE),
        re.compile(r"(?:\bin|\bunder|\binside|\bat|\bfrom)\s+" + path, re.IGNORECASE),
    ]
    for pattern in patterns:
        match = pattern.search(clean)
        if not match:
            continue
        candidate = _clean_path_token(match.group("path"))
        if candidate:
            return _workspace_root_from_path_candidate(candidate)
    quoted = re.finditer(r"(?P<quote>[\"'`])(?P<path>(?:~|\$HOME|/).+?)(?P=quote)", clean)
    for match in quoted:
        prefix = clean[max(0, match.start() - 48) : match.start()].lower()
        if not re.search(cue, prefix):
            continue
        candidate = _clean_path_token(match.group("path"))
        if candidate:
            return _workspace_root_from_path_candidate(candidate)
    return ""


def _clean_path_token(value: str) -> str:
    token = _clean(value)
    while len(token) > 1 and token[-1] in ".,;:!?)]}":
        token = token[:-1]
    return token


def _workspace_root_from_path_candidate(value: str) -> str:
    raw = _clean_path_token(value)
    if raw.startswith("$HOME/") or raw == "$HOME":
        raw = str(Path.home()) + raw[len("$HOME") :]
    candidate = Path(raw).expanduser()
    try:
        if candidate.exists() and candidate.is_file():
            candidate = candidate.parent
    except OSError:
        pass
    return str(candidate)


def _topic_key(turn: SurfaceTurn) -> str:
    workspace = turn.workspace_id or turn.workspace_root or "workspace"
    return re.sub(
        r"[^A-Za-z0-9_.:-]+",
        "_",
        f"{workspace}:{_surface_topic_key(turn)}",
    )


def _surface_topic_key(turn: SurfaceTurn) -> str:
    return _surface_topic_key_from_parts(
        privacy_scope=turn.privacy_scope or "unknown",
        surface=turn.surface or turn.surface_type or "surface",
        user=turn.user_id or turn.username or "user",
        topic=turn.native_thread_id or turn.thread_id or turn.session_id or "default",
    )


def _surface_topic_key_from_parts(
    *,
    privacy_scope: str,
    surface: str,
    user: str,
    topic: str,
) -> str:
    return re.sub(
        r"[^A-Za-z0-9_.:-]+",
        "_",
        f"{privacy_scope or 'unknown'}:{surface or 'surface'}:{user or 'user'}:{topic or 'default'}",
    )


def _control_command(lower: str) -> str | None:
    stripped = lower.strip()
    if not stripped:
        return None
    first = stripped.split(maxsplit=1)[0]
    aliases = {
        "/status": "status",
        "status": "status",
        "progress": "status",
        "/cancel": "cancel",
        "cancel": "cancel",
        "stop": "cancel",
        "/retry": "retry",
        "retry": "retry",
        "approve": "approve",
        "yes": "approve",
        "deny": "deny",
        "no": "deny",
    }
    return aliases.get(first)


def _explicit_queue_lane(lower: str, queue_action: str) -> str | None:
    normalized = queue_action.replace("-", "_")
    if normalized in {"append", "append_followup", "checkpoint_append", "inject"}:
        return "append"
    if normalized in {"continue", "continue_after_current", "after_current"}:
        return "continue_after_current"
    first = lower.strip().split(maxsplit=1)[0] if lower.strip() else ""
    if first in {"/append", "/inject"}:
        return "append"
    if first in {"/continue", "/continue-after-current"}:
        return "continue_after_current"
    return None


def _looks_like_followup(lower: str) -> bool:
    return any(
        cue in lower
        for cue in (
            "also",
            "instead",
            "continue",
            "keep going",
            "use this",
            "do that",
            "same task",
            "for this",
        )
    )


def _agent_suggestion_needed(lower: str, attachments: list[AttachmentRef]) -> bool:
    if len(attachments) > 1:
        return True
    if attachments and any(cue in lower for cue in ("analyze", "extract", "build", "turn into", "compare")):
        return True
    return any(
        cue in lower
        for cue in (
            "implement",
            "build",
            "create file",
            "write to",
            "refactor",
            "run tests",
            "make a website",
            "long running",
        )
    )


def _task_family_hints(
    lower: str,
    requested_mode: str,
    attachments: list[AttachmentRef],
) -> list[str]:
    hints: list[str] = []
    if requested_mode in {"build", "mutate"} or any(
        cue in lower for cue in ("code", "implement", "refactor", "test", "bug", "file")
    ):
        hints.append("coding")
    if any(cue in lower for cue in ("research", "cite", "verify", "paper", "pdf")):
        hints.append("research")
    if any(cue in lower for cue in ("website", "html", "css", "frontend", "page")):
        hints.append("website")
    if any(ref.kind in {"image", "figure"} for ref in attachments):
        hints.append("vision")
    if any(ref.kind == "pdf" for ref in attachments):
        hints.append("reader")
    return list(dict.fromkeys(hints))


def _legacy_attachment_from_text(text: str) -> dict[str, str] | None:
    clean = text.strip()
    markers = (
        ("[Attachment: ", ""),
        ("[Voice note: ", "audio"),
    )
    for prefix, kind in markers:
        if not clean.startswith(prefix):
            continue
        end = clean.find("]")
        if end <= len(prefix):
            continue
        path = clean[len(prefix) : end].strip()
        caption = clean[end + 1 :].strip()
        return {"path": path, "kind": kind, "caption": caption}
    return None


def _kind_from_path(path: str) -> AttachmentKind:
    lower = path.lower()
    if lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff")):
        return "image"
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith((".csv", ".json", ".jsonl", ".xlsx", ".xls", ".parquet")):
        return "data"
    if lower.endswith((".mp3", ".wav", ".m4a", ".ogg", ".oga")):
        return "audio"
    if lower.endswith((".mp4", ".mov", ".webm", ".mkv")):
        return "video"
    if lower:
        return "document"
    return "unknown"


def _normalize_kind(value: Any) -> AttachmentKind | None:
    raw = str(value or "").strip().lower()
    if raw in {"image", "figure", "pdf", "document", "audio", "video", "data", "unknown"}:
        return raw  # type: ignore[return-value]
    if raw in {"photo", "screenshot"}:
        return "image"
    if raw in {"file"}:
        return None
    return None


def _first_present(native: dict[str, Any], surface_context: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in native and native[key] not in (None, ""):
            return native[key]
        if key in surface_context and surface_context[key] not in (None, ""):
            return surface_context[key]
    return None


def _bounded_chat_history(
    raw_history: Any,
    *,
    max_turns: int = 24,
    max_chars_per_turn: int = 2000,
) -> list[dict[str, str]]:
    if not isinstance(raw_history, list):
        return []
    history: list[dict[str, str]] = []
    for item in raw_history[-max_turns:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue
        content = _compact_context_text(item.get("content"), limit=max_chars_per_turn)
        if content:
            history.append({"role": role, "content": content})
    return history


def _reply_context(
    surface_context: dict[str, Any],
    native: dict[str, Any],
) -> dict[str, Any]:
    reply_to_message_id = _string_or_none(
        _first_present(native, surface_context, "reply_to_message_id")
    )
    reply_to_text = _string_or_none(
        _first_present(native, surface_context, "reply_to_text")
    )
    if not reply_to_message_id and not reply_to_text:
        return {}
    return {
        "reply_to_message_id": reply_to_message_id or "",
        "reply_to_text": _compact_context_text(reply_to_text, limit=1200),
    }


def _agent_surface_context(surface_context: dict[str, Any]) -> dict[str, Any]:
    allowed: dict[str, Any] = {}
    for key in (
        "identity",
        "peers",
        "telegram",
        "conversation",
        "workspace",
        "workspace_root",
        "workspace_id",
        "workspace_mode",
        "workspace_source",
        "notes_root",
        "notes_feature",
        "notes_workspace",
        "active_note",
        "surface_profile",
        "agent_profile",
        "agent_backend",
        "agent_selection",
        "gui_for",
        "ui_surface",
        "privacy_scope",
        "capabilities",
        "selected_chunk",
        "selected_blueprint_node",
        "active_file",
        "selected_skills",
        "appended_attachments",
        "communication_policy",
        "execution_policy",
        "surface_policy",
    ):
        value = surface_context.get(key)
        if value not in (None, "", [], {}):
            allowed[key] = value
    if "telegram" in allowed and isinstance(allowed["telegram"], dict):
        telegram = dict(allowed["telegram"])
        if "reply_to_text" in telegram:
            telegram["reply_to_text"] = _compact_context_text(
                telegram.get("reply_to_text"),
                limit=1200,
            )
        allowed["telegram"] = telegram
    return allowed


def _compact_context_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _stable_id(*parts: str) -> str:
    payload = "\n".join(str(part or "") for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _string_or_none(value: Any) -> str | None:
    text = _clean(value)
    return text or None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None
