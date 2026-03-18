from __future__ import annotations

import asyncio
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
    from .dispatcher import ConcurrentDispatcher

from dan.server.chat_manager import ChatCompleteEvent, ChatStreamEvent

from .command_registry import CommandDescriptor, CommandRegistry, get_default_registry
from .identity import format_bare_prefix, format_prefix
from .models import (
    ConciergeState,
    IntentCategory,
    PendingAction,
    Project,
    ResolvedContext,
    SurfaceMessage,
    TaskTurn,
)
from .progress import ProgressReporter
from .project_store import ProjectStore
from .triage import TriageResult

logger = logging.getLogger(__name__)

_SERIALIZABLE_TYPES = (str, int, float, bool, type(None), list, dict)
_CONCIERGE_STATE_PREFIX = "concierge_state_"
_PREF_CONFIRM_WORDS = frozenset({"confirm all", "yes", "confirm"})
_BLOCKER_RE = re.compile(
    r"(?:blocked by|waiting on|need(?:ing)?|can'?t proceed until)\s+([^.\n]+)",
    re.IGNORECASE,
)
_FILE_PATH_RE = re.compile(r"(?:^|[\s\"'])(/[\w./-]+|[\w./-]+\.\w{1,6})(?=[\"'\s,;)]|$)")
_SENTENCE_END_RE = re.compile(r"[.!?]")


def _format_clarification_text(question: str, options: list[str] | None) -> str:
    if not options:
        return question
    option_lines = [f"{i}. {opt}" for i, opt in enumerate(options, 1)]
    return f"{question}\n\n{chr(10).join(option_lines)}"


