from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from .models import SurfaceMessage

logger = logging.getLogger(__name__)


class ContextGatherer:
    """Fetch only the context that triage says is needed, replacing speculative parallel prep."""

    async def gather(self, msg: SurfaceMessage, triage: Any, concierge: Any) -> Any:
        from .fan_out import fan_out_dict

        tasks: dict[str, Any] = {}

        tasks["context"] = lambda: asyncio.to_thread(
            concierge._resolve_context, msg,
        )

        if any(n == "memory" for n in triage.context_needs):
            tasks["memory"] = lambda: asyncio.to_thread(
                concierge._retrieve_memory_context, msg.text,
            )

        file_needs = [n[5:] for n in triage.context_needs if n.startswith("file:")]
        if file_needs:
            paths_copy = list(file_needs[:3])

            def _read_files() -> dict[str, str]:
                from pathlib import Path

                content: dict[str, str] = {}
                for p in paths_copy:
                    ep = Path(p).expanduser()
                    if ep.is_file():
                        try:
                            if ep.stat().st_size <= 100_000:
                                content[str(ep)] = ep.read_text(
                                    encoding="utf-8", errors="replace",
                                )[:4000]
                        except Exception:
                            pass
                return content

            tasks["auto_read"] = lambda: asyncio.to_thread(_read_files)

        domain_needs = [n[7:] for n in triage.context_needs if n.startswith("domain:")]
        if domain_needs and hasattr(concierge, "_retrieve_domain_expertise"):
            domain = domain_needs[0]
            tasks["domain"] = lambda: asyncio.to_thread(
                concierge._retrieve_domain_expertise, msg.text, domain,
            )

        timeout = float(os.environ.get("DAN_CONCIERGE_PREP_TIMEOUT", "5.0"))
        results = await fan_out_dict(tasks, timeout_per=timeout)

        context = results.get("context")
        if isinstance(context, Exception) or context is None:
            context = concierge._resolve_context(msg)

        project = getattr(context, "project", None) if context is not None else None
        project_id = str(getattr(project, "project_id", "") or "").strip() or None

        memory = results.get("memory", "")
        if isinstance(memory, Exception):
            memory = ""
        if project_id and any(n == "memory" for n in triage.context_needs):
            try:
                memory = await asyncio.to_thread(
                    concierge._retrieve_memory_context,
                    msg.text,
                    project_id=project_id,
                )
            except Exception:
                logger.debug("Project-scoped memory refresh failed", exc_info=True)
        if memory:
            msg.metadata["memory_context"] = memory

        auto_read = results.get("auto_read", {})
        if isinstance(auto_read, dict) and auto_read:
            msg.metadata["auto_read_content"] = auto_read

        domain_block = results.get("domain", "")
        if project_id and domain_needs and hasattr(concierge, "_retrieve_domain_expertise"):
            try:
                domain_block = await asyncio.to_thread(
                    concierge._retrieve_domain_expertise,
                    msg.text,
                    domain_needs[0],
                    project_id,
                )
            except Exception:
                logger.debug("Project-scoped domain refresh failed", exc_info=True)
        if isinstance(domain_block, str) and domain_block:
            msg.metadata["domain_expertise"] = domain_block

        return context


