from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol

if TYPE_CHECKING:
    from .session import Session, SessionManager, SessionResult, SessionTier
    from .triage import TriageResult

from dan.chat_events import (
    ChatCompleteEvent,
    ChatErrorEvent,
    ChatInterruptedEvent,
    ChatStreamEvent,
)
from dan.agent_runtime.synthesis import (
    SynthesisGapReview,
    collect_followup_signals as _collect_followup_signals_impl,
    collect_synthesis_uncertainties as _collect_synthesis_uncertainties_impl,
    deterministic_synthesis_gap_review as _deterministic_synthesis_gap_review_impl,
    extract_json_candidate as _extract_json_candidate_impl,
    find_synthesis_gap_reason as _find_synthesis_gap_reason_impl,
    normalize_subtask_items as _normalize_subtask_items_impl,
    parse_subtask_decomposition_response as _parse_subtask_decomposition_response_impl,
    parse_synthesis_review_response as _parse_synthesis_review_response_impl,
    planned_subtasks as _planned_subtasks_impl,
    significant_terms as _significant_terms_impl,
)
from dan.agent_runtime.orchestration import (
    build_mixed_execution_groups as _build_mixed_execution_groups_impl,
    estimate_child_tier as _estimate_child_tier_impl,
    execute_child_execution_policy as _execute_child_execution_policy_impl,
    merge_child_result_usage as _merge_child_result_usage_impl,
    merge_token_usage as _merge_token_usage_impl,
    mixed_subtask_depends_on_prior as _mixed_subtask_depends_on_prior_impl,
    plan_child_session as _plan_child_session_impl,
    should_llm_synthesize as _should_llm_synthesize_impl,
    synthesize_child_results as _synthesize_child_results_impl,
)
from dan.agent_runtime.synthesis_runtime import (
    cheap_llm_complete as _cheap_llm_complete_impl,
    maybe_llm_synthesize as _maybe_llm_synthesize_impl,
    plan_decomposition as _plan_decomposition_impl,
    resolve_default_llm_model as _resolve_default_llm_model_impl,
    review_synthesis_gap_reason_with_llm as _review_synthesis_gap_reason_with_llm_impl,
)

from .autonomy import autonomy_max_tool_turns, build_autonomy_announcement
from .identity import format_prefix
from .models import IntentCategory, PendingAction, ResolvedContext, RouteDecision, SurfaceMessage, TaskTurn
from .pending_actions import resolve_pending_reply

logger = logging.getLogger(__name__)

