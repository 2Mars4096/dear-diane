from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol

if TYPE_CHECKING:
    from .session import Session, SessionManager, SessionResult, SessionTier
    from .triage import TriageResult

from dan.server.chat_manager import (
    ChatCompleteEvent,
    ChatInterruptedEvent,
    ChatStreamEvent,
)

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
            duration_ms=(time.monotonic() - start) * 1000,
            child_results=child_results or {},
        ),
    )


def _build_prompt(session: Any) -> str:
    parts: list[str] = []
    triage = getattr(session, "triage", None)
    if triage:
        if getattr(triage, "goal", None):
            parts.append(f"User goal: {triage.goal}")
        if getattr(triage, "deliverable", None):
            parts.append(f"Expected deliverable: {triage.deliverable}")

    ctx = getattr(session, "context", None)
    if ctx and getattr(ctx, "domain", None):
        parts.append(f"Domain: {ctx.domain}")

    msg = getattr(session, "msg", None)
    metadata = getattr(msg, "metadata", None) if msg is not None else None
    if isinstance(metadata, dict):
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

    if mode == "build" or "workflow_edit" in action_hints or route_target == "workflow":
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

    workflow_id: str = str(metadata.get("workflow_id") or "")
    if not workflow_id:
        ctx = getattr(session, "context", None)
        proj = getattr(ctx, "project", None) if ctx else None
        linked = getattr(proj, "linked_workflow_ids", None) if proj else None
        if linked:
            workflow_id = linked[-1]
    if not workflow_id:
        workflow_id = "_scratch"

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
        allow_mutation_tool = (
            "workflow_edit" in required_action_hints
            or mode == "build"
            or route_target == "workflow"
        )

    stream_channel_id = str(metadata.get("stream_channel_id") or "").strip() or None
    attachment_prompt_context = str(metadata.get("attachment_prompt_context") or "").strip()
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
    }
    if model_override:
        result["model_override"] = model_override
        audit = result.get("audit_metadata") or {}
        audit["concierge_model_override"] = model_override
        result["audit_metadata"] = audit
    return result


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

        stage = _determine_stage(session)
        tier_resolver = getattr(self._concierge, "_tier_resolver", None)
        model_override = tier_resolver.resolve_model(stage) if tier_resolver else None

        system_prompt = _build_prompt(session)
        chat_params = _extract_chat_params(session, system_prompt, model_override=model_override)

        final_content = ""
        token_usage: dict[str, int] = {}
        saw_terminal = False

        try:
            async for event in self._concierge.chat_manager.send_message_with_tools(
                **chat_params,
            ):
                if isinstance(event, ChatInterruptedEvent):
                    saw_terminal = True
                    final_content = event.content
                    token_usage = dict(event.token_usage)
                    yield event
                    break
                if isinstance(event, ChatCompleteEvent):
                    saw_terminal = True
                    final_content = event.content
                    token_usage = dict(event.token_usage)
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

        if _cancel_requested(session):
            _mark_session_cancelled(manager, session, start=start, content=final_content)
            if not saw_terminal:
                yield _interrupted_event(final_content)
            return

        if not saw_terminal:
            synthesized = _synthesize_missing_terminal_event(session)
            final_content = synthesized.content
            yield synthesized

        from .session import SessionResult as _SR
        manager.set_result(
            session.id,
            _SR(
                content=final_content,
                metadata={"memory_recorded_by_chat_manager": True},
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

        triage = getattr(session, "triage", None)
        subtasks = getattr(triage, "subtasks", None) if triage else None
        task_ctx = getattr(session, "task_context", {}) or {}

        has_subtasks = (subtasks and len(subtasks) > 1) or len(task_ctx.get("subtasks", [])) > 1
        if not has_subtasks:
            return False

        if not manager.can_spawn_child(session.id):
            return False

        max_depth = getattr(session, "max_depth", 3)
        depth = getattr(session, "depth", 0)
        return depth < max_depth

    # -- direct execution (leaf) -------------------------------------------

    async def _execute_directly(
        self, session: Any, manager: Any, start: float
    ) -> AsyncIterator[ChatStreamEvent]:
        if _cancel_requested(session):
            _mark_session_cancelled(manager, session, start=start)
            yield _interrupted_event()
            return

        stage = _determine_stage(session)
        tier_resolver = getattr(self._concierge, "_tier_resolver", None)
        model_override = tier_resolver.resolve_model(stage) if tier_resolver else None

        system_prompt = _build_prompt(session)
        chat_params = _extract_chat_params(session, system_prompt, model_override=model_override)

        final_content = ""
        token_usage: dict[str, int] = {}
        saw_terminal = False

        try:
            async for event in self._concierge.chat_manager.send_message_with_tools(
                **chat_params,
            ):
                if isinstance(event, ChatInterruptedEvent):
                    saw_terminal = True
                    final_content = event.content
                    token_usage = dict(event.token_usage)
                    yield event
                    break
                if isinstance(event, ChatCompleteEvent):
                    saw_terminal = True
                    final_content = event.content
                    token_usage = dict(event.token_usage)
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

        if _cancel_requested(session):
            _mark_session_cancelled(manager, session, start=start, content=final_content)
            if not saw_terminal:
                yield _interrupted_event(final_content)
            return

        if not saw_terminal:
            synthesized = _synthesize_missing_terminal_event(session)
            final_content = synthesized.content
            yield synthesized

        from .session import SessionResult as _SR
        manager.set_result(
            session.id,
            _SR(
                content=final_content,
                metadata={"memory_recorded_by_chat_manager": True},
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

        if triage and getattr(triage, "subtasks", None):
            subtasks = list(triage.subtasks)
        else:
            task_ctx = getattr(session, "task_context", {}) or {}
            subtasks = list(task_ctx.get("subtasks", []))

        if not subtasks:
            subtasks = self._plan_decomposition(session)

        if _cancel_requested(session):
            _mark_session_cancelled(manager, session, start=start)
            yield _interrupted_event()
            return

        manager.update_state(session.id, "waiting")

        child_execution = getattr(session, "child_execution", "serial")
        child_results: dict[str, Any] = {}
        children: list[Any] = []
        interrupted = False
        interrupted_emitted = False

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
        final_content = self._synthesize(session, child_results, manager)

        from .session import SessionResult as _SR
        total_tokens: dict[str, int] = {}
        for r in child_results.values():
            for k, v in (getattr(r, "token_usage", {}) or {}).items():
                total_tokens[k] = total_tokens.get(k, 0) + v

        manager.set_result(
            session.id,
            _SR(
                content=final_content,
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
        if " and " in lower:
            return 2
        for kw in _MULTI_ACTION_KEYWORDS:
            if kw in lower:
                return 2
        return 1

    # -- plan decomposition (stub — LLM call in future) --------------------

    @staticmethod
    def _plan_decomposition(session: Any) -> list[str]:
        task = getattr(session, "task", "") or ""
        if not task:
            return []
        parts = [p.strip() for p in task.split(" and ") if p.strip()]
        return parts if len(parts) > 1 else [task]

    # -- running children --------------------------------------------------

    def _build_child_session(self, session: Any, manager: Any, task_desc: str) -> Any:
        child_tier = self._estimate_child_tier(task_desc)
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
            child.msg = parent_msg.model_copy(
                update={
                    "text": task_desc,
                    "metadata": {
                        **parent_msg.metadata,
                        "tiered_parent_session_id": session.id,
                        "tiered_root_session_id": session.root_id,
                        "tiered_child_task": task_desc,
                    },
                }
            )
        else:
            child.msg = SurfaceMessage(surface="", external_id="", text=task_desc)

        child.context = session.context

        try:
            from .triage import TriageResult

            parent_triage = getattr(session, "triage", None)
            child.triage = TriageResult(
                tier=child_tier,
                intent=getattr(parent_triage, "intent", "ask"),
                route=getattr(parent_triage, "route", None),
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

    @staticmethod
    def _synthesize(session: Any, child_results: dict[str, Any], manager: Any) -> str:
        parts: list[str] = []
        for child_id, result in child_results.items():
            child = manager.get(child_id)
            content = getattr(result, "content", None) if result else None
            if child and content:
                task_label = getattr(child, "task", child_id)
                parts.append(f"## {task_label}\n\n{content}")
        return "\n\n".join(parts) if parts else "Task completed but no content was produced."
