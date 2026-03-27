from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from .models import SurfaceMessage

logger = logging.getLogger(__name__)

_PROGRESS_DETAIL_MAX_LEN = 160


def _user_task_progress_detail(triage: Any, msg: SurfaceMessage) -> str | None:
    """Human-readable hint for concierge progress (phase events + reassurance).

    Without this, long runs only show the generic phase name "Executing".
    """
    goal = str(getattr(triage, "goal", "") or "").strip()
    deliverable = str(getattr(triage, "deliverable", "") or "").strip()
    text = goal or deliverable
    if not text:
        raw = str(getattr(msg, "text", "") or "").strip()
        if raw:
            text = raw.split("\n", 1)[0].strip()
    if not text:
        return None
    if len(text) > _PROGRESS_DETAIL_MAX_LEN:
        return f"{text[: _PROGRESS_DETAIL_MAX_LEN - 1].rstrip()}…"
    return text


_USER_TURN_METADATA_KEYS = frozenset({
    "workflow_id",
    "thread_id",
    "client_graph_revision",
    "mode",
    "requested_mode",
    "debug_context",
    "mentions",
    "selected_path",
    "attachment_prompt_context",
    "surface_context",
    "allow_mutation_tool",
    "route_target",
    "route_source",
    "scenario_id",
    "scenario_confidence",
    "concierge_stage",
    "session_tier",
    "skip_confirm",
    "clarification_answer",
    "selected_option",
    "pending_route_step",
    "attached_user_reply",
    "replay_source",
    "pending_attachment_source",
    "pending_action_kind",
    "pending_original_text",
    "pending_intent",
    "pending_created_at",
    "pending_resolved_value",
    "pending_requires_triage",
    "pending_user_turn_content",
    "pending_effective_text",
})


def _sanitize_user_turn_metadata(msg: SurfaceMessage | None) -> dict[str, Any]:
    if msg is None or not isinstance(getattr(msg, "metadata", None), dict):
        return {}
    metadata = getattr(msg, "metadata", {}) or {}
    return {
        key: metadata[key]
        for key in _USER_TURN_METADATA_KEYS
        if key in metadata
    }


def _user_turn_content(msg: SurfaceMessage | None) -> str:
    if msg is None:
        return ""
    metadata = getattr(msg, "metadata", None)
    if isinstance(metadata, dict):
        attached = metadata.get("pending_user_turn_content")
        if isinstance(attached, str) and attached.strip():
            return attached
    return str(getattr(msg, "text", "") or "")


def _normalize_context_file_path(raw_path: Any, *, workspace_root: str = "") -> str | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute() and workspace_root:
        path = Path(workspace_root).expanduser() / path
    try:
        return str(path.resolve())
    except Exception:
        return str(path)


def _read_bounded_files(
    paths: list[str],
    *,
    limit: int = 2,
    max_bytes: int = 100_000,
    max_chars: int = 2000,
) -> dict[str, str]:
    content: dict[str, str] = {}
    for raw_path in paths:
        normalized = _normalize_context_file_path(raw_path)
        if not normalized or normalized in content:
            continue
        file_path = Path(normalized)
        if not file_path.is_file():
            continue
        try:
            if file_path.stat().st_size > max_bytes:
                continue
            text = file_path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            continue
        if not text:
            continue
        content[normalized] = text[:max_chars]
        if len(content) >= limit:
            break
    return content


