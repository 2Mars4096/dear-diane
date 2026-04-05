from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal

from .models import PendingAction

_CONFIRM_NEGATIVE_WORDS = frozenset({"no", "n", "cancel", "stop"})
_CONFIRM_POSITIVE_WORDS = frozenset({"yes", "y", "ok", "okay", "do it", "go ahead", "sure"})
_SUPERSEDE_RE = re.compile(
    r"^(?:actually|instead|ignore\b|correction\b|wait\b|hold on\b|stop\b|cancel\b|change of plan\b|new instruction\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PendingReplyResolution:
    """Explicit outcome contract for pending confirmation/clarification replies."""

    action: Literal["cancel", "resume", "prompt_retry", "ignore"]
    pending_kind: Literal["confirm", "clarify"]
    replay_text: str = ""
    resolved_value: str = ""
    response_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    requires_triage: bool = False


def _looks_like_superseding_instruction(reply_lower: str) -> bool:
    if not reply_lower:
        return False
    if " instead" in reply_lower or reply_lower.startswith("instead "):
        return True
    return bool(_SUPERSEDE_RE.match(reply_lower))


def _append_pending_note(base_text: str, note: str) -> str:
    base = str(base_text or "").strip()
    suffix = str(note or "").strip()
    if not suffix:
        return base
    if not base:
        return f"[{suffix}]"
    return f"{base}\n[{suffix}]"


def _pending_requires_triage(pending: PendingAction) -> bool:
    metadata = getattr(pending, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    return bool(metadata.get("requires_triage"))


def _build_attachment_prompt_context(
    pending: PendingAction,
    *,
    step: str,
    reply: str,
    resolved_value: str = "",
    requires_triage: bool = False,
) -> str:
    lines = [
        "## Pending action attachment",
        "This turn is attached to the current project/task from a pending follow-up.",
        f"Pending kind: {pending.kind}",
        f"Pending intent: {pending.intent}",
    ]
    if pending.original_text:
        lines.append(f"Pending text: {pending.original_text}")
    if step == "approval_confirmation":
        lines.append(f"User confirmation: {reply}")
    elif step == "clarification_answer":
        lines.append(f"User clarification: {reply}")
    elif step == "clarification_choice":
        lines.append(f"Resolved option: {resolved_value or reply}")
    elif step == "superseding_instruction":
        lines.append(f"Current user instruction: {reply}")
    if requires_triage:
        if step == "superseding_instruction":
            lines.append(
                "Use the latest user instruction as authoritative for this project/task and do not replay the superseded pending text.",
            )
        else:
            lines.append(
                "Re-run triage using the pending text plus the user's clarification. The clarification resolves the ambiguity; do not skip triage.",
            )
    else:
        lines.append(
            "Apply the attached reply to the pending request for this project/task and do not treat the pending text as a new queued user turn.",
        )
    return "\n".join(lines)


def _attachment_metadata(
    pending: PendingAction,
    *,
    step: str,
    reply: str,
    effective_text: str,
    resolved_value: str = "",
    requires_triage: bool = False,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "pending_action_id": pending.action_id,
        "pending_action_kind": pending.kind,
        "pending_original_text": pending.original_text,
        "pending_intent": pending.intent,
        "pending_created_at": pending.created_at.isoformat(),
        "pending_attachment_source": "pending_follow_up",
        "pending_requires_triage": requires_triage,
        "pending_user_turn_content": reply,
        "pending_effective_text": effective_text,
        "pending_route_step": step,
        "attachment_prompt_context": _build_attachment_prompt_context(
            pending,
            step=step,
            reply=reply,
            resolved_value=resolved_value,
            requires_triage=requires_triage,
        ),
    }
    if pending.task_id:
        metadata["pending_task_id"] = pending.task_id
    if pending.attempt_session_id:
        metadata["pending_attempt_session_id"] = pending.attempt_session_id
    if resolved_value:
        metadata["pending_resolved_value"] = resolved_value
    return metadata


def resolve_pending_reply(
    pending: PendingAction,
    reply_text: str,
) -> PendingReplyResolution:
    """Interpret a user reply against a pending approval/clarification contract."""
    reply = str(reply_text or "").strip()
    reply_lower = reply.lower()

    if pending.kind == "confirm":
        if reply_lower in _CONFIRM_NEGATIVE_WORDS:
            return PendingReplyResolution(
                action="cancel",
                pending_kind="confirm",
            )
        if reply_lower in _CONFIRM_POSITIVE_WORDS:
            effective_text = _append_pending_note(
                pending.original_text,
                f"User confirmation: {reply}",
            )
            metadata = _attachment_metadata(
                pending,
                step="approval_confirmation",
                reply=reply,
                effective_text=effective_text,
            )
            metadata["skip_confirm"] = True
            return PendingReplyResolution(
                action="resume",
                pending_kind="confirm",
                replay_text=effective_text,
                metadata=metadata,
            )
        if _looks_like_superseding_instruction(reply_lower):
            effective_text = _append_pending_note(
                reply,
                f"Supersedes pending confirm: {pending.original_text}",
            )
            metadata = _attachment_metadata(
                pending,
                step="superseding_instruction",
                reply=reply,
                effective_text=effective_text,
                resolved_value=reply,
                requires_triage=True,
            )
            return PendingReplyResolution(
                action="resume",
                pending_kind="confirm",
                replay_text=effective_text,
                resolved_value=reply,
                metadata=metadata,
                requires_triage=True,
            )
        return PendingReplyResolution(
            action="prompt_retry",
            pending_kind="confirm",
            response_text="Please answer yes or no.",
        )

    if not reply:
        return PendingReplyResolution(
            action="prompt_retry",
            pending_kind="clarify",
            response_text="Please answer the clarification request.",
        )

    if not pending.options:
        requires_triage = _pending_requires_triage(pending)
        effective_text = _append_pending_note(
            pending.original_text,
            f"User clarification: {reply}",
        )
        metadata = _attachment_metadata(
            pending,
            step="clarification_answer",
            reply=reply,
            effective_text=effective_text,
            resolved_value=reply,
            requires_triage=requires_triage,
        )
        metadata["clarification_answer"] = reply
        return PendingReplyResolution(
            action="resume",
            pending_kind="clarify",
            replay_text=effective_text,
            resolved_value=reply,
            metadata=metadata,
            requires_triage=requires_triage,
        )

    if reply_lower.isdigit():
        idx = int(reply_lower) - 1
        if 0 <= idx < len(pending.options):
            option = pending.options[idx]
            requires_triage = _pending_requires_triage(pending)
            effective_text = _append_pending_note(
                pending.original_text,
                f"User selected option: {option}",
            )
            metadata = _attachment_metadata(
                pending,
                step="clarification_choice",
                reply=reply,
                effective_text=effective_text,
                resolved_value=option,
                requires_triage=requires_triage,
            )
            metadata.update({
                "selected_option": idx,
                "selected_path": option,
            })
            return PendingReplyResolution(
                action="resume",
                pending_kind="clarify",
                replay_text=effective_text,
                resolved_value=option,
                metadata=metadata,
                requires_triage=requires_triage,
            )

    for idx, option in enumerate(pending.options):
        option_lower = option.lower()
        basename = option_lower.rsplit("/", 1)[-1]
        if reply_lower and (reply_lower in option_lower or reply_lower in basename):
            requires_triage = _pending_requires_triage(pending)
            effective_text = _append_pending_note(
                pending.original_text,
                f"User selected option: {option}",
            )
            metadata = _attachment_metadata(
                pending,
                step="clarification_choice",
                reply=reply,
                effective_text=effective_text,
                resolved_value=option,
                requires_triage=requires_triage,
            )
            metadata.update({
                "selected_option": idx,
                "selected_path": option,
            })
            return PendingReplyResolution(
                action="resume",
                pending_kind="clarify",
                replay_text=effective_text,
                resolved_value=option,
                metadata=metadata,
                requires_triage=requires_triage,
            )

    if _looks_like_superseding_instruction(reply_lower):
        effective_text = _append_pending_note(
            reply,
            f"Supersedes pending clarify: {pending.original_text}",
        )
        return PendingReplyResolution(
            action="resume",
            pending_kind="clarify",
            replay_text=effective_text,
            resolved_value=reply,
            metadata=_attachment_metadata(
                pending,
                step="superseding_instruction",
                reply=reply,
                effective_text=effective_text,
                resolved_value=reply,
                requires_triage=True,
            ),
            requires_triage=True,
        )

    numbered_options = "\n".join(
        f"{idx + 1}. {option}"
        for idx, option in enumerate(pending.options[:8])
    )
    return PendingReplyResolution(
        action="prompt_retry",
        pending_kind="clarify",
        response_text=(
            "I couldn't match that reply. Please reply with a number:\n"
            f"{numbered_options}"
        ),
    )


__all__ = [
    "PendingReplyResolution",
    "resolve_pending_reply",
]
