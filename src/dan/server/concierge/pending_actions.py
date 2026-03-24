from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .models import PendingAction


@dataclass(frozen=True)
class PendingReplyResolution:
    """Explicit outcome contract for pending confirmation/clarification replies."""

    action: Literal["cancel", "resume", "prompt_retry", "ignore"]
    pending_kind: Literal["confirm", "clarify"]
    replay_text: str = ""
    resolved_value: str = ""
    response_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def resolve_pending_reply(
    pending: PendingAction,
    reply_text: str,
) -> PendingReplyResolution:
    """Interpret a user reply against a pending approval/clarification contract."""
    reply = str(reply_text or "").strip()
    reply_lower = reply.lower()

    if pending.kind == "confirm":
        if reply_lower in {"no", "n", "cancel", "stop"}:
            return PendingReplyResolution(
                action="cancel",
                pending_kind="confirm",
            )
        if reply_lower in {"yes", "y", "ok", "okay", "do it", "go ahead", "sure"}:
            return PendingReplyResolution(
                action="resume",
                pending_kind="confirm",
                replay_text=pending.original_text,
                metadata={
                    "skip_confirm": True,
                    "pending_route_step": "approval_confirmation",
                },
            )
        return PendingReplyResolution(
            action="prompt_retry",
            pending_kind="confirm",
            response_text="Please answer yes or no.",
        )

    if not pending.options:
        return PendingReplyResolution(
            action="resume",
            pending_kind="clarify",
            replay_text=f"{pending.original_text}\n[User clarification: {reply}]",
            resolved_value=reply,
            metadata={
                "clarification_answer": reply_text,
                "pending_route_step": "clarification_answer",
            },
        )

    if reply_lower.isdigit():
        idx = int(reply_lower) - 1
        if 0 <= idx < len(pending.options):
            option = pending.options[idx]
            return PendingReplyResolution(
                action="resume",
                pending_kind="clarify",
                replay_text=pending.original_text,
                resolved_value=option,
                metadata={
                    "selected_option": idx,
                    "selected_path": option,
                    "pending_route_step": "clarification_choice",
                },
            )

    for idx, option in enumerate(pending.options):
        option_lower = option.lower()
        basename = option_lower.rsplit("/", 1)[-1]
        if reply_lower and (reply_lower in option_lower or reply_lower in basename):
            return PendingReplyResolution(
                action="resume",
                pending_kind="clarify",
                replay_text=pending.original_text,
                resolved_value=option,
                metadata={
                    "selected_option": idx,
                    "selected_path": option,
                    "pending_route_step": "clarification_choice",
                },
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