def _collect_aggressive_related_paths(
    msg: SurfaceMessage,
    context: Any,
    *,
    existing_paths: set[str] | None = None,
    limit: int = 2,
) -> list[str]:
    metadata = getattr(msg, "metadata", {}) or {}
    surface_context = metadata.get("surface_context") if isinstance(metadata, dict) else {}
    if not isinstance(surface_context, dict):
        surface_context = {}
    workspace_root = str(surface_context.get("workspace_root") or "").strip()
    candidates: list[str] = []

    mentioned_files = surface_context.get("mentioned_files")
    if isinstance(mentioned_files, list):
        for file_ctx in mentioned_files:
            if not isinstance(file_ctx, dict):
                continue
            normalized = _normalize_context_file_path(
                file_ctx.get("path"),
                workspace_root=workspace_root,
            )
            if normalized:
                candidates.append(normalized)

    import_neighbors = surface_context.get("import_neighbors")
    if isinstance(import_neighbors, list):
        for item in import_neighbors:
            normalized = _normalize_context_file_path(item, workspace_root=workspace_root)
            if normalized:
                candidates.append(normalized)

    task_obj = getattr(context, "task", None) if context is not None else None
    artifacts = getattr(task_obj, "artifacts", None)
    if isinstance(artifacts, dict):
        for path in artifacts.values():
            normalized = _normalize_context_file_path(path)
            if normalized:
                candidates.append(normalized)

    seen = set(existing_paths or set())
    selected: list[str] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def _build_aggressive_task_snapshot(context: Any) -> str:
    project = getattr(context, "project", None) if context is not None else None
    task_obj = getattr(context, "task", None) if context is not None else None
    lines: list[str] = []

    summary = str(getattr(project, "summary", "") or "").strip()
    if summary:
        lines.append(f"Project summary: {summary[:300]}")

    pending_steps = getattr(task_obj, "pending_steps", None) or []
    cleaned_steps = [str(item or "").strip() for item in pending_steps if str(item or "").strip()]
    if cleaned_steps:
        lines.append("Pending steps: " + "; ".join(cleaned_steps[:3]))

    blocker = str(getattr(task_obj, "current_blocker", "") or "").strip()
    if blocker:
        lines.append(f"Current blocker: {blocker[:200]}")

    artifacts = getattr(task_obj, "artifacts", None)
    if isinstance(artifacts, dict) and artifacts:
        artifact_paths = [str(path or "").strip() for path in artifacts.values() if str(path or "").strip()]
        if artifact_paths:
            lines.append("Known artifacts: " + ", ".join(artifact_paths[:3]))

    return "\n".join(lines)


async def _build_aggressive_repo_snapshot(msg: SurfaceMessage) -> str:
    metadata = getattr(msg, "metadata", {}) or {}
    surface_context = metadata.get("surface_context") if isinstance(metadata, dict) else {}
    if not isinstance(surface_context, dict):
        return ""
    workspace_root = str(surface_context.get("workspace_root") or "").strip()
    if not workspace_root:
        return ""
    try:
        from dan.tools.git_status import git_status

        status = await git_status(workspace_root)
    except Exception:
        return ""

    lines: list[str] = []
    branch = str(status.get("branch") or "").strip()
    if branch:
        lines.append(f"Git branch: {branch}")
    staged = [str(item or "").strip() for item in status.get("staged", []) if str(item or "").strip()]
    modified = [str(item or "").strip() for item in status.get("modified", []) if str(item or "").strip()]
    untracked = [str(item or "").strip() for item in status.get("untracked", []) if str(item or "").strip()]
    if staged:
        lines.append("Staged changes: " + ", ".join(staged[:3]))
    if modified:
        lines.append("Modified files: " + ", ".join(modified[:3]))
    if untracked:
        lines.append("Untracked files: " + ", ".join(untracked[:3]))
    ahead = int(status.get("ahead", 0) or 0)
    behind = int(status.get("behind", 0) or 0)
    if ahead or behind:
        lines.append(f"Upstream delta: +{ahead} / -{behind}")
    return "\n".join(lines)


