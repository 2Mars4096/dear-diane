from __future__ import annotations

import asyncio
import copy
import dataclasses
import inspect
import json
import logging
import os
import re
import time
import uuid
from contextvars import ContextVar
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from ..dispatcher import ConcurrentDispatcher
    from ..learning_bundle import ConciergeLearningBundle
    from ..memory_services import MemoryServices

from dan.chat_events import ChatCompleteEvent, ChatStreamEvent
from dan.llm_surface import (
    complete_chat_surface,
    default_llm_model,
    provider_names,
)

from ..autonomy import (
    AutonomyPreference,
    normalize_autonomy_preference,
    normalize_legacy_autonomy_level,
)
from ..command_registry import CommandDescriptor, CommandRegistry, get_default_registry
from ..identity import format_bare_prefix, format_prefix
from ..models import (
    ConciergeGoal,
    ConciergeState,
    GoalProgressEntry,
    IntentCategory,
    PendingAction,
    Project,
    ResolvedContext,
    SurfaceMessage,
    Task,
    TaskTurn,
)
from ..memory_enrichment import (
    store_extracted_memories,
    store_extracted_preferences,
)
from ..memory_services import build_memory_services
from ..progress import ProgressReporter
from ..project_store import ProjectStore
from ..pending_actions import resolve_pending_reply
from ..resources import ResourceTracker
from ..triage import TriageResult

logger = logging.getLogger(__name__)

_SERIALIZABLE_TYPES = (str, int, float, bool, type(None), list, dict)
_PREF_CONFIRM_WORDS = frozenset({"confirm all", "yes", "confirm"})
_RUNTIME_UNIFIED_PROMPT_KEY = "prompts/runtime.unified_system"
_BLOCKER_RE = re.compile(
    r"(?:blocked by|waiting on|need(?:ing)?|can'?t proceed until)\s+([^.\n]+)",
    re.IGNORECASE,
)
_FILE_PATH_RE = re.compile(r"(?:^|[\s\"'])(/[\w./-]+|[\w./-]+\.\w{1,6})(?=[\"'\s,;)]|$)")
_SENTENCE_END_RE = re.compile(r"[.!?]")


def _run_coroutine_sync(
    awaitable: Any,
    *,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Any:
    if loop is not None and loop.is_running():
        return asyncio.run_coroutine_threadsafe(awaitable, loop).result()
    return asyncio.run(awaitable)


def _format_clarification_text(question: str, options: list[str] | None) -> str:
    if not options:
        return question
    option_lines = [f"{i}. {opt}" for i, opt in enumerate(options, 1)]
    return f"{question}\n\n{chr(10).join(option_lines)}"


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


def _extract_task_state(text: str) -> dict[str, Any]:
    completed: list[str] = []
    pending: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"[-*]?\s*\[x\]", stripped, re.IGNORECASE):
            step_text = re.sub(r"^[-*]?\s*\[x\]\s*", "", stripped, flags=re.IGNORECASE).strip()
            if step_text:
                completed.append(step_text)
            continue
        if re.match(r"[-*]?\s*\[ ?\]", stripped):
            step_text = re.sub(r"^[-*]?\s*\[ ?\]\s*", "", stripped).strip()
            if step_text:
                pending.append(step_text)

    blocker: str | None = None
    blocker_match = _BLOCKER_RE.search(text)
    if blocker_match:
        blocker = blocker_match.group(0).strip()

    artifacts: dict[str, str] = {}
    for match in _FILE_PATH_RE.finditer(text):
        path = match.group(1)
        name = path.rsplit("/", 1)[-1] if "/" in path else path
        if name and name not in artifacts:
            artifacts[name] = path

    return {
        "completed_steps": _dedupe_keep_order(completed),
        "pending_steps": _dedupe_keep_order(pending),
        "current_blocker": blocker,
        "artifacts": artifacts,
    }


def _summarize_step(step: str) -> str:
    match = _SENTENCE_END_RE.search(step)
    if match:
        return step[: match.end()].strip()
    if len(step) > 80:
        return step[:80].rstrip() + "..."
    return step


def _compact_task_history(steps: list[str], keep_full: int = 3) -> list[str]:
    if len(steps) <= keep_full:
        return list(steps)
    cutoff = len(steps) - keep_full
    compacted = [_summarize_step(step) for step in steps[:cutoff]]
    compacted.extend(steps[cutoff:])
    return compacted


class AutonomyLevel(str, Enum):
    INTERACTIVE = "interactive"
    SUPERVISED = "supervised"
    AUTONOMOUS = "autonomous"