_MISSING_TERMINAL_EVENT_FALLBACK = (
    "The response stream ended before a final answer was produced. "
    "Please ask me to continue from the latest progress."
)
_WORKFLOW_ACTIVITY_RE = re.compile(
    r"\b(?:workflow|graph|node|edge|mutation|plan_graph_mutations|watchlist|ticker|equity)\b",
    re.IGNORECASE,
)
_WORKFLOW_FOLLOWUP_RE = re.compile(
    r"\b(?:build|rebuild|delete|remove|retry|again|fix|update|test|run|work(?:ing)?|apply|approve|confirm)\b",
    re.IGNORECASE,
)
_WORKFLOW_APPROVAL_RE = re.compile(
    r"\b(?:apply|approve|confirm)\b",
    re.IGNORECASE,
)
_WORKFLOW_RUN_RE = re.compile(
    r"\b(?:run|test|execute|launch|start)\b",
    re.IGNORECASE,
)
_FURNACE_CONTROL_RE = re.compile(
    r"\b(?:furnace|distill(?:ation)?|recipe session|start session)\b",
    re.IGNORECASE,
)
_ANAPHORA_RE = re.compile(r"\b(?:it|that|this|those|them)\b", re.IGNORECASE)
_STAGE_PROMPT_OVERLAYS: dict[str, str] = {
    "conversation": (
        "## Concierge stage: conversation\n"
        "Act as a concise generalist. Answer directly, avoid unnecessary orchestration, "
        "and do not assume workflow editing unless the request is explicit."
    ),
    "conversation_plan": (
        "## Concierge stage: conversation_plan\n"
        "Act as an orchestrator. Clarify scope, decompose carefully, state assumptions, "
        "and decide whether the request should remain conversational or become workflow work."
    ),
    "conversation_debug": (
        "## Concierge stage: conversation_debug\n"
        "Act as a debugger. Focus on failures, validation gaps, and the shortest reliable path to diagnosis."
    ),
    "workflow_build": (
        "## Concierge stage: workflow_build\n"
        "Act as a workflow authoring surface. Keep workflow identity explicit and route the task through "
        "the standard DAN workflow-edit/build path instead of inventing a separate local workflow doctrine."
    ),
    "file_review": (
        "## Concierge stage: file_review\n"
        "Act as an execution-focused reviewer. Inspect the file/task directly and summarize concrete findings."
    ),
    "direct_task": (
        "## Concierge stage: direct_task\n"
        "Act as a practical executor. Prefer concrete actions and concise results over meta-planning."
    ),
    "experience_fallback": (
        "## Concierge stage: experience_fallback\n"
        "Act as a lightweight retrieval/synthesis surface. Reuse relevant prior work without over-committing to mutation."
    ),
}
_CHILD_METADATA_KEYS = frozenset({
    "workflow_id",
    "mode",
    "requested_mode",
    "client_graph_revision",
    "debug_context",
    "mentions",
    "selected_path",
    "attachment_prompt_context",
    "surface_context",
    "scenario_id",
    "scenario_confidence",
    "memory_context",
    "domain_expertise",
    "autonomy_recent_turns",
    "autonomy_task_snapshot",
    "autonomy_repo_snapshot",
    "auto_read_content",
    "autonomy_preference",
    "autonomy_resolution",
    "cancel_event",
})
_CHILD_HANDOFF_RECENT_TURN_LIMIT = 4
_CHILD_HANDOFF_FILE_LIMIT = 6
_CHILD_HANDOFF_SNIPPET_LIMIT = 3


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


def _history_has_workflow_activity(history: list[dict[str, str]]) -> bool:
    for turn in history[-6:]:
        content = str(turn.get("content") or "").strip()
        if content and _WORKFLOW_ACTIVITY_RE.search(content):
            return True
    return False


def _should_keep_mutation_tool_for_followup(
    message: str,
    history: list[dict[str, str]],
) -> bool:
    text = str(message or "").strip()
    if not text or not _history_has_workflow_activity(history):
        return False
    if _WORKFLOW_ACTIVITY_RE.search(text):
        return True
    if _WORKFLOW_APPROVAL_RE.search(text):
        return True
    return bool(_WORKFLOW_FOLLOWUP_RE.search(text) and _ANAPHORA_RE.search(text))


def _should_promote_ask_mode_for_workflow_followup(
    message: str,
    history: list[dict[str, str]],
) -> bool:
    text = str(message or "").strip()
    if not text or not _history_has_workflow_activity(history):
        return False
    if _WORKFLOW_APPROVAL_RE.search(text):
        return True
    if not _WORKFLOW_FOLLOWUP_RE.search(text):
        return False
    return bool(
        _WORKFLOW_ACTIVITY_RE.search(text)
        or _ANAPHORA_RE.search(text)
    )


def _should_prefer_workflow_run_followup(
    message: str,
    history: list[dict[str, str]],
) -> bool:
    text = str(message or "").strip()
    if not text or not _history_has_workflow_activity(history):
        return False
    if _FURNACE_CONTROL_RE.search(text):
        return False
    if _WORKFLOW_ACTIVITY_RE.search(text) and _WORKFLOW_RUN_RE.search(text):
        return True
    return bool(_WORKFLOW_RUN_RE.search(text) and _ANAPHORA_RE.search(text))


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
    return _planned_subtasks_impl(session)


def _significant_terms(text: str, *, limit: int | None = None) -> list[str]:
    return _significant_terms_impl(text, limit=limit)


def _collect_followup_signals(text: str, *, limit: int = 2) -> list[str]:
    return _collect_followup_signals_impl(text, limit=limit)