class ContextGatherer:
    """Fetch only the context that triage says is needed, replacing speculative parallel prep."""

    async def gather(
        self,
        msg: SurfaceMessage,
        triage: Any,
        concierge: Any,
        autonomy_resolution: Any | None = None,
    ) -> Any:
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
        if not isinstance(auto_read, dict):
            auto_read = {}

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

        if getattr(autonomy_resolution, "effective_level", "") == "aggressive":
            task_obj = getattr(context, "task", None) if context is not None else None
            recent_turns = []
            for turn in (getattr(task_obj, "turns", None) or [])[-4:]:
                role = str(getattr(turn, "role", "") or "").strip()
                content = str(getattr(turn, "content", "") or "").strip()
                if role and content:
                    recent_turns.append(f"{role}: {content[:200]}")
            if recent_turns:
                msg.metadata["autonomy_recent_turns"] = recent_turns
            task_snapshot = _build_aggressive_task_snapshot(context)
            if task_snapshot:
                msg.metadata["autonomy_task_snapshot"] = task_snapshot
            repo_snapshot = await _build_aggressive_repo_snapshot(msg)
            if repo_snapshot:
                msg.metadata["autonomy_repo_snapshot"] = repo_snapshot
            related_paths = _collect_aggressive_related_paths(
                msg,
                context,
                existing_paths=set(auto_read),
            )
            if related_paths:
                related_read = await asyncio.to_thread(_read_bounded_files, related_paths)
                if related_read:
                    auto_read = {**auto_read, **related_read}

        if auto_read:
            msg.metadata["auto_read_content"] = auto_read

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
        from dan.chat_events import ChatStreamEvent
        from .autonomy import resolve_autonomy

        state_scope_id = self._concierge._concierge_state_scope_key(msg.surface, msg.external_id)
        self._concierge._concierge_state = self._concierge._load_concierge_state(
            state_scope_id,
            legacy_scope_ids=[msg.external_id],
        )
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
            original_msg = msg
            immediate_event, triage_context, pending_triage, replay_msg = pending_resolution
            if immediate_event is not None:
                yield immediate_event
                return
            msg = replay_msg
            replay_metadata = getattr(msg, "metadata", None)
            if isinstance(replay_metadata, dict):
                original_reply = str(getattr(original_msg, "text", "") or "").strip()
                if original_reply:
                    replay_metadata.setdefault("attached_user_reply", original_reply)
                    replay_metadata.setdefault("pending_user_turn_content", original_reply)
                effective_text = str(getattr(msg, "text", "") or "").strip()
                if effective_text:
                    replay_metadata.setdefault("pending_effective_text", effective_text)
                if any(
                    key in replay_metadata
                    for key in ("skip_confirm", "clarification_answer", "selected_option", "pending_route_step")
                ):
                    replay_metadata.setdefault("replay_source", "pending_attachment")
                    replay_metadata.setdefault("pending_attachment_source", "pending_follow_up")
            if isinstance(replay_metadata, dict) and replay_metadata.get("pending_requires_triage"):
                triage, triage_context = await self._do_triage(msg)
            else:
                triage = pending_triage
        else:
            triage, triage_context = await self._do_triage(msg)

        turn_preference = None
        msg_metadata = getattr(msg, "metadata", None)
        if isinstance(msg_metadata, dict):
            turn_preference = msg_metadata.get("turn_autonomy_preference")
        project_preference = getattr(
            getattr(triage_context, "project", None),
            "autonomy_preference",
            None,
        )
        session_preference = getattr(self._concierge._concierge_state, "autonomy_preference", None)
        last_autonomy_level = getattr(self._concierge._concierge_state, "last_autonomy_level", None)
        route = getattr(triage, "route", None)
        autonomy_resolution = resolve_autonomy(
            text=msg.text,
            triage=triage,
            surface=msg.surface,
            turn_preference=turn_preference,
            session_preference=session_preference,
            project_preference=project_preference,
            env_preference=getattr(self._concierge, "_default_autonomy_preference", "auto"),
            last_effective_level=last_autonomy_level,
            action_hints=getattr(route, "action_hints", None),
        )
        if isinstance(msg_metadata, dict):
            msg_metadata["autonomy_preference"] = autonomy_resolution.preferred_level
            msg_metadata["autonomy_resolution"] = autonomy_resolution.model_dump()
        self._concierge._concierge_state.last_autonomy_level = autonomy_resolution.effective_level

        session = self._session_manager.create_root(
            msg,
            triage,
            triage.tier,
            autonomy_resolution=autonomy_resolution,
        )
        if isinstance(msg_metadata, dict):
            route_obj = getattr(triage, "route", None)
            msg_metadata["route_target"] = getattr(route_obj, "target", "")
            msg_metadata["route_source"] = str(getattr(triage, "route_source", "") or "")
            if getattr(triage, "scenario_id", None):
                msg_metadata["scenario_id"] = triage.scenario_id
            if getattr(triage, "scenario_confidence", None) is not None:
                msg_metadata["scenario_confidence"] = triage.scenario_confidence
            try:
                from .tier_executors import _determine_stage, _stage_prompt_overlay_id

                stage = _determine_stage(session)
                msg_metadata["concierge_stage"] = stage
                msg_metadata["prompt_overlay"] = _stage_prompt_overlay_id(stage)
            except Exception:
                logger.debug("Failed to resolve concierge stage for user-turn metadata", exc_info=True)
            msg_metadata["session_tier"] = int(session.tier)
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
                    _user_task_progress_detail(triage, msg),
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
        context = await self._context_gatherer.gather(
            msg,
            triage,
            self._concierge,
            autonomy_resolution=autonomy_resolution,
        )
        session.context = context

        execution_event = self._phase_event(
            msg.external_id,
            "execution",
            "Executing",
            _user_task_progress_detail(triage, msg),
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
            from .tier_executors import _determine_stage, _stage_prompt_overlay_id

            stage = _determine_stage(session)
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
                    "concierge_stage": stage,
                    "session_tier": session.tier if isinstance(session.tier, int) else session.tier.value,
                    "prompt_overlay": _stage_prompt_overlay_id(stage),
                    "route_source": str(getattr(session.triage, "route_source", "") or ""),
                    "scenario_id": getattr(session.triage, "scenario_id", None),
                    "scenario_confidence": getattr(session.triage, "scenario_confidence", None),
                    "autonomy_resolution": (
                        session.autonomy_resolution.model_dump()
                        if getattr(session, "autonomy_resolution", None) is not None
                        else {}
                    ),
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
                    "route_target": t.route_target,
                    "action_hints": t.action_hints,
                    "route_source": t.route_source,
                    "scenario_id": t.scenario_id,
                    "scenario_confidence": t.scenario_confidence,
                    "concierge_stage": t.concierge_stage,
                    "autonomy_level": t.autonomy_level,
                }
                for t in tree_trace
            ]

            if hasattr(self._concierge, "project_store") and context:
                from .models import TaskTurn

                intent_str = session.triage.intent if session.triage else "ask"
                record_feedback = getattr(self._concierge, "_maybe_record_turn_feedback", None)
                if callable(record_feedback) and session.msg is not None:
                    record_feedback(context, session.msg)
                user_turn_content = _user_turn_content(session.msg)
                self._concierge.project_store.append_turn(
                    context.project.project_id,
                    context.task.task_id,
                    TaskTurn(
                        role="user",
                        content=user_turn_content,
                        intent=intent_str,
                        metadata=_sanitize_user_turn_metadata(session.msg),
                    ),
                    session.msg.external_id if session.msg else "",
                )
                record_progress = getattr(self._concierge, "_record_task_progress_event", None)
                if callable(record_progress) and session.msg is not None:
                    record_progress(
                        context,
                        session.msg,
                        role="user_turn",
                        content=user_turn_content,
                        metadata=_sanitize_user_turn_metadata(session.msg),
                    )
                if result.content:
                    result_metadata = getattr(result, "metadata", {}) or {}
                    memory_already_recorded = bool(
                        result_metadata.get("memory_recorded_by_chat_manager")
                    )
                    assistant_metadata = {
                        **result_metadata,
                        "session_tree": trace_data,
                    }
                    record_assistant = getattr(self._concierge, "_record_assistant_turn", None)
                    if callable(record_assistant) and session.msg is not None:
                        record_assistant(
                            context,
                            session.msg,
                            result.content,
                        metadata={
                            **assistant_metadata,
                            "autonomy_resolution": (
                                session.autonomy_resolution.model_dump()
                                if getattr(session, "autonomy_resolution", None) is not None
                                else {}
                            ),
                        },
                            write_conversation_memory=not memory_already_recorded,
                        )
                    else:
                        self._concierge.project_store.append_turn(
                            context.project.project_id,
                            context.task.task_id,
                            TaskTurn(
                                role="assistant",
                                content=result.content,
                                metadata={
                                    **assistant_metadata,
                                    "autonomy_resolution": (
                                        session.autonomy_resolution.model_dump()
                                        if getattr(session, "autonomy_resolution", None) is not None
                                        else {}
                                    ),
                                },
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

            from .tier_executors import _determine_stage, _stage_prompt_overlay_id

            stage = _determine_stage(session)
            await self._concierge._emit_telemetry_event(
                "tiered_dispatch_complete",
                metadata={
                    "session_tree_depth": max((t.depth for t in tree_trace), default=0),
                    "session_tree_size": len(tree_trace),
                    "total_tokens": total_tokens,
                    "session_tree": trace_data,
                    "concierge_stage": stage,
                    "session_tier": session.tier if isinstance(session.tier, int) else session.tier.value,
                    "prompt_overlay": _stage_prompt_overlay_id(stage),
                    "route_source": str(getattr(session.triage, "route_source", "") or ""),
                    "scenario_id": getattr(session.triage, "scenario_id", None),
                    "scenario_confidence": getattr(session.triage, "scenario_confidence", None),
                    "autonomy_resolution": (
                        session.autonomy_resolution.model_dump()
                        if getattr(session, "autonomy_resolution", None) is not None
                        else {}
                    ),
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
                completion_status = ""
                if getattr(result, "metadata", None):
                    completion_status = str(result.metadata.get("completion_status", "") or "").strip().lower()
                if (
                    result.error == "cancelled"
                    or state_value == "cancelled"
                    or completion_status in {"interrupted", "partial", "cancelled"}
                ):
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
