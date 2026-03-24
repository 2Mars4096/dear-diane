from __future__ import annotations

import logging
import re
from typing import Any

from dan.agent_runtime.synthesis import (
    collect_synthesis_uncertainties,
    parse_subtask_decomposition_response,
    parse_synthesis_review_response,
    planned_subtasks,
)
from dan.chat_events import ChatCompleteEvent, ChatErrorEvent, ChatInterruptedEvent
from dan.llm_surface import complete_chat_surface, default_llm_model

logger = logging.getLogger(__name__)


def resolve_default_llm_model(chat_manager: Any) -> str | None:
    return default_llm_model(chat_manager)


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


def _pii_session_key_for_session(session: Any) -> str | None:
    msg = getattr(session, "msg", None)
    if msg is None:
        return None
    external_id = str(getattr(msg, "external_id", "") or "").strip()
    if external_id:
        return external_id
    session_id = str(getattr(msg, "session_id", "") or "").strip()
    if session_id:
        return session_id
    metadata = getattr(msg, "metadata", None)
    if isinstance(metadata, dict):
        thread_id = str(metadata.get("thread_id") or "").strip()
        if thread_id:
            return thread_id
    workflow_id = _workflow_id_for_session(session)
    return workflow_id or None


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


async def cheap_llm_complete(
    concierge: Any,
    session: Any,
    *,
    system_prompt: str,
    user_prompt: str,
    model_override: str | None = None,
) -> tuple[str | None, dict[str, int]]:
    chat_manager = getattr(concierge, "chat_manager", None)
    if chat_manager is None:
        return None, {}
    model = model_override or default_llm_model(chat_manager)
    if not model:
        return None, {}
    try:
        pii_session_key = _pii_session_key_for_session(session)
        tracker = getattr(concierge, "_resource_tracker", None) or getattr(chat_manager, "_resource_tracker", None)
        if tracker is not None:
            await tracker.wait_acquire("llm")
        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            result = await complete_chat_surface(
                chat_manager,
                messages=messages,
                model=model,
                temperature=0.0,
                pii_session_key=pii_session_key,
            )
        finally:
            if tracker is not None:
                await tracker.release("llm")
        text = str(getattr(result, "text", "") or "").strip()
        usage = dict(getattr(result, "usage", {}) or {})
        return text or None, usage
    except Exception:
        logger.debug("Lightweight concierge LLM helper failed", exc_info=True)
        return None, {}


async def plan_decomposition(
    concierge: Any,
    session: Any,
    *,
    model_override: str | None = None,
) -> tuple[list[str], dict[str, int]]:
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

    content, usage = await cheap_llm_complete(
        concierge,
        session,
        system_prompt=(
            "Decompose the user's task into the smallest useful ordered subtasks. "
            "Prefer 2-6 concrete steps. Do not invent new goals. "
            "If the task should stay as one step, return a single-item list. "
            'Return JSON only in one of these forms: {"subtasks":["step 1","step 2"]} or ["step 1","step 2"].'
        ),
        user_prompt="\n".join(prompt_parts),
        model_override=model_override,
    )
    if content:
        parsed = parse_subtask_decomposition_response(content, fallback_task=task)
        if parsed:
            return parsed, usage
    return fallback, {}


async def maybe_llm_synthesize(
    concierge: Any,
    session: Any,
    child_results: dict[str, Any],
    manager: Any,
    *,
    model_override: str | None = None,
) -> tuple[str | None, dict[str, int]]:
    if not child_results:
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
    subtasks = planned_subtasks(session)
    if subtasks:
        user_prompt_parts.append("Planned subtasks:\n- " + "\n- ".join(subtasks[:8]))
    uncertainties = collect_synthesis_uncertainties(child_results, manager)
    if uncertainties:
        user_prompt_parts.append("Known uncertainties:\n" + "\n".join(uncertainties[:6]))
    user_prompt_parts.append("Child outputs:\n" + "\n\n".join(child_sections[:6]))

    content, usage = await cheap_llm_complete(
        concierge,
        session,
        system_prompt=(
            "You are synthesizing child task outputs into one coherent final response for the user. "
            "Merge overlapping information, preserve important specifics, and do not invent facts. "
            "If any child output is incomplete, blocked, or uncertain, mention that clearly in the final response. "
            "Return plain text only."
        ),
        user_prompt="\n\n".join(part for part in user_prompt_parts if part.strip()),
        model_override=model_override,
    )
    return (content, usage) if content else (None, {})


async def review_synthesis_gap_reason_with_llm(
    concierge: Any,
    session: Any,
    child_results: dict[str, Any],
    manager: Any,
    ambiguous_reason: str,
    *,
    review_chat_params: dict[str, Any],
) -> tuple[str | None, dict[str, int]]:
    chat_manager = getattr(concierge, "chat_manager", None)
    if chat_manager is None or not hasattr(chat_manager, "send_message"):
        return None, {}
    triage = getattr(session, "triage", None)
    planned = planned_subtasks(session)
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
    if planned:
        review_payload_parts.append("Planned subtasks:\n- " + "\n- ".join(planned[:8]))
    review_payload_parts.append(f"Deterministic ambiguous signal: {ambiguous_reason}")
    review_payload_parts.append("Child outputs:\n" + "\n\n".join(child_sections[:6]))
    review_payload = "\n\n".join(part for part in review_payload_parts if part.strip())
    final_content = ""
    token_usage: dict[str, int] = {}
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
    decision, reason = parse_synthesis_review_response(final_content)
    if decision == "remediate":
        return reason or ambiguous_reason, token_usage
    if decision != "accept":
        logger.warning("Synthesis review fallback returned unparseable content: %r", final_content[:400])
    return None, token_usage


__all__ = [
    "cheap_llm_complete",
    "maybe_llm_synthesize",
    "plan_decomposition",
    "resolve_default_llm_model",
    "review_synthesis_gap_reason_with_llm",
]