def _collect_synthesis_uncertainties(child_results: dict[str, Any], manager: Any) -> list[str]:
    return _collect_synthesis_uncertainties_impl(child_results, manager)


def _deterministic_synthesis_gap_review(
    session: Any,
    child_results: dict[str, Any],
    manager: Any,
) -> SynthesisGapReview:
    return _deterministic_synthesis_gap_review_impl(session, child_results, manager)


def _find_synthesis_gap_reason(session: Any, child_results: dict[str, Any], manager: Any) -> str | None:
    return _find_synthesis_gap_reason_impl(session, child_results, manager)


def _parse_synthesis_review_response(content: str) -> tuple[str | None, str]:
    return _parse_synthesis_review_response_impl(content)


def _extract_json_candidate(text: str) -> Any | None:
    return _extract_json_candidate_impl(text)


def _normalize_subtask_items(tasks: list[Any], *, fallback_task: str) -> list[str]:
    return _normalize_subtask_items_impl(tasks, fallback_task=fallback_task)


def _parse_subtask_decomposition_response(content: str, *, fallback_task: str) -> list[str] | None:
    return _parse_subtask_decomposition_response_impl(
        content,
        fallback_task=fallback_task,
    )


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


def _trim_text(value: Any, *, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:limit]


def _string_list(values: Any, *, limit: int | None = None) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned = [str(item or "").strip() for item in values if str(item or "").strip()]
    if limit is not None:
        return cleaned[:limit]
    return cleaned


def _append_unique_string(target: list[str], value: Any, *, limit: int) -> None:
    text = str(value or "").strip()
    if not text or text in target:
        return
    target.append(text)
    if len(target) > limit:
        del target[limit:]


def _build_child_metadata_seed(parent_metadata: dict[str, Any]) -> dict[str, Any]:
    child_metadata: dict[str, Any] = {}
    for key in _CHILD_METADATA_KEYS:
        if key not in parent_metadata:
            continue
        value = parent_metadata[key]
        if key == "cancel_event":
            child_metadata[key] = value
            continue
        try:
            child_metadata[key] = copy.deepcopy(value)
        except Exception:
            logger.debug("Failed to copy child metadata key %s", key, exc_info=True)
    return child_metadata


def _extract_child_file_refs(parent_metadata: dict[str, Any], context: Any) -> list[str]:
    file_refs: list[str] = []
    surface_context = parent_metadata.get("surface_context")
    if isinstance(surface_context, dict):
        mentioned_files = surface_context.get("mentioned_files")
        if isinstance(mentioned_files, list):
            for item in mentioned_files:
                if isinstance(item, dict):
                    _append_unique_string(file_refs, item.get("path"), limit=_CHILD_HANDOFF_FILE_LIMIT)
                else:
                    _append_unique_string(file_refs, item, limit=_CHILD_HANDOFF_FILE_LIMIT)
        import_neighbors = surface_context.get("import_neighbors")
        if isinstance(import_neighbors, list):
            for item in import_neighbors:
                _append_unique_string(file_refs, item, limit=_CHILD_HANDOFF_FILE_LIMIT)
    auto_read_content = parent_metadata.get("auto_read_content")
    if isinstance(auto_read_content, dict):
        for path in auto_read_content:
            _append_unique_string(file_refs, path, limit=_CHILD_HANDOFF_FILE_LIMIT)
    task_obj = getattr(context, "task", None) if context is not None else None
    artifacts = getattr(task_obj, "artifacts", None)
    if isinstance(artifacts, dict):
        for item in artifacts.values():
            _append_unique_string(file_refs, item, limit=_CHILD_HANDOFF_FILE_LIMIT)
    _append_unique_string(
        file_refs,
        parent_metadata.get("selected_path"),
        limit=_CHILD_HANDOFF_FILE_LIMIT,
    )
    return file_refs[:_CHILD_HANDOFF_FILE_LIMIT]