class TieredDispatcher:
    """Central coordinator: triage -> gather context -> dispatch to tier executor -> track lifecycle."""

    def __init__(
        self,
        *,
        session_manager: Any,
        triage_fn: Any,
        executors: dict[int, Any],
        context_gatherer: ContextGatherer,
        concierge: Any,
    ) -> None:
        self._session_manager = session_manager
        self._triage_fn = triage_fn
        self._executors = executors
        self._context_gatherer = context_gatherer
        self._concierge = concierge

    async def dispatch(self, msg: SurfaceMessage) -> AsyncIterator[Any]:
        from dan.server.chat_manager import ChatStreamEvent

        self._concierge._concierge_state = self._concierge._load_concierge_state(msg.external_id)
        self._ensure_progress_session(msg)

        intake_event = self._phase_event(
            msg.external_id,
            "intake",
            "Understanding your request",
        )
        if intake_event is not None:
            yield intake_event

        pending_resolution = self._resolve_pending(msg)
        if pending_resolution is not None:
            immediate_event, triage_context, pending_triage, replay_msg = pending_resolution
            if immediate_event is not None:
                yield immediate_event
                return
            msg = replay_msg
            triage = pending_triage
        else:
            triage, triage_context = await self._do_triage(msg)

        session = self._session_manager.create_root(msg, triage, triage.tier)
        execution_order = getattr(triage, "execution_order", "")
        if execution_order == "serial":
            session.child_execution = "serial"
        elif execution_order == "mixed":
            # Preserve mixed intent. Multi-step executor treats mixed conservatively.
            session.child_execution = "mixed"
        else:
            session.child_execution = "parallel"

        if triage.tier == 0:
            executor = self._executors.get(0)
            if executor:
                session.context = triage_context
                execution_event = self._phase_event(
                    msg.external_id,
                    "execution",
                    "Executing",
                )
                if execution_event is not None:
                    yield execution_event
                async for event in executor.execute(session, self._session_manager):
                    yield event
                await self._on_any_session_complete(session)
                await self._on_root_session_complete(session)
                return

        context_event = self._phase_event(
            msg.external_id,
            "context",
            "Gathering relevant context",
        )
        if context_event is not None:
            yield context_event
        context = await self._context_gatherer.gather(msg, triage, self._concierge)
        session.context = context

        execution_event = self._phase_event(
            msg.external_id,
            "execution",
            "Executing",
        )
        if execution_event is not None:
            yield execution_event
        executor = self._executors.get(triage.tier)
        if executor is None:
            executor = self._executors.get(1)

        async for event in executor.execute(session, self._session_manager):
            yield event

        await self._on_any_session_complete(session)
        await self._on_root_session_complete(session)

    def _resolve_pending(self, msg: SurfaceMessage):
        resolve_fn = getattr(self._concierge, "_resolve_pending_follow_up", None)
        if callable(resolve_fn):
            return resolve_fn(msg)
        return None

    def _ensure_progress_session(self, msg: SurfaceMessage) -> None:
        ensure_fn = getattr(self._concierge, "_ensure_progress_session", None)
        if callable(ensure_fn):
            ensure_fn(msg)

    def _phase_event(
        self,
        external_id: str,
        phase_id: str,
        name: str,
        detail: str | None = None,
        *,
        force: bool = True,
    ) -> Any | None:
        make_phase_event = getattr(self._concierge, "_make_phase_event", None)
        if not callable(make_phase_event):
            return None
        return make_phase_event(
            external_id,
            phase_id,
            name,
            detail,
            force=force,
        )

    async def _do_triage(self, msg: SurfaceMessage) -> tuple[Any, Any]:
        """Run triage LLM call. Returns (TriageResult, ResolvedContext)."""
        from .triage import fast_classify_text

        fast = fast_classify_text(msg.text)
        if fast is not None:
            context = self._concierge._resolve_context(msg)
            return fast, context

        context = self._concierge._resolve_context(msg)
        concierge_state = getattr(self._concierge, "_concierge_state", None)
        llm_complete = getattr(self._concierge, "_triage_llm_complete", None)

        result = await self._triage_fn(
            msg.text,
            context,
            llm_complete,
            concierge_state=concierge_state,
            project_store=getattr(self._concierge, "project_store", None),
        )
        return result, context

    async def _on_any_session_complete(self, session: Any) -> None:
        try:
            await self._concierge._emit_telemetry_event(
                "session_complete",
                metadata={
                    "session_id": session.id,
                    "parent_id": session.parent_id,
                    "root_id": session.root_id,
                    "tier": session.tier if isinstance(session.tier, int) else session.tier.value,
                    "depth": session.depth,
                    "task": session.task[:200],
                    "state": session.state if isinstance(session.state, str) else session.state.value,
                    "duration_ms": session.result.duration_ms if session.result else 0,
                    "token_usage": session.result.token_usage if session.result else {},
                    "tools_used": session.result.tools_used if session.result else [],
                    "model_used": session.result.model_used if session.result else None,
                    "children_count": len(session.children),
                    "error": session.result.error if session.result else None,
                },
            )
        except Exception:
            logger.debug("Session telemetry emission failed", exc_info=True)

    async def _on_root_session_complete(self, session: Any) -> None:
        if session.parent_id is not None:
            return

        try:
            context = session.context
            result = session.result
            if context is None or result is None:
                return

            tree_trace = self._session_manager.build_trace(session.id)
            trace_data = [
                {
                    "session_id": t.session_id,
                    "parent_id": t.parent_id,
                    "tier": t.tier if isinstance(t.tier, int) else t.tier.value,
                    "depth": t.depth,
                    "task": t.task[:200],
                    "state": t.state if isinstance(t.state, str) else t.state.value,
                    "duration_ms": t.duration_ms,
                    "token_usage": t.token_usage,
                    "tools_used": t.tools_used,
                    "error": t.error,
                }
                for t in tree_trace
            ]

            if hasattr(self._concierge, "project_store") and context:
                from .models import TaskTurn

                intent_str = session.triage.intent if session.triage else "ask"
                self._concierge.project_store.append_turn(
                    context.project.project_id,
                    context.task.task_id,
                    TaskTurn(role="user", content=session.msg.text if session.msg else "", intent=intent_str),
                    session.msg.external_id if session.msg else "",
                )
                if result.content:
                    result_metadata = getattr(result, "metadata", {}) or {}
                    memory_already_recorded = bool(
                        result_metadata.get("memory_recorded_by_chat_manager")
                    )
                    assistant_metadata = {"session_tree": trace_data}
                    record_assistant = getattr(self._concierge, "_record_assistant_turn", None)
                    if callable(record_assistant) and session.msg is not None:
                        record_assistant(
                            context,
                            session.msg,
                            result.content,
                            metadata=assistant_metadata,
                            write_conversation_memory=not memory_already_recorded,
                        )
                    else:
                        self._concierge.project_store.append_turn(
                            context.project.project_id,
                            context.task.task_id,
                            TaskTurn(
                                role="assistant",
                                content=result.content,
                                metadata=assistant_metadata,
                            ),
                            session.msg.external_id if session.msg else "",
                        )
                    store_memory = getattr(self._concierge, "_store_memory_candidates", None)
                    if callable(store_memory) and session.msg is not None:
                        store_memory(
                            session.msg.text,
                            result.content,
                            None,
                            project_id=context.project.project_id,
                            domain=getattr(context, "domain", None),
                            include_episode=not memory_already_recorded,
                        )

            total_tokens: dict[str, int] = {}
            for t in tree_trace:
                for k, v in t.token_usage.items():
                    total_tokens[k] = total_tokens.get(k, 0) + v

            await self._concierge._emit_telemetry_event(
                "tiered_dispatch_complete",
                metadata={
                    "session_tree_depth": max((t.depth for t in tree_trace), default=0),
                    "session_tree_size": len(tree_trace),
                    "total_tokens": total_tokens,
                    "session_tree": trace_data,
                },
            )

            from .models import IntentCategory

            intent_cat = None
            if session.triage:
                try:
                    intent_cat = IntentCategory(session.triage.intent)
                except (ValueError, AttributeError):
                    pass
            if hasattr(self._concierge, "_finalize_task"):
                state_value = session.state if isinstance(session.state, str) else session.state.value
                task_status_override = None
                if result.error == "cancelled" or state_value == "cancelled":
                    task_status_override = "paused"
                elif result.error or state_value == "failed":
                    task_status_override = "blocked"
                self._concierge._finalize_task(
                    context,
                    session.msg,
                    intent_cat or IntentCategory.ASK,
                    bool(result.content),
                    task_status_override=task_status_override,
                )

            self._concierge._save_concierge_state(
                session.msg.external_id if session.msg else "",
                self._concierge._concierge_state,
            )

            self._session_manager.prune_completed(max_age_seconds=300)
        except Exception:
            logger.debug("Root session bookkeeping failed", exc_info=True)