def _extract_task_state(conversation_text: str) -> dict[str, Any]:
    completed: list[str] = []
    pending: list[str] = []
    for line in conversation_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"[-*]?\s*\[x\]", stripped, re.IGNORECASE):
            text = re.sub(r"^[-*]?\s*\[x\]\s*", "", stripped, flags=re.IGNORECASE).strip()
            if text:
                completed.append(text)
            continue
        if re.match(r"[-*]?\s*\[ ?\]", stripped):
            text = re.sub(r"^[-*]?\s*\[ ?\]\s*", "", stripped).strip()
            if text:
                pending.append(text)

    blocker: str | None = None
    blocker_match = _BLOCKER_RE.search(conversation_text)
    if blocker_match:
        blocker = blocker_match.group(0).strip()

    artifacts: dict[str, str] = {}
    for match in _FILE_PATH_RE.finditer(conversation_text):
        path = match.group(1)
        name = path.rsplit("/", 1)[-1] if "/" in path else path
        if name and name not in artifacts:
            artifacts[name] = path

    return {
        "completed_steps": completed,
        "pending_steps": pending,
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


def _persist_task_state(project_store: ProjectStore, project_id: str, task: Any) -> None:
    for surface_dir in sorted(project_store.base_dir.iterdir()):
        if not surface_dir.is_dir():
            continue
        surface_id = surface_dir.name
        project = project_store.get_project(project_id, surface_id)
        if project is None:
            continue
        for index, stored_task in enumerate(project.tasks):
            if stored_task.task_id == task.task_id:
                project.tasks[index] = task
                project_store.save_project(project)
                return


class AutonomyLevel(str, Enum):
    INTERACTIVE = "interactive"
    SUPERVISED = "supervised"
    AUTONOMOUS = "autonomous"


class Concierge:

    _REASSURANCE_INITIAL_DELAY: float = float(
        os.environ.get("DAN_CONCIERGE_REASSURANCE_DELAY", "10")
    )
    _REASSURANCE_REPEAT_INTERVAL: float = float(
        os.environ.get("DAN_CONCIERGE_REASSURANCE_INTERVAL", "20")
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
        self._current_turn_event_id: str | None = None
        self._telem_is_fast_command = False
        self._telem_model: str | None = None
        self._telem_intent: str | None = None
        self._last_context: Any = None
        self.bot_name = bot_name
        self.auto_summarize_turn_threshold: int = 10
        self._bg_memory_tasks: set[asyncio.Task[None]] = set()
        self._interaction_counter: int = 0
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

        # Behavior store
        self._behavior_store: Any = None
        self._behavior_changelog: Any = None
        self._param_registry: Any = None
        self._param_logger: Any = None
        self._pattern_accumulator: Any = None
        try:
            from dan.engine.behavior_store import (
                BehaviorStore, BehaviorChangeLog, AdaptableParameterRegistry,
                ParameterDecisionLogger, PatternAccumulator,
            )
            from dan.engine.behavior_seeds import register_all_seeds

            self._behavior_store = BehaviorStore()
            self._behavior_changelog = BehaviorChangeLog()
            self._param_registry = AdaptableParameterRegistry()
            self._param_logger = ParameterDecisionLogger(
                telemetry_store=self._telemetry_store,
            )
            self._pattern_accumulator = PatternAccumulator(self._behavior_store)
            register_all_seeds(self._behavior_store, self._param_registry)

            from dan.server.concierge.domain_learning import register_seed_domains
            register_seed_domains(self._behavior_store)

            from dan.providers.tier_defaults import register_seed_tier_maps
            from dan.providers.costs import register_seed_cost_table
            register_seed_tier_maps(self._behavior_store)
            register_seed_cost_table(self._behavior_store)
            if hasattr(self.chat_manager, "set_behavior_store"):
                self.chat_manager.set_behavior_store(self._behavior_store)
        except Exception as exc:
            logger.warning("BehaviorStore init failed: %s", exc)
            self._behavior_store = None
            self._behavior_changelog = None
            self._param_registry = None
            self._param_logger = None
            self._pattern_accumulator = None

        self._skill_store: Any = None
        self._schedule_store: Any = None
        self._schedule_history_store: Any = None
        try:
            from .scheduler import ScheduleHistoryStore, ScheduleStore

            self._schedule_store = ScheduleStore()
            self._schedule_history_store = ScheduleHistoryStore()
        except Exception:
            logger.debug("Schedule store init failed", exc_info=True)

        self._pii_registry: Any = None
        try:
            from .pii_tokenizer import SensitiveWordRegistry

            self._pii_registry = SensitiveWordRegistry.load()
        except Exception:
            logger.debug("PII registry init failed", exc_info=True)

        self._computer_config: Any = None
        self._computer_lease: Any = None
        self._computer_audit: Any = None
        self._computer_controller: Any = None
        try:
            from .computer_policy import AuditLog, ComputerControlConfig
            from .computer_use import ComputerUseLeaseManager

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
            from .tiering import ConciergeTierResolver

            tier_map_env = os.environ.get("DAN_TIER_MAP", "").strip()
            user_tier_map = None
            if tier_map_env:
                try:
                    raw = json.loads(tier_map_env)
                    user_tier_map = normalize_tier_map(raw)
                except Exception:
                    pass

            provider_names: list[str] = []
            providers = getattr(chat_manager, "_providers", None)
            if providers is not None:
                provider_names = (
                    providers.provider_names()
                    if hasattr(providers, "provider_names")
                    else list(getattr(providers, "_providers", {}).keys())
                )

            full_tier_map = resolve_tier_map(provider_names, user_tier_map)
            fallback_model = (
                os.environ.get("DAN_CHAT_MODEL", "").strip()
                or os.environ.get("DAN_LLM_MODEL", "").strip()
                or "claude-sonnet-4-6"
            )
            self._tier_resolver = ConciergeTierResolver(full_tier_map, fallback_model)
        except Exception as exc:
            logger.warning("Tier resolver init failed: %s", exc)

        # Tiered dispatcher (always enabled)
        from .session import SessionManager
        from .triage import triage as triage_fn
        from .tier_executors import InstantExecutor, SingleShotExecutor, MultiStepExecutor
        from .tiered_dispatch import TieredDispatcher, ContextGatherer

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
            str(getattr(self.chat_manager, "_chat_model", "") or "").strip()
            or os.environ.get("DAN_CHAT_MODEL", "").strip()
            or os.environ.get("DAN_LLM_MODEL", "").strip()
        )
        try:
            providers = getattr(self.chat_manager, "_providers", None)
            if providers is not None:
                provider_names = (
                    providers.provider_names()
                    if hasattr(providers, "provider_names")
                    else list(getattr(providers, "_providers", {}).keys())
                )
                if set(provider_names) == {"default"} and configured_model:
                    return configured_model
                from dan.providers.tier_defaults import resolve_tier_map
                tier_map = resolve_tier_map(provider_names)
                micro_model = str(tier_map.get("micro", "") or "").strip()
                if micro_model:
                    return micro_model
        except Exception:
            pass
        return configured_model or "gpt-4o-mini"

    async def _triage_llm_complete(self, messages: list[dict[str, str]]) -> str:
        providers = getattr(self.chat_manager, "_providers", None)
        if providers is None:
            raise RuntimeError("No provider registry available")
        model = self._resolve_triage_model()
        provider = providers.resolve(model)
        result = await provider.complete(
            messages=messages, model=model, temperature=0.0, max_tokens=60,
        )
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
            from dan.server.telemetry import TelemetryEvent
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
            from dan.server.telemetry import TelemetryEvent

            duration_ms = (time.monotonic() - start_time) * 1000
            _ctx = self._last_context
            if model is None:
                model = getattr(self, "_telem_model", None)
            if intent is None:
                intent = getattr(self, "_telem_intent", None)
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
            from dan.server.telemetry import TelemetryQuery as TQ

            events = await self._telemetry_store.query(
                TQ(session_id=msg.external_id),
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
            from .scheduler import DeliveryTarget, TriggerContext

            resolved_project_id = str(msg.metadata.get("resolved_project_id") or "").strip() or None
            resolved_task_id = str(msg.metadata.get("resolved_task_id") or "").strip() or None
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

    async def _try_fast_command(
        self, msg: SurfaceMessage,
    ) -> ChatCompleteEvent | None:
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
            from .progress_ux import CLIProgressRenderer
        except ImportError:
            return None
        if surface == "cli":
            return CLIProgressRenderer()
        return None

    def _ensure_progress_session(self, msg: SurfaceMessage) -> None:
        if msg.external_id in self._progress_sessions:
            return
        try:
            from .progress_ux import (
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
        from dan.server.telemetry import generate_event_id

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
                    continue
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
        self._concierge_state = self._load_concierge_state(msg.external_id)
        self._concierge_state.last_interaction_at = time.time()
        self._telem_model = getattr(self.chat_manager, "_chat_model", None)

        fast_event = await self._try_fast_command(msg)
        if fast_event is not None:
            self._save_concierge_state(msg.external_id, self._concierge_state)
            yield fast_event
            return

        self._ensure_progress_session(msg)

        async for event in self._tiered_dispatcher.dispatch(msg):
            yield event

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_concierge_state(self, surface_id: str) -> ConciergeState:
        if not self.memory_kernel:
            return ConciergeState()
        from dan.engine.memory_kernel import MemoryItem, MemoryScope, MemoryType

        item_id = _CONCIERGE_STATE_PREFIX + surface_id
        item = self.memory_kernel.get(item_id)
        if item is None:
            return ConciergeState()
        try:
            data = json.loads(item.content)
            return ConciergeState.model_validate(data)
        except Exception:
            logger.debug("Failed to parse concierge state for %s", surface_id, exc_info=True)
            return ConciergeState()

    def _save_concierge_state(self, surface_id: str, state: ConciergeState) -> None:
        if not self.memory_kernel:
            return
        from dan.engine.memory_kernel import MemoryItem, MemoryScope, MemoryType

        item_id = _CONCIERGE_STATE_PREFIX + surface_id
        item = MemoryItem(
            id=item_id,
            content=state.model_dump_json(),
            memory_type=MemoryType.WORKING_STATE,
            scope=MemoryScope.USER,
            metadata={"surface_id": surface_id},
        )
        self.memory_kernel.store(item)

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
                conversation_text = "\n".join(
                    t.content for t in context.task.turns if t.content
                )
                state = _extract_task_state(conversation_text)
                context.task.completed_steps = _compact_task_history(state["completed_steps"])
                context.task.pending_steps = state["pending_steps"]
                context.task.current_blocker = state.get("current_blocker")
                context.task.artifacts = state.get("artifacts", {})
                _persist_task_state(
                    self.project_store,
                    context.project.project_id,
                    context.task,
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
            from .domain_learning import DomainReflector
            from dan.engine.learning_tiers import is_feature_enabled

            if not self.memory_kernel or not is_feature_enabled("domain_learning"):
                return
            providers = getattr(self.chat_manager, "_providers", None)
            if providers is None:
                return
            model = self._resolve_triage_model()

            class _ThreadedReflectionLLM:
                def __init__(self, provider_registry: Any, model_name: str) -> None:
                    self._provider_registry = provider_registry
                    self._model_name = model_name

                def complete(self, prompt: str, max_tokens: int = 1000) -> str:
                    async def _call() -> str:
                        provider = self._provider_registry.resolve(self._model_name)
                        result = await provider.complete(
                            messages=[{"role": "user", "content": prompt}],
                            model=self._model_name,
                            temperature=0.2,
                            max_tokens=max_tokens,
                        )
                        return result.text
                    return asyncio.run(_call())

            threaded_llm = _ThreadedReflectionLLM(providers, model)
            reflector = DomainReflector(memory_kernel=self.memory_kernel, llm=threaded_llm)
            turns = list(context.task.turns)
            items = await asyncio.to_thread(reflector.reflect, domain, turns)
            if items:
                project_id = getattr(getattr(context, "project", None), "project_id", None)
                if project_id:
                    from dan.engine.memory_kernel import MemoryScope
                    for item in items:
                        item.scope = MemoryScope.PROJECT
                        item.metadata = dict(item.metadata or {})
                        item.metadata["project_id"] = project_id
                self.memory_kernel.store_many(items)
                logger.info("Domain reflection extracted %d items for %s", len(items), domain)
            await asyncio.to_thread(self._maybe_run_domain_template_upgrade, domain, threaded_llm)
        except Exception:
            logger.debug("Domain reflection failed", exc_info=True)

    def _maybe_run_domain_template_upgrade(self, domain: str, llm: Any) -> None:
        try:
            from dan.engine.learning_tiers import is_feature_enabled
            from dan.engine.memory_kernel import MemoryType
            from .domain_learning import (
                DomainTemplateUpgrader,
                get_or_create_template,
                save_domain_template,
            )
            if not is_feature_enabled("domain_template_upgrade"):
                return
            template = get_or_create_template(domain)
            if not template.metadata.get("needs_llm_upgrade"):
                return
            items: list[Any] = []
            for mem_type in (
                MemoryType.FACT, MemoryType.PREFERENCE,
                MemoryType.PRINCIPLE, MemoryType.WORKFLOW_PATTERN,
            ):
                items.extend(
                    item
                    for item in self.memory_kernel.list_by_type(mem_type)
                    if "domain_knowledge" in (item.tags or [])
                    and item.metadata.get("domain") == domain
                )
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
        if not self.memory_kernel:
            return ""
        try:
            from dan.engine.memory_kernel import MemoryType, classify_task_type

            scored = self.memory_kernel.retrieve_by_task(
                message,
                task_type=classify_task_type(message, has_active_build=has_active_build),
                limit=10,
                project_id=project_id,
            )
            if not scored:
                return ""
            lines = ["Relevant context from memory:"]
            seen_contents: set[str] = set()
            for si in scored[:8]:
                if si.item.memory_type == MemoryType.WORKING_STATE:
                    continue
                content = si.item.content[:200]
                if not content or content in seen_contents:
                    continue
                seen_contents.add(content)
                tag = si.item.memory_type.value.upper()
                scope_hint = " (project)" if si.item.scope.value == "project" else ""
                lines.append(f"- [{tag}{scope_hint}] {content}")
            if len(lines) == 1:
                return ""
            block = "\n".join(lines)
            return block[:max_chars].rstrip() + ("..." if len(block) > max_chars else "")
        except Exception:
            logger.debug("Memory retrieval failed in concierge", exc_info=True)
            return ""

    def _retrieve_domain_expertise(
        self,
        query: str,
        domain: str,
        project_id: str | None = None,
        *,
        max_items: int = 8,
        max_chars: int = 1200,
    ) -> str:
        if not self.memory_kernel or not domain:
            return ""
        try:
            from dan.engine.memory_kernel import (
                MemoryScope, MemoryType,
                _rank_fact, _rank_preference, _rank_principle, _rank_workflow_pattern,
            )
            type_rankers = {
                MemoryType.FACT: (_rank_fact, 0.30),
                MemoryType.PREFERENCE: (_rank_preference, 0.20),
                MemoryType.PRINCIPLE: (_rank_principle, 0.25),
                MemoryType.WORKFLOW_PATTERN: (_rank_workflow_pattern, 0.25),
            }
            all_scored: list[tuple[float, Any]] = []
            for mem_type, (ranker, weight) in type_rankers.items():
                items = self.memory_kernel.list_by_type(mem_type)
                type_scored: list[tuple[float, Any]] = []
                for item in items:
                    if "domain_knowledge" not in (item.tags or []):
                        continue
                    if item.metadata.get("domain") != domain:
                        continue
                    if (
                        item.scope == MemoryScope.PROJECT
                        and item.metadata.get("project_id") != project_id
                    ):
                        continue
                    score = ranker(item, query) + 0.3
                    type_scored.append((min(score, 1.0), item))
                type_scored.sort(key=lambda x: x[0], reverse=True)
                if not type_scored:
                    continue
                type_limit = max(1, round(max_items * weight))
                all_scored.extend(type_scored[:type_limit])

            all_scored.sort(key=lambda x: x[0], reverse=True)
            if not all_scored:
                return ""
            lines = [f"[Domain Expertise: {domain}]"]
            for _score, item in all_scored[:max_items]:
                cat = (item.metadata.get("category") or "general").upper()
                lines.append(f"- [{cat}] {item.content[:200]}")
            block = "\n".join(lines)
            return block[:max_chars].rstrip() + ("..." if len(block) > max_chars else "")
        except Exception:
            logger.debug("Domain expertise retrieval failed", exc_info=True)
            return ""

    def _extract_memory_candidates(
        self,
        message: str,
        response: str,
        goal_context: dict[str, Any] | None = None,
    ) -> list[Any]:
        if not self.memory_kernel:
            return []
        from dan.engine.memory_kernel import MemoryItem, MemoryScope, MemoryType

        candidates: list[MemoryItem] = []
        user_preview = " ".join(message.split()).strip()[:150]
        assistant_preview = " ".join(response.split()).strip()[:200]
        if user_preview:
            episode_content = f"User: {user_preview}. Assistant: {assistant_preview}"
            candidates.append(
                MemoryItem(
                    content=episode_content,
                    memory_type=MemoryType.EPISODE,
                    scope=MemoryScope.SESSION,
                )
            )
        if goal_context:
            errors = goal_context.get("error_history") or []
            desc = goal_context.get("description", "")[:200]
            if errors and desc:
                failure_content = f"Goal: {desc}. Last error: {str(errors[-1])[:300]}"
                candidates.append(
                    MemoryItem(
                        content=failure_content,
                        memory_type=MemoryType.FAILURE_PATTERN,
                        scope=MemoryScope.USER,
                        metadata=goal_context.get("metadata") or {},
                    )
                )
        return candidates

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
        from .fan_out import fan_out_dict
        tasks = {
            "preferences": lambda: asyncio.to_thread(
                self._try_extract_preferences, message, response, project_id=project_id,
            ),
            "memory_extraction": lambda: asyncio.to_thread(
                self._try_memory_extraction, message, response, goal_context,
                project_id=project_id, domain=domain,
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
        if self.memory_kernel is None:
            return
        if os.environ.get("DAN_PREFERENCE_EXTRACTION", "1").strip() != "1":
            return
        try:
            from dan.engine.domain_taxonomy import format_domain_label
            from dan.engine.preference_extractor import PreferenceExtractor
            extractor = PreferenceExtractor(
                behavior_store=getattr(self, "_behavior_store", None),
            )
            messages = [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": assistant_message},
            ]
            prefs = extractor.extract_from_messages(messages)
            if not prefs:
                return
            for key, value in prefs.items():
                if not value:
                    continue
                if isinstance(value, dict):
                    for sub_key, sub_val in value.items():
                        self.memory_kernel.store_preference(
                            content=f"{key}: {sub_key} -> {sub_val}",
                            tags=[key, sub_key],
                            project_id=project_id,
                        )
                elif isinstance(value, list):
                    for entry in value:
                        content_value = (
                            format_domain_label(entry)
                            if key == "domains"
                            else str(entry)
                        )
                        self.memory_kernel.store_preference(
                            content=f"{key}: {content_value}",
                            tags=[key],
                            project_id=project_id,
                        )
                elif isinstance(value, str) and value:
                    self.memory_kernel.store_preference(
                        content=f"{key}: {value}", tags=[key], project_id=project_id,
                    )
        except Exception:
            logger.debug("Preference extraction failed", exc_info=True)

    def _try_memory_extraction(
        self,
        user_message: str,
        assistant_message: str,
        goal_context: dict[str, Any] | None = None,
        project_id: str | None = None,
        domain: str | None = None,
    ) -> None:
        if self.memory_kernel is None:
            return
        if os.environ.get("DAN_MEMORY_EXTRACTION", "1").strip() != "1":
            return
        try:
            from dan.engine.memory_extractor import MemoryExtractor
            extractor = MemoryExtractor()
            tool_activity = (goal_context or {}).get("metadata", {}).get("tool_calls")
            candidates = asyncio.run(extractor.extract_with_llm(
                user_message=user_message,
                assistant_message=assistant_message,
                tool_calls=tool_activity if isinstance(tool_activity, list) else None,
                goal_context=goal_context,
            ))
            self._remember_search_dirs_from_candidates(candidates)
            for candidate in candidates:
                candidate_tags = list(candidate.tags or [])
                candidate_metadata = dict(candidate.metadata or {})
                if domain:
                    if "domain_knowledge" not in candidate_tags:
                        candidate_tags.append("domain_knowledge")
                    candidate_metadata.setdefault("domain", domain)
                if candidate.memory_type == "fact":
                    self.memory_kernel.store_fact(
                        candidate.content, tags=candidate_tags,
                        project_id=project_id, metadata=candidate_metadata,
                    )
                elif candidate.memory_type == "preference":
                    self.memory_kernel.store_preference(
                        candidate.content, tags=candidate_tags,
                        project_id=project_id, metadata=candidate_metadata,
                    )
        except Exception:
            logger.debug("Memory extraction failed", exc_info=True)

    def _remember_search_dirs_from_candidates(self, candidates: list[Any]) -> None:
        if self.user_profile is None or not hasattr(self.user_profile, "merge_search_dirs"):
            return
        directories: list[str] = []
        for candidate in candidates:
            if getattr(candidate, "memory_type", None) != "fact":
                continue
            candidate_tags = list(getattr(candidate, "tags", None) or [])
            candidate_metadata = dict(getattr(candidate, "metadata", None) or {})
            if not (
                candidate_metadata.get("is_directory")
                or "search_dir" in candidate_tags
            ):
                continue
            path_value = str(candidate_metadata.get("path", "") or "").strip()
            if not path_value and ":" in str(getattr(candidate, "content", "")):
                path_value = str(candidate.content).split(":", 1)[1].strip()
            if path_value:
                directories.append(path_value)
        if not directories:
            return
        try:
            changed = bool(self.user_profile.merge_search_dirs(directories))
            if changed:
                from dan.engine.user_profile import save_user_profile

                save_user_profile(self.user_profile)
        except Exception:
            logger.debug("Failed to persist extracted search directories", exc_info=True)

    def _store_domain_validation_warnings(
        self, warnings: list[str], domain: str | None,
    ) -> None:
        if not self.memory_kernel or not warnings:
            return
        from dan.engine.memory_kernel import MemoryItem, MemoryScope, MemoryType
        for warning in warnings:
            self.memory_kernel.store(MemoryItem(
                content=warning,
                memory_type=MemoryType.FACT,
                scope=MemoryScope.USER,
                tags=["domain_validation_warning"],
                metadata={"domain": domain or "unknown"},
            ))

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
        reply = msg.text.strip().lower()

        if pending.kind == "confirm":
            if reply in {"no", "n", "cancel", "stop"}:
                self.project_store.clear_pending_action(project.project_id, msg.external_id)
                self.project_store.update_task_status(project.project_id, task.task_id, "paused", msg.external_id)
                return self._complete_event(content=f"{format_prefix(project.label)} Cancelled."), context, TriageResult(
                    tier=1, intent=pending.intent, confidence=1.0, goal=pending.original_text,
                ), msg
            if reply in {"yes", "y", "ok", "okay", "do it", "go ahead", "sure"}:
                self.project_store.clear_pending_action(project.project_id, msg.external_id)
                replay_msg = msg.model_copy(
                    update={
                        "text": pending.original_text,
                        "metadata": self._with_resolved_context_metadata(
                            {**pending.metadata, **msg.metadata, "skip_confirm": True}, context,
                        ),
                    }
                )
                return None, context, TriageResult(
                    tier=1, intent=pending.intent, confidence=1.0, goal=pending.original_text,
                ), replay_msg
            return self._complete_event(content=f"{format_prefix(project.label)} Please answer yes or no."), context, TriageResult(
                tier=1, intent=pending.intent, confidence=1.0, goal=pending.original_text,
            ), msg

        if pending.kind == "clarify":
            if not pending.options:
                self.project_store.clear_pending_action(project.project_id, msg.external_id)
                replay_metadata = {**pending.metadata, **msg.metadata, "clarification_answer": msg.text}
                replay_msg = msg.model_copy(
                    update={
                        "text": f"{pending.original_text}\n[User clarification: {msg.text}]",
                        "metadata": self._with_resolved_context_metadata(replay_metadata, context),
                    }
                )
                return None, context, TriageResult(
                    tier=1, intent=pending.intent, confidence=1.0, goal=pending.original_text,
                ), replay_msg
            if reply.isdigit():
                idx = int(reply) - 1
                if 0 <= idx < len(pending.options):
                    return self._build_clarification_resume(project, msg, pending, idx, context)
            reply_norm = reply.strip()
            for idx, option in enumerate(pending.options):
                option_lower = option.lower()
                basename = option_lower.rsplit("/", 1)[-1]
                if reply_norm and (reply_norm in option_lower or reply_norm in basename):
                    return self._build_clarification_resume(project, msg, pending, idx, context)
            if pending.options:
                numbered_options = "\n".join(
                    f"{idx + 1}. {option}" for idx, option in enumerate(pending.options[:8])
                )
                return self._complete_event(
                    content=(
                        f"{format_prefix(project.label)} I couldn't match that reply. "
                        f"Please reply with a number:\n{numbered_options}"
                    )
                ), context, TriageResult(
                    tier=1, intent=pending.intent, confidence=1.0, goal=pending.original_text,
                ), msg
            return self._complete_event(content=f"{format_prefix(project.label)} I couldn't resolve that choice."), context, TriageResult(
                tier=1, intent=pending.intent, confidence=1.0, goal=pending.original_text,
            ), msg

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
            from .domain_learning import detect_domain
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

    def _build_clarification_resume(
        self,
        project: Project,
        msg: SurfaceMessage,
        pending: PendingAction,
        idx: int,
        context: ResolvedContext,
    ) -> tuple[None, ResolvedContext, TriageResult, SurfaceMessage]:
        self.project_store.clear_pending_action(project.project_id, msg.external_id)
        replay_msg = msg.model_copy(
            update={
                "text": pending.original_text,
                "metadata": self._with_resolved_context_metadata(
                    {**pending.metadata, **msg.metadata, "selected_option": idx, "selected_path": pending.options[idx]},
                    context,
                ),
            }
        )
        return None, context, TriageResult(
            tier=1, intent=pending.intent, confidence=1.0, goal=pending.original_text,
        ), replay_msg

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # /goal command
    # ------------------------------------------------------------------

    def handle_goal_command(self, msg: SurfaceMessage) -> str:
        """Handle ``/goal`` — create, list, or inspect concierge goals."""
        from .models import ConciergeGoal

        text = msg.text.strip()
        args = text.split(None, 1)
        subcommand = args[1].strip() if len(args) > 1 else ""

        state = self._concierge_state

        if not subcommand or subcommand == "list":
            if not state.active_goals:
                return "No active goals."
            lines = ["**Active Goals:**"]
            for g in state.active_goals:
                lines.append(f"  [{g.status}] `{g.id}` — {g.description[:120]}")
            return "\n".join(lines)

        if subcommand == "clear":
            count = len(state.active_goals)
            state.active_goals.clear()
            self._save_concierge_state(msg.external_id, state)
            return f"Cleared {count} goal(s)."

        goal = ConciergeGoal(description=subcommand)
        state.active_goals.append(goal)
        self._save_concierge_state(msg.external_id, state)

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
) -> "Concierge | tuple[Concierge, ConcurrentDispatcher]":
    project_store = ProjectStore()
    progress_reporter = ProgressReporter(
        run_manager=getattr(capability_context, "run_manager", None),
        activity_tracker=getattr(capability_context, "activity_tracker", None),
    )
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
    )
    if not enable_dispatcher:
        return concierge
    from .dispatcher import ConcurrentDispatcher
    from .resources import ResourceBudget, ResourceTracker

    resource_tracker = ResourceTracker(ResourceBudget.from_env())
    dispatcher = ConcurrentDispatcher(
        concierge,
        max_concurrent_projects=max_concurrent_projects,
        resource_tracker=resource_tracker,
    )
    return concierge, dispatcher