def _build_child_handoff_context(session: Any, parent_metadata: dict[str, Any]) -> dict[str, Any]:
    handoff_context: dict[str, Any] = {}
    recent_turns = _string_list(
        parent_metadata.get("autonomy_recent_turns"),
        limit=_CHILD_HANDOFF_RECENT_TURN_LIMIT,
    )
    if recent_turns:
        handoff_context["recent_turns"] = recent_turns
    for target_key, source_key, limit in (
        ("task_snapshot", "autonomy_task_snapshot", 2000),
        ("repo_snapshot", "autonomy_repo_snapshot", 2000),
        ("memory_context", "memory_context", 4000),
        ("domain_expertise", "domain_expertise", 4000),
    ):
        trimmed = _trim_text(parent_metadata.get(source_key), limit=limit)
        if trimmed:
            handoff_context[target_key] = trimmed

    file_refs = _extract_child_file_refs(parent_metadata, getattr(session, "context", None))
    if file_refs:
        handoff_context["file_refs"] = file_refs

    auto_read_content = parent_metadata.get("auto_read_content")
    if isinstance(auto_read_content, dict):
        snippets: dict[str, str] = {}
        for path, content in list(auto_read_content.items())[:_CHILD_HANDOFF_SNIPPET_LIMIT]:
            path_text = str(path or "").strip()
            content_text = _trim_text(content, limit=2000)
            if path_text and content_text:
                snippets[path_text] = content_text
        if snippets:
            handoff_context["file_snippets"] = snippets

    return handoff_context


def _copy_context_for_child(parent_context: Any) -> Any:
    if parent_context is None:
        return None

    copied_context: Any | None = None
    if hasattr(parent_context, "model_copy"):
        try:
            copied_context = parent_context.model_copy(deep=True)
        except Exception:
            logger.debug("model_copy(deep=True) failed for child context", exc_info=True)
    if copied_context is None:
        try:
            copied_context = copy.deepcopy(parent_context)
        except Exception:
            logger.debug("deepcopy failed for child context; building fallback snapshot", exc_info=True)
            copied_context = SimpleNamespace(
                project=copy.deepcopy(getattr(parent_context, "project", None)),
                task=copy.deepcopy(getattr(parent_context, "task", None)),
                domain=getattr(parent_context, "domain", None),
                confidence=getattr(parent_context, "confidence", None),
                is_new_project=False,
                is_new_task=False,
            )

    task_obj = getattr(copied_context, "task", None)
    if task_obj is not None and hasattr(task_obj, "turns"):
        try:
            task_obj.turns = []
        except Exception:
            logger.debug("Failed to clear child task turns", exc_info=True)
    project_obj = getattr(copied_context, "project", None)
    if project_obj is not None and hasattr(project_obj, "pending_action"):
        try:
            project_obj.pending_action = None
        except Exception:
            logger.debug("Failed to clear child pending action", exc_info=True)
    return copied_context


