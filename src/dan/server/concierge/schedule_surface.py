from __future__ import annotations

"""User-facing schedule command semantics for the concierge surface."""

import re
from typing import Any, Awaitable, Callable

from .command_registry import get_default_registry
from .models import SurfaceMessage
from .scheduler import DeliveryTarget, TriggerContext, parse_nl_schedule

_WORKFLOW_SCHEDULE_EXPLICIT_RE = re.compile(
    r"\b(?:schedule|automate|automation|automated|recurring|cron)\b",
    re.IGNORECASE,
)
_WORKFLOW_SCHEDULE_SUBJECT_RE = re.compile(
    r"\b(?:workflow|graph|pipeline|execution|exeuction|it|that|this)\b",
    re.IGNORECASE,
)
_WORKFLOW_SCHEDULE_TRIGGER_RE = re.compile(
    r"\b(?:daily|weekly|monthly|weekdays|every\s+\d+\s+(?:hours?|minutes?|days?|h|m|d)|"
    r"at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
    re.IGNORECASE,
)
_WORKFLOW_SCHEDULE_MULTI_ACTION_RE = re.compile(
    r"\b(?:delete|remove|increase|decrease|edit|fix|patch|change|update|add|build|rebuild)\b",
    re.IGNORECASE,
)


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _usable_graph_store(*candidates: Any) -> Any | None:
    for candidate in candidates:
        if candidate is None:
            continue
        if type(candidate).__module__.startswith("unittest.mock"):
            continue
        if callable(getattr(candidate, "get_graph", None)):
            return candidate
    return None


def resolve_default_workflow_id(
    msg: SurfaceMessage,
    *,
    project_store: Any,
    capability_context: Any,
) -> str | None:
    metadata = (
        msg.metadata
        if isinstance(getattr(msg, "metadata", None), dict)
        else {}
    )
    workflow_id = str(metadata.get("workflow_id") or "").strip()
    if workflow_id:
        return workflow_id

    active_projects = project_store.list_active(msg.external_id)
    if active_projects:
        linked = list(active_projects[0].linked_workflow_ids or [])
        if linked:
            workflow_id = str(linked[-1] or "").strip()
            if workflow_id:
                return workflow_id

    workflow_id = str(
        getattr(capability_context, "workflow_id", "") or ""
    ).strip()
    return workflow_id or None


