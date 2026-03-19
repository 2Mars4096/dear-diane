from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol

if TYPE_CHECKING:
    from .session import Session, SessionManager, SessionResult, SessionTier
    from .triage import TriageResult

from dan.server.chat_manager import (
    ChatCompleteEvent,
    ChatErrorEvent,
    ChatInterruptedEvent,
    ChatStreamEvent,
)

from .autonomy import autonomy_max_tool_turns, build_autonomy_announcement
from .identity import format_prefix
from .models import IntentCategory, PendingAction, ResolvedContext, RouteDecision, SurfaceMessage, TaskTurn

logger = logging.getLogger(__name__)

_SINGLE_ACTION_VERBS = frozenset({
    "search", "read", "find", "lookup", "check", "get",
    "fetch", "calculate", "compute", "summarize", "translate",
})
_MULTI_ACTION_KEYWORDS = frozenset({
    "research", "analyze", "write", "build", "create",
    "develop", "design", "investigate",
})
_MISSING_TERMINAL_EVENT_FALLBACK = (
    "The response stream ended before a final answer was produced. "
    "Please ask me to continue from the latest progress."
)
_GOAL_REVIEW_STOPWORDS = frozenset({
    "about", "across", "after", "against", "before", "brief", "child", "children",
    "combined", "deliverable", "finish", "from", "goal", "into", "original",
    "parent", "remaining", "result", "results", "review", "session", "task",
    "tasks", "that", "them", "then", "this", "with", "work",
})
_PENDING_CHECKBOX_RE = re.compile(r"(?im)^\s*[-*]?\s*\[\s\]\s+(.+)$")
_OPEN_LOOP_REVIEW_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(?:todo|fixme)\b", re.IGNORECASE),
        "contains TODO-style follow-up markers",
    ),
    (
        re.compile(
            r"\b(?:could not|couldn't|unable to|failed to|blocked by|waiting on)\b",
            re.IGNORECASE,
        ),
        "reports incomplete execution",
    ),
    (
        re.compile(
            r"\b(?:not yet|not implemented|not found|follow[- ]up|remaining|next steps?|pending)\b",
            re.IGNORECASE,
        ),
        "calls out remaining follow-up work",
    ),
)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class TierExecutor(Protocol):
    async def execute(
        self,
        session: Any,
        manager: Any,
    ) -> AsyncIterator[ChatStreamEvent]: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SynthesisGapReview:
    hard_gap_reason: str | None = None
    ambiguous_gap_reason: str | None = None

def _complete_event(content: str, **kwargs: Any) -> ChatCompleteEvent:
    return ChatCompleteEvent(
        message_id=uuid.uuid4().hex[:12],
        content=content,
        token_usage=kwargs.get("token_usage", {}),
        context_window=kwargs.get("context_window", 0),
        graph_revision=kwargs.get("graph_revision", ""),
        detected_mode=kwargs.get("detected_mode"),
    )


def _progress_event(task_label: str) -> ChatCompleteEvent:
    return ChatCompleteEvent(
        message_id=uuid.uuid4().hex[:12],
        content=f"Working on: {task_label[:100]}...",
        token_usage={},
        context_window=0,
        graph_revision="",
        detected_mode="progress_ack",
        phase_label=task_label[:100] if task_label else None,
    )


def _interrupted_event(content: str = "") -> ChatInterruptedEvent:
    return ChatInterruptedEvent(
        message_id=uuid.uuid4().hex[:12],
        content=content,
        token_usage={},
    )


def _synthesize_missing_terminal_event(session: Any) -> ChatCompleteEvent:
    logger.warning(
        "Session %s completed without a terminal chat event; synthesizing fallback",
        getattr(session, "id", "unknown"),
    )
    return _complete_event(_MISSING_TERMINAL_EVENT_FALLBACK)


def _autonomy_level(session: Any, default: str = "balanced") -> str:
    resolution = getattr(session, "autonomy_resolution", None)
    level = getattr(resolution, "effective_level", "") if resolution is not None else ""
    return str(level or default)


def _task_mentions_workflow(task_desc: str) -> bool:
    return bool(re.search(r"\b(?:workflow|graph|node|edge|mutation|build|edit)\b", task_desc, re.IGNORECASE))


def _filter_child_route(parent_route: Any, task_desc: str) -> Any:
    if parent_route is None:
        return None
    action_hints = [
        str(hint).strip()
        for hint in getattr(parent_route, "action_hints", None) or []
        if str(hint).strip()
    ]
    if not action_hints and not getattr(parent_route, "target", None):
        return parent_route

    workflow_related = {"workflow_edit", "workflow_build", "workflow_query"}
    if _task_mentions_workflow(task_desc):
        filtered_hints = action_hints
        target = getattr(parent_route, "target", "general")
    else:
        filtered_hints = [hint for hint in action_hints if hint not in workflow_related]
        target = "general" if getattr(parent_route, "target", "") == "workflow" else getattr(parent_route, "target", "general")

    if (
        filtered_hints == action_hints
        and target == getattr(parent_route, "target", None)
    ):
        return parent_route

    try:
        return parent_route.model_copy(
            update={
                "target": target,
                "action_hints": filtered_hints,
            }
        )
    except Exception:
        return parent_route


def _prepend_autonomy_announcement(session: Any, content: str) -> str:
    if getattr(session, "parent_id", None) is not None:
        return content
    announcement = build_autonomy_announcement(getattr(session, "autonomy_resolution", None))
    if not announcement:
        return content
    if not content:
        return announcement
    if content.startswith(announcement):
        return content
    return f"{announcement}\n\n{content}"


def _fallback_split_task(task: str) -> list[str]:
    text = str(task or "").strip()
    if not text:
        return []
    parts = [
        part.strip(" \t\r\n,.;:")
        for part in re.split(r"\s+\band\b\s+", text, flags=re.IGNORECASE)
        if part.strip(" \t\r\n,.;:")
    ]
    return parts if len(parts) > 1 else [text]


def _workflow_id_for_session(session: Any) -> str:
    msg = getattr(session, "msg", None)
    metadata = getattr(msg, "metadata", None) if msg is not None else None
    workflow_id = str(metadata.get("workflow_id") or "") if isinstance(metadata, dict) else ""
    if workflow_id:
        return workflow_id
    ctx = getattr(session, "context", None)
    proj = getattr(ctx, "project", None) if ctx else None
    linked = getattr(proj, "linked_workflow_ids", None) if proj else None
    if linked:
        workflow_id = str(linked[-1] or "").strip()
        if workflow_id:
            return workflow_id
    return "_scratch"


def _resolve_session_model_override(concierge: Any, session: Any) -> str | None:
    tier_resolver = getattr(concierge, "_tier_resolver", None)
    return tier_resolver.resolve_model(_determine_stage(session)) if tier_resolver else None


def _planned_subtasks(session: Any) -> list[str]:
    triage = getattr(session, "triage", None)
    if triage is not None and getattr(triage, "subtasks", None):
        return [
            str(task).strip()
            for task in getattr(triage, "subtasks", [])
            if str(task).strip()
        ]
    task_context = getattr(session, "task_context", None)
    if isinstance(task_context, dict):
        return [
            str(task).strip()
            for task in task_context.get("subtasks", [])
            if str(task).strip()
        ]
    return []