class Concierge:

    _REASSURANCE_INITIAL_DELAY: float = float(
        os.environ.get("DAN_CONCIERGE_REASSURANCE_DELAY", "3")
    )
    _REASSURANCE_REPEAT_INTERVAL: float = float(
        os.environ.get("DAN_CONCIERGE_REASSURANCE_INTERVAL", "5")
    )
    _MIN_PHASE_EVENT_INTERVAL: float = 1.0

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def __init__(
        self,
        *,
        project_store: ProjectStore,
        chat_manager: Any,
        capability_context: Any,
        user_profile: Any = None,
        conversation_memory: Any = None,
        progress_reporter: ProgressReporter | None = None,
        meta_controller: Any = None,
        bot_name: str | None = None,
        memory_kernel: Any = None,
        autonomy_level: str | None = None,
        mcp_bridge: Any = None,
        capability_registry: Any = None,
        tool_registry: Any = None,
        engine: Any = None,
        telemetry_store: Any = None,
        learning_bundle: "ConciergeLearningBundle | None" = None,
        memory_services: "MemoryServices | None" = None,
    ) -> None:
        self.project_store = project_store
        self.chat_manager = chat_manager
        self.capability_context = capability_context
        self.user_profile = user_profile
        self.conversation_memory = conversation_memory
        self.progress_reporter = progress_reporter
        self.meta_controller = meta_controller
        self.memory_kernel = memory_kernel
        self.mcp_bridge = mcp_bridge
        self._capability_registry = capability_registry
        self._tool_registry = tool_registry
        self._engine = engine
        self._run_manager = getattr(capability_context, "run_manager", None)
        self._graph_store = getattr(capability_context, "graph_store", None)
        self._telemetry_store = telemetry_store
        resource_tracker = getattr(chat_manager, "_resource_tracker", None)
        self._resource_tracker = (
            resource_tracker if isinstance(resource_tracker, ResourceTracker) else None
        )
        self._current_turn_event_id: str | None = None
        self._telem_is_fast_command = False
        self._telem_model: str | None = None
        self._telem_intent: str | None = None
        self._last_context: Any = None
        self.bot_name = bot_name
        self.auto_summarize_turn_threshold: int = 10
        self._bg_memory_tasks: set[asyncio.Task[None]] = set()
        self._interaction_counter: int = 0
        self._default_autonomy_preference = normalize_legacy_autonomy_level(
            autonomy_level or os.environ.get("DAN_CONCIERGE_AUTONOMY", "auto"),
        )
        self._volatile_concierge_states: dict[str, ConciergeState] = {}
        self._volatile_concierge_state_locks: dict[str, asyncio.Lock] = {}
        self._pending_preference_surface: dict[str, list[Any]] = {}
        self._progress_sessions: dict[str, Any] = {}

        self._current_surface_id_var: ContextVar[str | None] = ContextVar(
            "concierge_current_surface_id", default=None,
        )
        self._concierge_state_var: ContextVar[ConciergeState | None] = ContextVar(
            "concierge_state", default=None,
        )
        self._domain_warning_context_var: ContextVar[tuple[str, ...]] = ContextVar(
            "concierge_domain_warnings", default=(),
        )

        if learning_bundle is None:
            from ..learning_bundle import build_concierge_learning_bundle

            learning_bundle = build_concierge_learning_bundle(
                chat_manager=self.chat_manager,
                telemetry_store=self._telemetry_store,
                memory_kernel=self.memory_kernel,
                runtime_prompt_key=_RUNTIME_UNIFIED_PROMPT_KEY,
            )

        self._behavior_store = learning_bundle.behavior_store
        self._behavior_changelog = learning_bundle.behavior_changelog
        self._param_registry = learning_bundle.param_registry
        self._param_logger = learning_bundle.param_logger
        self._pattern_accumulator = learning_bundle.pattern_accumulator
        self._correction_store = learning_bundle.correction_store
        self._adaptation_registry = learning_bundle.adaptation_registry
        self._prompt_tracker = learning_bundle.prompt_tracker
        self._feature_enabled = learning_bundle.feature_enabled or (lambda _feature: False)
        self._analyze_turn_feedback = learning_bundle.analyze_turn_feedback
        self._memory_services = memory_services or build_memory_services(
            memory_kernel=self.memory_kernel,
        )

        self._skill_store: Any = None
        self._schedule_store: Any = None
        self._schedule_history_store: Any = None
        try:
            from ..scheduler import ScheduleHistoryStore, ScheduleStore

            self._schedule_store = ScheduleStore()
            self._schedule_history_store = ScheduleHistoryStore()
        except Exception:
            logger.debug("Schedule store init failed", exc_info=True)

        self._pii_registry: Any = None
        try:
            from dan.llm_core.pii_tokenizer import SensitiveWordRegistry

            self._pii_registry = SensitiveWordRegistry.load()
        except Exception:
            logger.debug("PII registry init failed", exc_info=True)

        self._computer_config: Any = None
        self._computer_lease: Any = None
        self._computer_audit: Any = None
        self._computer_controller: Any = None
        try:
            from ..computer_policy import AuditLog, ComputerControlConfig
            from ..computer_use import ComputerUseLeaseManager

            self._computer_config = ComputerControlConfig.load()
            self._computer_lease = ComputerUseLeaseManager()
            self._computer_audit = AuditLog()
        except Exception:
            logger.debug("Computer control init failed", exc_info=True)

        self._triage_model: str = (
            os.environ.get("DAN_TRIAGE_MODEL", "").strip()
            or os.environ.get("DAN_CLASSIFIER_MODEL", "").strip()
        )

        # Tier resolver — maps concierge stages to concrete models
        self._tier_resolver: Any = None
        try:
            from dan.providers.tier_defaults import normalize_tier_map, resolve_tier_map
            from ..tiering import ConciergeTierResolver

            tier_map_env = os.environ.get("DAN_TIER_MAP", "").strip()
            user_tier_map = None
            if tier_map_env:
                try:
                    raw = json.loads(tier_map_env)
                    user_tier_map = normalize_tier_map(raw)
                except Exception:
                    pass

            full_tier_map = resolve_tier_map(
                provider_names(chat_manager),
                user_tier_map,
            )
            fallback_model = (
                os.environ.get("DAN_CHAT_MODEL", "").strip()
                or os.environ.get("DAN_LLM_MODEL", "").strip()
                or "claude-sonnet-4-6"
            )
            self._tier_resolver = ConciergeTierResolver(full_tier_map, fallback_model)
        except Exception as exc:
            logger.warning("Tier resolver init failed: %s", exc)

        # Tiered dispatcher (always enabled)
        from ..session import SessionManager
        from ..triage import triage as triage_fn
        from ..tier_executors import InstantExecutor, SingleShotExecutor, MultiStepExecutor
        from ..tiered_dispatch import TieredDispatcher, ContextGatherer

        self._session_manager = SessionManager()
        _multi_executor = MultiStepExecutor(self, dispatcher=None)
        self._tiered_dispatcher = TieredDispatcher(
            session_manager=self._session_manager,
            triage_fn=triage_fn,
            executors={
                0: InstantExecutor(self),
                1: SingleShotExecutor(self),
                2: _multi_executor,
            },
            context_gatherer=ContextGatherer(),
            concierge=self,
        )
        _multi_executor._dispatcher = self._tiered_dispatcher

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def _current_surface_id(self) -> str | None:
        return self._current_surface_id_var.get()

    @_current_surface_id.setter
    def _current_surface_id(self, value: str | None) -> None:
        self._current_surface_id_var.set(value)

    @property
    def _concierge_state(self) -> ConciergeState:
        state = self._concierge_state_var.get()
        if state is None:
            state = ConciergeState()
            self._concierge_state_var.set(state)
        return state

    @_concierge_state.setter
    def _concierge_state(self, value: ConciergeState) -> None:
        self._concierge_state_var.set(value)

    def _domain_warning_context(self) -> ContextVar[tuple[str, ...]]:
        return self._domain_warning_context_var

    # ------------------------------------------------------------------
    # LLM helpers
    # ------------------------------------------------------------------

    def _resolve_triage_model(self) -> str:
        if self._triage_model:
            return self._triage_model
        if self._tier_resolver is not None:
            return self._tier_resolver.resolve_model("classification")
        configured_model = (
            default_llm_model(self.chat_manager)
            or os.environ.get("DAN_CHAT_MODEL", "").strip()
            or os.environ.get("DAN_LLM_MODEL", "").strip()
        )
        try:
            names = provider_names(self.chat_manager)
            if names:
                if set(names) == {"default"} and configured_model:
                    return configured_model
                from dan.providers.tier_defaults import resolve_tier_map
                tier_map = resolve_tier_map(names)
                micro_model = str(tier_map.get("micro", "") or "").strip()
                if micro_model:
                    return micro_model
        except Exception:
            pass
        return configured_model or "gpt-4o-mini"

    async def _triage_llm_complete(self, messages: list[dict[str, str]]) -> str:
        model = self._resolve_triage_model()
        tracker = getattr(self, "_resource_tracker", None)
        if tracker is not None:
            await tracker.wait_acquire("llm")
        try:
            result = await complete_chat_surface(
                self.chat_manager,
                messages=messages,
                model=model,
                temperature=0.0,
                max_tokens=256,
                pii_session_key=self._current_surface_id,
            )
        finally:
            if tracker is not None:
                await tracker.release("llm")
        return result.text

    # ------------------------------------------------------------------
    # Context resolution
    # ------------------------------------------------------------------

    def _resolve_context(self, msg: SurfaceMessage) -> ResolvedContext:
        trigger_context = msg.metadata.get("trigger_context")
        if isinstance(trigger_context, dict):
            project_id = str(trigger_context.get("project_id") or "").strip()
            task_id = str(trigger_context.get("task_id") or "").strip()
            if project_id:
                project = self.project_store.get_project(project_id, msg.external_id)
                if project is None:
                    project = self.project_store.get_project_any_surface(project_id)
                task = None
                if task_id and project is not None:
                    for candidate in project.tasks:
                        if candidate.task_id == task_id:
                            task = candidate
                            break
                if task is None:
                    task = self.project_store.get_current_task(project_id, msg.external_id)
                    if task is None and project is not None:
                        task = self.project_store.get_current_task_any_surface(project_id)
                if project is not None and task is not None:
                    return ResolvedContext(
                        project=project,
                        task=task,
                        is_new_project=False,
                        is_new_task=False,
                        confidence=1.0,
                        domain=project.domain,
                    )

        active = self.project_store.list_active(msg.external_id)
        if active:
            project = active[0]
            task = self.project_store.get_current_task(project.project_id, msg.external_id)
            if task is None:
                label = msg.text.strip()[:60].replace("\n", " ") or "task"
                task = self.project_store.add_task(project.project_id, label, msg.external_id)
            return ResolvedContext(
                project=project,
                task=task,
                is_new_project=False,
                is_new_task=False,
                confidence=0.8,
                domain=project.domain,
            )

        label = msg.text.strip()[:60].replace("\n", " ") or "project"
        project = self.project_store.create_project(label, msg.external_id)
        task = self.project_store.add_task(project.project_id, label, msg.external_id)
        project = self.project_store.get_project(project.project_id, msg.external_id) or project
        return ResolvedContext(
            project=project,
            task=task,
            is_new_project=True,
            is_new_task=True,
            confidence=0.0,
        )

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    async def _emit_telemetry_event(self, event_type: str, **kwargs) -> None:
        store = getattr(self, "_telemetry_store", None)
        if store is None:
            return
        try:
            from dan.telemetry_api import TelemetryEvent
            parent = getattr(self, "_current_turn_event_id", None)
            ev = TelemetryEvent(event_type=event_type, parent_event_id=parent, **kwargs)
            await store.record(ev)
        except Exception:
            logger.debug("Telemetry emit failed", exc_info=True)

    async def _emit_turn_telemetry(
        self,
        *,
        turn_event_id: str,
        start_time: float,
        msg: SurfaceMessage,
        tokens: dict[str, int],
        cost: float,
        is_fast_command: bool,
        success: bool = True,
        model: str | None = None,
        intent: str | None = None,
    ) -> None:
        if self._telemetry_store is None:
            return
        try:
            from dan.telemetry_api import TelemetryEvent

            duration_ms = (time.monotonic() - start_time) * 1000
            _ctx = self._last_context
            if model is None:
                model = getattr(self, "_telem_model", None)
            if intent is None:
                intent = getattr(self, "_telem_intent", None)
            autonomy_metadata = {}
            msg_metadata = getattr(msg, "metadata", {}) or {}
            if isinstance(msg_metadata, dict):
                autonomy_resolution = msg_metadata.get("autonomy_resolution")
                if isinstance(autonomy_resolution, dict) and autonomy_resolution:
                    autonomy_metadata["autonomy_resolution"] = autonomy_resolution
            ev = TelemetryEvent(
                id=turn_event_id,
                event_type="fast_command" if is_fast_command else "chat_turn",
                project_id=getattr(getattr(_ctx, "project", None), "project_id", None),
                task_id=getattr(getattr(_ctx, "task", None), "task_id", None),
                surface=msg.surface,
                session_id=msg.external_id,
                model=model,
                intent=intent,
                prompt_tokens=tokens.get("prompt_tokens", 0),
                completion_tokens=tokens.get("completion_tokens", 0),
                total_tokens=tokens.get("total_tokens", 0),
                estimated_cost=cost,
                duration_ms=duration_ms,
                success=success,
                metadata=autonomy_metadata,
            )
            await self._telemetry_store.record(ev)
        except Exception:
            logger.debug("Telemetry emit failed", exc_info=True)

    # ------------------------------------------------------------------
    # Fast command dispatch
    # ------------------------------------------------------------------

    def _is_fast_command(self, text: str) -> bool:
        lower = text.strip().lower()
        registry = get_default_registry()
        if registry.is_fast_command(lower):
            return True
        pending = self._pending_preference_surface.get(self._current_surface_id)
        if pending and (lower in _PREF_CONFIRM_WORDS or lower.startswith("reject")):
            return True
        return False

    async def _coerce_fast_command_result(self, result: Any) -> ChatCompleteEvent | None:
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            result = await result
        if isinstance(result, ChatCompleteEvent):
            return result
        if isinstance(result, str) and result:
            return self._complete_event(content=result)
        return None

    async def _dispatch_registry_fast_command(
        self,
        msg: SurfaceMessage,
        descriptor: CommandDescriptor,
        registry: CommandRegistry,
    ) -> ChatCompleteEvent | None:
        handler_name = (descriptor.handler or "").rsplit(".", 1)[-1]
        if descriptor.handler and ".Concierge." in descriptor.handler:
            bound_handler = getattr(self, handler_name, None)
            if bound_handler is None:
                return None
            return await self._coerce_fast_command_result(bound_handler(msg))

        if descriptor.name == "/build":
            msg.metadata["requested_mode"] = "build"
            stripped_text = re.sub(
                r"^/(build|workflow)\b", "", msg.text, count=1, flags=re.IGNORECASE,
            ).strip()
            if stripped_text:
                msg.text = stripped_text
            return None

        if descriptor.name == "/help":
            parts = msg.text.split(None, 1)
            group = parts[1].strip() if len(parts) == 2 else None
            formatter = (
                registry.format_help_plain
                if self._is_messaging_surface(msg)
                else registry.format_help
            )
            return self._complete_event(content=formatter(msg.surface or "all", group=group))

        if descriptor.name == "/cost":
            if self._telemetry_store is None:
                return self._complete_event(
                    content="Telemetry is not enabled — no cost data available.",
                )
            from dan.telemetry_api import TelemetryQuery as TQ

            telemetry_session_id = str(
                getattr(msg, "session_id", "") or msg.external_id or "",
            ).strip()
            events = await self._telemetry_store.query(
                TQ(session_id=telemetry_session_id),
            )
            if not events:
                return self._complete_event(
                    content="No token usage recorded for this session yet.",
                )
            per_model: dict[str, dict[str, float]] = {}
            for ev in events:
                m = ev.model or "unknown"
                s = per_model.setdefault(m, {
                    "prompt": 0, "completion": 0, "total": 0,
                    "cost": 0.0, "calls": 0,
                })
                s["prompt"] += ev.prompt_tokens
                s["completion"] += ev.completion_tokens
                s["total"] += ev.total_tokens
                s["cost"] += ev.estimated_cost
                s["calls"] += 1
            lines = ["**Session Token Usage**\n"]
            g_p = g_c = g_t = 0
            g_cost = 0.0
            for model_name, s in sorted(
                per_model.items(), key=lambda x: x[1]["cost"], reverse=True,
            ):
                lines.append(
                    f"  **{model_name}** ({int(s['calls'])} calls): "
                    f"{int(s['prompt']):,} prompt + {int(s['completion']):,} completion "
                    f"= {int(s['total']):,} tokens — ${s['cost']:.4f}"
                )
                g_p += int(s["prompt"])
                g_c += int(s["completion"])
                g_t += int(s["total"])
                g_cost += s["cost"]
            lines.append(
                f"\n**Total**: {g_p:,} prompt + {g_c:,} completion "
                f"= {g_t:,} tokens — ${g_cost:.4f}"
            )
            chat_store = getattr(self.chat_manager, "_chat_store", None)
            metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
            thread_id = str(
                getattr(msg, "session_id", "")
                or metadata.get("thread_id")
                or msg.external_id
                or ""
            ).strip()
            workflow_id = str(metadata.get("workflow_id") or "").strip()
            active_projects = self.project_store.list_active(msg.external_id)
            if not workflow_id and active_projects:
                linked = list(active_projects[0].linked_workflow_ids or [])
                if linked:
                    workflow_id = str(linked[-1] or "").strip()
            if not workflow_id:
                workflow_id = str(
                    getattr(self.capability_context, "workflow_id", "") or "_scratch"
                ).strip()
            if chat_store is not None and thread_id and workflow_id:
                try:
                    meta = chat_store.get_thread_meta(workflow_id, thread_id)
                except Exception:
                    meta = {}
                summary = meta.get("latest_citation_summary") if isinstance(meta, dict) else None
                if isinstance(summary, dict) and summary.get("ran"):
                    verified = int(summary.get("verified", 0) or 0)
                    unverified = int(summary.get("unverified", 0) or 0)
                    lines.append(
                        f"\n**Citations**: {verified} verified, {unverified} unverified"
                    )
            return self._complete_event(content="\n".join(lines))

        if descriptor.name == "/retry":
            active = self.project_store.list_active(msg.external_id)
            if not active:
                return self._complete_event(
                    content="No active project — nothing to retry.",
                )
            project = active[0]
            task = self.project_store.get_current_task(
                project.project_id, msg.external_id,
            )
            if task is None:
                return self._complete_event(
                    content="No active task — nothing to retry.",
                )
            last_user_turn = None
            for turn in reversed(task.turns):
                if turn.role == "user" and turn.content.strip():
                    last_user_turn = turn
                    break
            if last_user_turn is None:
                return self._complete_event(
                    content="No prior user message found to retry.",
                )
            replay_metadata = {
                **msg.metadata,
                **(last_user_turn.metadata if isinstance(last_user_turn.metadata, dict) else {}),
            }
            for key in (
                "cancel_event",
                "stream_channel_id",
                "resolved_project_id",
                "resolved_task_id",
                "resolved_domain",
            ):
                if key in msg.metadata:
                    replay_metadata[key] = msg.metadata[key]
            replay_metadata.pop("autonomy_preference", None)
            replay_metadata.pop("autonomy_resolution", None)
            replay_metadata.pop("turn_autonomy_preference", None)
            replay_metadata["retried_via_command"] = True
            msg.text = last_user_turn.content
            msg.metadata = replay_metadata
            return None

        handler = registry.resolve_handler(descriptor.name)
        if handler is None:
            return None

        if descriptor.name == "/skill":
            return await self._coerce_fast_command_result(
                handler(msg.text, getattr(self, "_skill_store", None)),
            )

        if descriptor.name == "/schedule":
            if self._schedule_store is None:
                return self._complete_event(content="Scheduling is unavailable.")
            from ..scheduler import DeliveryTarget, TriggerContext

            metadata = (
                msg.metadata
                if isinstance(getattr(msg, "metadata", None), dict)
                else {}
            )
            resolved_project_id = str(metadata.get("resolved_project_id") or "").strip() or None
            resolved_task_id = str(metadata.get("resolved_task_id") or "").strip() or None
            default_workflow_id = str(metadata.get("workflow_id") or "").strip() or None
            if not default_workflow_id:
                active_projects = self.project_store.list_active(msg.external_id)
                if active_projects:
                    linked = list(active_projects[0].linked_workflow_ids or [])
                    if linked:
                        default_workflow_id = str(linked[-1] or "").strip() or None
            if not default_workflow_id:
                default_workflow_id = str(
                    getattr(self.capability_context, "workflow_id", "") or ""
                ).strip() or None
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
            return await self._coerce_fast_command_result(
                handler(
                    msg.text,
                    self._schedule_store,
                    self._schedule_history_store,
                    default_trigger_context=trigger_context,
                    default_delivery_target=delivery_target,
                    default_workflow_id=default_workflow_id,
                ),
            )

        if descriptor.name == "/plan":
            args_str = re.sub(r"^/plan\b", "", msg.text, count=1, flags=re.IGNORECASE).strip()
            return await self._coerce_fast_command_result(handler(self, msg.text, args_str))

        if descriptor.name == "/pii":
            if self._pii_registry is None:
                return self._complete_event(content="PII protection is unavailable.")
            return await self._coerce_fast_command_result(
                handler(msg.text, self._pii_registry, session_key=msg.external_id),
            )

        if descriptor.name == "/domains":
            return await self._coerce_fast_command_result(
                handler(
                    msg.text,
                    self.user_profile,
                    memory_kernel=self.memory_kernel,
                    behavior_store=self._behavior_store,
                ),
            )

        if descriptor.name == "/computer":
            if (
                self._computer_config is None
                or self._computer_lease is None
                or self._computer_audit is None
            ):
                return self._complete_event(content="Computer control is unavailable.")
            return await self._coerce_fast_command_result(
                handler(
                    msg.text,
                    self._computer_config,
                    self._computer_lease,
                    self._computer_audit,
                    controller=self._computer_controller,
                ),
            )

        params = list(inspect.signature(handler).parameters.values())
        if len(params) == 0:
            return await self._coerce_fast_command_result(handler())
        if len(params) == 1:
            if params[0].name in {"msg", "message", "surface_message"}:
                return await self._coerce_fast_command_result(handler(msg))
            return await self._coerce_fast_command_result(handler(msg.text))
        if len(params) == 2 and params[1].name in {"surface_id", "external_id"}:
            return await self._coerce_fast_command_result(
                handler(msg.text, getattr(msg, "external_id", None)),
            )
        return None

    async def handle_search_command(self, msg: SurfaceMessage) -> ChatCompleteEvent:
        args_str = re.sub(r"^/search\b", "", msg.text, count=1, flags=re.IGNORECASE).strip()
        if not args_str:
            return self._complete_event(content="Usage: `/search <query>`")
        if self.capability_context is None:
            return self._complete_event(content="Web search is unavailable.")

        from dan.web_surface import handle_web_search

        if dataclasses.is_dataclass(self.capability_context):
            ctx = dataclasses.replace(
                self.capability_context,
                workflow_id="_scratch",
                grounding_required=True,
                thread_id=str(getattr(msg, "session_id", "") or getattr(msg, "external_id", "") or "").strip() or None,
                web_budget_state={},
                search_state={},
            )
        else:
            ctx = copy.copy(self.capability_context)
            setattr(ctx, "workflow_id", "_scratch")
            setattr(ctx, "grounding_required", True)
            setattr(
                ctx,
                "thread_id",
                str(getattr(msg, "session_id", "") or getattr(msg, "external_id", "") or "").strip() or None,
            )
            setattr(ctx, "web_budget_state", {})
            setattr(ctx, "search_state", {})
        result = await handle_web_search(
            {
                "query": args_str,
                "num_results": 5,
                "fetch_content": True,
                "search_depth": "thorough",
            },
            ctx,
        )
        return self._complete_event(content=result.message)

    async def _try_fast_command(
        self, msg: SurfaceMessage,
    ) -> ChatCompleteEvent | None:
        from ..triage import fast_classify_text

        fast_social = fast_classify_text(msg.text)
        normalized = msg.text.strip().lower()
        if (
            fast_social is not None
            and fast_social.is_social
            and normalized not in {"yes", "no", "confirm", "cancel"}
        ):
            self._telem_is_fast_command = True
            return self._complete_event(content=fast_social.social_response or "")

        if not self._is_fast_command(msg.text):
            return None
        registry = get_default_registry()
        descriptor = registry.match(msg.text)
        if descriptor is not None and descriptor.kind == "chat":
            dispatch_result = await self._dispatch_registry_fast_command(
                msg, descriptor, registry,
            )
            if dispatch_result is not None:
                self._telem_is_fast_command = True
                return dispatch_result
        pref_confirmation = self.handle_preference_confirmation(
            msg.external_id, msg.text,
        )
        if pref_confirmation:
            self._telem_is_fast_command = True
            return self._complete_event(content=f"{format_prefix()} {pref_confirmation}")
        return None

    def handle_preference_confirmation(self, surface_id: str, message: str) -> str:
        pending = self._pending_preference_surface.get(surface_id) or []
        if self.memory_kernel is None or not pending:
            return ""
        lower = message.strip().lower()
        if lower in ("confirm all", "yes", "confirm"):
            confirmed = []
            for item in pending:
                result = self.memory_kernel.confirm_preference(item.id)
                if result:
                    confirmed.append(result.content)
            self._pending_preference_surface.pop(surface_id, None)
            if confirmed:
                return f"Confirmed {len(confirmed)} preference(s). They'll be used going forward."
            return ""
        if lower.startswith("reject"):
            parts = lower.split()
            rejected = []
            for part in parts[1:]:
                try:
                    idx = int(part) - 1
                    if 0 <= idx < len(pending):
                        item = pending[idx]
                        result = self.memory_kernel.reject_preference(item.id)
                        if result:
                            rejected.append(result.content)
                except (ValueError, IndexError):
                    continue
            self._pending_preference_surface[surface_id] = [
                p for p in pending if p.content not in rejected
            ]
            if rejected:
                return f"Rejected {len(rejected)} preference(s). They won't be used going forward."
            return ""
        return ""

    # ------------------------------------------------------------------
    # Progress / reassurance
    # ------------------------------------------------------------------

    @staticmethod
    def _is_messaging_surface(msg: SurfaceMessage) -> bool:
        surface = str(getattr(msg, "surface", "") or "").split(":", 1)[0]
        return surface in {"telegram", "whatsapp", "whatsapp-web", "email"}

    @staticmethod
    def _format_elapsed_seconds(elapsed_seconds: float) -> str:
        seconds = max(1, int(round(elapsed_seconds)))
        if seconds < 60:
            return f"{seconds}s elapsed"
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            if seconds == 0:
                return f"{minutes}m elapsed"
            return f"{minutes}m {seconds}s elapsed"
        hours, minutes = divmod(minutes, 60)
        if minutes == 0 and seconds == 0:
            return f"{hours}h elapsed"
        if seconds == 0:
            return f"{hours}h {minutes}m elapsed"
        return f"{hours}h {minutes}m {seconds}s elapsed"

    def _format_progress_status(
        self, prefix: str, label: str, elapsed_seconds: float,
    ) -> str:
        clean_label = label.strip().rstrip(".!?")
        if elapsed_seconds > 0:
            return (
                f"{prefix} — {clean_label} "
                f"({self._format_elapsed_seconds(elapsed_seconds)})"
            )
        return f"{prefix} — {clean_label}"

    def _set_progress_phase(
        self,
        external_id: str,
        phase_id: str,
        name: str,
        detail: str | None = None,
    ) -> bool:
        session = self._progress_sessions.get(external_id)
        if session is None:
            return False
        current = session.get_current_phase()
        created_phase = False
        if current is None or current.id != phase_id:
            if current is not None and current.status == "active":
                summary = current.summary or (
                    current.sub_steps[-1] if current.sub_steps else current.name
                )
                session.complete_phase(current.id, summary)
            session.start_phase(phase_id, name)
            created_phase = True
        if detail:
            if created_phase:
                phase = session.get_current_phase()
                if phase is not None:
                    phase.sub_steps.append(detail)
                    return created_phase
            session.update_phase(phase_id, detail)
        return created_phase

    def _make_phase_event(
        self,
        external_id: str,
        phase_id: str,
        name: str,
        detail: str | None = None,
        *,
        force: bool = False,
    ) -> ChatCompleteEvent | None:
        created_phase = self._set_progress_phase(external_id, phase_id, name, detail)
        session = self._progress_sessions.get(external_id)
        if session is None:
            return None
        if getattr(session, "verbosity", "minimal") == "minimal":
            return None
        now = time.monotonic()
        last_phase_event_time = float(
            getattr(session, "_last_phase_event_time", 0.0) or 0.0,
        )
        if (
            not force
            and not created_phase
            and now - last_phase_event_time < self._MIN_PHASE_EVENT_INTERVAL
        ):
            return None
        session._last_phase_event_time = now
        label = detail or name
        elapsed_seconds = session.elapsed_total()
        return ChatCompleteEvent(
            message_id=uuid.uuid4().hex[:12],
            content=self._format_progress_status("Working on it", label, elapsed_seconds),
            token_usage={},
            context_window=0,
            graph_revision="",
            detected_mode="progress_ack",
            phase_label=label,
        )

    def _create_progress_renderer(self, surface: str, msg: Any) -> Any | None:
        try:
            from ..progress_ux import CLIProgressRenderer
        except ImportError:
            return None
        if surface == "cli":
            return CLIProgressRenderer()
        return None

    def _ensure_progress_session(self, msg: SurfaceMessage) -> None:
        if msg.external_id in self._progress_sessions:
            return
        try:
            from ..progress_ux import (
                ProgressSession,
                get_user_verbosity_override,
                resolve_verbosity,
            )
            surface = msg.surface or "cli"
            verbosity = (
                get_user_verbosity_override(msg.external_id)
                or resolve_verbosity(surface)
            )
            progress_session = ProgressSession(surface=surface, verbosity=verbosity)
            renderer = self._create_progress_renderer(surface, msg)
            if renderer is not None:
                progress_session.renderer = renderer
            self._progress_sessions[msg.external_id] = progress_session
            self._set_progress_phase(
                msg.external_id, "intake", "Understanding your request",
            )
        except Exception:
            logger.debug("Progress session init failed", exc_info=True)

    def _build_reassurance_message(
        self, msg: SurfaceMessage, reassurance_count: int,
    ) -> str:
        progress_session = self._progress_sessions.get(msg.external_id)
        elapsed_seconds = (
            progress_session.elapsed_total()
            if progress_session is not None
            else 0.0
        )
        prefix = "Working on it" if reassurance_count == 0 else "Still working"
        if progress_session is not None:
            phase = progress_session.get_current_phase()
            if phase is not None:
                latest = (
                    phase.sub_steps[-1]
                    if phase.sub_steps
                    else (phase.summary or phase.name)
                ).strip().rstrip(".!?")
                if latest:
                    return self._format_progress_status(prefix, latest, elapsed_seconds)

        _ACTIVITY_LABELS = [
            "Gathering relevant context",
            "Researching your request",
            "Analyzing information",
            "Pulling things together",
        ]
        idx = min(reassurance_count, len(_ACTIVITY_LABELS) - 1)
        return self._format_progress_status(prefix, _ACTIVITY_LABELS[idx], elapsed_seconds)

    # ------------------------------------------------------------------
    # Core processing
    # ------------------------------------------------------------------

    async def process(self, msg: SurfaceMessage) -> AsyncIterator[ChatStreamEvent]:
        from dan.telemetry_api import generate_event_id

        turn_event_id = generate_event_id()
        self._current_turn_event_id = turn_event_id
        _telem_start = time.monotonic()
        _telem_tokens: dict[str, int] = {}
        _telem_cost = 0.0
        self._telem_is_fast_command = False
        self._telem_model = None
        self._telem_intent = None
        _telem_success = True

        if self._REASSURANCE_INITIAL_DELAY <= 0 or self._is_messaging_surface(msg):
            try:
                async for event in self._process_inner(msg):
                    if isinstance(event, ChatCompleteEvent):
                        _tu = getattr(event, "token_usage", {}) or {}
                        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                            _telem_tokens[k] = _telem_tokens.get(k, 0) + _tu.get(k, 0)
                        _telem_cost += getattr(event, "estimated_cost", 0) or 0
                    if (
                        self._is_messaging_surface(msg)
                        and isinstance(event, ChatCompleteEvent)
                        and getattr(event, "detected_mode", None) == "progress_ack"
                        and not getattr(event, "phase_label", None)
                    ):
                        continue
                    yield event
            except Exception:
                _telem_success = False
                raise
            finally:
                self._progress_sessions.pop(msg.external_id, None)
                await self._emit_turn_telemetry(
                    turn_event_id=turn_event_id,
                    start_time=_telem_start,
                    msg=msg,
                    tokens=_telem_tokens,
                    cost=_telem_cost,
                    is_fast_command=self._telem_is_fast_command,
                    success=_telem_success,
                )
            return

        _sentinel = object()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        inner_done = asyncio.Event()

        async def _drain_inner() -> None:
            try:
                async for event in self._process_inner(msg):
                    await queue.put(event)
            except Exception as exc:
                await queue.put(exc)
            finally:
                inner_done.set()
                await queue.put(_sentinel)

        drain_task = asyncio.create_task(_drain_inner())
        first_event_received = False
        progress_unlocked = False
        reassurance_count = 0
        start_time = time.monotonic()

        try:
            while True:
                timeout = (
                    self._REASSURANCE_INITIAL_DELAY
                    if not first_event_received
                    else self._REASSURANCE_REPEAT_INTERVAL
                )
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    progress_session = self._progress_sessions.get(msg.external_id)
                    if (
                        progress_session is not None
                        and getattr(progress_session, "verbosity", "compact") == "minimal"
                        and reassurance_count > 0
                    ):
                        reassurance_count += 1
                        continue
                    yield ChatCompleteEvent(
                        message_id=uuid.uuid4().hex[:12],
                        content=self._build_reassurance_message(msg, reassurance_count),
                        token_usage={},
                        context_window=0,
                        graph_revision="",
                        detected_mode="progress_ack",
                    )
                    reassurance_count += 1
                    progress_unlocked = True
                    continue

                if item is _sentinel:
                    break
                if isinstance(item, Exception):
                    raise item
                if isinstance(item, ChatCompleteEvent):
                    _tu = getattr(item, "token_usage", {}) or {}
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        _telem_tokens[k] = _telem_tokens.get(k, 0) + _tu.get(k, 0)
                    _telem_cost += getattr(item, "estimated_cost", 0) or 0
                is_phase_event = (
                    isinstance(item, ChatCompleteEvent)
                    and getattr(item, "detected_mode", None) == "progress_ack"
                )
                if is_phase_event and not progress_unlocked:
                    progress_unlocked = True
                    first_event_received = True
                if not is_phase_event:
                    first_event_received = True
                yield item

            elapsed = time.monotonic() - start_time
            if elapsed > 30.0 and hasattr(self.capability_context, "event_bus") and self.capability_context.event_bus:
                self.capability_context.event_bus.broadcast({
                    "event_type": "notification",
                    "title": "Response Ready",
                    "message": f"Your long-running request ({int(elapsed)}s) has completed.",
                    "level": "info",
                    "surface_id": msg.external_id,
                })
        except Exception:
            _telem_success = False
            raise
        finally:
            self._progress_sessions.pop(msg.external_id, None)
            await self._emit_turn_telemetry(
                turn_event_id=turn_event_id,
                start_time=_telem_start,
                msg=msg,
                tokens=_telem_tokens,
                cost=_telem_cost,
                is_fast_command=self._telem_is_fast_command,
                success=_telem_success,
            )
            if not drain_task.done():
                drain_task.cancel()
                try:
                    await drain_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _process_inner(self, msg: SurfaceMessage) -> AsyncIterator[ChatStreamEvent]:
        self._current_surface_id = msg.external_id
        state_scope_id = self._concierge_state_scope_key(msg.surface, msg.external_id)
        lock = self._volatile_concierge_state_locks.setdefault(state_scope_id, asyncio.Lock())
        async with lock:
            self._concierge_state = self._load_concierge_state(
                state_scope_id,
                legacy_scope_ids=[msg.external_id],
            )
            self._concierge_state.last_interaction_at = time.time()
            self._telem_model = default_llm_model(self.chat_manager) or None
            try:
                fast_event = await self._try_fast_command(msg)
                if fast_event is not None:
                    yield fast_event
                    return

                self._ensure_progress_session(msg)
                intake_event = self._make_phase_event(
                    msg.external_id,
                    "intake",
                    "Understanding your request",
                    force=True,
                )
                if intake_event is not None:
                    yield intake_event

                async for event in self._tiered_dispatcher.dispatch(msg):
                    yield event
            finally:
                self._save_concierge_state(state_scope_id, self._concierge_state)

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _concierge_state_scope_key(surface: str | None, external_id: str | None) -> str:
        surface_text = str(surface or "").strip()
        external_text = str(external_id or "").strip()
        if surface_text and external_text:
            return f"{surface_text}::{external_text}"
        return external_text or surface_text

    def _load_concierge_state(
        self,
        scope_id: str,
        *,
        legacy_scope_ids: list[str] | None = None,
    ) -> ConciergeState:
        return self._memory_services.load_concierge_state(
            scope_id,
            legacy_scope_ids=legacy_scope_ids,
            volatile_states=self._volatile_concierge_states,
        )

    def _save_concierge_state(self, scope_id: str, state: ConciergeState) -> None:
        self._memory_services.save_concierge_state(
            scope_id,
            state,
            volatile_states=self._volatile_concierge_states,
        )

    # ------------------------------------------------------------------
    # Goal progress
    # ------------------------------------------------------------------

    def _select_goal_for_context(self, context: Any) -> ConciergeGoal | None:
        active_goals = [
            goal for goal in self._concierge_state.active_goals
            if goal.status in {"active", "paused"}
        ]
        if not active_goals:
            return None
        project_id = getattr(getattr(context, "project", None), "project_id", None)
        task_id = getattr(getattr(context, "task", None), "task_id", None)
        for goal in reversed(active_goals):
            if goal.project_id == project_id and (not goal.task_id or goal.task_id == task_id):
                return goal
        for goal in reversed(active_goals):
            if goal.project_id in {None, "", project_id}:
                return goal
        return active_goals[-1]

    @staticmethod
    def _goal_progress_summary(goal: ConciergeGoal) -> str:
        pending = goal.progress.pending_steps[:2]
        completed = goal.progress.completed_steps[-1:] if goal.progress.completed_steps else []
        detail_parts: list[str] = []
        if completed:
            detail_parts.append("done: " + "; ".join(completed))
        if pending:
            detail_parts.append("next: " + "; ".join(pending))
        if goal.progress.current_blocker:
            detail_parts.append("blocker: " + goal.progress.current_blocker[:80])
        return " | ".join(detail_parts)

    def _bind_goal_to_context(self, goal: ConciergeGoal, context: Any) -> bool:
        changed = False
        project = getattr(context, "project", None)
        task = getattr(context, "task", None)
        project_id = getattr(project, "project_id", None)
        task_id = getattr(task, "task_id", None)
        task_label = str(getattr(task, "label", "") or "")
        if project_id and goal.project_id != project_id:
            if not goal.project_id:
                goal.project_id = project_id
                changed = True
        if task_id and goal.task_id != task_id:
            if not goal.task_id:
                goal.task_id = task_id
                changed = True
        if goal.progress.project_id != project_id and project_id:
            goal.progress.project_id = project_id
            changed = True
        if goal.progress.task_id != task_id and task_id:
            goal.progress.task_id = task_id
            changed = True
        if task_label and goal.progress.task_label != task_label:
            goal.progress.task_label = task_label
            changed = True
        return changed

    @staticmethod
    def _progress_patch_from_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        progress = metadata.get("progress")
        if not isinstance(progress, dict):
            return {}
        patch: dict[str, Any] = {}
        if "completed_steps" in progress:
            patch["completed_steps"] = _dedupe_keep_order(list(progress.get("completed_steps") or []))
        if "pending_steps" in progress:
            patch["pending_steps"] = _dedupe_keep_order(list(progress.get("pending_steps") or []))
        if "current_blocker" in progress:
            blocker = str(progress.get("current_blocker") or "").strip()
            patch["current_blocker"] = blocker or None
        if "clear_blocker" in progress:
            patch["clear_blocker"] = bool(progress.get("clear_blocker"))
        if "artifacts" in progress and isinstance(progress.get("artifacts"), dict):
            patch["artifacts"] = {
                str(name): str(path)
                for name, path in dict(progress.get("artifacts") or {}).items()
                if str(name or "").strip() and str(path or "").strip()
            }
        if "note" in progress:
            patch["note"] = str(progress.get("note") or "").strip()
        return patch

    def _apply_goal_progress_update(
        self,
        goal: ConciergeGoal,
        update: GoalProgressEntry,
    ) -> None:
        snapshot = goal.progress
        snapshot.project_id = goal.project_id or snapshot.project_id
        snapshot.task_id = update.task_id or goal.task_id or snapshot.task_id
        snapshot.task_label = update.task_label or snapshot.task_label
        if update.completed_steps:
            snapshot.completed_steps = _dedupe_keep_order(
                [*snapshot.completed_steps, *update.completed_steps],
            )
        if update.pending_steps:
            completed = set(snapshot.completed_steps)
            snapshot.pending_steps = [
                step for step in _dedupe_keep_order(update.pending_steps)
                if step not in completed
            ]
        elif update.completed_steps and snapshot.pending_steps:
            completed = set(snapshot.completed_steps)
            snapshot.pending_steps = [
                step for step in snapshot.pending_steps if step not in completed
            ]
        if update.clear_blocker:
            snapshot.current_blocker = None
        elif update.current_blocker is not None:
            snapshot.current_blocker = update.current_blocker
        if update.artifacts:
            snapshot.artifacts.update(update.artifacts)
        if update.note:
            snapshot.last_note = update.note[:200]
        snapshot.updated_at = update.timestamp
        goal.updated_at = update.timestamp
        goal.progress_log.append(update)
        if len(goal.progress_log) > 40:
            goal.progress_log = goal.progress_log[-40:]

    def _project_goal_progress_to_task(
        self,
        context: Any,
        msg: SurfaceMessage,
        goal: ConciergeGoal,
        *,
        clear_blocker: bool = False,
    ) -> None:
        snapshot = goal.progress
        self.project_store.update_task_progress(
            context.project.project_id,
            context.task.task_id,
            msg.external_id,
            completed_steps=_compact_task_history(snapshot.completed_steps),
            pending_steps=snapshot.pending_steps,
            current_blocker=snapshot.current_blocker,
            artifacts=snapshot.artifacts,
            goal_id=goal.id,
            progress_updated_at=snapshot.updated_at,
            clear_blocker=clear_blocker,
        )
        self._refresh_context_task_from_store(context, msg.external_id)

    def _record_task_progress_event(
        self,
        context: Any,
        msg: SurfaceMessage,
        *,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        note: str = "",
        clear_blocker: bool = False,
    ) -> None:
        text_patch = _extract_task_state(content) if content else {
            "completed_steps": [],
            "pending_steps": [],
            "current_blocker": None,
            "artifacts": {},
        }
        metadata_patch = self._progress_patch_from_metadata(metadata)
        content_has_pending = bool(text_patch["pending_steps"])
        metadata_has_pending = "pending_steps" in metadata_patch
        completed_steps = _dedupe_keep_order([
            *text_patch["completed_steps"],
            *list(metadata_patch.get("completed_steps") or []),
        ])
        pending_steps = list(metadata_patch["pending_steps"]) if metadata_has_pending else list(text_patch["pending_steps"])
        blocker = text_patch.get("current_blocker")
        if "current_blocker" in metadata_patch:
            blocker = metadata_patch.get("current_blocker")
        artifacts = dict(text_patch.get("artifacts", {}))
        artifacts.update(dict(metadata_patch.get("artifacts") or {}))
        detail_note = str(metadata_patch.get("note") or note or "").strip()
        should_apply = bool(
            completed_steps
            or artifacts
            or clear_blocker
            or metadata_has_pending
            or content_has_pending
            or blocker is not None
            or detail_note
        )
        if not should_apply:
            return

        goal = self._select_goal_for_context(context)
        state_scope_id = self._concierge_state_scope_key(msg.surface, msg.external_id)
        if goal is not None:
            self._bind_goal_to_context(goal, context)
            update = GoalProgressEntry(
                source=role if role in {"goal_command", "user_turn", "assistant_turn", "task_finalize", "system"} else "system",
                task_id=context.task.task_id,
                task_label=context.task.label,
                note=detail_note,
                completed_steps=completed_steps,
                pending_steps=pending_steps if (metadata_has_pending or content_has_pending) else [],
                current_blocker=blocker,
                clear_blocker=clear_blocker or bool(metadata_patch.get("clear_blocker")),
                artifacts=artifacts,
            )
            self._apply_goal_progress_update(goal, update)
            self._save_concierge_state(state_scope_id, self._concierge_state)
            self._project_goal_progress_to_task(
                context,
                msg,
                goal,
                clear_blocker=update.clear_blocker,
            )
            return

        completed = _dedupe_keep_order([*context.task.completed_steps, *completed_steps])
        pending = list(context.task.pending_steps)
        if metadata_has_pending or content_has_pending:
            completed_set = set(completed)
            pending = [step for step in _dedupe_keep_order(pending_steps) if step not in completed_set]
        elif completed_steps and pending:
            completed_set = set(completed)
            pending = [step for step in pending if step not in completed_set]
        merged_artifacts = dict(context.task.artifacts)
        merged_artifacts.update(artifacts)
        if clear_blocker or bool(metadata_patch.get("clear_blocker")):
            current_blocker = None
            clear_task_blocker = True
        elif blocker is not None:
            current_blocker = blocker
            clear_task_blocker = False
        else:
            current_blocker = context.task.current_blocker
            clear_task_blocker = False
        self.project_store.update_task_progress(
            context.project.project_id,
            context.task.task_id,
            msg.external_id,
            completed_steps=_compact_task_history(completed),
            pending_steps=pending,
            current_blocker=current_blocker,
            artifacts=merged_artifacts,
            progress_updated_at=time.time(),
            clear_blocker=clear_task_blocker,
        )
        self._refresh_context_task_from_store(context, msg.external_id)

    # ------------------------------------------------------------------
    # Turn recording
    # ------------------------------------------------------------------

    def _record_assistant_turn(
        self,
        context,
        msg: SurfaceMessage,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        write_conversation_memory: bool = True,
    ) -> None:
        self.project_store.append_turn(
            context.project.project_id,
            context.task.task_id,
            TaskTurn(
                role="assistant",
                content=content,
                intent=None,
                metadata=metadata or {},
            ),
            msg.external_id,
        )
        self._record_task_progress_event(
            context,
            msg,
            role="assistant_turn",
            content=content,
            metadata=metadata,
        )
        if self.conversation_memory is not None and content and write_conversation_memory:
            summary = f"{context.project.label}: {content[:160]}"
            self.conversation_memory.add_summary(
                summary=summary,
                workflow_id=(context.project.linked_workflow_ids[-1] if context.project.linked_workflow_ids else ""),
                topic_tags=[context.project.label, context.task.label],
            )
        summary_text = self._build_project_summary(context, content)
        if summary_text:
            self.project_store.update_project_summary(
                context.project.project_id, summary_text, msg.external_id,
            )
        self._maybe_auto_summarize(context, msg, on_complete=False)

    def _build_project_summary(self, context, content: str) -> str:
        parts = [context.project.summary.strip(), f"Latest task {context.task.label}: {content[:200].strip()}"]
        summary = " ".join(part for part in parts if part).strip()
        return summary[:400]

    def _maybe_auto_summarize(
        self, context: ResolvedContext, msg: SurfaceMessage, *, on_complete: bool = False,
    ) -> None:
        turn_count = len(context.task.turns)
        should_trigger = on_complete or (
            turn_count > 0 and turn_count % self.auto_summarize_turn_threshold == 0
        )
        if not should_trigger:
            return
        recent = context.task.turns[-10:]
        lines = [f"{t.role}: {t.content[:120]}" for t in recent if t.content]
        if not lines:
            return
        summary = f"{context.project.label} / {context.task.label}: " + " | ".join(
            t.content[:80].strip() for t in recent[-3:] if t.content
        )
        try:
            self.project_store.update_project_summary(
                context.project.project_id, summary[:400], msg.external_id,
            )
        except Exception:
            logger.debug("Auto-summarize failed for project %s", context.project.project_id, exc_info=True)

    # ------------------------------------------------------------------
    # Live learning loop
    # ------------------------------------------------------------------

    @staticmethod
    def _feedback_signal_threshold() -> float:
        return 0.7

    @staticmethod
    def _prompt_proposal_min_negative_signals() -> int:
        raw = os.environ.get("DAN_PROMPT_PROPOSAL_MIN_NEGATIVE", "20").strip()
        try:
            return max(1, int(raw))
        except ValueError:
            return 20

    def _latest_assistant_turn(self, task: Task) -> TaskTurn | None:
        for turn in reversed(task.turns):
            if turn.role == "assistant" and turn.content.strip():
                return turn
        return None

    def _prompt_key_for_turn(self, turn: TaskTurn | None) -> str | None:
        if turn is None or not isinstance(getattr(turn, "metadata", None), dict):
            return None
        metadata = turn.metadata
        prompt_key = str(metadata.get("active_prompt_key") or "").strip()
        if prompt_key:
            return prompt_key
        if metadata.get("session_tree") or metadata.get("autonomy_resolution"):
            return _RUNTIME_UNIFIED_PROMPT_KEY
        return None

    def _prompt_baseline_quality(self, prompt_key: str, *, limit: int = 50) -> float:
        tracker = getattr(self, "_prompt_tracker", None)
        if tracker is None:
            return 1.0
        history = tracker.get_system_prompt_history(prompt_key, limit=limit)
        qualities = [
            float(item.get("quality_score", 1.0) or 0.0)
            for item in history
            if isinstance(item, dict)
        ]
        if not qualities:
            return 1.0
        return max(0.0, min(1.0, sum(qualities) / len(qualities)))

    def _record_prompt_outcome(
        self,
        prompt_key: str | None,
        *,
        outcome: bool,
        quality_score: float,
    ) -> None:
        tracker = getattr(self, "_prompt_tracker", None)
        if tracker is None or not prompt_key:
            return
        try:
            tracker.record_system_prompt_outcome(
                prompt_key,
                outcome=outcome,
                quality_score=quality_score,
            )
            self._update_prompt_candidate_measurements(prompt_key)
        except Exception:
            logger.debug("Failed to record prompt outcome for %s", prompt_key, exc_info=True)

    def _store_correction_actions(
        self,
        actions: list[dict[str, Any]],
        *,
        project_id: str | None = None,
    ) -> None:
        if self.memory_kernel is None:
            return
        for action in actions:
            action_type = str(action.get("type") or "").strip()
            value = str(action.get("value") or "").strip()
            if not value:
                continue
            try:
                if action_type == "preference":
                    self.memory_kernel.store_preference(
                        value,
                        confirmed=True,
                        project_id=project_id,
                        tags=["correction_feedback"],
                    )
                elif action_type == "principle":
                    self.memory_kernel.store_principle(
                        value,
                        confidence=0.9,
                        project_id=project_id,
                        tags=["correction_feedback"],
                    )
            except Exception:
                logger.debug("Failed to persist correction action %s", action_type, exc_info=True)

    def _has_prompt_candidate(self, prompt_key: str) -> bool:
        registry = getattr(self, "_adaptation_registry", None)
        if registry is None:
            return False
        for candidate in [
            *registry.list_pending(),
            *registry.list_applied(),
            *registry.list_queued(),
        ]:
            if getattr(candidate, "parameter_key", None) == prompt_key:
                return True
        return False

    def _has_recent_prompt_rollback(self, prompt_key: str) -> bool:
        registry = getattr(self, "_adaptation_registry", None)
        if registry is None:
            return False
        cooldown_raw = os.environ.get("DAN_PROMPT_REPROPOSE_COOLDOWN_SECONDS", "3600").strip()
        try:
            cooldown_seconds = max(0, int(cooldown_raw))
        except ValueError:
            cooldown_seconds = 3600
        now = time.time()
        for candidate in getattr(registry, "_candidates", {}).values():
            if getattr(candidate, "parameter_key", None) != prompt_key:
                continue
            if getattr(candidate, "status", None) != "rolled_back":
                continue
            created_at = getattr(candidate, "created_at", None)
            created_ts = created_at.timestamp() if hasattr(created_at, "timestamp") else 0.0
            if now - created_ts <= cooldown_seconds:
                return True
        return False

    def _recent_corrections_for_prompt(
        self,
        prompt_key: str,
        *,
        limit: int = 20,
    ) -> list[Any]:
        store = getattr(self, "_correction_store", None)
        if store is None:
            return []
        matches: list[Any] = []
        for record in reversed(store.list_recent(limit * 3)):
            if getattr(record, "active_prompt_key", None) != prompt_key:
                continue
            signal = getattr(record, "signal", None)
            if signal is None or float(getattr(signal, "confidence", 0.0) or 0.0) < self._feedback_signal_threshold():
                continue
            matches.append(record)
            if len(matches) >= limit:
                break
        return matches

    def _prompt_adjustment_bullets(self, records: list[Any]) -> list[str]:
        bullets: list[str] = []
        for record in records:
            signal = getattr(record, "signal", None)
            if signal is None:
                continue
            correction_text = str(getattr(signal, "correction_text", "") or "").strip()
            lower = correction_text.lower()
            preference = str(getattr(signal, "extracted_preference", "") or "").strip()
            principle = str(getattr(signal, "extracted_principle", "") or "").strip()
            correction_type = str(getattr(signal, "correction_type", "") or "").strip()

            if preference:
                bullets.append(f"Honor explicit user preference: {preference}.")
            if principle:
                if "summarize first" in principle.lower():
                    bullets.append("Lead with a short summary before the supporting detail when the response is long.")
                elif any(token in principle.lower() for token in ("short", "concise")):
                    bullets.append("Default to concise answers unless the user explicitly asks for more depth.")
                else:
                    bullets.append(f"Follow this response rule when relevant: {principle}.")
            elif correction_type == "style":
                if any(token in lower for token in ("short", "concise", "verbose", "wordy")):
                    bullets.append("Default to concise answers unless the user explicitly asks for more depth.")
                elif "summarize first" in lower:
                    bullets.append("Lead with a short summary before the supporting detail when the response is long.")
            elif correction_type == "override":
                bullets.append("When the user explicitly overrides a prior choice, follow the replacement and do not repeat the superseded default.")
            elif correction_type in {"negation", "redo"}:
                bullets.append("If the user indicates the prior answer was off target, restate the corrected goal briefly and continue from the correction instead of repeating the prior framing.")

        return _dedupe_keep_order([bullet.strip() for bullet in bullets if bullet.strip()])

    def _build_prompt_candidate_payload(
        self,
        prompt_key: str,
        records: list[Any],
    ) -> tuple[str, str, list[str], list[str]] | None:
        store = getattr(self, "_behavior_store", None)
        if store is None:
            return None
        before_value = store.get(prompt_key, "")
        if not isinstance(before_value, str) or not before_value.strip():
            return None

        bullets = self._prompt_adjustment_bullets(records)
        if not bullets:
            return None

        existing_lower = before_value.lower()
        missing = [bullet for bullet in bullets if bullet.lower() not in existing_lower]
        if not missing:
            return None

        marker = "## Learned response adjustments"
        if marker.lower() in existing_lower:
            after_value = before_value.rstrip() + "\n" + "\n".join(f"- {bullet}" for bullet in missing)
        else:
            after_value = before_value.rstrip() + "\n\n" + marker + "\n" + "\n".join(
                f"- {bullet}" for bullet in missing
            )
        evidence = _dedupe_keep_order([
            str(getattr(getattr(record, "signal", None), "correction_text", "") or "").strip()[:160]
            for record in records[:5]
            if str(getattr(getattr(record, "signal", None), "correction_text", "") or "").strip()
        ])
        return before_value, after_value, missing, evidence

    def _rollback_prompt_candidate(self, candidate: Any) -> None:
        prompt_key = str(getattr(candidate, "parameter_key", "") or "").strip()
        before_value = getattr(candidate, "before_value", None)
        if not prompt_key or not isinstance(before_value, str) or self._behavior_store is None:
            return
        try:
            self._behavior_store.set(
                prompt_key,
                before_value,
                reason=f"Rolled back adaptation {candidate.id}",
                evidence=[str(getattr(candidate, "last_outcome", "") or "Regression detected")],
            )
        except Exception:
            logger.warning("Failed to roll back prompt candidate %s", candidate.id, exc_info=True)

    def _apply_prompt_candidate(self, candidate_id: str, *, auto_apply: bool) -> bool:
        registry = getattr(self, "_adaptation_registry", None)
        store = getattr(self, "_behavior_store", None)
        if registry is None or store is None:
            return False
        candidate = registry.get(candidate_id)
        if candidate is None or candidate.status != "pending":
            return False
        prompt_key = str(getattr(candidate, "parameter_key", "") or "").strip()
        after_value = getattr(candidate, "after_value", None)
        before_value = getattr(candidate, "before_value", None)
        if not prompt_key or not isinstance(after_value, str) or not isinstance(before_value, str):
            return False
        current_value = store.get(prompt_key, "")
        if current_value != before_value:
            logger.warning(
                "Skipping stale prompt candidate %s for %s: prompt changed since proposal",
                candidate.id,
                prompt_key,
            )
            return False
        baseline_quality = self._prompt_baseline_quality(prompt_key)
        try:
            store.set(
                prompt_key,
                after_value,
                reason=f"{'Auto-applied' if auto_apply else 'Approved'} prompt adaptation {candidate.id}",
                evidence=list(getattr(candidate, "evidence", []) or []),
            )
            if auto_apply:
                registry.auto_apply_candidate(candidate.id, baseline_quality=baseline_quality)
            else:
                registry.approve(candidate.id, baseline_quality=baseline_quality)
            return True
        except Exception:
            logger.warning("Failed to apply prompt candidate %s", candidate.id, exc_info=True)
            try:
                store.set(
                    prompt_key,
                    before_value,
                    reason=f"Restore prompt after failed adaptation {candidate.id}",
                    evidence=[],
                )
            except Exception:
                logger.debug("Prompt restore after failed apply also failed", exc_info=True)
            return False

    def _maybe_propose_prompt_candidate(self, prompt_key: str) -> None:
        tracker = getattr(self, "_prompt_tracker", None)
        registry = getattr(self, "_adaptation_registry", None)
        if tracker is None or registry is None or not prompt_key:
            return
        if self._has_prompt_candidate(prompt_key):
            return
        if self._has_recent_prompt_rollback(prompt_key):
            return

        proposal = tracker.propose_prompt_variant(
            prompt_key,
            adaptation_registry=registry,
            min_negative_signals=self._prompt_proposal_min_negative_signals(),
        )
        if not proposal or not proposal.get("proposal_id"):
            return

        candidate = registry.get(str(proposal["proposal_id"]))
        if candidate is None:
            return

        records = self._recent_corrections_for_prompt(
            prompt_key,
            limit=self._prompt_proposal_min_negative_signals(),
        )
        payload = self._build_prompt_candidate_payload(prompt_key, records)
        if payload is None:
            try:
                registry.reject(candidate.id)
            except Exception:
                logger.debug("Failed to reject incomplete prompt candidate %s", candidate.id, exc_info=True)
            return

        before_value, after_value, bullets, evidence = payload
        candidate.parameter_key = prompt_key
        candidate.before_value = before_value
        candidate.after_value = after_value
        candidate.rollback_path = prompt_key
        candidate.scope = "global"
        candidate.evidence = _dedupe_keep_order([*list(candidate.evidence or []), *evidence])
        guidance = "; ".join(bullets[:3])
        candidate.description = (
            f"{candidate.description} Proposed append-only guidance: {guidance}"
            if guidance else candidate.description
        ).strip()

        if candidate.auto_apply:
            self._apply_prompt_candidate(candidate.id, auto_apply=True)

    def _update_prompt_candidate_measurements(self, prompt_key: str) -> None:
        tracker = getattr(self, "_prompt_tracker", None)
        registry = getattr(self, "_adaptation_registry", None)
        if tracker is None or registry is None or not prompt_key:
            return
        history = tracker.get_system_prompt_history(prompt_key, limit=500)
        if not history:
            return

        for candidate in list(registry.list_applied()):
            if getattr(candidate, "parameter_key", None) != prompt_key:
                continue
            if candidate.applied_at is None:
                continue
            applied_ts = candidate.applied_at.timestamp()
            post_history = [
                item for item in history
                if float(item.get("recorded_at", 0.0) or 0.0) >= applied_ts
            ]
            if not post_history:
                continue

            qualities = [
                float(item.get("quality_score", 1.0) or 0.0)
                for item in post_history
                if isinstance(item, dict)
            ]
            quality = max(0.0, min(1.0, sum(qualities) / max(len(qualities), 1)))
            interaction_count = len(post_history)
            registry.record_post_adaptation_outcome(
                candidate.id,
                quality_metric=quality,
                interaction_count=interaction_count,
            )

            current_candidate = registry.get(candidate.id) or candidate
            if (
                current_candidate.status == "applied"
                and interaction_count >= current_candidate.measurement_target
            ):
                baseline_failure_rate = max(
                    0.0,
                    1.0 - float(current_candidate.baseline_quality or 0.0),
                )
                regression = tracker.check_prompt_regression(
                    prompt_key,
                    baseline_failure_rate=baseline_failure_rate,
                    window_size=current_candidate.measurement_target,
                )
                if regression is not None and current_candidate.status == "applied":
                    registry.rollback(
                        current_candidate.id,
                        (
                            "Prompt regression detected: "
                            f"{regression['current_failure_rate']:.0%} recent failure rate"
                        ),
                    )
                    current_candidate = registry.get(current_candidate.id) or current_candidate
                if current_candidate.status == "applied":
                    registry.complete_measurement(current_candidate.id)

            if current_candidate.status == "rolled_back":
                self._rollback_prompt_candidate(current_candidate)

    def _maybe_record_turn_feedback(
        self,
        context: Any,
        msg: SurfaceMessage,
    ) -> None:
        task = getattr(context, "task", None)
        if task is None:
            return
        previous_assistant = self._latest_assistant_turn(task)
        if previous_assistant is None:
            return

        prompt_key = self._prompt_key_for_turn(previous_assistant)
        project_id = getattr(getattr(context, "project", None), "project_id", None)
        analyzer = self._analyze_turn_feedback
        if analyzer is None:
            return

        analysis = analyzer(
            user_message=msg.text,
            previous_assistant_message=previous_assistant.content,
            prompt_key=prompt_key,
            min_confidence=self._feedback_signal_threshold(),
        )
        if analysis is None:
            return
        if analysis.is_positive_outcome:
            self._record_prompt_outcome(
                prompt_key,
                outcome=True,
                quality_score=1.0,
            )
            return

        if self._correction_store is not None and analysis.correction_record is not None:
            self._correction_store.add(analysis.correction_record)
        self._store_correction_actions(analysis.actions, project_id=project_id)
        self._record_prompt_outcome(
            prompt_key,
            outcome=False,
            quality_score=0.0,
        )
        if prompt_key:
            self._maybe_propose_prompt_candidate(prompt_key)

    # ------------------------------------------------------------------
    # Task finalization
    # ------------------------------------------------------------------

    def _finalize_task(
        self,
        context,
        msg: SurfaceMessage,
        intent: IntentCategory,
        has_content: bool,
        task_status_override: str | None = None,
    ) -> None:
        if not has_content and task_status_override is None:
            return
        status = task_status_override or (
            "completed"
            if intent in (IntentCategory.PLAN, IntentCategory.AGENT, IntentCategory.ASK)
            else "paused"
        )
        self.project_store.update_task_status(
            context.project.project_id,
            context.task.task_id,
            status,
            msg.external_id,
        )
        self._refresh_context_task_from_store(context, msg.external_id)

        if status in ("completed", "paused"):
            try:
                self._record_task_progress_event(
                    context,
                    msg,
                    role="task_finalize",
                    content="",
                    metadata=None,
                    note=f"Task {status}",
                    clear_blocker=(status == "completed"),
                )
            except Exception:
                logger.debug("Auto-populate task state failed", exc_info=True)

        if status == "completed":
            self._maybe_auto_summarize(context, msg, on_complete=True)
            self._trigger_domain_reflection(context, msg)

    def _refresh_context_task_from_store(self, context: Any, surface_id: str) -> None:
        try:
            fresh_project = self.project_store.get_project(
                context.project.project_id, surface_id,
            )
            if fresh_project is None:
                return
            for fresh_task in fresh_project.tasks:
                if fresh_task.task_id == context.task.task_id:
                    context.project = fresh_project
                    context.task = fresh_task
                    return
        except Exception:
            logger.debug("Context refresh from store failed", exc_info=True)

    def _trigger_domain_reflection(self, context: Any, msg: SurfaceMessage) -> None:
        domain = getattr(context, "domain", None)
        if not domain or not self.memory_kernel:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._domain_reflect_async(context, domain))
        self._bg_memory_tasks.add(task)
        task.add_done_callback(self._bg_memory_tasks.discard)

    async def _domain_reflect_async(self, context: Any, domain: str) -> None:
        try:
            from ..domain_learning import DomainReflector

            if not self.memory_kernel or not self._feature_enabled("domain_learning"):
                return
            model = self._resolve_triage_model()
            main_loop = asyncio.get_running_loop()
            pii_session_key = self._current_surface_id

            class _ThreadedReflectionLLM:
                def __init__(
                    self,
                    chat_manager: Any,
                    model_name: str,
                    loop: asyncio.AbstractEventLoop,
                    pii_key: str | None,
                ) -> None:
                    self._chat_manager = chat_manager
                    self._model_name = model_name
                    self._loop = loop
                    self._pii_key = pii_key

                def complete(self, prompt: str, max_tokens: int = 1000) -> str:
                    async def _call() -> str:
                        messages = [{"role": "user", "content": prompt}]
                        result = await complete_chat_surface(
                            self._chat_manager,
                            messages=messages,
                            model=self._model_name,
                            temperature=0.2,
                            max_tokens=max_tokens,
                            pii_session_key=self._pii_key,
                        )
                        return result.text
                    return _run_coroutine_sync(_call(), loop=self._loop)

            threaded_llm = _ThreadedReflectionLLM(
                self.chat_manager,
                model,
                main_loop,
                pii_session_key,
            )
            reflector = DomainReflector(
                memory_kernel=self.memory_kernel,
                llm=threaded_llm,
                feature_enabled=self._feature_enabled,
            )
            turns = list(context.task.turns)
            items = await asyncio.to_thread(reflector.reflect, domain, turns)
            if items:
                project_id = getattr(getattr(context, "project", None), "project_id", None)
                self._memory_services.apply_project_scope(items, project_id)
                self.memory_kernel.store_many(items)
                logger.info("Domain reflection extracted %d items for %s", len(items), domain)
            await asyncio.to_thread(self._maybe_run_domain_template_upgrade, domain, threaded_llm)
        except Exception:
            logger.debug("Domain reflection failed", exc_info=True)

    def _maybe_run_domain_template_upgrade(self, domain: str, llm: Any) -> None:
        try:
            from ..domain_learning import (
                DomainTemplateUpgrader,
                get_or_create_template,
                save_domain_template,
            )
            if not self._feature_enabled("domain_template_upgrade"):
                return
            template = get_or_create_template(domain)
            if not template.metadata.get("needs_llm_upgrade"):
                return
            items = self._memory_services.list_domain_upgrade_items(domain)
            upgrader = DomainTemplateUpgrader(llm=llm)
            updated = upgrader.upgrade(domain, template, items)
            target = updated or template
            target.metadata["last_llm_upgrade_item_count"] = len(items)
            target.metadata.pop("needs_llm_upgrade", None)
            save_domain_template(target)
        except Exception:
            logger.debug("Domain template upgrade failed", exc_info=True)

    # ------------------------------------------------------------------
    # Memory helpers (used by tiered_dispatch lifecycle hooks)
    # ------------------------------------------------------------------

    def _retrieve_memory_context(
        self,
        message: str,
        *,
        has_active_build: bool = False,
        project_id: str | None = None,
        max_chars: int = 1500,
    ) -> str:
        return self._memory_services.retrieve_memory_context(
            message,
            has_active_build=has_active_build,
            project_id=project_id,
            max_chars=max_chars,
        )

    def _retrieve_domain_expertise(
        self,
        query: str,
        domain: str,
        project_id: str | None = None,
        *,
        max_items: int = 8,
        max_chars: int = 1200,
    ) -> str:
        return self._memory_services.retrieve_domain_expertise(
            query,
            domain,
            project_id=project_id,
            max_items=max_items,
            max_chars=max_chars,
        )

    def _extract_memory_candidates(
        self,
        message: str,
        response: str,
        goal_context: dict[str, Any] | None = None,
    ) -> list[Any]:
        return self._memory_services.extract_memory_candidates(
            message,
            response,
            goal_context,
        )

    def _store_memory_candidates(
        self,
        message: str,
        response: str,
        goal_context: dict[str, Any] | None = None,
        project_id: str | None = None,
        domain: str | None = None,
        include_episode: bool = True,
    ) -> None:
        if not self.memory_kernel:
            return
        domain_warnings = list(self._domain_warning_context().get())
        self._domain_warning_context().set(())
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._store_memory_candidates_sync(
                message, response, goal_context, project_id,
                domain=domain, domain_warnings=domain_warnings,
                include_episode=include_episode,
            )
            return
        task = loop.create_task(
            self._store_memory_candidates_async(
                message, response, goal_context, project_id,
                domain=domain, domain_warnings=domain_warnings,
                include_episode=include_episode,
            ),
        )
        self._bg_memory_tasks.add(task)
        task.add_done_callback(self._bg_memory_tasks.discard)

    def _store_memory_candidates_sync(
        self,
        message: str,
        response: str,
        goal_context: dict[str, Any] | None = None,
        project_id: str | None = None,
        domain: str | None = None,
        domain_warnings: list[str] | None = None,
        include_episode: bool = True,
    ) -> None:
        if include_episode:
            self._store_episode_candidates(message, response, goal_context)
        self._try_extract_preferences(message, response, project_id=project_id)
        self._try_memory_extraction(message, response, goal_context, project_id=project_id, domain=domain)
        if domain_warnings:
            self._store_domain_validation_warnings(domain_warnings, domain)

    async def _store_memory_candidates_async(
        self,
        message: str,
        response: str,
        goal_context: dict[str, Any] | None = None,
        project_id: str | None = None,
        domain: str | None = None,
        domain_warnings: list[str] | None = None,
        include_episode: bool = True,
    ) -> None:
        from ..fan_out import fan_out_dict
        tasks = {
            "preferences": lambda: asyncio.to_thread(
                self._try_extract_preferences, message, response, project_id=project_id,
            ),
            "memory_extraction": lambda: asyncio.to_thread(
                self._try_memory_extraction, message, response, goal_context,
                project_id=project_id, domain=domain, main_loop=asyncio.get_running_loop(),
            ),
        }
        if include_episode:
            tasks["episode"] = lambda: asyncio.to_thread(
                self._store_episode_candidates, message, response, goal_context,
            )
        if domain_warnings:
            warnings_copy = list(domain_warnings)
            tasks["domain_validation_log"] = lambda: asyncio.to_thread(
                self._store_domain_validation_warnings, warnings_copy, domain,
            )
        results = await fan_out_dict(tasks)
        for name, result in results.items():
            if isinstance(result, Exception):
                logger.debug("Memory extraction step '%s' failed", name, exc_info=result)

    def _store_episode_candidates(
        self, message: str, response: str, goal_context: dict[str, Any] | None = None,
    ) -> None:
        candidates = self._extract_memory_candidates(message, response, goal_context)
        for item in candidates:
            try:
                self.memory_kernel.store(item)
            except Exception:
                logger.debug("Failed to store memory candidate", exc_info=True)

    def _try_extract_preferences(
        self, user_message: str, assistant_message: str, project_id: str | None = None,
    ) -> None:
        store_extracted_preferences(
            memory_kernel=self.memory_kernel,
            behavior_store=getattr(self, "_behavior_store", None),
            user_message=user_message,
            assistant_message=assistant_message,
            project_id=project_id,
        )

    def _try_memory_extraction(
        self,
        user_message: str,
        assistant_message: str,
        goal_context: dict[str, Any] | None = None,
        project_id: str | None = None,
        domain: str | None = None,
        main_loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        store_extracted_memories(
            memory_kernel=self.memory_kernel,
            user_profile=self.user_profile,
            user_message=user_message,
            assistant_message=assistant_message,
            goal_context=goal_context,
            project_id=project_id,
            domain=domain,
            main_loop=main_loop,
            run_coroutine_sync=_run_coroutine_sync,
        )

    def _store_domain_validation_warnings(
        self, warnings: list[str], domain: str | None,
    ) -> None:
        self._memory_services.store_domain_validation_warnings(warnings, domain)

    # ------------------------------------------------------------------
    # Pending follow-up resolution (used by TieredDispatcher)
    # ------------------------------------------------------------------

    def _resolve_pending_follow_up(
        self,
        msg: SurfaceMessage,
    ) -> tuple[ChatCompleteEvent | None, ResolvedContext, TriageResult, SurfaceMessage] | None:
        pending_projects = self.project_store.list_pending_projects(msg.external_id)
        if not pending_projects:
            return None
        project = self._select_pending_project(msg, pending_projects)
        if isinstance(project, ChatCompleteEvent):
            return project, self._pending_context_fallback(msg.external_id, pending_projects), TriageResult(
                tier=1, intent="ask", confidence=1.0, goal=msg.text,
            ), msg
        if project is None or project.pending_action is None:
            return None
        task = self.project_store.get_current_task(project.project_id, msg.external_id)
        if task is None:
            return None
        context = ResolvedContext(
            project=project, task=task,
            is_new_project=False, is_new_task=False,
            confidence=1.0, domain=project.domain,
        )
        pending = project.pending_action
        context = self._populate_resolved_context_domain(context, pending.original_text or msg.text)
        resolution = resolve_pending_reply(pending, msg.text)
        triage = TriageResult(
            tier=1,
            intent=pending.intent,
            confidence=1.0,
            goal=pending.original_text,
        )

        if resolution.action == "cancel":
            self.project_store.clear_pending_action(project.project_id, msg.external_id)
            self.project_store.update_task_status(project.project_id, task.task_id, "paused", msg.external_id)
            return self._complete_event(
                content=f"{format_prefix(project.label)} Cancelled.",
            ), context, triage, msg

        if resolution.action == "resume":
            self.project_store.clear_pending_action(project.project_id, msg.external_id)
            replay_msg = msg.model_copy(
                update={
                    "text": resolution.replay_text,
                    "metadata": self._with_resolved_context_metadata(
                        {**pending.metadata, **msg.metadata, **resolution.metadata},
                        context,
                    ),
                }
            )
            return None, context, triage, replay_msg

        if resolution.action == "prompt_retry":
            return self._complete_event(
                content=f"{format_prefix(project.label)} {resolution.response_text}",
            ), context, triage, msg

        return None

    def _select_pending_project(self, msg: SurfaceMessage, projects: list[Project]) -> Project | ChatCompleteEvent | None:
        if len(projects) == 1:
            return projects[0]
        lower = msg.text.lower()
        for project in projects:
            if f"/project {project.label.lower()}" in lower:
                return project
        for project in projects:
            label = project.label.lower()
            if label and label in lower:
                return project
        labels = ", ".join(project.label for project in projects[:5])
        return self._complete_event(
            content=f"{format_bare_prefix()} Multiple projects are waiting for a reply. Which project? Use /project <label>. Pending: {labels}"
        )

    def _pending_context_fallback(self, surface_id: str, projects: list[Project]) -> ResolvedContext:
        project = projects[0]
        task = self.project_store.get_current_task(project.project_id, surface_id)
        if task is None:
            task = self.project_store.add_task(project.project_id, project.label, surface_id)
        return ResolvedContext(
            project=project, task=task,
            is_new_project=False, is_new_task=False,
            confidence=1.0, domain=project.domain,
        )

    def _populate_resolved_context_domain(self, context: ResolvedContext, text: str) -> ResolvedContext:
        if context.domain:
            return context
        try:
            from ..domain_learning import detect_domain
            detected = detect_domain(
                text, context.project,
                behavior_store=self._behavior_store,
                pattern_accumulator=self._pattern_accumulator,
            )
            if detected:
                context.domain = detected
        except Exception:
            logger.debug("Pending context domain detection failed", exc_info=True)
        return context

    def _with_resolved_context_metadata(
        self, metadata: dict[str, Any], context: ResolvedContext,
    ) -> dict[str, Any]:
        enriched = dict(metadata)
        enriched["resolved_project_id"] = context.project.project_id
        enriched["resolved_task_id"] = context.task.task_id
        if context.domain:
            enriched["resolved_domain"] = context.domain
        return enriched

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # /autonomy command
    # ------------------------------------------------------------------

    def handle_autonomy_command(self, msg: SurfaceMessage) -> str:
        """Handle ``/autonomy`` — inspect or change session/project autonomy."""
        text = msg.text.strip()
        tokens = text.split()
        args = tokens[1:] if len(tokens) > 1 else []
        project_scope = False
        requested: str | None = None

        for arg in args:
            if arg == "--project":
                project_scope = True
                continue
            if requested is None:
                requested = normalize_autonomy_preference(arg, default="")
        state = self._concierge_state
        if requested == "":
            return "Usage: `/autonomy [auto|careful|balanced|aggressive] [--project]`"
        if requested and requested not in {
            AutonomyPreference.AUTO.value,
            AutonomyPreference.CAREFUL.value,
            AutonomyPreference.BALANCED.value,
            AutonomyPreference.AGGRESSIVE.value,
        }:
            requested = None

        msg_metadata = getattr(msg, "metadata", None)
        resolved_project_id = None
        if isinstance(msg_metadata, dict):
            resolved_project_id = str(msg_metadata.get("resolved_project_id") or "").strip() or None
        active_projects = self.project_store.list_active(msg.external_id)
        project = None
        if resolved_project_id:
            project = self.project_store.get_project(resolved_project_id, msg.external_id)
        elif len(active_projects) == 1:
            project = active_projects[0]
        project_pref = normalize_autonomy_preference(
            getattr(project, "autonomy_preference", None),
            default=AutonomyPreference.AUTO.value,
        )
        session_pref = normalize_autonomy_preference(
            getattr(state, "autonomy_preference", None),
            default=AutonomyPreference.AUTO.value,
        )
        state_scope_id = self._concierge_state_scope_key(msg.surface, msg.external_id)

        if requested is None:
            lines = [
                f"Session autonomy preference: `{session_pref}`",
                f"Project autonomy preference: `{project_pref}`",
                f"Default autonomy preference: `{self._default_autonomy_preference}`",
            ]
            last_effective = normalize_autonomy_preference(
                getattr(state, "last_autonomy_level", None),
                default="",
            )
            if last_effective:
                lines.append(f"Last effective autonomy: `{last_effective}`")
            return "\n".join(lines)

        if project_scope:
            if project is None:
                if len(active_projects) > 1:
                    return (
                        "Multiple active projects are open on this surface. "
                        "Use `/autonomy --project` from within a project-scoped turn."
                    )
                return "No active project found for `--project` autonomy update."
            project.autonomy_preference = requested
            self.project_store.save_project(project)
            if not state.autonomy_preference and requested != AutonomyPreference.AUTO.value:
                state.last_autonomy_level = requested
            self._save_concierge_state(state_scope_id, state)
            return f"Project autonomy preference set to `{requested}`."

        state.autonomy_preference = requested
        if requested == AutonomyPreference.AUTO.value:
            state.last_autonomy_level = None
        else:
            state.last_autonomy_level = requested
        self._save_concierge_state(state_scope_id, state)
        return f"Session autonomy preference set to `{requested}`."

    # ------------------------------------------------------------------
    # /goal command
    # ------------------------------------------------------------------

    def handle_goal_command(self, msg: SurfaceMessage) -> str:
        """Handle ``/goal`` — create, list, or inspect concierge goals."""
        text = msg.text.strip()
        args = text.split(None, 1)
        subcommand = args[1].strip() if len(args) > 1 else ""

        state = self._concierge_state
        state_scope_id = self._concierge_state_scope_key(msg.surface, msg.external_id)

        if not subcommand or subcommand == "list":
            if not state.active_goals:
                return "No active goals."
            lines = ["**Active Goals:**"]
            for g in state.active_goals:
                summary = self._goal_progress_summary(g)
                detail = f" ({summary})" if summary else ""
                lines.append(f"  [{g.status}] `{g.id}` — {g.description[:120]}{detail}")
            return "\n".join(lines)

        if subcommand == "clear":
            count = len(state.active_goals)
            state.active_goals.clear()
            self._save_concierge_state(state_scope_id, state)
            return f"Cleared {count} goal(s)."

        goal = ConciergeGoal(description=subcommand)
        bound_context: ResolvedContext | None = None
        active_projects = self.project_store.list_active(msg.external_id)
        if active_projects:
            project = active_projects[0]
            task = self.project_store.get_current_task(project.project_id, msg.external_id)
            if task is not None:
                bound_context = ResolvedContext(
                    project=project,
                    task=task,
                    is_new_project=False,
                    is_new_task=False,
                    confidence=1.0,
                    domain=project.domain,
                )
                self._bind_goal_to_context(goal, bound_context)
                goal.progress.completed_steps = list(task.completed_steps)
                goal.progress.pending_steps = list(task.pending_steps)
                goal.progress.current_blocker = task.current_blocker
                goal.progress.artifacts = dict(task.artifacts)
                goal.progress.updated_at = time.time()
        state.active_goals.append(goal)
        self._save_concierge_state(state_scope_id, state)
        if bound_context is not None:
            try:
                self._project_goal_progress_to_task(bound_context, msg, goal)
            except Exception:
                logger.debug("Failed to project goal binding onto task", exc_info=True)

        logger.warning(
            "GoalLoopExecutor not wired — goal stored but not executed "
            "(goal_id=%s, description=%s)",
            goal.id,
            goal.description[:80],
        )

        return (
            f"Goal stored: `{goal.id}` — {goal.description[:120]}\n\n"
            f"**Note:** Autonomous goal execution is not yet wired. "
            f"The goal is tracked for context but will not auto-execute."
        )

    # ------------------------------------------------------------------
    # Learning commands
    # ------------------------------------------------------------------

    def handle_corrections_command(self, msg: SurfaceMessage) -> str:
        store = getattr(self, "_correction_store", None)
        if store is None or store.count() == 0:
            return "No corrections recorded."
        records = list(reversed(store.list_recent(10)))
        lines = [f"Recent corrections ({store.count()} total):"]
        for record in records:
            signal = getattr(record, "signal", None)
            if signal is None:
                continue
            correction_type = str(getattr(signal, "correction_type", "unknown") or "unknown")
            confidence = float(getattr(signal, "confidence", 0.0) or 0.0)
            correction_text = str(getattr(signal, "correction_text", "") or "").strip()
            prompt_key = str(getattr(record, "active_prompt_key", "") or "").strip()
            line = (
                f"- [{correction_type}] {confidence:.0%} confidence: "
                f"{correction_text[:120] or '(no text)'}"
            )
            if prompt_key:
                line += f" ({prompt_key})"
            lines.append(line)
        return "\n".join(lines)

    def handle_adaptations_command(self, msg: SurfaceMessage) -> str:
        registry = getattr(self, "_adaptation_registry", None)
        if registry is None:
            return "Adaptation registry is unavailable."
        pending = registry.list_pending()
        queued = registry.list_queued()
        applied = registry.list_applied()
        if not pending and not queued and not applied:
            return "No adaptations."

        lines: list[str] = []
        if pending:
            lines.append("Pending:")
            for candidate in pending[:10]:
                lines.append(
                    f"- `{candidate.id}` [{candidate.source}] {candidate.description[:120]} "
                    f"(confidence={candidate.confidence:.0%}, samples={candidate.sample_size or 0})"
                )
        if queued:
            lines.append("Queued:")
            for candidate in queued[:10]:
                lines.append(
                    f"- `{candidate.id}` [{candidate.source}] {candidate.description[:120]}"
                )
        if applied:
            lines.append("Applied:")
            for candidate in applied[:10]:
                outcome = str(getattr(candidate, "last_outcome", "") or "").strip()
                suffix = f" — {outcome[:80]}" if outcome else ""
                lines.append(
                    f"- `{candidate.id}` [{candidate.source}] {candidate.description[:120]}{suffix}"
                )
        return "\n".join(lines)

    def _approve_candidate(self, candidate_id: str) -> str:
        registry = getattr(self, "_adaptation_registry", None)
        if registry is None:
            return "Adaptation registry is unavailable."
        candidate = registry.get(candidate_id)
        if candidate is None:
            return f"Unknown adaptation `{candidate_id}`."
        if candidate.status != "pending":
            return f"Adaptation `{candidate_id}` is `{candidate.status}`."

        if candidate.source == "prompt_opt":
            if self._apply_prompt_candidate(candidate_id, auto_apply=False):
                return f"Approved and applied `{candidate_id}`."
            return f"Failed to apply `{candidate_id}`."

        baseline_quality = 0.0
        try:
            if candidate.parameter_key and candidate.after_value is not None and self._behavior_store is not None:
                self._behavior_store.set(
                    candidate.parameter_key,
                    candidate.after_value,
                    reason=f"Approved adaptation {candidate.id}",
                    evidence=list(candidate.evidence or []),
                )
            registry.approve(candidate.id, baseline_quality=baseline_quality)
        except Exception:
            logger.warning("Failed to approve adaptation %s", candidate.id, exc_info=True)
            return f"Failed to approve `{candidate_id}`."
        return f"Approved `{candidate_id}`."

    def handle_approve_command(self, msg: SurfaceMessage) -> str:
        candidate_id = re.sub(r"^/approve\b", "", msg.text, count=1, flags=re.IGNORECASE).strip()
        if not candidate_id:
            return "Usage: `/approve <adaptation-id>`"
        return self._approve_candidate(candidate_id)

    def handle_reject_command(self, msg: SurfaceMessage) -> str:
        registry = getattr(self, "_adaptation_registry", None)
        if registry is None:
            return "Adaptation registry is unavailable."
        candidate_id = re.sub(r"^/reject\b", "", msg.text, count=1, flags=re.IGNORECASE).strip()
        if not candidate_id:
            return "Usage: `/reject <adaptation-id>`"
        candidate = registry.get(candidate_id)
        if candidate is None:
            return f"Unknown adaptation `{candidate_id}`."
        if candidate.status != "pending":
            return f"Adaptation `{candidate_id}` is `{candidate.status}`."
        try:
            registry.reject(candidate.id)
        except Exception:
            logger.warning("Failed to reject adaptation %s", candidate.id, exc_info=True)
            return f"Failed to reject `{candidate_id}`."
        return f"Rejected `{candidate_id}`."

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _complete_event(self, *, content: str, stream_channel_id: str | None = None) -> ChatCompleteEvent:
        return ChatCompleteEvent(
            message_id=uuid.uuid4().hex[:12],
            content=content,
            token_usage={},
            context_window=0,
            graph_revision="",
            stream_channel_id=stream_channel_id,
        )


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------

def build_concierge(
    *,
    chat_manager: Any,
    capability_context: Any,
    user_profile: Any = None,
    conversation_memory: Any = None,
    meta_controller: Any = None,
    memory_kernel: Any = None,
    autonomy_level: str | None = None,
    enable_dispatcher: bool = True,
    max_concurrent_projects: int = 5,
    mcp_bridge: Any = None,
    capability_registry: Any = None,
    tool_registry: Any = None,
    telemetry_store: Any = None,
    project_store_base_dir: str | Path | None = None,
) -> "Concierge | tuple[Concierge, ConcurrentDispatcher]":
    from ..learning_bundle import build_concierge_learning_bundle

    project_store = ProjectStore(base_dir=project_store_base_dir)
    progress_reporter = ProgressReporter(
        run_manager=getattr(capability_context, "run_manager", None),
        activity_tracker=getattr(capability_context, "activity_tracker", None),
    )
    learning_bundle = build_concierge_learning_bundle(
        chat_manager=chat_manager,
        telemetry_store=telemetry_store,
        memory_kernel=memory_kernel,
        runtime_prompt_key=_RUNTIME_UNIFIED_PROMPT_KEY,
    )
    memory_services = build_memory_services(memory_kernel=memory_kernel)
    concierge = Concierge(
        project_store=project_store,
        chat_manager=chat_manager,
        capability_context=capability_context,
        user_profile=user_profile,
        conversation_memory=conversation_memory,
        progress_reporter=progress_reporter,
        meta_controller=meta_controller,
        memory_kernel=memory_kernel,
        autonomy_level=autonomy_level,
        mcp_bridge=mcp_bridge,
        capability_registry=capability_registry,
        tool_registry=tool_registry,
        telemetry_store=telemetry_store,
        learning_bundle=learning_bundle,
        memory_services=memory_services,
    )
    if not enable_dispatcher:
        return concierge
    from ..dispatcher import ConcurrentDispatcher
    from ..resources import ResourceBudget, ResourceTracker

    resource_tracker = ResourceTracker(ResourceBudget.from_env())
    concierge._resource_tracker = resource_tracker
    setattr(chat_manager, "_resource_tracker", resource_tracker)
    dispatcher = ConcurrentDispatcher(
        concierge,
        max_concurrent_projects=max_concurrent_projects,
        resource_tracker=resource_tracker,
    )
    return concierge, dispatcher