def _render_handoff_prompt_block(handoff: Any) -> str:
    if not isinstance(handoff, dict):
        return ""

    lines: list[str] = []
    goal = handoff.get("goal")
    if isinstance(goal, dict):
        parent_task = _trim_text(goal.get("parent_task"), limit=400)
        delegated_task = _trim_text(goal.get("delegated_task"), limit=400)
        route_target = _trim_text(goal.get("route_target"), limit=120)
        action_hints = _string_list(goal.get("action_hints"))
        if parent_task:
            lines.append(f"Parent task: {parent_task}")
        if delegated_task:
            lines.append(f"Delegated task: {delegated_task}")
        if route_target:
            lines.append(f"Route target: {route_target}")
        if action_hints:
            lines.append("Action hints: " + ", ".join(action_hints[:6]))

    context = handoff.get("context")
    if isinstance(context, dict):
        recent_turns = _string_list(context.get("recent_turns"), limit=_CHILD_HANDOFF_RECENT_TURN_LIMIT)
        if recent_turns:
            lines.append("Recent task turns:")
            lines.extend(recent_turns)
        task_snapshot = _trim_text(context.get("task_snapshot"), limit=2000)
        if task_snapshot:
            lines.append(f"Task snapshot:\n{task_snapshot}")
        repo_snapshot = _trim_text(context.get("repo_snapshot"), limit=2000)
        if repo_snapshot:
            lines.append(f"Repo snapshot:\n{repo_snapshot}")
        memory_context = _trim_text(context.get("memory_context"), limit=4000)
        if memory_context:
            lines.append(f"Relevant memory:\n{memory_context}")
        domain_expertise = _trim_text(context.get("domain_expertise"), limit=4000)
        if domain_expertise:
            lines.append(f"Relevant domain expertise:\n{domain_expertise}")
        file_refs = _string_list(context.get("file_refs"), limit=_CHILD_HANDOFF_FILE_LIMIT)
        if file_refs:
            lines.append("Related files: " + ", ".join(file_refs))
        file_snippets = context.get("file_snippets")
        if isinstance(file_snippets, dict) and file_snippets:
            snippets: list[str] = []
            for path, content in list(file_snippets.items())[:_CHILD_HANDOFF_SNIPPET_LIMIT]:
                text = _trim_text(content, limit=2000)
                if text:
                    snippets.append(f"[{path}]\n{text}")
            if snippets:
                lines.append("Relevant file content:\n" + "\n\n".join(snippets))

    constraints = handoff.get("constraints")
    if isinstance(constraints, dict):
        constraint_bits: list[str] = []
        if constraints.get("read_only_parent_context") is True:
            constraint_bits.append("parent context is read-only")
        if constraints.get("copy_on_write_metadata") is True:
            constraint_bits.append("child metadata is isolated")
        if constraints.get("allow_mutation_tool") is not None:
            constraint_bits.append(
                "allow_mutation_tool="
                + ("true" if constraints.get("allow_mutation_tool") else "false")
            )
        if constraint_bits:
            lines.append("Constraints: " + ", ".join(constraint_bits))

    return_channel = handoff.get("return_channel")
    if isinstance(return_channel, dict):
        return_kind = _trim_text(return_channel.get("kind"), limit=120)
        parent_session_id = _trim_text(return_channel.get("parent_session_id"), limit=120)
        if return_kind or parent_session_id:
            pieces = [piece for piece in [return_kind, parent_session_id] if piece]
            lines.append("Return channel: " + " -> ".join(pieces))

    if not lines:
        return ""
    return "Child handoff:\n" + "\n".join(lines)


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
    stage = _determine_stage(session)
    parts.append(f"Concierge stage: {stage}")
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
    handoff_prompt = ""
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
        handoff_prompt = _render_handoff_prompt_block(task_context.get("handoff"))
        if handoff_prompt:
            parts.append(handoff_prompt)

    msg = getattr(session, "msg", None)
    metadata = getattr(msg, "metadata", None) if msg is not None else None
    if isinstance(metadata, dict) and not handoff_prompt:
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


def _stage_prompt_overlay(stage: str) -> str:
    return _STAGE_PROMPT_OVERLAYS.get(stage, _STAGE_PROMPT_OVERLAYS["conversation"])


