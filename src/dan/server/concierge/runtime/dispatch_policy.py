from __future__ import annotations

"""Concierge dispatch-mode policy.

This keeps the ack-vs-foreground decision narrow: only explicit long-running
signals background a turn; normal ask/agent/plan work stays foreground.
"""

from typing import Any

from ..followup_classifier import FollowUpResolution, FollowUpType
from ..models import SurfaceMessage
from ..task_registry import DispatchMode

_FOREGROUND_WORKFLOW_QUERY_BLOCKERS = frozenset(
    {"workflow_run", "run_control", "workflow_edit", "write_file"}
)
_EXPLICIT_BACKGROUND_METADATA_KEYS = (
    "background",
    "run_in_background",
    "defer_reply",
    "detached",
    "detached_mutation",
)


def _normalized_action_hints(route: Any) -> set[str]:
    return {
        str(item or "").strip().lower()
        for item in list(getattr(route, "action_hints", None) or [])
        if str(item or "").strip()
    }


def _explicit_background_requested(metadata: dict[str, Any]) -> bool:
    for key in _EXPLICIT_BACKGROUND_METADATA_KEYS:
        value = metadata.get(key)
        if isinstance(value, bool):
            if value:
                return True
            continue
        if str(value or "").strip().lower() in {"1", "true", "yes", "background", "detached"}:
            return True
    return False


def _is_workflow_query_only(target: str, action_hints: set[str]) -> bool:
    return (
        target == "workflow"
        and "workflow_query" in action_hints
        and action_hints.isdisjoint(_FOREGROUND_WORKFLOW_QUERY_BLOCKERS)
    )


def select_dispatch_mode(
    msg: SurfaceMessage,
    triage: Any,
    follow_up: FollowUpResolution | None,
) -> DispatchMode:
    if follow_up is not None and follow_up.follow_up_type == FollowUpType.QUERY_STATUS:
        return DispatchMode.INLINE
    if triage is None:
        return DispatchMode.FOREGROUND
    if getattr(triage, "is_social", False):
        return DispatchMode.INLINE

    text = str(getattr(msg, "text", "") or "").strip()
    if text.startswith("/"):
        return DispatchMode.INLINE

    route = getattr(triage, "route", None)
    target = str(getattr(route, "target", "") or "").strip().lower()
    action_hints = _normalized_action_hints(route)
    metadata = (
        msg.metadata
        if isinstance(getattr(msg, "metadata", None), dict)
        else {}
    )

    if getattr(triage, "tier", 1) == 0:
        return DispatchMode.INLINE
    if _is_workflow_query_only(target, action_hints):
        return DispatchMode.FOREGROUND

    if {"workflow_run", "run_control"} & action_hints:
        return DispatchMode.BACKGROUND

    if _explicit_background_requested(metadata):
        if "workflow_edit" in action_hints and not metadata.get("skip_confirm"):
            return DispatchMode.FOREGROUND
        return DispatchMode.BACKGROUND

    return DispatchMode.FOREGROUND