def _significant_terms(text: str, *, limit: int | None = None) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[a-z0-9][a-z0-9._/-]*", text.lower()):
        token = raw.strip("._/-")
        if len(token) < 4 or token.isdigit() or token in _GOAL_REVIEW_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if limit is not None and len(terms) >= limit:
            break
    return terms


def _collect_followup_signals(text: str, *, limit: int = 2) -> list[str]:
    if not str(text or "").strip():
        return []
    signals: list[str] = []
    pending_items = [
        item.strip()
        for item in _PENDING_CHECKBOX_RE.findall(text)
        if str(item).strip()
    ]
    if pending_items:
        preview = "; ".join(pending_items[:2])
        signals.append(f"contains pending checklist items ({preview})")
    for pattern, label in _OPEN_LOOP_REVIEW_PATTERNS:
        if pattern.search(text):
            signals.append(label)
        if len(signals) >= limit:
            break
    deduped: list[str] = []
    seen: set[str] = set()
    for signal in signals:
        if signal in seen:
            continue
        seen.add(signal)
        deduped.append(signal)
        if len(deduped) >= limit:
            break
    return deduped


def _collect_synthesis_uncertainties(child_results: dict[str, Any], manager: Any) -> list[str]:
    uncertainties: list[str] = []
    for child_id, result in child_results.items():
        child = manager.get(child_id)
        label = getattr(child, "task", child_id)
        content = str(getattr(result, "content", "") or "").strip()
        error = str(getattr(result, "error", "") or "").strip()
        if error:
            uncertainties.append(f"- {label}: {error}")
            continue
        for signal in _collect_followup_signals(content, limit=1):
            uncertainties.append(f"- {label}: {signal}")
    return uncertainties