def infer_workflow_schedule_trigger(text: str) -> str | None:
    stripped = str(text or "").strip()
    if not stripped:
        return None

    parsed = parse_nl_schedule(stripped)
    if parsed is not None:
        _action, trigger = parsed
        return str(trigger or "").strip() or None

    lower = stripped.lower()
    if _WORKFLOW_SCHEDULE_TRIGGER_RE.search(lower) is None:
        return None

    if "weekdays" in lower:
        time_match = re.search(r"\bweekdays\s+at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", lower)
        if time_match:
            return f"weekdays at {time_match.group(1)}"
        return "weekdays at 9am"
    if "daily" in lower:
        time_match = re.search(r"\bdaily\s+at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", lower)
        if time_match:
            return f"daily at {time_match.group(1)}"
        return "every day at 9am"
    if "weekly" in lower:
        return "every 7d"
    if "monthly" in lower:
        return "every 30d"

    every_match = re.search(
        r"\bevery\s+\d+\s+(?:hours?|minutes?|days?|h|m|d)\b",
        lower,
    )
    if every_match:
        return every_match.group(0)

    time_match = re.search(r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", lower)
    if time_match:
        return f"daily at {time_match.group(1)}"
    return None


def coerce_workflow_schedule_followup_message(
    msg: SurfaceMessage,
    *,
    schedule_store: Any,
    project_store: Any,
    capability_context: Any,
) -> SurfaceMessage | None:
    if schedule_store is None:
        return None

    text = str(getattr(msg, "text", "") or "").strip()
    if not text or text.startswith("/"):
        return None
    if _WORKFLOW_SCHEDULE_MULTI_ACTION_RE.search(text) and "." in text:
        return None
    if _WORKFLOW_SCHEDULE_MULTI_ACTION_RE.search(text) and "," in text:
        return None

    default_workflow_id = resolve_default_workflow_id(
        msg,
        project_store=project_store,
        capability_context=capability_context,
    )
    if not default_workflow_id:
        return None

    trigger = infer_workflow_schedule_trigger(text)
    if not trigger:
        return None

    has_explicit_schedule = bool(_WORKFLOW_SCHEDULE_EXPLICIT_RE.search(text))
    has_subject_reference = bool(_WORKFLOW_SCHEDULE_SUBJECT_RE.search(text))
    has_run_phrase = bool(
        re.search(r"\b(?:run|execute|launch|start)\b", text, re.IGNORECASE)
    )
    if not (
        (has_explicit_schedule and has_subject_reference)
        or (has_subject_reference and has_run_phrase)
    ):
        return None

    metadata = dict(msg.metadata or {})
    metadata["workflow_schedule_followup_bridge"] = True
    metadata["workflow_schedule_original_text"] = text
    return msg.model_copy(
        update={
            "text": f"/schedule workflow current {trigger}",
            "metadata": metadata,
        }
    )


async def apply_embedded_workflow_schedule_followup(
    msg: SurfaceMessage,
    *,
    schedule_store: Any,
    project_store: Any,
    capability_context: Any,
    dispatch_fast_command: Callable[[SurfaceMessage, Any, Any], Awaitable[Any | None]],
) -> SurfaceMessage:
    if schedule_store is None:
        return msg

    text = str(getattr(msg, "text", "") or "").strip()
    if not text or text.startswith("/"):
        return msg

    clauses = [
        re.sub(r"\s+", " ", part).strip(" ,.;")
        for part in re.split(r"[.!?]\s+|,\s*", text)
        if str(part or "").strip(" ,.;")
    ]
    if len(clauses) < 2:
        return msg

    registry = get_default_registry()
    descriptor = registry.get("/schedule")
    if descriptor is None or descriptor.kind != "chat":
        return msg

    for index, clause in enumerate(clauses):
        clause_msg = msg.model_copy(update={"text": clause})
        bridged_schedule_msg = coerce_workflow_schedule_followup_message(
            clause_msg,
            schedule_store=schedule_store,
            project_store=project_store,
            capability_context=capability_context,
        )
        if bridged_schedule_msg is None:
            continue

        schedule_result = await dispatch_fast_command(
            bridged_schedule_msg,
            descriptor,
            registry,
        )
        if schedule_result is None:
            continue

        remaining_clauses = [item for idx, item in enumerate(clauses) if idx != index]
        remaining_text = ". ".join(
            item.strip()
            for item in remaining_clauses
            if str(item or "").strip()
        ).strip()
        if not remaining_text:
            return msg

        metadata = dict(msg.metadata or {})
        prefetched_actions = list(metadata.get("prefilled_action_contexts") or [])
        schedule_note = str(getattr(schedule_result, "content", "") or "").strip()
        if schedule_note:
            prefetched_actions.append(
                f"Workflow scheduling was already completed during routing: {schedule_note}"
            )
        metadata["prefilled_action_contexts"] = _dedupe_keep_order(prefetched_actions)
        metadata["workflow_schedule_followup_bridge"] = True
        metadata["workflow_schedule_original_text"] = text
        return msg.model_copy(
            update={
                "text": remaining_text,
                "metadata": metadata,
            }
        )

    return msg


def build_schedule_command_kwargs(
    msg: SurfaceMessage,
    *,
    schedule_store: Any,
    schedule_history_store: Any,
    project_store: Any,
    capability_context: Any,
    chat_manager: Any,
    user_profile: Any,
) -> dict[str, Any]:
    metadata = (
        msg.metadata
        if isinstance(getattr(msg, "metadata", None), dict)
        else {}
    )
    resolved_project_id = str(metadata.get("resolved_project_id") or "").strip() or None
    resolved_task_id = str(metadata.get("resolved_task_id") or "").strip() or None
    default_workflow_id = resolve_default_workflow_id(
        msg,
        project_store=project_store,
        capability_context=capability_context,
    )
    active_projects = project_store.list_active(msg.external_id)
    project_workflow_ids = (
        list(active_projects[0].linked_workflow_ids or [])
        if active_projects
        else []
    )
    trigger_context = TriggerContext(
        source_surface=msg.surface or "schedule",
        project_id=resolved_project_id,
        task_id=resolved_task_id,
        user_id=msg.external_id or None,
        thread_key=msg.session_id or msg.external_id or None,
    )
    delivery_target = DeliveryTarget(
        surface=msg.surface or "cli",
        conversation_key=msg.external_id or None,
        user_id=msg.external_id or None,
        project_id=resolved_project_id,
        thread_key=msg.session_id or msg.external_id or None,
    )
    graph_store = _usable_graph_store(
        getattr(capability_context, "graph_store", None),
        getattr(chat_manager, "_graph_store", None),
    )
    return {
        "text": msg.text,
        "store": schedule_store,
        "history_store": schedule_history_store,
        "default_trigger_context": trigger_context,
        "default_delivery_target": delivery_target,
        "default_workflow_id": default_workflow_id,
        "graph_store": graph_store,
        "project_workflow_ids": project_workflow_ids,
        "expected_workflow_revision": str(metadata.get("client_graph_revision") or "").strip() or None,
        "default_timezone": str(getattr(user_profile, "preferred_timezone", "") or "").strip() or None,
    }