def _stage_prompt_overlay_id(stage: str) -> str:
    return f"concierge_stage:{stage}"


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
    stage = _determine_stage(session)
    stage_overlay = _stage_prompt_overlay(stage)
    stage_overlay_id = _stage_prompt_overlay_id(stage)

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
    if mode == "ask" and _should_promote_ask_mode_for_workflow_followup(message, history):
        mode = "agent"
    surface: str = getattr(msg, "surface", None) or "server"
    cancel_event = _cancel_event(session)
    if cancel_event is not None and not hasattr(cancel_event, "is_set"):
        cancel_event = None
    thread_id: str | None = (
        str(getattr(msg, "session_id", "") or metadata.get("thread_id") or "").strip() or None
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

    if _should_prefer_workflow_run_followup(message, history):
        required_action_hints = [
            hint
            for hint in required_action_hints
            if hint not in {"run_control", "workflow_edit"}
        ]
        if "workflow_run" not in required_action_hints:
            required_action_hints.append("workflow_run")

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
        if not allow_mutation_tool and _should_keep_mutation_tool_for_followup(message, history):
            allow_mutation_tool = True

    stream_channel_id = str(metadata.get("stream_channel_id") or "").strip() or None
    attachment_prompt_context = str(metadata.get("attachment_prompt_context") or "").strip()
    autonomy_resolution = getattr(session, "autonomy_resolution", None)
    prompt_context = system_prompt
    extra_system_sections = [stage_overlay]
    if attachment_prompt_context:
        extra_system_sections.append(attachment_prompt_context)
    if run_control_instruction:
        extra_system_sections.append(run_control_instruction)
    extra_system_instructions = "\n\n".join(
        section.strip()
        for section in extra_system_sections
        if section and section.strip()
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
        "extra_system_instructions": extra_system_instructions,
        "required_action_hints": required_action_hints,
        "stream_channel_id": stream_channel_id,
        "memory_project_id": memory_project_id,
        "include_memory_kernel_context": not bool(str(metadata.get("memory_context") or "").strip()),
        "max_tool_turns": autonomy_max_tool_turns(
            getattr(autonomy_resolution, "effective_level", None),
        ),
        "autonomy_resolution": autonomy_resolution,
    }
    audit = result.get("audit_metadata") or {}
    audit["concierge_stage"] = stage
    audit["concierge_prompt_overlay"] = stage_overlay_id
    audit["active_prompt_key"] = "prompts/runtime.unified_system"
    audit["session_tier"] = int(getattr(session, "tier", 1))
    audit["route_source"] = str(getattr(triage, "route_source", "") or "")
    audit["scenario_id"] = getattr(triage, "scenario_id", None)
    audit["scenario_confidence"] = getattr(triage, "scenario_confidence", None)
    result["audit_metadata"] = audit
    if model_override:
        result["model_override"] = model_override
        audit["concierge_model_override"] = model_override
        result["audit_metadata"] = audit
    return result


def _chat_result_metadata(
    chat_params: dict[str, Any],
    chat_manager: Any,
    *,
    include_memory_recorded: bool = True,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"completion_status": "completed"}
    if include_memory_recorded:
        metadata["memory_recorded_by_chat_manager"] = True
    audit = dict(chat_params.get("audit_metadata") or {})
    active_prompt_key = str(audit.get("active_prompt_key") or "").strip()
    if active_prompt_key:
        metadata["active_prompt_key"] = active_prompt_key
    if "active_prompt_version" in audit:
        metadata["active_prompt_version"] = audit.get("active_prompt_version")
    model_used = str(
        chat_params.get("model_override")
        or _resolve_default_llm_model_impl(chat_manager)
        or "",
    ).strip()
    if model_used:
        metadata["model_used"] = model_used
    return metadata


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
        resolution = resolve_pending_reply(pending, reply.text)

        if resolution.action == "cancel":
            return f"{format_prefix(project.label)} Cancelled."
        if resolution.action == "resume" and resolution.pending_kind == "clarify":
            return resolution.resolved_value or resolution.replay_text
        if resolution.action == "prompt_retry":
            return f"{format_prefix(project.label)} {resolution.response_text}"
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
                metadata=_chat_result_metadata(chat_params, self._concierge.chat_manager),
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
                metadata=_chat_result_metadata(chat_params, self._concierge.chat_manager),
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
        mixed_groups: list[list[str]] = []
        if child_execution == "mixed":
            mixed_groups = self._build_mixed_execution_groups(subtasks)
        child_results: dict[str, Any] = {}
        children: list[Any] = []
        interrupted = False
        interrupted_emitted = False
        synthesis_review_usage: dict[str, int] = {}
        synthesis_usage: dict[str, int] = {}

        try:
            execution_resolution = None

            def _build_execution_child(task_desc: str, previous_result: str | None) -> Any:
                child = self._build_child_session(session, manager, task_desc)
                if previous_result:
                    task_ctx = getattr(child, "task_context", {}) or {}
                    task_ctx["previous_result"] = previous_result
                    child.task_context = task_ctx
                return child

            async def _run_single_child(child: Any) -> AsyncIterator[ChatStreamEvent]:
                async for event in self._run_child(child, manager):
                    yield event

            async def _run_parallel_children(children_to_run: Any) -> AsyncIterator[ChatStreamEvent]:
                async for event in self._run_children_parallel(list(children_to_run), session, manager):
                    yield event

            def _child_result(child: Any) -> Any | None:
                child_state = manager.get(child.id)
                if child_state is None:
                    return None
                return getattr(child_state, "result", None)

            def _child_result_content(result: Any) -> str | None:
                return getattr(result, "content", None) if result is not None else None

            def _synthesize_results(current_child_results: Any) -> str:
                return self._synthesize(session, dict(current_child_results), manager)

            def _on_single_child_error(_child: Any, _exc: Exception) -> None:
                logger.exception("Child execution failed for session %s", session.id)

            async for item in _execute_child_execution_policy_impl(
                subtasks,
                child_execution=child_execution,
                build_child=_build_execution_child,
                run_child=_run_single_child,
                run_parallel_children=_run_parallel_children,
                child_progress_event=lambda child: self._child_progress_event(session, child),
                child_result=_child_result,
                child_result_content=_child_result_content,
                synthesize_results=_synthesize_results,
                is_cancelled=lambda: _cancel_requested(session),
                can_spawn_child=lambda: manager.can_spawn_child(session.id),
                mixed_groups=mixed_groups,
                on_single_child_error=_on_single_child_error,
            ):
                if isinstance(item, ChatStreamEvent):
                    yield item
                    continue
                execution_resolution = item

            if execution_resolution is None:
                raise RuntimeError("Child execution policy produced no resolution")

            children = list(execution_resolution.children)
            child_results = dict(execution_resolution.child_results)
            interrupted = execution_resolution.interrupted
            interrupted_emitted = execution_resolution.interrupted_emitted

            if not children and not interrupted and not _cancel_requested(session):
                async for event in self._execute_directly(session, manager, start):
                    yield event
                return
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
        total_tokens = _merge_token_usage_impl(
            _merge_child_result_usage_impl(child_results.values()),
            synthesis_review_usage,
            synthesis_usage,
            decomposition_usage,
        )

        manager.set_result(
            session.id,
            _SR(
                content=final_content,
                metadata=(
                    {
                        "completion_status": "completed",
                        "active_prompt_key": "prompts/runtime.unified_system",
                        "model_used": str(
                            _resolve_session_model_override(self._concierge, session)
                            or _resolve_default_llm_model_impl(self._concierge.chat_manager)
                            or ""
                        ).strip(),
                    }
                    if synthesized_content is not None else {"completion_status": "completed"}
                ),
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
        return _estimate_child_tier_impl(task_desc)

    @staticmethod
    def _mixed_subtask_depends_on_prior(task_desc: str) -> bool:
        return _mixed_subtask_depends_on_prior_impl(task_desc)

    @classmethod
    def _build_mixed_execution_groups(cls, subtasks: list[str]) -> list[list[str]]:
        return _build_mixed_execution_groups_impl(subtasks)

    async def _cheap_llm_complete(
        self,
        session: Any,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str | None, dict[str, int]]:
        return await _cheap_llm_complete_impl(
            self._concierge,
            session,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model_override=_resolve_session_model_override(self._concierge, session),
        )

    # -- plan decomposition -------------------------------------------------

    async def _plan_decomposition(self, session: Any) -> tuple[list[str], dict[str, int]]:
        return await _plan_decomposition_impl(
            self._concierge,
            session,
            model_override=_resolve_session_model_override(self._concierge, session),
        )

    @staticmethod
    def _should_llm_synthesize(session: Any, child_results: dict[str, Any]) -> bool:
        return _should_llm_synthesize_impl(
            autonomy_level=_autonomy_level(session),
            child_results_count=len(child_results),
        )

    # -- running children --------------------------------------------------

    def _build_child_session(self, session: Any, manager: Any, task_desc: str) -> Any:
        parent_triage = getattr(session, "triage", None)
        parent_msg = getattr(session, "msg", None)
        parent_route = getattr(parent_triage, "route", None)
        parent_metadata: dict[str, Any] = {}
        if parent_msg is not None:
            raw_parent_metadata = getattr(parent_msg, "metadata", None)
            if isinstance(raw_parent_metadata, dict):
                parent_metadata = raw_parent_metadata

        child_tier = self._estimate_child_tier(task_desc)
        child = manager.create_child(
            parent_id=session.id,
            task=task_desc,
            tier=child_tier,
            task_context={},
        )
        child_lane_id = f"tiered-child-{child.id}"
        child_plan = _plan_child_session_impl(
            task_desc=task_desc,
            parent_session_id=session.id,
            root_session_id=session.root_id,
            parent_task=getattr(session, "task", ""),
            parent_task_context=getattr(session, "task_context", {}) or {},
            has_parent_route=parent_route is not None,
            parent_route_target=getattr(parent_route, "target", None),
            parent_action_hints=getattr(parent_route, "action_hints", None) or [],
            parent_message_session_id=getattr(parent_msg, "session_id", None),
            parent_message_thread_id=parent_metadata.get("thread_id"),
            parent_handoff_context=_build_child_handoff_context(session, parent_metadata),
            child_session_id=child.id,
            child_thread_id=child_lane_id,
        )
        child_route = parent_route
        if parent_route is not None:
            planned_hints = list(child_plan.action_hints)
            current_hints = list(getattr(parent_route, "action_hints", None) or [])
            current_target = getattr(parent_route, "target", None)
            if planned_hints != current_hints or child_plan.route_target != current_target:
                try:
                    child_route = parent_route.model_copy(
                        update={
                            "target": child_plan.route_target,
                            "action_hints": planned_hints,
                        }
                    )
                except Exception:
                    child_route = parent_route

        child.tier = child_plan.child_tier
        child.task_context = child_plan.task_context

        if parent_msg is not None:
            child_metadata = _build_child_metadata_seed(parent_metadata)
            child_metadata = {
                **child_metadata,
                **child_plan.metadata_patch,
                "thread_id": child_lane_id,
            }
            child.msg = parent_msg.model_copy(
                deep=True,
                update={
                    "text": task_desc,
                    "session_id": child_lane_id,
                    "metadata": child_metadata,
                }
            )
        else:
            child.msg = SurfaceMessage(
                surface="",
                external_id="",
                text=task_desc,
                session_id=child_lane_id,
                metadata=dict(child_plan.metadata_patch),
            )

        child.context = _copy_context_for_child(session.context)

        try:
            from .triage import TriageResult

            child.triage = TriageResult(
                tier=child_plan.child_tier,
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
        review_chat_params = _extract_text_chat_context_params(
            session,
            model_override=_resolve_session_model_override(self._concierge, session),
        )
        return await _review_synthesis_gap_reason_with_llm_impl(
            self._concierge,
            session,
            child_results,
            manager,
            ambiguous_reason,
            review_chat_params=review_chat_params,
        )

    async def _maybe_llm_synthesize(
        self,
        session: Any,
        child_results: dict[str, Any],
        manager: Any,
    ) -> tuple[str | None, dict[str, int]]:
        if not self._should_llm_synthesize(session, child_results):
            return None, {}
        return await _maybe_llm_synthesize_impl(
            self._concierge,
            session,
            child_results,
            manager,
            model_override=_resolve_session_model_override(self._concierge, session),
        )

    @staticmethod
    def _synthesize(session: Any, child_results: dict[str, Any], manager: Any) -> str:
        child_outputs: list[tuple[str, str | None]] = []
        for child_id, result in child_results.items():
            child = manager.get(child_id)
            content = getattr(result, "content", None) if result else None
            if child and content:
                task_label = getattr(child, "task", child_id)
                child_outputs.append((task_label, content))
        uncertainties = _collect_synthesis_uncertainties(child_results, manager)
        return _synthesize_child_results_impl(
            child_outputs,
            include_uncertainties=_autonomy_level(session) == "careful",
            uncertainties=uncertainties,
        )