def _deterministic_synthesis_gap_review(
    session: Any,
    child_results: dict[str, Any],
    manager: Any,
) -> _SynthesisGapReview:
    if not child_results:
        return _SynthesisGapReview(hard_gap_reason="no child sessions produced usable output")
    missing_output: list[str] = []
    errored: list[str] = []
    child_labels: list[str] = []
    combined_evidence_parts: list[str] = []
    for child_id, result in child_results.items():
        child = manager.get(child_id)
        label = getattr(child, "task", child_id)
        child_labels.append(label)
        content = str(getattr(result, "content", "") or "").strip()
        error = str(getattr(result, "error", "") or "").strip()
        if error:
            errored.append(f"{label}: {error}")
        elif not content:
            missing_output.append(label)
        else:
            combined_evidence_parts.append(f"{label}\n{content}")
    if errored:
        return _SynthesisGapReview(
            hard_gap_reason="one or more child sessions failed: " + "; ".join(errored[:3]),
        )
    if missing_output:
        return _SynthesisGapReview(
            hard_gap_reason="one or more child sessions produced no content: "
            + ", ".join(missing_output[:3]),
        )
    expected_subtasks = _planned_subtasks(session)
    if expected_subtasks:
        missing_subtasks = [task for task in expected_subtasks if task not in child_labels]
        if missing_subtasks:
            return _SynthesisGapReview(
                hard_gap_reason="planned subtasks were not completed: "
                + ", ".join(missing_subtasks[:3]),
            )
    ambiguous_reasons: list[str] = []
    uncertainties = _collect_synthesis_uncertainties(child_results, manager)
    if uncertainties:
        trimmed = [item.removeprefix("- ").strip() for item in uncertainties[:3]]
        ambiguous_reasons.append(
            "child results still show unresolved follow-up work: " + "; ".join(trimmed),
        )
    triage = getattr(session, "triage", None)
    goal_text = "\n".join(
        part for part in [
            str(getattr(session, "task", "") or "").strip(),
            str(getattr(triage, "goal", "") or "").strip(),
            str(getattr(triage, "deliverable", "") or "").strip(),
        ]
        if part
    )
    goal_terms = _significant_terms(goal_text, limit=6)
    if len(goal_terms) < 3:
        return _SynthesisGapReview(
            ambiguous_gap_reason="; ".join(ambiguous_reasons[:2]) if ambiguous_reasons else None,
        )
    evidence_terms = set(_significant_terms("\n\n".join(combined_evidence_parts)))
    missing_goal_terms = [term for term in goal_terms if term not in evidence_terms]
    if len(missing_goal_terms) >= max(2, len(goal_terms) // 2):
        ambiguous_reasons.append(
            "combined child results do not clearly cover the original goal: keyword coverage is missing for "
            + ", ".join(missing_goal_terms[:3]),
        )
    return _SynthesisGapReview(
        ambiguous_gap_reason="; ".join(ambiguous_reasons[:2]) if ambiguous_reasons else None,
    )


def _find_synthesis_gap_reason(session: Any, child_results: dict[str, Any], manager: Any) -> str | None:
    review = _deterministic_synthesis_gap_review(session, child_results, manager)
    return review.hard_gap_reason or review.ambiguous_gap_reason


def _parse_synthesis_review_response(content: str) -> tuple[str | None, str]:
    text = str(content or "").strip()
    if not text:
        return None, ""
    candidate = text
    if candidate.startswith("```"):
        first_newline = candidate.find("\n")
        if first_newline != -1:
            candidate = candidate[first_newline + 1 :]
        if candidate.endswith("```"):
            candidate = candidate[:-3]
        candidate = candidate.strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            return None, text
        try:
            payload = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None, text
    if not isinstance(payload, dict):
        return None, text
    decision = str(payload.get("decision", "") or "").strip().lower()
    reason = str(payload.get("reason", "") or "").strip()
    if decision not in {"accept", "remediate"}:
        return None, text
    return decision, reason or text


def _extract_json_candidate(text: str) -> Any | None:
    candidate = str(text or "").strip()
    if not candidate:
        return None
    if candidate.startswith("```"):
        first_newline = candidate.find("\n")
        if first_newline != -1:
            candidate = candidate[first_newline + 1 :]
        if candidate.endswith("```"):
            candidate = candidate[:-3]
        candidate = candidate.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start_obj = candidate.find("{")
        end_obj = candidate.rfind("}")
        if start_obj != -1 and end_obj > start_obj:
            try:
                return json.loads(candidate[start_obj : end_obj + 1])
            except json.JSONDecodeError:
                pass
        start_list = candidate.find("[")
        end_list = candidate.rfind("]")
        if start_list != -1 and end_list > start_list:
            try:
                return json.loads(candidate[start_list : end_list + 1])
            except json.JSONDecodeError:
                pass
    return None


def _normalize_subtask_items(tasks: list[Any], *, fallback_task: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tasks:
        item = str(raw or "").strip()
        if not item:
            continue
        item = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", item).strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
        if len(normalized) >= 8:
            break
    return normalized or ([fallback_task] if fallback_task else [])


def _parse_subtask_decomposition_response(content: str, *, fallback_task: str) -> list[str] | None:
    payload = _extract_json_candidate(content)
    raw_tasks: list[Any] | None = None
    if isinstance(payload, dict):
        candidate = payload.get("subtasks")
        if isinstance(candidate, list):
            raw_tasks = candidate
    elif isinstance(payload, list):
        raw_tasks = payload

    if raw_tasks is None:
        bullets = [
            re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
            for line in str(content or "").splitlines()
            if re.match(r"^\s*(?:[-*]|\d+[.)])\s+", line)
        ]
        if bullets:
            raw_tasks = bullets

    if raw_tasks is None:
        return None
    return _normalize_subtask_items(raw_tasks, fallback_task=fallback_task)


def _cancel_event(session: Any) -> Any | None:
    msg = getattr(session, "msg", None)
    metadata = getattr(msg, "metadata", None) if msg is not None else None
    if isinstance(metadata, dict):
        event = metadata.get("cancel_event")
        if hasattr(event, "is_set"):
            return event
    task_context = getattr(session, "task_context", None)
    if isinstance(task_context, dict):
        event = task_context.get("cancel_event")
        if hasattr(event, "is_set"):
            return event
    return None


def _cancel_requested(session: Any) -> bool:
    event = _cancel_event(session)
    return bool(event is not None and event.is_set())


def _mark_session_cancelled(
    manager: Any,
    session: Any,
    *,
    start: float,
    content: str = "",
    child_results: dict[str, Any] | None = None,
) -> None:
    from .session import SessionResult as _SR

    try:
        manager.cancel_tree(session.root_id)
    except Exception:
        logger.debug("Failed to cancel session tree %s", getattr(session, "root_id", None), exc_info=True)

    current = manager.get(session.id)
    if current is None:
        return
    manager.set_result(
        session.id,
        _SR(
            content=content,
            error="cancelled",
            metadata={
                "completion_status": "interrupted" if content else "cancelled",
            },
            duration_ms=(time.monotonic() - start) * 1000,
            child_results=child_results or {},
        ),
    )


def _build_prompt(session: Any) -> str:
    parts: list[str] = []
    triage = getattr(session, "triage", None)
    autonomy_resolution = getattr(session, "autonomy_resolution", None)
    if autonomy_resolution is not None:
        parts.append(
            "Autonomy: "
            f"{getattr(autonomy_resolution, 'effective_level', 'balanced')}"
            f" ({getattr(autonomy_resolution, 'source', 'fallback')})"
        )
    if triage:
        if getattr(triage, "goal", None):
            parts.append(f"User goal: {triage.goal}")
        if getattr(triage, "deliverable", None):
            parts.append(f"Expected deliverable: {triage.deliverable}")

    ctx = getattr(session, "context", None)
    if ctx and getattr(ctx, "domain", None):
        parts.append(f"Domain: {ctx.domain}")

    task_context = getattr(session, "task_context", None)
    if isinstance(task_context, dict):
        previous_result = str(task_context.get("previous_result", "") or "").strip()
        if previous_result:
            parts.append(f"Previous step result:\n{previous_result[:4000]}")
        synthesis_context = str(task_context.get("synthesis_context", "") or "").strip()
        if synthesis_context:
            parts.append(f"Parent synthesis context:\n{synthesis_context[:4000]}")
        remediation_reason = str(task_context.get("remediation_reason", "") or "").strip()
        if remediation_reason:
            parts.append(f"Remediation reason: {remediation_reason}")

    msg = getattr(session, "msg", None)
    metadata = getattr(msg, "metadata", None) if msg is not None else None
    if isinstance(metadata, dict):
        recent_turns = metadata.get("autonomy_recent_turns")
        if isinstance(recent_turns, list):
            cleaned_turns = [str(item or "").strip() for item in recent_turns if str(item or "").strip()]
            if cleaned_turns:
                parts.append("Recent task turns:\n" + "\n".join(cleaned_turns[:4]))
        task_snapshot = str(metadata.get("autonomy_task_snapshot", "") or "").strip()
        if task_snapshot:
            parts.append(f"Task snapshot:\n{task_snapshot[:2000]}")
        repo_snapshot = str(metadata.get("autonomy_repo_snapshot", "") or "").strip()
        if repo_snapshot:
            parts.append(f"Repo snapshot:\n{repo_snapshot[:2000]}")
        memory_context = str(metadata.get("memory_context", "") or "").strip()
        if memory_context:
            parts.append(f"Relevant memory:\n{memory_context[:4000]}")

        domain_expertise = str(metadata.get("domain_expertise", "") or "").strip()
        if domain_expertise:
            parts.append(f"Relevant domain expertise:\n{domain_expertise[:4000]}")

        auto_read = metadata.get("auto_read_content")
        if isinstance(auto_read, dict) and auto_read:
            snippets: list[str] = []
            for path, content in list(auto_read.items())[:3]:
                text = str(content or "").strip()
                if text:
                    snippets.append(f"[{path}]\n{text[:2000]}")
            if snippets:
                parts.append("Relevant file content:\n" + "\n\n".join(snippets))

    return "\n".join(parts) if parts else ""


def _determine_stage(session: Any) -> str:
    """Map session triage/mode to a concierge stage name for tier resolution."""
    triage = getattr(session, "triage", None)
    route = getattr(triage, "route", None) if triage else None
    intent = getattr(triage, "intent", "ask") if triage else "ask"

    msg = getattr(session, "msg", None)
    metadata = getattr(msg, "metadata", None) if msg is not None else None
    mode = _request_mode(metadata)

    action_hints = getattr(route, "action_hints", []) if route else []
    route_target = getattr(route, "target", "") if route else ""
    workflow_query_only = (
        route_target == "workflow"
        and "workflow_query" in action_hints
        and set(action_hints) <= {"workflow_query"}
    )

    if (
        mode == "build"
        or "workflow_edit" in action_hints
        or "workflow_build" in action_hints
        or (route_target == "workflow" and not workflow_query_only)
    ):
        return "workflow_build"
    if intent == "plan" or mode == "plan":
        return "conversation_plan"
    if mode == "debug":
        return "conversation_debug"
    if route_target == "file":
        return "file_review"
    if "experience_lookup" in action_hints:
        return "experience_fallback"
    if route_target in ("run", "memory", "web") and intent == "agent":
        return "direct_task"
    return "conversation"


def _extract_chat_params(
    session: Any, system_prompt: str, model_override: str | None = None,
) -> dict[str, Any]:
    """Extract parameters for ``ChatManager.send_message_with_tools()``."""
    msg = getattr(session, "msg", None)
    metadata = getattr(msg, "metadata", None) if msg is not None else None
    if not isinstance(metadata, dict):
        metadata = {}
    triage = getattr(session, "triage", None)
    route = getattr(triage, "route", None)

    workflow_id = _workflow_id_for_session(session)

    message: str = getattr(msg, "text", "") or getattr(session, "task", "") or "Hello"

    history: list[dict[str, str]] = list(metadata.get("request_history", None) or [])
    if not history:
        ctx = getattr(session, "context", None)
        task_obj = getattr(ctx, "task", None) if ctx else None
        if task_obj:
            for turn in (getattr(task_obj, "turns", None) or [])[-4:]:
                if turn.role in ("user", "assistant") and turn.content:
                    history.append({"role": turn.role, "content": turn.content})

    mode = _request_mode(metadata)
    surface: str = getattr(msg, "surface", None) or "server"
    cancel_event = _cancel_event(session)
    if cancel_event is not None and not hasattr(cancel_event, "is_set"):
        cancel_event = None
    thread_id: str | None = (
        str(metadata.get("thread_id") or getattr(msg, "session_id", "") or "").strip() or None
    )
    client_graph_revision: str | None = (
        str(metadata.get("client_graph_revision") or "").strip() or None
    )
    debug_context = str(metadata.get("debug_context") or "")
    mentions = metadata.get("mentions")
    if not isinstance(mentions, list):
        mentions = None
    surface_context = metadata.get("surface_context")
    if not isinstance(surface_context, dict):
        surface_context = None
    ctx = getattr(session, "context", None)
    project = getattr(ctx, "project", None) if ctx else None
    memory_project_id = str(getattr(project, "project_id", "") or "").strip() or None

    required_action_hints: list[str] = []
    if route is not None:
        for hint in getattr(route, "action_hints", None) or []:
            hint_text = str(hint or "").strip()
            if hint_text and hint_text not in required_action_hints:
                required_action_hints.append(hint_text)

    # Furnace/run-control turns should be operational (session lifecycle API calls),
    # not purely narrative summaries.
    normalized_message = (message or "").lower()
    if (
        "run_control" in required_action_hints
        and any(token in normalized_message for token in ("furnace", "distill", "recipe session", "start session"))
    ):
        run_control_instruction = (
            "For this turn, execute run control operations instead of prose-only output. "
            "If starting a furnace session, call local API endpoints in order: "
            "POST /api/furnace/sessions -> POST /api/furnace/sessions/{id}/sources "
            "-> POST /api/furnace/sessions/{id}/start when source inputs are provided. "
            "Your final response must include session_id and status."
        )
    else:
        run_control_instruction = ""

    allow_mutation_tool = metadata.get("allow_mutation_tool")
    if not isinstance(allow_mutation_tool, bool):
        route_target = getattr(route, "target", "") if route is not None else ""
        workflow_query_only = (
            route_target == "workflow"
            and "workflow_query" in required_action_hints
            and set(required_action_hints) <= {"workflow_query"}
        )
        allow_mutation_tool = (
            "workflow_edit" in required_action_hints
            or "workflow_build" in required_action_hints
            or mode == "build"
            or (route_target == "workflow" and not workflow_query_only)
        )

    stream_channel_id = str(metadata.get("stream_channel_id") or "").strip() or None
    attachment_prompt_context = str(metadata.get("attachment_prompt_context") or "").strip()
    autonomy_resolution = getattr(session, "autonomy_resolution", None)
    prompt_context = system_prompt
    if attachment_prompt_context:
        prompt_context = (
            f"{system_prompt}\n\n{attachment_prompt_context}"
            if system_prompt
            else attachment_prompt_context
        )
    if run_control_instruction:
        prompt_context = (
            f"{prompt_context}\n\n{run_control_instruction}"
            if prompt_context
            else run_control_instruction
        )

    result = {
        "workflow_id": workflow_id,
        "message": message,
        "history": history,
        "thread_id": thread_id,
        "client_graph_revision": client_graph_revision,
        "mode": mode,
        "cancel_event": cancel_event,
        "debug_context": debug_context,
        "prompt_context": prompt_context,
        "mentions": mentions,
        "surface_context": surface_context,
        "allow_mutation_tool": allow_mutation_tool,
        "surface": surface,
        "extra_system_instructions": attachment_prompt_context,
        "required_action_hints": required_action_hints,
        "stream_channel_id": stream_channel_id,
        "memory_project_id": memory_project_id,
        "include_memory_kernel_context": not bool(str(metadata.get("memory_context") or "").strip()),
        "max_tool_turns": autonomy_max_tool_turns(
            getattr(autonomy_resolution, "effective_level", None),
        ),
        "autonomy_resolution": autonomy_resolution,
    }
    if model_override:
        result["model_override"] = model_override
        audit = result.get("audit_metadata") or {}
        audit["concierge_model_override"] = model_override
        result["audit_metadata"] = audit
    return result


def _extract_text_chat_context_params(
    session: Any,
    *,
    model_override: str | None = None,
) -> dict[str, Any]:
    base = _extract_chat_params(session, "", model_override=model_override)
    return {
        "workflow_id": base.get("workflow_id", "_scratch"),
        "thread_id": base.get("thread_id"),
        "client_graph_revision": base.get("client_graph_revision"),
        "cancel_event": base.get("cancel_event"),
        "surface_context": base.get("surface_context"),
        "surface": base.get("surface"),
        "extra_system_instructions": base.get("extra_system_instructions", ""),
        "memory_project_id": base.get("memory_project_id"),
        "include_memory_kernel_context": base.get("include_memory_kernel_context", True),
        "model_override": base.get("model_override"),
        "autonomy_resolution": base.get("autonomy_resolution"),
    }


def _request_mode(metadata: Any) -> str:
    """Return the effective request mode, preserving explicit build overrides."""
    if not isinstance(metadata, dict):
        return "agent"
    requested_mode = str(metadata.get("requested_mode") or "").strip().lower()
    if requested_mode in {"build", "mutate"}:
        return requested_mode
    mode = str(metadata.get("mode") or "").strip()
    return mode or "agent"


# ---------------------------------------------------------------------------
# Tier 0 — Instant
# ---------------------------------------------------------------------------

class InstantExecutor:
    """Social turns and pending follow-ups. No async work, no children."""

    def __init__(self, concierge: Any) -> None:
        self._concierge = concierge

    async def execute(
        self,
        session: Any,
        manager: Any,
    ) -> AsyncIterator[ChatStreamEvent]:
        start = time.monotonic()
        if _cancel_requested(session):
            _mark_session_cancelled(manager, session, start=start)
            yield _interrupted_event()
            return

        triage = getattr(session, "triage", None)

        if triage and getattr(triage, "is_social", False) and getattr(triage, "social_response", None):
            manager.update_state(session.id, "running")
            manager.update_state(session.id, "completed")
            from .session import SessionResult as _SR
            manager.set_result(session.id, _SR(content=triage.social_response))
            yield _complete_event(triage.social_response)
            return

        if self._has_pending(session):
            response = self._resolve_pending(session)
            if response is not None:
                manager.update_state(session.id, "running")
                manager.update_state(session.id, "completed")
                from .session import SessionResult as _SR
                manager.set_result(session.id, _SR(content=response))
                yield _complete_event(response)
                return

        manager.update_state(session.id, "running")
        manager.update_state(session.id, "completed")
        from .session import SessionResult as _SR
        fallback = "Got it."
        manager.set_result(session.id, _SR(content=fallback))
        yield _complete_event(fallback)

    # -- pending action helpers --

    @staticmethod
    def _has_pending(session: Any) -> bool:
        ctx = getattr(session, "context", None)
        if ctx is None:
            return False
        project = getattr(ctx, "project", None)
        return project is not None and getattr(project, "pending_action", None) is not None

    @staticmethod
    def _resolve_pending(session: Any) -> str | None:
        ctx = session.context
        project = ctx.project
        pending: PendingAction = project.pending_action  # type: ignore[assignment]
        reply = getattr(session, "msg", None)
        if reply is None:
            return None
        text = reply.text.strip().lower()

        if pending.kind == "confirm":
            if text in {"no", "n", "cancel", "stop"}:
                return f"{format_prefix(project.label)} Cancelled."
            if text in {"yes", "y", "ok", "sure", "go", "proceed"}:
                return None  # caller should replay original
        elif pending.kind == "clarify":
            if pending.options and text.isdigit():
                idx = int(text) - 1
                if 0 <= idx < len(pending.options):
                    return pending.options[idx]
        return None


# ---------------------------------------------------------------------------
# Tier 1 — Single-Shot
# ---------------------------------------------------------------------------

class SingleShotExecutor:
    """Q&A, lookups, simple tasks — one handler call, no children."""

    def __init__(self, concierge: Any) -> None:
        self._concierge = concierge

    async def execute(
        self,
        session: Any,
        manager: Any,
    ) -> AsyncIterator[ChatStreamEvent]:
        start = time.monotonic()
        if _cancel_requested(session):
            _mark_session_cancelled(manager, session, start=start)
            yield _interrupted_event()
            return

        manager.update_state(session.id, "running")

        model_override = _resolve_session_model_override(self._concierge, session)

        system_prompt = _build_prompt(session)
        chat_params = _extract_chat_params(session, system_prompt, model_override=model_override)

        final_content = ""
        token_usage: dict[str, int] = {}
        saw_terminal = False
        interrupted = False

        try:
            async for event in self._concierge.chat_manager.send_message_with_tools(
                **chat_params,
            ):
                if isinstance(event, ChatInterruptedEvent):
                    saw_terminal = True
                    interrupted = True
                    final_content = _prepend_autonomy_announcement(session, event.content)
                    token_usage = dict(event.token_usage)
                    yield event.model_copy(update={"content": final_content})
                    break
                if isinstance(event, ChatCompleteEvent):
                    saw_terminal = True
                    final_content = _prepend_autonomy_announcement(session, event.content)
                    token_usage = dict(event.token_usage)
                    yield event.model_copy(update={"content": final_content})
                    continue
                yield event
        except Exception as exc:
            from dan.providers import LLMAuthenticationError
            if isinstance(exc, LLMAuthenticationError):
                final_content = str(exc)
                saw_terminal = True
                yield ChatCompleteEvent(
                    message_id=uuid.uuid4().hex[:12],
                    content=final_content,
                    token_usage={},
                    context_window=0,
                    graph_revision="",
                )
            else:
                logger.exception("SingleShot execution failed for session %s", session.id)

        if interrupted or _cancel_requested(session):
            _mark_session_cancelled(manager, session, start=start, content=final_content)
            if not saw_terminal:
                yield _interrupted_event(final_content)
            return

        if not saw_terminal:
            synthesized = _synthesize_missing_terminal_event(session)
            final_content = _prepend_autonomy_announcement(session, synthesized.content)
            yield synthesized.model_copy(update={"content": final_content})

        from .session import SessionResult as _SR
        manager.set_result(
            session.id,
            _SR(
                content=final_content,
                metadata={
                    "memory_recorded_by_chat_manager": True,
                    "completion_status": "completed",
                },
                token_usage=token_usage,
                duration_ms=(time.monotonic() - start) * 1000,
            ),
        )
        manager.update_state(session.id, "completed")


# ---------------------------------------------------------------------------
# Tier 2 — Recursive Multi-Step
# ---------------------------------------------------------------------------

class MultiStepExecutor:
    """Complex tasks: decompose into child sessions or execute directly."""

    def __init__(self, concierge: Any, dispatcher: Any) -> None:
        self._concierge = concierge
        self._dispatcher = dispatcher

    async def execute(
        self,
        session: Any,
        manager: Any,
    ) -> AsyncIterator[ChatStreamEvent]:
        start = time.monotonic()
        if _cancel_requested(session):
            _mark_session_cancelled(manager, session, start=start)
            yield _interrupted_event()
            return

        manager.update_state(session.id, "running")

        if self._should_decompose(session, manager):
            async for event in self._decompose_and_execute(session, manager, start):
                yield event
        else:
            async for event in self._execute_directly(session, manager, start):
                yield event

    # -- decision -----------------------------------------------------------

    @staticmethod
    def _should_decompose(session: Any, manager: Any) -> bool:
        tier = getattr(session, "tier", None)
        if tier is not None and tier != 2:
            return False

        # Workflow build/edit turns must stay on the direct chat-manager path so
        # the graph generation / mutation fast paths can run against the target
        # workflow instead of decomposing into generic child sessions.
        if _determine_stage(session) == "workflow_build":
            return False

        triage = getattr(session, "triage", None)
        subtasks = getattr(triage, "subtasks", None) if triage else None
        task_ctx = getattr(session, "task_context", {}) or {}
        task = str(getattr(session, "task", "") or "").strip()

        explicit_subtasks = list(subtasks or task_ctx.get("subtasks", []) or [])
        autonomy_level = _autonomy_level(session)
        if explicit_subtasks:
            if autonomy_level == "aggressive":
                has_subtasks = len(explicit_subtasks) >= 1
            else:
                has_subtasks = len(explicit_subtasks) > 1
        else:
            inferred_subtasks = _fallback_split_task(task)
            has_subtasks = len(inferred_subtasks) > 1
        if not has_subtasks:
            return False

        if not manager.can_spawn_child(session.id):
            return False

        max_depth = getattr(session, "max_depth", 3)
        depth = getattr(session, "depth", 0)
        if depth >= max_depth:
            return False

        subtask_count = len(explicit_subtasks)
        if autonomy_level == "careful":
            return subtask_count > 2
        return True

    # -- direct execution (leaf) -------------------------------------------

    async def _execute_directly(
        self, session: Any, manager: Any, start: float
    ) -> AsyncIterator[ChatStreamEvent]:
        if _cancel_requested(session):
            _mark_session_cancelled(manager, session, start=start)
            yield _interrupted_event()
            return

        model_override = _resolve_session_model_override(self._concierge, session)

        system_prompt = _build_prompt(session)
        chat_params = _extract_chat_params(session, system_prompt, model_override=model_override)

        final_content = ""
        token_usage: dict[str, int] = {}
        saw_terminal = False
        interrupted = False

        try:
            async for event in self._concierge.chat_manager.send_message_with_tools(
                **chat_params,
            ):
                if isinstance(event, ChatInterruptedEvent):
                    saw_terminal = True
                    interrupted = True
                    final_content = _prepend_autonomy_announcement(session, event.content)
                    token_usage = dict(event.token_usage)
                    yield event.model_copy(update={"content": final_content})
                    break
                if isinstance(event, ChatCompleteEvent):
                    saw_terminal = True
                    final_content = _prepend_autonomy_announcement(session, event.content)
                    token_usage = dict(event.token_usage)
                    yield event.model_copy(update={"content": final_content})
                    continue
                yield event
        except Exception as exc:
            from dan.providers import LLMAuthenticationError
            if isinstance(exc, LLMAuthenticationError):
                final_content = str(exc)
                saw_terminal = True
                yield ChatCompleteEvent(
                    message_id=uuid.uuid4().hex[:12],
                    content=final_content,
                    token_usage={},
                    context_window=0,
                    graph_revision="",
                )
            else:
                logger.exception("MultiStep direct execution failed for session %s", session.id)

        if interrupted or _cancel_requested(session):
            _mark_session_cancelled(manager, session, start=start, content=final_content)
            if not saw_terminal:
                yield _interrupted_event(final_content)
            return

        if not saw_terminal:
            synthesized = _synthesize_missing_terminal_event(session)
            final_content = _prepend_autonomy_announcement(session, synthesized.content)
            yield synthesized.model_copy(update={"content": final_content})

        from .session import SessionResult as _SR
        manager.set_result(
            session.id,
            _SR(
                content=final_content,
                metadata={
                    "memory_recorded_by_chat_manager": True,
                    "completion_status": "completed",
                },
                token_usage=token_usage,
                duration_ms=(time.monotonic() - start) * 1000,
            ),
        )
        manager.update_state(session.id, "completed")

    # -- decompose and execute ---------------------------------------------

    async def _decompose_and_execute(
        self, session: Any, manager: Any, start: float
    ) -> AsyncIterator[ChatStreamEvent]:
        triage = getattr(session, "triage", None)
        subtasks: list[str] = []
        decomposition_usage: dict[str, int] = {}

        if triage and getattr(triage, "subtasks", None):
            subtasks = list(triage.subtasks)
        else:
            task_ctx = getattr(session, "task_context", {}) or {}
            subtasks = list(task_ctx.get("subtasks", []))

        if not subtasks:
            subtasks, decomposition_usage = await self._plan_decomposition(session)
        if len(subtasks) <= 1:
            async for event in self._execute_directly(session, manager, start):
                yield event
            return

        if _cancel_requested(session):
            _mark_session_cancelled(manager, session, start=start)
            yield _interrupted_event()
            return

        manager.update_state(session.id, "waiting")

        child_execution = getattr(session, "child_execution", "serial")
        if child_execution == "mixed":
            logger.debug(
                "Session %s requested mixed child execution; using serial fallback until hybrid scheduling lands",
                session.id,
            )
            child_execution = "serial"
        child_results: dict[str, Any] = {}
        children: list[Any] = []
        interrupted = False
        interrupted_emitted = False
        synthesis_review_usage: dict[str, int] = {}
        synthesis_usage: dict[str, int] = {}

        try:
            if child_execution == "parallel":
                for task_desc in subtasks:
                    if _cancel_requested(session) or not manager.can_spawn_child(session.id):
                        break
                    children.append(self._build_child_session(session, manager, task_desc))

                if _cancel_requested(session):
                    interrupted = True
                elif not children:
                    async for event in self._execute_directly(session, manager, start):
                        yield event
                    return
                else:
                    async for event in self._run_children_parallel(children, session, manager):
                        if isinstance(event, ChatInterruptedEvent):
                            interrupted = True
                            interrupted_emitted = True
                            yield event
                            break
                        yield event
            else:
                previous_child = None
                for task_desc in subtasks:
                    if _cancel_requested(session):
                        interrupted = True
                        break
                    if not manager.can_spawn_child(session.id):
                        break

                    child = self._build_child_session(session, manager, task_desc)
                    children.append(child)
                    if previous_child is not None:
                        prev = manager.get(previous_child.id)
                        if (
                            prev
                            and getattr(prev, "result", None)
                            and getattr(prev.result, "content", None)
                        ):
                            task_ctx = getattr(child, "task_context", {}) or {}
                            task_ctx["previous_result"] = prev.result.content
                            child.task_context = task_ctx
                    previous_child = child

                    progress_event = self._child_progress_event(session, child)
                    if progress_event is not None:
                        yield progress_event

                    try:
                        async for event in self._run_child(child, manager):
                            if isinstance(event, ChatInterruptedEvent):
                                interrupted = True
                                interrupted_emitted = True
                                yield event
                                break
                            yield event
                    except Exception:
                        logger.exception("Child execution failed for session %s", session.id)

                    child_state = manager.get(child.id)
                    if child_state and getattr(child_state, "result", None):
                        child_results[child.id] = child_state.result
                    if interrupted:
                        break

            for child in children:
                c = manager.get(child.id)
                if c and getattr(c, "result", None):
                    child_results[child.id] = c.result
        except Exception:
            logger.exception("Child execution failed for session %s", session.id)

        if interrupted or _cancel_requested(session):
            _mark_session_cancelled(
                manager,
                session,
                start=start,
                child_results=child_results,
            )
            if not interrupted_emitted:
                yield _interrupted_event()
            return

        all_failed = not child_results
        if all_failed and children:
            logger.warning(
                "All %d children of session %s failed; falling back to direct execution",
                len(children), session.id,
            )
            manager.update_state(session.id, "running")
            async for event in self._execute_directly(session, manager, start):
                yield event
            return

        manager.update_state(session.id, "running")
        if _autonomy_level(session) == "aggressive":
            gap_reason, synthesis_review_usage = await self._review_synthesis_gap_reason(
                session,
                child_results,
                manager,
            )
            if gap_reason and manager.can_spawn_child(session.id):
                remediation_child = self._build_child_session(
                    session,
                    manager,
                    "Review the child results against the original goal and finish any remaining work.",
                )
                remediation_child.task_context = {
                    **(getattr(remediation_child, "task_context", {}) or {}),
                    "remediation_reason": (
                        f"review combined child results against the original goal: {gap_reason}"
                        if gap_reason
                        else "review combined child results against the original goal"
                    ),
                    "synthesis_context": self._synthesize(session, child_results, manager),
                }
                progress_event = self._child_progress_event(session, remediation_child)
                if progress_event is not None:
                    yield progress_event
                async for event in self._run_child(remediation_child, manager):
                    if isinstance(event, ChatInterruptedEvent):
                        _mark_session_cancelled(
                            manager,
                            session,
                            start=start,
                            child_results=child_results,
                        )
                        yield event
                        return
                    yield event
                remediation_state = manager.get(remediation_child.id)
                if remediation_state and getattr(remediation_state, "result", None):
                    child_results[remediation_child.id] = remediation_state.result

        synthesized_content, synthesis_usage = await self._maybe_llm_synthesize(
            session,
            child_results,
            manager,
        )
        final_content = synthesized_content or self._synthesize(session, child_results, manager)
        final_content = _prepend_autonomy_announcement(session, final_content)

        from .session import SessionResult as _SR
        total_tokens: dict[str, int] = {}
        for r in child_results.values():
            for k, v in (getattr(r, "token_usage", {}) or {}).items():
                total_tokens[k] = total_tokens.get(k, 0) + v
        for k, v in synthesis_review_usage.items():
            total_tokens[k] = total_tokens.get(k, 0) + v
        for k, v in synthesis_usage.items():
            total_tokens[k] = total_tokens.get(k, 0) + v
        for k, v in decomposition_usage.items():
            total_tokens[k] = total_tokens.get(k, 0) + v

        manager.set_result(
            session.id,
            _SR(
                content=final_content,
                metadata={"completion_status": "completed"},
                token_usage=total_tokens,
                duration_ms=(time.monotonic() - start) * 1000,
                child_results=child_results,
            ),
        )
        manager.update_state(session.id, "completed")
        yield _complete_event(final_content, token_usage=total_tokens)

    # -- child tier heuristic ----------------------------------------------

    @staticmethod
    def _estimate_child_tier(task_desc: str) -> int:
        first_word = task_desc.strip().split()[0].lower() if task_desc.strip() else ""
        if first_word in _SINGLE_ACTION_VERBS:
            return 1

        lower = task_desc.lower()
        if len(_fallback_split_task(lower)) > 1:
            return 2
        for kw in _MULTI_ACTION_KEYWORDS:
            if kw in lower:
                return 2
        return 1

    async def _cheap_llm_complete(
        self,
        session: Any,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str | None, dict[str, int]]:
        chat_manager = getattr(self._concierge, "chat_manager", None)
        providers = getattr(chat_manager, "_providers", None) if chat_manager is not None else None
        if providers is None or not hasattr(providers, "resolve"):
            return None, {}
        model = _resolve_session_model_override(self._concierge, session) or getattr(chat_manager, "_chat_model", "")
        if not model:
            return None, {}
        try:
            provider = providers.resolve(model)
            result = await provider.complete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=model,
                temperature=0.0,
            )
            text = str(getattr(result, "text", "") or "").strip()
            usage = dict(getattr(result, "usage", {}) or {})
            return text or None, usage
        except Exception:
            logger.debug("Lightweight concierge LLM helper failed", exc_info=True)
            return None, {}

    # -- plan decomposition -------------------------------------------------

    async def _plan_decomposition(self, session: Any) -> tuple[list[str], dict[str, int]]:
        task = str(getattr(session, "task", "") or "").strip()
        if not task:
            return [], {}

        fallback = _fallback_split_task(task)
        triage = getattr(session, "triage", None)
        goal = str(getattr(triage, "goal", "") or "").strip()
        deliverable = str(getattr(triage, "deliverable", "") or "").strip()
        prompt_parts = [f"Task: {task}"]
        if goal and goal != task:
            prompt_parts.append(f"Goal: {goal}")
        if deliverable and deliverable not in {task, goal}:
            prompt_parts.append(f"Deliverable: {deliverable}")

        content, usage = await self._cheap_llm_complete(
            session,
            system_prompt=(
                "Decompose the user's task into the smallest useful ordered subtasks. "
                "Prefer 2-6 concrete steps. Do not invent new goals. "
                "If the task should stay as one step, return a single-item list. "
                'Return JSON only in one of these forms: {"subtasks":["step 1","step 2"]} or ["step 1","step 2"].'
            ),
            user_prompt="\n".join(prompt_parts),
        )
        if content:
            parsed = _parse_subtask_decomposition_response(content, fallback_task=task)
            if parsed:
                return parsed, usage
        return fallback, {}

    @staticmethod
    def _should_llm_synthesize(session: Any, child_results: dict[str, Any]) -> bool:
        if not child_results:
            return False
        level = _autonomy_level(session)
        if level == "aggressive":
            return True
        if level == "balanced":
            return len(child_results) >= 3
        return False

    # -- running children --------------------------------------------------

    def _build_child_session(self, session: Any, manager: Any, task_desc: str) -> Any:
        child_tier = self._estimate_child_tier(task_desc)
        parent_triage = getattr(session, "triage", None)
        child_route = _filter_child_route(getattr(parent_triage, "route", None), task_desc)
        child = manager.create_child(
            parent_id=session.id,
            task=task_desc,
            tier=child_tier,
            task_context={
                "parent_task": getattr(session, "task", ""),
                "parent_context": getattr(session, "task_context", {}),
            },
        )

        parent_msg = getattr(session, "msg", None)
        if parent_msg is not None:
            child_metadata = {
                **parent_msg.metadata,
                "tiered_parent_session_id": session.id,
                "tiered_root_session_id": session.root_id,
                "tiered_child_task": task_desc,
            }
            if child_route is not None:
                child_metadata["route_target"] = getattr(child_route, "target", child_metadata.get("route_target"))
                child_metadata["allow_mutation_tool"] = bool(
                    getattr(child_route, "target", "") == "workflow"
                    and any(
                        hint in {"workflow_edit", "workflow_build"}
                        for hint in getattr(child_route, "action_hints", None) or []
                    )
                )
            child.msg = parent_msg.model_copy(
                update={
                    "text": task_desc,
                    "metadata": child_metadata,
                }
            )
        else:
            child.msg = SurfaceMessage(surface="", external_id="", text=task_desc)

        child.context = session.context

        try:
            from .triage import TriageResult

            child.triage = TriageResult(
                tier=child_tier,
                intent=getattr(parent_triage, "intent", "ask"),
                route=child_route,
                confidence=getattr(parent_triage, "confidence", 0.6),
                goal=task_desc,
                deliverable=task_desc,
                execution_order="parallel",
            )
        except Exception:
            child.triage = None

        return child

    def _child_progress_event(self, session: Any, child: Any) -> ChatCompleteEvent | None:
        parent_msg = getattr(session, "msg", None)
        make_phase_event = getattr(self._concierge, "_make_phase_event", None)
        if callable(make_phase_event) and parent_msg is not None:
            event = make_phase_event(
                parent_msg.external_id,
                "execution",
                "Executing",
                getattr(child, "task", ""),
                force=True,
            )
            if event is not None:
                return event
        return _progress_event(getattr(child, "task", ""))

    async def _run_children_parallel(
        self, children: list[Any], session: Any, manager: Any
    ) -> AsyncIterator[ChatStreamEvent]:
        output_queue: asyncio.Queue[tuple[str, ChatStreamEvent | None]] = asyncio.Queue()

        async def run_one(child: Any) -> None:
            try:
                progress_event = self._child_progress_event(session, child)
                if progress_event is not None:
                    await output_queue.put((child.id, progress_event))
                async for event in self._run_child(child, manager):
                    await output_queue.put((child.id, event))
            except Exception as exc:
                logger.warning("Child session %s failed: %s", child.id, exc)
                manager.update_state(child.id, "failed")
            finally:
                await output_queue.put((child.id, None))

        tasks = [asyncio.create_task(run_one(child)) for child in children]
        try:
            active = {child.id for child in children}
            while active:
                if _cancel_requested(session):
                    yield _interrupted_event()
                    return
                try:
                    child_id, event = await asyncio.wait_for(output_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                if event is None:
                    active.discard(child_id)
                    continue
                yield event
                if isinstance(event, ChatInterruptedEvent):
                    return
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_child(
        self, child: Any, manager: Any
    ) -> AsyncIterator[ChatStreamEvent]:
        tier = int(getattr(child, "tier", 1))
        executor: TierExecutor | None = None
        if self._dispatcher is not None:
            executor = getattr(self._dispatcher, "_executors", {}).get(tier)
        if executor is None:
            if tier == 2:
                executor = MultiStepExecutor(self._concierge, self._dispatcher)
            elif tier == 1:
                executor = SingleShotExecutor(self._concierge)
            else:
                executor = InstantExecutor(self._concierge)

        try:
            async for event in executor.execute(child, manager):
                yield event
        finally:
            on_complete = getattr(self._dispatcher, "_on_any_session_complete", None)
            child_session = manager.get(child.id) or child
            raw_state = getattr(child_session, "state", "")
            state = raw_state.value if hasattr(raw_state, "value") else str(raw_state)
            if callable(on_complete) and state in {"completed", "failed", "cancelled"}:
                await on_complete(child_session)

    # -- synthesis ---------------------------------------------------------

    async def _review_synthesis_gap_reason(
        self,
        session: Any,
        child_results: dict[str, Any],
        manager: Any,
    ) -> tuple[str | None, dict[str, int]]:
        review = _deterministic_synthesis_gap_review(session, child_results, manager)
        if review.hard_gap_reason:
            return review.hard_gap_reason, {}
        if not review.ambiguous_gap_reason:
            return None, {}
        llm_gap_reason, token_usage = await self._review_synthesis_gap_reason_with_llm(
            session,
            child_results,
            manager,
            review.ambiguous_gap_reason,
        )
        return llm_gap_reason, token_usage

    async def _review_synthesis_gap_reason_with_llm(
        self,
        session: Any,
        child_results: dict[str, Any],
        manager: Any,
        ambiguous_reason: str,
    ) -> tuple[str | None, dict[str, int]]:
        chat_manager = getattr(self._concierge, "chat_manager", None)
        if chat_manager is None or not hasattr(chat_manager, "send_message"):
            return None, {}
        triage = getattr(session, "triage", None)
        planned_subtasks = _planned_subtasks(session)
        child_sections: list[str] = []
        for child_id, result in child_results.items():
            child = manager.get(child_id)
            label = getattr(child, "task", child_id)
            content = str(getattr(result, "content", "") or "").strip()
            error = str(getattr(result, "error", "") or "").strip()
            body = error or content or "(no output)"
            if len(body) > 1200:
                body = body[:1200].rstrip() + "..."
            child_sections.append(f"### {label}\n{body}")
        review_payload_parts = [
            f"Original goal: {str(getattr(triage, 'goal', '') or getattr(session, 'task', '') or '').strip()}",
            f"Deliverable: {str(getattr(triage, 'deliverable', '') or '').strip()}",
        ]
        if planned_subtasks:
            review_payload_parts.append("Planned subtasks:\n- " + "\n- ".join(planned_subtasks[:8]))
        review_payload_parts.append(f"Deterministic ambiguous signal: {ambiguous_reason}")
        review_payload_parts.append("Child outputs:\n" + "\n\n".join(child_sections[:6]))
        review_payload = "\n\n".join(part for part in review_payload_parts if part.strip())
        final_content = ""
        token_usage: dict[str, int] = {}
        review_chat_params = _extract_text_chat_context_params(
            session,
            model_override=_resolve_session_model_override(self._concierge, session),
        )
        try:
            async for event in chat_manager.send_message(
                workflow_id=review_chat_params["workflow_id"],
                message=review_payload,
                history=[],
                thread_id=review_chat_params["thread_id"],
                client_graph_revision=review_chat_params["client_graph_revision"],
                mode="agent",
                cancel_event=review_chat_params["cancel_event"],
                prompt_context=(
                    "You are doing an internal execution-quality review for the parent session. "
                    "Judge whether the child results fully satisfy the original goal.\n\n"
                    "Use the deterministic signal only as a hint. If the apparent issue is just wording "
                    "or keyword mismatch, choose accept. If there is a real missing requirement or unfinished work, "
                    "choose remediate.\n\n"
                    "Return JSON only in this exact schema:\n"
                    '{"decision":"accept"|"remediate","reason":"one sentence"}'
                ),
                surface_context=review_chat_params["surface_context"],
                surface=review_chat_params["surface"],
                extra_system_instructions=review_chat_params["extra_system_instructions"],
                memory_project_id=review_chat_params["memory_project_id"],
                include_memory_kernel_context=review_chat_params["include_memory_kernel_context"],
                model_override=review_chat_params["model_override"],
                autonomy_resolution=review_chat_params["autonomy_resolution"],
                record_summary=False,
            ):
                if isinstance(event, ChatErrorEvent):
                    logger.warning("Synthesis review fallback failed: %s", event.error)
                    return None, {}
                if hasattr(event, "accumulated"):
                    final_content = str(getattr(event, "accumulated", "") or final_content)
                if isinstance(event, (ChatCompleteEvent, ChatInterruptedEvent)):
                    final_content = str(getattr(event, "content", "") or final_content)
                    token_usage = dict(getattr(event, "token_usage", {}) or {})
                    break
        except Exception:
            logger.warning("Synthesis review fallback raised unexpectedly", exc_info=True)
            return None, {}
        decision, reason = _parse_synthesis_review_response(final_content)
        if decision == "remediate":
            return reason or ambiguous_reason, token_usage
        if decision != "accept":
            logger.warning("Synthesis review fallback returned unparseable content: %r", final_content[:400])
        return None, token_usage

    async def _maybe_llm_synthesize(
        self,
        session: Any,
        child_results: dict[str, Any],
        manager: Any,
    ) -> tuple[str | None, dict[str, int]]:
        if not self._should_llm_synthesize(session, child_results):
            return None, {}

        triage = getattr(session, "triage", None)
        child_sections: list[str] = []
        for child_id, result in child_results.items():
            child = manager.get(child_id)
            label = getattr(child, "task", child_id)
            content = str(getattr(result, "content", "") or "").strip()
            error = str(getattr(result, "error", "") or "").strip()
            body = error or content or "(no output)"
            if len(body) > 1200:
                body = body[:1200].rstrip() + "..."
            child_sections.append(f"## {label}\n{body}")

        user_prompt_parts = [
            f"Original task: {str(getattr(session, 'task', '') or '').strip()}",
            f"Goal: {str(getattr(triage, 'goal', '') or '').strip()}",
            f"Deliverable: {str(getattr(triage, 'deliverable', '') or '').strip()}",
        ]
        planned_subtasks = _planned_subtasks(session)
        if planned_subtasks:
            user_prompt_parts.append("Planned subtasks:\n- " + "\n- ".join(planned_subtasks[:8]))
        uncertainties = _collect_synthesis_uncertainties(child_results, manager)
        if uncertainties:
            user_prompt_parts.append("Known uncertainties:\n" + "\n".join(uncertainties[:6]))
        user_prompt_parts.append("Child outputs:\n" + "\n\n".join(child_sections[:6]))

        content, usage = await self._cheap_llm_complete(
            session,
            system_prompt=(
                "You are synthesizing child task outputs into one coherent final response for the user. "
                "Merge overlapping information, preserve important specifics, and do not invent facts. "
                "If any child output is incomplete, blocked, or uncertain, mention that clearly in the final response. "
                "Return plain text only."
            ),
            user_prompt="\n\n".join(part for part in user_prompt_parts if part.strip()),
        )
        return (content, usage) if content else (None, {})

    @staticmethod
    def _synthesize(session: Any, child_results: dict[str, Any], manager: Any) -> str:
        parts: list[str] = []
        for child_id, result in child_results.items():
            child = manager.get(child_id)
            content = getattr(result, "content", None) if result else None
            if child and content:
                task_label = getattr(child, "task", child_id)
                parts.append(f"## {task_label}\n\n{content}")
        if not parts:
            return "Task completed but no content was produced."
        combined = "\n\n".join(parts)
        uncertainties = _collect_synthesis_uncertainties(child_results, manager)
        if _autonomy_level(session) == "careful" and uncertainties:
            combined = f"{combined}\n\n## Uncertainties\n" + "\n".join(uncertainties)
        return combined
