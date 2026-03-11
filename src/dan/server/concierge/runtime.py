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
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal

if TYPE_CHECKING:
    from .dispatcher import ConcurrentDispatcher

from dan.server.chat_manager import ChatCompleteEvent, ChatErrorEvent, ChatStreamEvent

from .classifier import ClassificationResult, IntentCategory, classify_intent, classify_intent_llm
from .command_registry import CommandDescriptor, CommandRegistry, get_default_registry
from .context_resolver import ProjectContextResolver, ResolvedContext
from .entity_grounding import (
    EntityContext,
    GuardContext,
    GuardResult,
    ground_entities,
    guard_classification,
    guard_response_relevance,
    guard_understanding,
    guards_enabled,
)
from .identity import format_bare_prefix, format_prefix, starts_with_prefix
from .models import ConciergeGoal, ConciergeState, SurfaceMessage

logger = logging.getLogger(__name__)
from .handlers import (
    ConversationHandler,
    DirectTaskHandler,
    ExperienceHandler,
    FileHandler,
    HandlerRegistry,
    MetaGoalHandler,
    PublishHandler,
    RunHandler,
    StatusHandler,
    WorkflowBuildHandler,
    WorkflowQueryHandler,
)
from .models import PendingAction, Project, TaskTurn
from .policy import ActionPolicy, resolve_policy
from .progress import ProgressReporter
from .project_store import ProjectStore
from .promotion import WorkflowPromoter
from .build_session import BuildSessionManager, BuildSessionStatus
from .reuse_decision import ReuseCandidate, reuse_first_decision
from .executor import ExecutionResult, ExecutionSelector
from .memory_bridge import WorkflowMemoryIndex, enrich_planning_context
from .solver import GoalResolver, PlanBuilder, SolverDecision

_NUMERIC_CLAIM_RE_LEGACY = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d+(?:\.\d+)?%"
    r"|\b(?:price|close|open|high|low|volume|cap)\b[^.]*?\$?\d",
    re.IGNORECASE,
)
_unsourced_claim_warnings: int = 0


_CONCIERGE_STATE_PREFIX = "concierge_state_"


class AutonomyLevel(str, Enum):
    """When to check in with the user during goal execution (29-2 §4)."""

    INTERACTIVE = "interactive"  # Check in after every step (build, run, repair)
    SUPERVISED = "supervised"   # Check in on failure or completion
    AUTONOMOUS = "autonomous"   # Only check in on unrecoverable failure


_SURFACE_AUTONOMY_DEFAULTS: dict[str, str] = {
    "whatsapp": AutonomyLevel.AUTONOMOUS.value,
    "telegram": AutonomyLevel.AUTONOMOUS.value,
    "cli": AutonomyLevel.SUPERVISED.value,
    "editor": AutonomyLevel.SUPERVISED.value,
    "server": AutonomyLevel.SUPERVISED.value,
}

_AUTONOMY_OVERRIDE_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("just do it", "go ahead", "do it", "run it"), AutonomyLevel.AUTONOMOUS.value),
    (("show me every step", "step by step", "walk me through"), AutonomyLevel.INTERACTIVE.value),
)

_FAST_COMMAND_PREFIXES = ("/save", "/build-", "/memory-", "/mcp", "/model", "/cost", "/retry", "/status", "/skill", "/project")
_PREF_CONFIRM_WORDS = frozenset({"confirm all", "yes", "confirm"})

_GREETING_TOKENS = frozenset({
    "hi", "hello", "hey", "yo", "sup", "hola", "howdy",
    "thanks", "thank", "ty", "thx",
    "bye", "goodbye", "ok", "okay", "sure",
    "yes", "no", "yeah", "nah", "nope", "yep",
    "good", "morning", "afternoon", "evening",
})

_FOLLOW_UP_PHRASES = frozenset({
    "confirm", "go ahead", "do it", "proceed", "continue",
    "sounds good", "let's go", "yes please", "run it",
    "approved", "looks good", "lgtm", "ship it",
})

_COMPLETION_GUARD_MIN_RESPONSE_LEN = 50


class Concierge:
    def __init__(
        self,
        *,
        project_store: ProjectStore,
        context_resolver: ProjectContextResolver,
        chat_manager: Any,
        capability_context: Any,
        user_profile: Any = None,
        conversation_memory: Any = None,
        queue: Any = None,
        progress_reporter: ProgressReporter | None = None,
        promoter: WorkflowPromoter | None = None,
        meta_controller: Any = None,
        goal_resolver: GoalResolver | None = None,
        plan_builder: PlanBuilder | None = None,
        memory_index: WorkflowMemoryIndex | None = None,
        execution_selector: ExecutionSelector | None = None,
        bot_name: str | None = None,
        memory_kernel: Any = None,
        autonomy_level: str | None = None,
        mcp_bridge: Any = None,
        capability_registry: Any = None,
        tool_registry: Any = None,
        telemetry_store: Any = None,
    ) -> None:
        self.project_store = project_store
        self.context_resolver = context_resolver
        self.chat_manager = chat_manager
        self.capability_context = capability_context
        self.user_profile = user_profile
        self.conversation_memory = conversation_memory
        # Deprecated compatibility kwarg: runtime queueing now belongs to ConcurrentDispatcher.
        _ = queue
        self.progress_reporter = progress_reporter
        self.promoter = promoter
        self.meta_controller = meta_controller
        self.goal_resolver = goal_resolver
        self.plan_builder = plan_builder or PlanBuilder()
        self.memory_index = memory_index
        self.memory_kernel = memory_kernel
        self.mcp_bridge = mcp_bridge
        self._capability_registry = capability_registry
        self._tool_registry = tool_registry
        self._telemetry_store = telemetry_store
        self._current_turn_event_id: str | None = None
        self._telem_is_fast_command = False
        self._telem_model: str | None = None
        self._telem_intent: str | None = None
        self._last_context: Any = None
        _raw = (autonomy_level or os.environ.get("DAN_CONCIERGE_AUTONOMY", "supervised")).strip().lower()
        if _raw in (e.value for e in AutonomyLevel):
            self._autonomy_level = _raw
        else:
            self._autonomy_level = AutonomyLevel.SUPERVISED.value
        self.bot_name = bot_name
        self._current_surface_id_var: ContextVar[str | None] = ContextVar(
            "concierge_current_surface_id",
            default=None,
        )
        self._concierge_state_var: ContextVar[ConciergeState | None] = ContextVar(
            "concierge_state",
            default=None,
        )
        self._memory_context_var: ContextVar[str] = ContextVar(
            "concierge_memory_context",
            default="",
        )
        self.auto_summarize_turn_threshold: int = 10
        self._bg_memory_tasks: set[asyncio.Task[None]] = set()
        self._interaction_counter: int = 0
        self._pending_preference_surface: dict[str, list[Any]] = {}
        self.handlers = HandlerRegistry()
        self.handlers.register(IntentCategory.FILE_REQUEST, FileHandler(user_profile=user_profile, chat_manager=chat_manager))
        self.handlers.register(IntentCategory.DIRECT_TASK, DirectTaskHandler(chat_manager))
        self.handlers.register(IntentCategory.RUN_CONTROL, RunHandler(capability_context, chat_manager=chat_manager))
        self.handlers.register(IntentCategory.STATUS_CHECK, StatusHandler(capability_context, progress_reporter))
        self.handlers.register(IntentCategory.EXPERIENCE_QUERY, ExperienceHandler(capability_context, chat_manager=chat_manager))
        self.handlers.register(IntentCategory.PUBLISH_SHARE, PublishHandler(capability_context))
        self.handlers.register(IntentCategory.WORKFLOW_QUERY, WorkflowQueryHandler(capability_context))
        build_handler = WorkflowBuildHandler(chat_manager)
        self.handlers.register(IntentCategory.WORKFLOW_BUILD, build_handler)
        self.handlers.register(IntentCategory.META_GOAL, MetaGoalHandler(meta_controller, capability_context=capability_context))
        self.handlers.register(IntentCategory.CONVERSATION, ConversationHandler(chat_manager))
        self.execution_selector = execution_selector or ExecutionSelector(self.handlers)
        self._progress_sessions: dict[str, Any] = {}
        self._goal_loop_states: dict[str, dict[str, Any]] = {}
        self._presence_tracker: Any = None
        self._schedule_store: Any = None
        self._schedule_history_store: Any = None
        self._task_scheduler: Any = None
        self._follow_up_config: Any = None
        self._follow_up_queue: Any = None
        self._follow_up_engine: Any = None
        self._correction_store: Any = None
        self._adaptation_registry: Any = None
        self._health_counters: Any = None
        self._computer_config: Any = None
        self._computer_lease: Any = None
        self._computer_audit: Any = None
        self._learning_tier: Any = None
        self._classifier_model: str = os.environ.get(
            "DAN_CLASSIFIER_MODEL", ""
        )

    def _resolve_classifier_model(self) -> str:
        """Return the model name used for intent classification (micro tier)."""
        if self._classifier_model:
            return self._classifier_model
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
        if configured_model:
            return configured_model
        return "gpt-4o-mini"

    async def _classify_llm_complete(self, messages: list[dict[str, str]]) -> str:
        """Thin wrapper that calls the provider registry with the micro-tier model."""
        providers = getattr(self.chat_manager, "_providers", None)
        if providers is None:
            raise RuntimeError("No provider registry available")
        model = self._resolve_classifier_model()
        provider = providers.resolve(model)
        result = await provider.complete(
            messages=messages, model=model, temperature=0.0, max_tokens=60,
        )
        return result.text

    async def _emit_telemetry_event(self, event_type: str, **kwargs) -> None:
        """Fire-and-forget telemetry emission."""
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

    @property
    def _memory_context(self) -> str:
        return self._memory_context_var.get()

    @_memory_context.setter
    def _memory_context(self, value: str) -> None:
        self._memory_context_var.set(value)

    def _is_fast_command(self, text: str) -> bool:
        """Check if text is a known command that can skip LLM/memory prep.

        Uses the command registry for registered chat commands and falls back
        to the legacy prefix tuple for backward compatibility.
        """
        lower = text.strip().lower()
        registry = get_default_registry()
        if registry.is_fast_command(lower):
            return True
        if any(lower.startswith(p) for p in _FAST_COMMAND_PREFIXES):
            return True
        pending = self._pending_preference_surface.get(self._current_surface_id)
        if pending and (lower in _PREF_CONFIRM_WORDS or lower.startswith("reject")):
            return True
        return False

    async def _handle_model_command(self, msg: SurfaceMessage) -> ChatCompleteEvent | None:
        """Handle /model fast command for in-chat model switching."""
        text = msg.text.strip()
        if not text.startswith("/model"):
            return None

        pfx = format_bare_prefix()
        parts = text.split()
        if len(parts) == 1:
            current = getattr(self.chat_manager, "_chat_model", "unknown")
            return self._complete_event(content=f"{pfx} Current model: {current}")
            
        # Parse args
        args = parts[1:]
        save = False
        if "--save" in args:
            save = True
            args.remove("--save")
            
        if not args:
            current = getattr(self.chat_manager, "_chat_model", "unknown")
            return self._complete_event(content=f"{pfx} Current model: {current}")
            
        model_name = args[0]
        
        # Validate via ProviderRegistry
        try:
            registry = getattr(self.chat_manager, "_providers", None)
            if not registry and hasattr(self.capability_context, "chat_manager"):
                registry = getattr(self.capability_context.chat_manager, "_providers", None)
            
            if registry:
                registry.resolve(model_name)
            else:
                logger.warning("ProviderRegistry not found; skipping model validation")
        except KeyError:
            # Try to get list of registered providers
            providers = []
            if registry and hasattr(registry, "_providers"):
                providers = list(registry._providers.keys())
            
            err_msg = f"{pfx} Unknown model: {model_name}."
            if providers:
                err_msg += f" Registered providers: {', '.join(providers)}"
            return self._complete_event(content=err_msg)
            
        # Update in-memory
        self.chat_manager._chat_model = model_name
        
        # Persist if requested
        if save:
            try:
                from dan.utils.env import update_env_file
                update_env_file("DAN_CHAT_MODEL", model_name)
                return self._complete_event(content=f"{pfx} Model changed to {model_name} and saved to .env")
            except Exception as e:
                logger.error("Failed to save model to .env", exc_info=True)
                return self._complete_event(content=f"{pfx} Model changed to {model_name} for this session, but failed to save to .env: {e}")
                
        return self._complete_event(content=f"{pfx} Model changed to {model_name} for this session.")

    async def _handle_cost_command(self, msg: SurfaceMessage) -> ChatCompleteEvent | None:
        """Handle /cost command to show total cost for current thread."""
        text = msg.text.strip().lower()
        if not text.startswith("/cost"):
            return None
        
        ctx = self.context_resolver.resolve(msg)
        workflow_id = ctx.project.project_id
        thread_id = ctx.project.thread_id
        
        try:
            chat_store = self.chat_manager._mention_resolver._chat_resolver._store
        except AttributeError:
            return self._complete_event(content=f"{format_prefix(ctx.project.label)} Chat store not available.")
            
        total_cost = 0.0
        thread = chat_store.get_thread(workflow_id, thread_id)
        if thread:
            for m in thread.messages:
                if m.estimated_cost:
                    total_cost += m.estimated_cost
                
        return self._complete_event(content=f"{format_prefix(ctx.project.label)} Total estimated cost for this thread: ${total_cost:.4f}")

    # -- /analytics ----------------------------------------------------------

    async def _handle_analytics_command(self, msg: SurfaceMessage) -> str:
        """Handle /analytics command — query telemetry store and present usage reports."""
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        from dan.server.telemetry import NullTelemetryStore, TelemetryQuery

        store = self._telemetry_store
        if store is None or isinstance(store, NullTelemetryStore):
            return "Telemetry is not enabled. Set DAN_TELEMETRY=1 to enable."

        raw = msg.text.strip()
        args = raw[len("/analytics"):].strip() if raw.lower().startswith("/analytics") else raw

        since_days = 7
        since_dt: _dt | None = None
        since_match = re.search(r"--since\s+(\d+)d", args)
        if since_match:
            since_days = int(since_match.group(1))
            args = args[: since_match.start()] + args[since_match.end() :]
        else:
            date_match = re.search(r"--since\s+(\d{4}-\d{2}-\d{2})", args)
            if date_match:
                since_dt = _dt.fromisoformat(date_match.group(1)).replace(tzinfo=_tz.utc)
                args = args[: date_match.start()] + args[date_match.end() :]

        if since_dt is None:
            since_dt = _dt.now(_tz.utc) - _td(days=since_days)

        group_by_match = re.search(r"--by\s+(\w+)", args)
        group_by: list[str] | None = None
        if group_by_match:
            group_by = [group_by_match.group(1)]
            args = args[: group_by_match.start()] + args[group_by_match.end() :]

        args = args.strip()

        if args.startswith("export"):
            return await self._analytics_export(store, since_dt, args)
        if args.startswith("project"):
            project_name = args[len("project") :].strip()
            return await self._analytics_project(store, project_name, since_dt, group_by, msg)

        if since_match:
            header_label = f"last {since_days} days"
        elif date_match:
            header_label = f"since {date_match.group(1)}"
        else:
            header_label = f"last {since_days} days"
        return await self._analytics_default(store, since_dt, header_label, group_by)

    async def _analytics_default(
        self,
        store: Any,
        since: Any,
        header_label: str,
        group_by: list[str] | None,
    ) -> str:
        from dan.server.telemetry import TelemetryQuery

        q = TelemetryQuery(since=since, limit=10_000)
        all_events = await store.query(q)
        if not all_events:
            return "No telemetry data found for the given time range."

        total_prompt = sum(e.prompt_tokens for e in all_events)
        total_completion = sum(e.completion_tokens for e in all_events)
        total_tokens = sum(e.total_tokens for e in all_events)
        total_cost = sum(e.estimated_cost for e in all_events)

        chat_turns = [e for e in all_events if e.event_type == "chat_turn"]
        fast_cmds = [e for e in all_events if e.event_type == "fast_command"]
        runs = [e for e in all_events if e.event_type == "workflow_run"]
        tools = [e for e in all_events if e.event_type == "tool_call"]

        avg_response = 0.0
        if chat_turns:
            avg_response = sum(e.duration_ms for e in chat_turns) / len(chat_turns) / 1000

        lines = [
            f"Usage Report ({header_label})",
            "\u2500" * 30,
            f"Total tokens: {total_tokens:,} (in: {total_prompt:,} / out: {total_completion:,})",
            f"Total cost: ${total_cost:.2f}",
        ]
        if chat_turns:
            lines.append(f"Avg response time: {avg_response:.1f}s")
        lines.append(
            f"Turns: {len(chat_turns)} | Commands: {len(fast_cmds)} | Runs: {len(runs)} | Tools: {len(tools)}"
        )
        lines.append("")

        effective_group = group_by or ["event_type"]
        rows = await store.aggregate(q, group_by=effective_group)
        if rows:
            label = effective_group[0] if len(effective_group) == 1 else "+".join(effective_group)
            lines.append(f"By {label}:")
            for r in sorted(rows, key=lambda x: x.total_cost, reverse=True):
                key_val = " / ".join(str(v) for v in r.group_key.values() if v)
                avg_s = r.avg_duration_ms / 1000 if r.avg_duration_ms else 0
                lines.append(
                    f"  {key_val:20s}  {r.total_tokens:>8,} tok  ${r.total_cost:>6.2f}  {avg_s:.1f}s avg  {r.count} events"
                )

        return "\n".join(lines)

    async def _analytics_project(
        self,
        store: Any,
        project_name: str,
        since: Any,
        group_by: list[str] | None,
        msg: SurfaceMessage,
    ) -> str:
        from dan.server.telemetry import TelemetryQuery

        if not project_name:
            return "Usage: /analytics project <name>"

        surface_id = msg.external_id
        projects = self.project_store.list_projects(surface_id)
        matched = None
        for p in projects:
            if project_name.lower() in p.label.lower():
                matched = p
                break

        if matched is None:
            return f"No project matching '{project_name}' found."

        q = TelemetryQuery(project_id=matched.project_id, since=since, limit=10_000)
        all_events = await store.query(q)
        if not all_events:
            return f"No telemetry data for project '{matched.label}' in the given time range."

        total_tokens = sum(e.total_tokens for e in all_events)
        total_cost = sum(e.estimated_cost for e in all_events)
        chat_turns = [e for e in all_events if e.event_type == "chat_turn"]

        lines = [
            f"Project: {matched.label}",
            "\u2500" * 30,
            f"Total tokens: {total_tokens:,}",
            f"Total cost: ${total_cost:.2f}",
            f"Chat turns: {len(chat_turns)}",
            "",
        ]

        effective_group = group_by or ["model"]
        rows = await store.aggregate(q, group_by=effective_group)
        if rows:
            label = effective_group[0]
            lines.append(f"By {label}:")
            for r in sorted(rows, key=lambda x: x.total_cost, reverse=True):
                key_val = " / ".join(str(v) for v in r.group_key.values() if v)
                lines.append(
                    f"  {key_val:20s}  {r.total_tokens:>8,} tok  ${r.total_cost:>6.2f}  {r.count} events"
                )

        return "\n".join(lines)

    async def _analytics_export(self, store: Any, since: Any, args: str) -> str:
        from datetime import datetime as _dt
        from pathlib import Path

        from dan.server.telemetry import TelemetryQuery

        fmt = "jsonl"
        fmt_match = re.search(r"--format\s+(jsonl|csv)", args)
        if fmt_match:
            fmt = fmt_match.group(1)

        q = TelemetryQuery(since=since, limit=100_000)
        export_dir = Path.home() / ".dan" / "exports"
        timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")

        if fmt == "csv":
            path = export_dir / f"telemetry_{timestamp}.csv"
            count = await store.export_csv(q, path=path)
        else:
            path = export_dir / f"telemetry_{timestamp}.jsonl"
            count = await store.export_jsonl(q, path=path)

        return f"Exported {count} events to {path}"

    async def _handle_retry_command(self, msg: SurfaceMessage) -> ChatCompleteEvent | None:
        """Handle /retry command to re-process the last user message."""
        text = msg.text.strip().lower()
        if not text.startswith("/retry"):
            return None
            
        ctx = self.context_resolver.resolve(msg)
        workflow_id = ctx.project.project_id
        thread_id = ctx.project.thread_id
        
        try:
            chat_store = self.chat_manager._mention_resolver._chat_resolver._store
        except AttributeError:
            return self._complete_event(content=f"{format_prefix(ctx.project.label)} Chat store not available.")
            
        thread = chat_store.get_thread(workflow_id, thread_id)
        last_user_msg = None
        if thread:
            for m in reversed(thread.messages):
                if m.role == "user":
                    last_user_msg = m.content
                    break
                
        if not last_user_msg:
            return self._complete_event(content=f"{format_prefix(ctx.project.label)} No previous user message found to retry.")
            
        # We can't easily yield the stream from here since we return a single event,
        # but wait, fast commands return ChatCompleteEvent. 
        # To actually re-process, we should probably return None and let the caller handle it,
        # or we just mutate the message text and let the normal flow handle it.
        # Let's mutate the message text.
        msg.text = last_user_msg
        return None  # Return None so it falls through to normal processing with the new text

    async def _handle_status_command(self, msg: SurfaceMessage) -> ChatCompleteEvent | None:
        """Handle /status command to show system status."""
        text = msg.text.strip().lower()
        if not text.startswith("/status"):
            return None
            
        ctx = self.context_resolver.resolve(msg)
        
        from dan.server.capability_handlers import handle_get_activity
        res = await handle_get_activity({}, self.capability_context)
        
        current_model = getattr(self.chat_manager, "_chat_model", "unknown")
        status_text = f"{format_prefix(ctx.project.label)} System Status\n"
        status_text += f"Model: {current_model}\n"
        status_text += f"Cost Tracking: {'Enabled' if os.environ.get('DAN_SHOW_COST') == '1' else 'Disabled'}\n\n"
        status_text += res.message

        # 31-15 §4-2: Learning section
        status_text += self._format_learning_status()
        
        return self._complete_event(content=status_text)

    def _format_learning_status(self) -> str:
        """Build the Learning section for /status output."""
        lines: list[str] = ["\n\n**Learning**"]
        try:
            from dan.engine.learning_tiers import (
                resolve_learning_tier,
                features_enabled_at_tier,
            )

            tier = self._learning_tier if self._learning_tier is not None else resolve_learning_tier()
            enabled = features_enabled_at_tier(tier)
            lines.append(f"Active tier: {tier}")
            lines.append(f"Enabled features: {', '.join(sorted(enabled)) if enabled else 'none'}")
        except Exception:
            lines.append("Tier: unknown")

        if self._health_counters is not None:
            try:
                summary = self._health_counters.get_summary()
                total_events = sum(c["attempted"] for c in summary.values())
                last_ts = 0.0
                for path_name, counts in summary.items():
                    att = counts["attempted"]
                    if att > 0:
                        rate = counts["succeeded"] / att
                        lines.append(
                            f"  {path_name}: {att} events ({rate:.0%} success)"
                        )
                lines.append(f"Total events: {total_events}")
            except Exception:
                lines.append("Health counters: error reading")
        else:
            lines.append("Health counters: not initialized")

        # §4-3: Minimum-sample warnings
        try:
            model_samples = 0
            if self.memory_kernel is not None:
                from dan.engine.memory_kernel import MemoryType, MemoryScope
                items = self.memory_kernel.list_by_type(
                    MemoryType.EPISODE, scope=MemoryScope.WORKFLOW, limit=500,
                )
                model_samples = sum(
                    1 for item in items
                    if item.metadata.get("tracker") == "model_outcome"
                )
            if model_samples < 15:
                lines.append(
                    f"  ⚠ Model learning: {model_samples}/15 samples collected "
                    f"— recommendations not yet active"
                )
        except Exception:
            pass

        return "\n".join(lines)

    async def _handle_cancel_command(self, msg: SurfaceMessage) -> ChatCompleteEvent | None:
        """Handle /cancel command by cancelling the requested run reference."""
        text = msg.text.strip()
        if not text.startswith("/cancel"):
            return None

        parts = text.split(maxsplit=1)
        run_ref = parts[1].strip() if len(parts) > 1 else "latest"

        from dan.server.capability_handlers import handle_cancel_run

        result = await handle_cancel_run({"run_id": run_ref}, self.capability_context)
        ctx = self.context_resolver.resolve(msg)
        return self._complete_event(
            content=f"{format_prefix(ctx.project.label)} {result.message}"
        )

    async def _run_goal_loop(self, external_id: str, state: Any, original_msg: Any) -> None:
        """Background task running the goal loop executor (31-6)."""
        from .goal_loop import GoalLoopExecutor, LLMJudgeEvaluator, make_progress_callback, get_tier_prompt

        try:
            evaluator = LLMJudgeEvaluator(llm_fn=self._goal_llm_evaluate)
            executor = GoalLoopExecutor(goal=state.goal, evaluator=evaluator, state=state)

            notify_fn = None
            event_bus = getattr(self, "capability_context", None) and getattr(self.capability_context, "event_bus", None)
            if event_bus is not None:
                def _notify(msg_text: str) -> None:
                    try:
                        event_bus.broadcast({
                            "event_type": "notification",
                            "data": {"title": "Goal Loop", "body": msg_text},
                        })
                    except Exception:
                        pass
                notify_fn = _notify

            callback = make_progress_callback(notify_fn=notify_fn)

            async def attempt_fn(attempt_number: int, strategy_tier: int, best_result: Any, previous_attempts: list) -> dict:
                prompt = get_tier_prompt(
                    strategy_tier, state.goal, best_result,
                    previous_attempts[-3:] if previous_attempts else [],
                )
                return {"approach_summary": f"Attempt {attempt_number} at tier {strategy_tier}", "prompt": prompt}

            await executor.run_loop(attempt_fn=attempt_fn, on_progress=callback)
        except Exception:
            logger.exception("Goal loop execution failed for %s", external_id)

    async def _goal_llm_evaluate(self, context: dict) -> float:
        """Placeholder LLM judge evaluation — returns a score based on context."""
        return context.get("score", 5.0)

    async def _coerce_fast_command_result(self, result: Any) -> ChatCompleteEvent | None:
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, ChatCompleteEvent):
            return result
        if result is None:
            return None
        return self._complete_event(content=str(result))

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

        handler = registry.resolve_handler(descriptor.name)
        if handler is None:
            return None

        if descriptor.name == "/goal":
            goal_context = self._goal_loop_states.setdefault(msg.external_id, {})
            result = await self._coerce_fast_command_result(
                handler(msg.text, context=goal_context),
            )
            # 31-6: Spawn goal loop execution if state was created
            goal_state = goal_context.get("goal_state")
            if goal_state is not None:
                from .goal_loop import GoalLoopState
                if isinstance(goal_state, GoalLoopState) and goal_state.status == "running":
                    asyncio.create_task(
                        self._run_goal_loop(msg.external_id, goal_state, msg)
                    )
            return result
        if descriptor.name == "/goal-status":
            goal_state = self._goal_loop_states.get(msg.external_id, {}).get("goal_state")
            return await self._coerce_fast_command_result(
                handler(msg.text, context=goal_state),
            )
        if descriptor.name == "/goal-stop":
            goal_state = self._goal_loop_states.get(msg.external_id, {}).get("goal_state")
            result = await self._coerce_fast_command_result(
                handler(msg.text, context=goal_state),
            )
            if goal_state is not None:
                self._goal_loop_states.pop(msg.external_id, None)
            return result
        if descriptor.name == "/follow-ups":
            from .follow_up import FollowUpQueue, load_follow_up_config

            config = self._follow_up_config or load_follow_up_config()
            queue = self._follow_up_queue or FollowUpQueue()
            return await self._coerce_fast_command_result(handler(msg.text, config, queue))
        if descriptor.name == "/schedule":
            from .scheduler import DeliveryTarget, ScheduleStore, TriggerContext

            store = self._schedule_store or ScheduleStore()
            if hasattr(store, "load"):
                store.load()
            default_trigger_context = None
            default_delivery_target = None
            lower_text = msg.text.strip().lower()
            if lower_text.startswith("/schedule add"):
                default_trigger_context = TriggerContext(
                    source_surface=msg.surface or "cli",
                    user_id=msg.external_id,
                    thread_key=str(msg.metadata.get("thread_id") or "") or None,
                )
                default_delivery_target = DeliveryTarget(
                    surface=msg.surface or "cli",
                    conversation_key=msg.external_id,
                    user_id=msg.external_id,
                    thread_key=str(msg.metadata.get("thread_id") or "") or None,
                )
                active_projects = self.project_store.list_active(msg.external_id)
                if active_projects:
                    schedule_project = active_projects[0]
                    schedule_task = self.project_store.get_current_task(
                        schedule_project.project_id,
                        msg.external_id,
                    )
                    if schedule_task is not None:
                        default_trigger_context = default_trigger_context.model_copy(
                            update={
                                "project_id": schedule_project.project_id,
                                "task_id": schedule_task.task_id,
                            }
                        )
                        default_delivery_target = default_delivery_target.model_copy(
                            update={"project_id": schedule_project.project_id},
                        )
            return await self._coerce_fast_command_result(
                handler(
                    msg.text,
                    store,
                    self._schedule_history_store,
                    default_trigger_context=default_trigger_context,
                    default_delivery_target=default_delivery_target,
                )
            )
        if descriptor.name == "/completion":
            return await self._coerce_fast_command_result(
                handler(msg.text, self.capability_context),
            )
        if descriptor.name == "/pii":
            from .pii_tokenizer import SensitiveWordRegistry

            pii_session_key = str(
                msg.metadata.get("thread_id")
                or msg.metadata.get("workflow_id")
                or msg.external_id
            )
            return await self._coerce_fast_command_result(
                handler(
                    msg.text,
                    SensitiveWordRegistry.load(),
                    session_key=pii_session_key,
                )
            )
        if descriptor.name == "/resume":
            return await self._coerce_fast_command_result(
                handler(msg.text, self.project_store),
            )
        if descriptor.name == "/sync":
            from .continuity import PresenceTracker

            tracker = self._presence_tracker or PresenceTracker()
            return await self._coerce_fast_command_result(
                handler(msg.text, self.project_store, tracker),
            )
        if descriptor.name == "/progress":
            return await self._coerce_fast_command_result(
                handler(msg.text, surface_id=msg.external_id),
            )
        if descriptor.name == "/corrections":
            from dan.engine.correction_memory import CorrectionStore

            return await self._coerce_fast_command_result(
                handler(msg.text, self._correction_store or CorrectionStore()),
            )
        if descriptor.name == "/adaptations":
            from dan.engine.adaptation_registry import AdaptationRegistry

            return await self._coerce_fast_command_result(
                handler(msg.text, self._adaptation_registry or AdaptationRegistry()),
            )
        if descriptor.name == "/computer":
            from .computer_policy import AuditLog, ComputerControlConfig
            from .computer_use import ComputerUseLeaseManager

            config = self._computer_config or ComputerControlConfig.load()
            return await self._coerce_fast_command_result(
                handler(
                    msg.text,
                    config,
                    self._computer_lease or ComputerUseLeaseManager(),
                    self._computer_audit or AuditLog(),
                )
            )
        if descriptor.name == "/skill":
            from dan.server.skill_store import SkillStore, handle_skill_command

            store = getattr(self, "_skill_store", None) or SkillStore()
            return await self._coerce_fast_command_result(
                handle_skill_command(msg.text, store),
            )

        params = list(inspect.signature(handler).parameters.values())
        if len(params) == 0:
            return await self._coerce_fast_command_result(handler())
        if len(params) == 1:
            if params[0].name in {"msg", "message", "surface_message"}:
                return await self._coerce_fast_command_result(handler(msg))
            return await self._coerce_fast_command_result(handler(msg.text))
        return None

    async def _try_fast_command(
        self, msg: SurfaceMessage,
    ) -> ChatCompleteEvent | None:
        """Dispatch known commands instantly, skipping expensive prep."""
        if not self._is_fast_command(msg.text):
            return None
        registry = get_default_registry()
        descriptor = registry.match(msg.text)
        if descriptor is not None and descriptor.kind == "chat":
            dispatch_result = await self._dispatch_registry_fast_command(
                msg,
                descriptor,
                registry,
            )
            if dispatch_result is not None:
                self._telem_is_fast_command = True
                return dispatch_result
        pref_confirmation = self.handle_preference_confirmation(
            msg.external_id, msg.text,
        )
        if pref_confirmation:
            self._telem_is_fast_command = True
            return self._complete_event(
                content=f"{format_prefix()} {pref_confirmation}",
            )
        return None

    # ------------------------------------------------------------------
    # Reassurance timer: if the inner work hasn't produced a response
    # within _REASSURANCE_INITIAL_DELAY seconds, emit a calming progress
    # note so the user knows we're still working.
    # ------------------------------------------------------------------

    _REASSURANCE_INITIAL_DELAY: float = float(
        os.environ.get("DAN_CONCIERGE_REASSURANCE_DELAY", "10")
    )
    _REASSURANCE_REPEAT_INTERVAL: float = float(
        os.environ.get("DAN_CONCIERGE_REASSURANCE_INTERVAL", "20")
    )

    _REASSURANCE_MESSAGES = [
        "Working on your request — this might take a moment\u2026",
        "Still working on it \u2014 gathering information\u2026",
        "Hang tight \u2014 pulling things together\u2026",
        "Almost there \u2014 finishing up\u2026",
    ]

    def _summarize_progress_request(self, text: str, *, max_chars: int = 96) -> str:
        clean = re.sub(r"\s+", " ", text).strip()
        clean = re.sub(
            r"^(?:also|and|one more thing|another thing|another question)[,:]?\s+",
            "",
            clean,
            flags=re.IGNORECASE,
        )
        if len(clean) > max_chars:
            clean = clean[: max_chars - 1].rstrip() + "…"
        return clean

    def _progress_ack_content(self, msg: SurfaceMessage) -> str:
        surface = str(getattr(msg, "surface", "") or "").split(":", 1)[0]
        if surface in {"telegram", "whatsapp", "whatsapp-web", "email"}:
            return "Working on it..."
        ack_text = self._summarize_progress_request(msg.text) or "Working on your request..."
        return f"Got it. {ack_text}"

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

    @staticmethod
    def _is_messaging_surface(msg: SurfaceMessage) -> bool:
        surface = str(getattr(msg, "surface", "") or "").split(":", 1)[0]
        return surface in {"telegram", "whatsapp", "whatsapp-web", "email"}

    def _format_progress_status(
        self,
        prefix: str,
        label: str,
        elapsed_seconds: float,
    ) -> str:
        clean_label = label.strip().rstrip(".!?")
        if elapsed_seconds > 0:
            return (
                f"{prefix} — {clean_label} "
                f"({self._format_elapsed_seconds(elapsed_seconds)})"
            )
        return f"{prefix} — {clean_label}"

    _MIN_PHASE_EVENT_INTERVAL: float = 1.0

    def _set_progress_phase(
        self,
        external_id: str,
        phase_id: str,
        name: str,
        detail: str | None = None,
    ) -> None:
        session = self._progress_sessions.get(external_id)
        if session is None:
            return
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
                    return
            session.update_phase(phase_id, detail)

    def _make_phase_event(
        self,
        external_id: str,
        phase_id: str,
        name: str,
        detail: str | None = None,
    ) -> ChatCompleteEvent | None:
        """Set the progress phase and return a progress_ack event if appropriate.

        Returns an event only when the surface verbosity is not ``minimal``
        and at least ``_MIN_PHASE_EVENT_INTERVAL`` seconds have passed since
        the last emitted phase event (to avoid spamming edits).
        """
        self._set_progress_phase(external_id, phase_id, name, detail)
        session = self._progress_sessions.get(external_id)
        if session is None:
            return None
        if getattr(session, "verbosity", "minimal") == "minimal":
            return None
        now = time.monotonic()
        last_phase_event_time = float(
            getattr(session, "_last_phase_event_time", 0.0) or 0.0,
        )
        if now - last_phase_event_time < self._MIN_PHASE_EVENT_INTERVAL:
            return None
        session._last_phase_event_time = now
        label = detail or name
        elapsed_seconds = session.elapsed_total()
        return ChatCompleteEvent(
            message_id=uuid.uuid4().hex[:12],
            content=self._format_progress_status(
                "Working on it",
                label,
                elapsed_seconds,
            ),
            token_usage={},
            context_window=0,
            graph_revision="",
            detected_mode="progress_ack",
        )

    def _create_progress_renderer(self, surface: str, msg: Any) -> Any | None:
        """Return a surface-specific ProgressRenderer, or None for null/unsupported."""
        try:
            from .progress_ux import CLIProgressRenderer
        except ImportError:
            return None
        if surface == "cli":
            return CLIProgressRenderer()
        return None

    def _progress_label_for_intent(self, intent: IntentCategory) -> str:
        labels = {
            IntentCategory.FILE_REQUEST: "Searching files and documents",
            IntentCategory.DIRECT_TASK: "Working through the request",
            IntentCategory.RUN_CONTROL: "Preparing the workflow run",
            IntentCategory.WORKFLOW_BUILD: "Planning the workflow",
            IntentCategory.WORKFLOW_QUERY: "Inspecting the workflow",
            IntentCategory.EXPERIENCE_QUERY: "Checking similar past work",
            IntentCategory.PUBLISH_SHARE: "Preparing sharing and publish steps",
            IntentCategory.STATUS_CHECK: "Checking current status",
            IntentCategory.META_GOAL: "Planning the broader goal",
            IntentCategory.CONVERSATION: "Thinking through the request",
        }
        return labels.get(intent, "Working through the request")

    def _build_reassurance_message(
        self,
        msg: SurfaceMessage,
        reassurance_count: int,
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
                    return self._format_progress_status(
                        prefix,
                        latest,
                        elapsed_seconds,
                    )

        if self._is_messaging_surface(msg):
            return self._format_progress_status(
                prefix,
                "preparing response",
                elapsed_seconds,
            )

        summary = self._summarize_progress_request(msg.text)
        if summary:
            return self._format_progress_status(
                prefix,
                summary,
                elapsed_seconds,
            )

        idx = min(reassurance_count, len(self._REASSURANCE_MESSAGES) - 1)
        fallback = self._REASSURANCE_MESSAGES[idx].rstrip("…")
        if elapsed_seconds > 0:
            return f"{fallback} ({self._format_elapsed_seconds(elapsed_seconds)})"
        return self._REASSURANCE_MESSAGES[idx]

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
                surface=getattr(msg, "surface_id", None),
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
                        _telem_tokens["prompt_tokens"] = _telem_tokens.get("prompt_tokens", 0) + _tu.get("prompt_tokens", 0)
                        _telem_tokens["completion_tokens"] = _telem_tokens.get("completion_tokens", 0) + _tu.get("completion_tokens", 0)
                        _telem_tokens["total_tokens"] = _telem_tokens.get("total_tokens", 0) + _tu.get("total_tokens", 0)
                        _telem_cost += getattr(event, "estimated_cost", 0) or 0
                    if (
                        self._is_messaging_surface(msg)
                        and isinstance(event, ChatCompleteEvent)
                        and getattr(event, "detected_mode", None) == "progress_ack"
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
                if not first_event_received:
                    timeout = self._REASSURANCE_INITIAL_DELAY
                else:
                    timeout = self._REASSURANCE_REPEAT_INTERVAL

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
                    _telem_tokens["prompt_tokens"] = _telem_tokens.get("prompt_tokens", 0) + _tu.get("prompt_tokens", 0)
                    _telem_tokens["completion_tokens"] = _telem_tokens.get("completion_tokens", 0) + _tu.get("completion_tokens", 0)
                    _telem_tokens["total_tokens"] = _telem_tokens.get("total_tokens", 0) + _tu.get("total_tokens", 0)
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
                    "surface_id": msg.surface_id,
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

        # Fast-path: known commands respond instantly without LLM/memory prep.
        fast_event = await self._try_fast_command(msg)
        if fast_event is not None:
            self._save_concierge_state(msg.external_id, self._concierge_state)
            yield fast_event
            return

        # 31-11: Check for resumable tasks on session start
        try:
            from .resume import ResumeProtocol
            _resume_proto = ResumeProtocol()
            _resumable = _resume_proto.check_resumable_tasks(self.project_store)
            if _resumable:
                _auto_match = _resume_proto.auto_resume_match(msg.text, _resumable)
                if _auto_match is not None:
                    _resume_ctx = _resume_proto.generate_resume_prompt(_auto_match)
                    msg = msg.model_copy(update={
                        "metadata": {**msg.metadata, "resume_context": _resume_ctx, "resume_task_id": _auto_match.task_id},
                    })
        except Exception:
            logger.debug("Resume check failed", exc_info=True)

        # 31-13: Detect surface switch for cross-surface continuity
        try:
            from .continuity import detect_surface_switch, generate_handoff_context
            _switch_project_id = detect_surface_switch(
                msg.external_id, msg.text, self.project_store,
            )
            if _switch_project_id:
                _handoff_ctx = generate_handoff_context(
                    _switch_project_id, self.project_store,
                )
                if _handoff_ctx:
                    msg = msg.model_copy(update={
                        "metadata": {**msg.metadata, "handoff_context": _handoff_ctx},
                    })
        except Exception:
            logger.debug("Surface switch detection failed", exc_info=True)

        # 31-14: Initialize progress session for this request
        try:
            from .progress_ux import (
                ProgressSession,
                get_user_verbosity_override,
                resolve_verbosity,
            )
            _surface = msg.surface or "cli"
            _verbosity = get_user_verbosity_override(msg.external_id) or resolve_verbosity(_surface)
            _progress_session = ProgressSession(
                surface=_surface,
                verbosity=_verbosity,
            )
            _renderer = self._create_progress_renderer(_surface, msg)
            if _renderer is not None:
                _progress_session.renderer = _renderer
            self._progress_sessions[msg.external_id] = _progress_session
            self._set_progress_phase(
                msg.external_id,
                "intake",
                "Understanding your request",
                self._summarize_progress_request(msg.text),
            )
        except Exception:
            logger.debug("Progress session init failed", exc_info=True)

        has_active_build = any(
            goal.status in ("active", "paused") and goal.context.get("build_session_id")
            for goal in self._concierge_state.active_goals
        )

        # 31-14 §3-2: Instant ack removed — the reassurance timer in
        # process() now handles "Working on it…" after a short delay so
        # that quick replies (Hi, Hello) never show an interim bubble.

        _precomputed_context = None
        _speculative_reuse: tuple[str, Any] | None = None
        _phase_evt = self._make_phase_event(
            msg.external_id,
            "context",
            "Gathering context",
            "Gathering relevant context",
        )
        if _phase_evt is not None:
            yield _phase_evt
        if os.environ.get("DAN_CONCIERGE_PARALLEL_PREP", "1") == "1":
            from .fan_out import fan_out_dict

            prep_tasks: dict[str, Any] = {
                "memory": lambda: asyncio.to_thread(
                    self._retrieve_memory_context, msg.text,
                    has_active_build=has_active_build,
                ),
                "context": lambda: asyncio.to_thread(
                    self.context_resolver.resolve, msg,
                ),
            }
            # 29-5 §6-2: speculatively start reuse search concurrently
            if self.memory_kernel and not msg.metadata.get("reuse_choice"):
                from .reuse_decision import reuse_first_decision

                prep_tasks["reuse"] = lambda: asyncio.to_thread(
                    reuse_first_decision, self.memory_kernel, msg.text,
                )
            
            prep_timeout = float(os.environ.get("DAN_CONCIERGE_PREP_TIMEOUT", "5.0"))
            prep = await fan_out_dict(prep_tasks, timeout_per=prep_timeout)
            
            self._memory_context = prep.get("memory", "")
            if isinstance(self._memory_context, Exception):
                logger.debug("Parallel memory retrieval failed", exc_info=self._memory_context)
                self._memory_context = ""
            _precomputed_context = prep.get("context")
            if isinstance(_precomputed_context, Exception):
                logger.debug("Parallel context resolution failed", exc_info=_precomputed_context)
                _precomputed_context = None
            _reuse_raw = prep.get("reuse")
            if isinstance(_reuse_raw, tuple) and len(_reuse_raw) == 2:
                _speculative_reuse = _reuse_raw
            elif isinstance(_reuse_raw, Exception):
                logger.debug("Speculative reuse search failed", exc_info=_reuse_raw)
        else:
            self._memory_context = self._retrieve_memory_context(
                msg.text, has_active_build=has_active_build,
            )

        self._save_concierge_state(msg.external_id, self._concierge_state)

        # ── UNDERSTAND phase: entity grounding ──────────────────────
        _entity_ctx = EntityContext(
            matched_projects=[], matched_tasks=[], matched_workflows=[],
            unresolved_refs=[], is_about_project=False,
        )
        if guards_enabled():
            try:
                _entity_ctx = ground_entities(msg.text, self.project_store, msg.external_id)
                if _entity_ctx.matched_projects:
                    logger.info(
                        "Entity grounding matched %d project(s): %s",
                        len(_entity_ctx.matched_projects),
                        ", ".join(p.label for p in _entity_ctx.matched_projects[:3]),
                    )
            except Exception:
                logger.debug("Entity grounding failed", exc_info=True)

        pending_resolution = self._resolve_pending_follow_up(msg)
        if pending_resolution is not None:
            immediate_event, context, classification, msg = pending_resolution
            self._last_context = context
            if immediate_event is not None:
                yield immediate_event
                return
        else:
            context = (
                _precomputed_context
                if _precomputed_context is not None
                else self.context_resolver.resolve(msg)
            )
            self._last_context = context
            _cls_start = time.monotonic()
            classification = await classify_intent_llm(
                msg.text, context, self._classify_llm_complete,
            )
            _intent_str = classification.intent.value if hasattr(classification.intent, "value") else str(classification.intent)
            self._telem_intent = _intent_str
            await self._emit_telemetry_event(
                "classification",
                intent=_intent_str,
                duration_ms=(time.monotonic() - _cls_start) * 1000,
                metadata={"confidence": getattr(classification, "confidence", None)},
            )

        # ── UNDERSTAND phase: Guard 1 — classification coherence ──
        _guard_ctx = GuardContext(
            message=msg, entity_ctx=_entity_ctx, classification=classification,
        )
        if guards_enabled():
            try:
                _g1_start = time.monotonic()
                _g1 = guard_classification(_guard_ctx)
                await self._emit_telemetry_event(
                    "guard_check",
                    duration_ms=(time.monotonic() - _g1_start) * 1000,
                    guard_action=_g1.action,
                    success=_g1.passed,
                    metadata={"guard": "classification", "notes": _g1.notes},
                )
                for _note in _g1.notes:
                    logger.info("Guard 1: %s", _note)
                if not _g1.passed:
                    if _g1.action == "short_circuit" and _g1.short_circuit_response:
                        label = self._format_reply_label(context, msg)
                        sc_content = _g1.short_circuit_response
                        if label and not sc_content.startswith("["):
                            sc_content = f"{label} {sc_content}"
                        self.project_store.append_turn(
                            context.project.project_id,
                            context.task.task_id,
                            TaskTurn(role="user", content=msg.text, intent="status_check"),
                            msg.external_id,
                        )
                        self._record_assistant_turn(context, msg, sc_content)
                        self._finalize_task(context, msg, IntentCategory.STATUS_CHECK, True)
                        yield self._complete_event(content=sc_content)
                        return
                    if _g1.action == "reclassify":
                        classification = await classify_intent_llm(
                            msg.text, context, self._classify_llm_complete,
                        )
                        _guard_ctx.classification = classification
                    if _g1.action == "clarify" and _g1.clarification_question:
                        self.project_store.append_turn(
                            context.project.project_id,
                            context.task.task_id,
                            TaskTurn(role="user", content=msg.text, intent=classification.intent.value),
                            msg.external_id,
                        )
                        self._record_assistant_turn(context, msg, _g1.clarification_question)
                        yield self._complete_event(content=_g1.clarification_question)
                        return
            except Exception:
                logger.debug("Guard 1 (classification) failed", exc_info=True)

        _resolved_project_id = context.project.project_id
        if _resolved_project_id and self.memory_kernel and self._memory_context is not None:
            _mem_start = time.monotonic()
            _project_supplement = self._retrieve_memory_context(
                msg.text,
                has_active_build=has_active_build,
                project_id=_resolved_project_id,
            )
            await self._emit_telemetry_event(
                "memory_retrieval",
                duration_ms=(time.monotonic() - _mem_start) * 1000,
                metadata={"has_results": bool(_project_supplement), "source": "context_building"},
            )
            if _project_supplement and _project_supplement not in (self._memory_context or ""):
                existing = (self._memory_context or "").rstrip()
                self._memory_context = f"{existing}\n{_project_supplement}".strip() if existing else _project_supplement

        _phase_evt = self._make_phase_event(
            msg.external_id,
            "execution",
            "Working on the request",
            self._progress_label_for_intent(classification.intent),
        )
        if _phase_evt is not None:
            yield _phase_evt

        try:
            if self._presence_tracker is not None:
                self._presence_tracker.update(
                    surface_id=msg.external_id,
                    surface_type=msg.surface or "cli",
                    project_id=context.project.project_id,
                )
        except Exception:
            logger.debug("Presence tracking update failed", exc_info=True)

        try:
            correction_store = self._correction_store
            if correction_store is not None:
                previous_assistant_turn = next(
                    (
                        turn.content
                        for turn in reversed(context.task.turns)
                        if turn.role == "assistant"
                    ),
                    "",
                )
                if previous_assistant_turn:
                    from dan.engine.correction_memory import (
                        CorrectionRecord,
                        detect_correction,
                        route_correction,
                    )

                    signal = detect_correction(msg.text, previous_assistant_turn)
                    if signal is not None:
                        actions = route_correction(signal)
                        if actions:
                            correction_store.add(
                                CorrectionRecord(signal=signal, actions=actions),
                            )
                        if actions and self._adaptation_registry is not None:
                            from dan.engine.adaptation_registry import AdaptationCandidate

                            for action in actions:
                                if action.get("type") not in {"preference", "principle"}:
                                    continue
                                value = str(
                                    action.get("value")
                                    or signal.correction_text
                                    or signal.original_output
                                ).strip()
                                self._adaptation_registry.add(
                                    AdaptationCandidate(
                                        source="principle",
                                        evidence=[signal.correction_text],
                                        confidence=signal.confidence,
                                        sample_size=1,
                                        scope="conversation",
                                        description=value[:200],
                                    )
                                )
                        # 31-15 §3-4: Store preferences/principles in MemoryKernel
                        if actions and self.memory_kernel is not None:
                            _correction_project_id = context.project.project_id
                            for action in actions:
                                atype = action.get("type")
                                avalue = action.get("value", "")
                                if atype == "preference" and avalue:
                                    self.memory_kernel.store_preference(
                                        avalue, confirmed=False,
                                        project_id=_correction_project_id,
                                    )
                                elif atype == "principle" and avalue:
                                    self.memory_kernel.store_principle(
                                        avalue,
                                        confidence=signal.confidence,
                                        project_id=_correction_project_id,
                                    )
        except Exception:
            logger.debug("Correction detection failed", exc_info=True)

        action_policy, _execution_policy = resolve_policy(
            intent=classification.intent,
            text=msg.text,
            context=context,
            user_profile=self.user_profile,
            surface=msg.surface,
        )
        if action_policy == ActionPolicy.CONFIRM and not msg.metadata.get("skip_confirm"):
            self.project_store.set_pending_action(
                context.project.project_id,
                PendingAction(
                    kind="confirm",
                    intent=classification.intent.value,
                    original_text=msg.text,
                    metadata=dict(msg.metadata),
                ),
                msg.external_id,
            )
            self.project_store.append_turn(
                context.project.project_id,
                context.task.task_id,
                TaskTurn(role="user", content=msg.text, intent=classification.intent.value),
                msg.external_id,
            )
            self.project_store.update_task_status(
                context.project.project_id,
                context.task.task_id,
                "paused",
                msg.external_id,
            )
            yield self._complete_event(content=f"{format_prefix(context.project.label)} Please confirm before I do that.")
            return

        # 31-14 §4-2/4-3/4-4: Preflight clarification for expensive tasks
        try:
            from .progress_ux import (
                format_quick_confirm,
                generate_preflight_questions,
                should_preflight_clarify,
            )
            _est_time = float(msg.metadata.get("estimated_time", 0))
            _est_cost = float(msg.metadata.get("estimated_cost", 0))
            if should_preflight_clarify(_est_time, _est_cost):
                _pf_context = {
                    "estimated_time": _est_time,
                    "estimated_cost": _est_cost,
                    **{k: v for k, v in msg.metadata.items()
                       if k not in ("estimated_time", "estimated_cost")},
                }
                _pf_questions = generate_preflight_questions(msg.text, _pf_context)
                if _pf_questions:
                    _pf_defaults = [f"(auto)" for _ in _pf_questions]
                    _pf_confirm = format_quick_confirm(_pf_questions, _pf_defaults)
                    yield ChatCompleteEvent(
                        message_id=uuid.uuid4().hex[:12],
                        content=_pf_confirm.checkpoint.summary,
                        token_usage={},
                        context_window=0,
                        graph_revision="",
                        detected_mode="preflight_clarify",
                    )
        except Exception:
            logger.debug("Preflight clarification failed", exc_info=True)

        use_goal_orchestrator = (
            self._should_use_goal_orchestrator(classification)
            or self._should_resume_goal_follow_up(
                msg.text,
                classification,
                context.project.project_id,
            )
        )

        if self.goal_resolver is not None and not use_goal_orchestrator:
            async for event in self._solver_path(msg, context, classification, entity_ctx=_entity_ctx):
                yield event
            return

        if use_goal_orchestrator:
            goal, action = self._continue_or_new(
                msg.text,
                self._concierge_state.active_goals,
                context.project.project_id,
            )
            goal.context.setdefault("project_id", context.project.project_id)
            if action == "pause":
                goal.status = "paused"
                goal.updated_at = time.time()
                idx = next((i for i, g in enumerate(self._concierge_state.active_goals) if g.id == goal.id), None)
                if idx is not None:
                    self._concierge_state.active_goals[idx] = goal
                self._save_concierge_state(msg.external_id, self._concierge_state)
                self.project_store.append_turn(
                    context.project.project_id,
                    context.task.task_id,
                    TaskTurn(role="user", content=msg.text, intent=classification.intent.value),
                    msg.external_id,
                )
                pause_msg = f"{format_prefix(context.project.label)} Goal paused. Say 'continue' to resume."
                self._record_assistant_turn(context, msg, pause_msg)
                self._finalize_task(context, msg, classification.intent, True, task_status_override="paused")
                yield self._complete_event(content=pause_msg)
                return
            if action == "new":
                self._pause_active_goals(msg.external_id)
                override = self._autonomy_override_from_message(msg.text)
                if override:
                    goal.context["autonomy_override"] = override
                goal.context["effective_autonomy"] = (
                    goal.context.get("autonomy_override")
                    or self._effective_autonomy(msg.surface)
                )
                self._concierge_state.active_goals.append(goal)
                self._save_concierge_state(msg.external_id, self._concierge_state)
            elif goal.context.get("effective_autonomy") is None:
                goal.context["effective_autonomy"] = self._effective_autonomy(msg.surface)
            if action == "continue" and goal.context.get("build_session_id") and self.memory_kernel:
                build_mgr = BuildSessionManager(memory_kernel=self.memory_kernel)
                session = build_mgr.load(goal.context["build_session_id"])
                if session:
                    if goal.context.get("build_start_over"):
                        session.status = BuildSessionStatus.DRAFTING.value
                        session.iteration_count = 0
                        session.iterations.clear()
                        session.workflow_id = None
                        build_mgr.save(session)
                        goal.context.pop("build_start_over", None)
                        goal.workflow_id = None
                        goal.plan_result = None
                        goal.run_history.clear()
                        goal.iteration = 0
                        if session.user_feedback:
                            for feedback in session.user_feedback[-5:]:
                                self._append_build_feedback_context(goal, feedback)
                    elif session.status not in ("completed", "failed"):
                        reply = msg.text.strip()
                        if self._message_requests_skip_smoke(reply):
                            goal.context["skip_smoke"] = True
                        if reply and reply not in (
                            "yes", "y", "continue", "go", "go ahead", "do it", "fix it", "try again",
                            "looks good", "looks great", "perfect", "accept", "approved",
                            "run it", "run", "test it", "test",
                            "start over", "start again", "reset", "from scratch",
                            "don't test", "do not test", "skip smoke", "skip the smoke test",
                        ):
                            build_mgr.add_user_feedback(session, reply)
                            self._append_build_feedback_context(goal, reply)
                            build_mgr.save(session)
            if self._memory_context:
                current_context = (goal.context.get("memory_context") or "").strip()
                if current_context and self._memory_context not in current_context:
                    goal.context["memory_context"] = f"{self._memory_context}\n{current_context}"
                else:
                    goal.context["memory_context"] = self._memory_context
            reuse_choice = msg.metadata.get("reuse_choice")
            reuse_candidate = msg.metadata.get("reuse_candidate")
            if (
                self._is_workflow_build_goal(goal)
                and self.memory_kernel
                and not reuse_choice
                and action == "new"
            ):
                # 29-5 §6-2: use speculative reuse result from parallel prep if available
                if _speculative_reuse is not None:
                    decision, candidate = _speculative_reuse
                else:
                    decision, candidate = reuse_first_decision(self.memory_kernel, goal.description)
                logger.info(
                    "Reuse decision: %s for goal '%s'",
                    decision,
                    (goal.description or "")[:120],
                )
                if decision in ("reuse", "adapt") and candidate:
                    opts = [
                        f"Reuse: {candidate.content[:60]}...",
                        "Adapt",
                        "Start fresh",
                    ]
                    self.project_store.set_pending_action(
                        context.project.project_id,
                        PendingAction(
                            kind="clarify",
                            intent=IntentCategory.META_GOAL.value,
                            original_text=msg.text,
                            options=opts,
                            metadata={
                                "reuse_candidate": {
                                    "workflow_id": candidate.workflow_id,
                                    "content": candidate.content,
                                    "score": candidate.score,
                                    "success_rate": candidate.success_rate,
                                },
                            },
                        ),
                        msg.external_id,
                    )
                    self.project_store.append_turn(
                        context.project.project_id,
                        context.task.task_id,
                        TaskTurn(role="user", content=msg.text, intent=classification.intent.value),
                        msg.external_id,
                    )
                    proposal = f"I found a similar workflow. Want me to reuse it, adapt it, or start fresh? Reply 1, 2, or 3."
                    self._record_assistant_turn(context, msg, proposal)
                    yield self._complete_event(content=f"{format_prefix(context.project.label)} {proposal}")
                    return
            if reuse_choice:
                logger.info(
                    "Reuse choice applied: %s for goal '%s'",
                    reuse_choice,
                    (goal.description or "")[:120],
                )
            if reuse_choice == "reuse" and reuse_candidate:
                goal.context["reuse_workflow_id"] = reuse_candidate.get("workflow_id")
            if reuse_choice == "adapt" and reuse_candidate:
                goal.context["adapt_workflow_id"] = reuse_candidate.get("workflow_id")
                goal.context["memory_context"] = (
                    (goal.context.get("memory_context") or "")
                    + f"\n[Adapt from workflow: {reuse_candidate.get('content', '')[:200]}]"
                )
            if self._is_workflow_build_goal(goal) and self.memory_kernel:
                build_session = self._get_or_create_build_session(goal)
                if build_session is not None:
                    goal.context["build_session_id"] = build_session.id
            self.project_store.append_turn(
                context.project.project_id,
                context.task.task_id,
                TaskTurn(role="user", content=msg.text, intent=classification.intent.value),
                msg.external_id,
            )
            last_content = ""
            if goal.context.get("build_accept") and goal.context.get("build_session_id"):
                last_content = await self._handle_build_accept(goal, msg, context)
                if last_content:
                    yield self._complete_event(content=f"{format_prefix(context.project.label)} {last_content}")
                    self._record_assistant_turn(context, msg, last_content)
                    self._upsert_goal(goal)
                    self._save_concierge_state(msg.external_id, self._concierge_state)
                    self._finalize_task(
                        context,
                        msg,
                        classification.intent,
                        True,
                        task_status_override=self._task_status_for_goal(goal),
                    )
                    return
            if goal.context.get("build_run_test") and goal.context.get("build_session_id"):
                last_content = await self._handle_build_run_test(goal, msg, context)
                if last_content:
                    yield self._complete_event(content=f"{format_prefix(context.project.label)} {last_content}")
                    self._record_assistant_turn(context, msg, last_content)
                    self._upsert_goal(goal)
                    self._save_concierge_state(msg.external_id, self._concierge_state)
                    self._finalize_task(
                        context,
                        msg,
                        classification.intent,
                        True,
                        task_status_override=self._task_status_for_goal(goal),
                    )
                    return
            # ── UNDERSTAND phase: Guard 2 on goal orchestrator path ──
            if guards_enabled() and _entity_ctx is not None:
                try:
                    from .solver import ExecutionMode as _EM, SolverDecision as _SD
                    _intent_mode_map = {
                        IntentCategory.META_GOAL: _EM.META_DELEGATE,
                        IntentCategory.WORKFLOW_BUILD: _EM.WORKFLOW_BUILD,
                    }
                    _proxy = _SD(
                        user_goal=goal.description,
                        requested_deliverable="",
                        execution_mode=_intent_mode_map.get(classification.intent, _EM.DIRECT_ACTION),
                        assumptions=[],
                        confidence=0.8,
                    )
                    _g2_ctx = GuardContext(
                        message=msg, entity_ctx=_entity_ctx,
                        classification=classification, solver_decision=_proxy,
                    )
                    _g2_start = time.monotonic()
                    _g2 = guard_understanding(_g2_ctx)
                    await self._emit_telemetry_event(
                        "guard_check",
                        duration_ms=(time.monotonic() - _g2_start) * 1000,
                        guard_action=_g2.action,
                        success=_g2.passed,
                        metadata={"guard": "understanding_goal_orch", "notes": _g2.notes},
                    )
                    for _note in _g2.notes:
                        logger.info("Guard 2 (goal orch): %s", _note)
                    if not _g2.passed and _g2.action == "clarify" and _g2.clarification_question:
                        self.project_store.append_turn(
                            context.project.project_id,
                            context.task.task_id,
                            TaskTurn(role="user", content=msg.text, intent=classification.intent.value),
                            msg.external_id,
                        )
                        self._record_assistant_turn(context, msg, _g2.clarification_question)
                        yield self._complete_event(content=_g2.clarification_question)
                        return
                except Exception:
                    logger.debug("Guard 2 (goal orch) failed", exc_info=True)

            async for event in self._execute_goal(goal, msg, context):
                if getattr(event, "type", "") == "chat_complete":
                    last_content = getattr(event, "content", "") or ""
                yield event
            self._upsert_goal(goal)
            self._save_concierge_state(msg.external_id, self._concierge_state)
            self._record_assistant_turn(context, msg, last_content or "Goal executed.")
            self._store_memory_candidates(
                msg.text,
                last_content or "Goal executed.",
                goal_context={
                    "description": goal.description,
                    "error_history": goal.error_history,
                    "metadata": {"goal_id": goal.id},
                },
                project_id=context.project.project_id,
            )
            self._finalize_task(
                context,
                msg,
                classification.intent,
                True,
                task_status_override=self._task_status_for_goal(goal),
            )
            return

        # 31-14 §3-4: phase transition before handler dispatch
        _phase_evt = self._make_phase_event(msg.external_id, "execution", "Executing", classification.intent.value)
        if _phase_evt is not None:
            yield _phase_evt

        handler = self.handlers.get(classification.intent)
        result = await handler.handle(msg, context, classification)

        if result.clarification is not None:
            self.project_store.set_pending_action(
                context.project.project_id,
                PendingAction(
                    kind="clarify",
                    intent=classification.intent.value,
                    original_text=msg.text,
                    options=result.clarification.options or [],
                    metadata=dict(msg.metadata),
                ),
                msg.external_id,
            )
            self.project_store.append_turn(
                context.project.project_id,
                context.task.task_id,
                TaskTurn(role="user", content=msg.text, intent=classification.intent.value),
                msg.external_id,
            )
            self.project_store.append_turn(
                context.project.project_id,
                context.task.task_id,
                TaskTurn(role="assistant", content=result.clarification.question, intent=None),
                msg.external_id,
            )
            self.project_store.update_task_status(
                context.project.project_id,
                context.task.task_id,
                "paused",
                msg.external_id,
            )
            yield self._complete_event(content=result.clarification.question)
            return

        if result.project_update:
            linked_workflow_id = result.project_update.get("linked_workflow_id")
            if linked_workflow_id:
                self.project_store.link_workflow(context.project.project_id, linked_workflow_id, msg.external_id)
            linked_run_id = result.project_update.get("linked_run_id")
            if linked_run_id:
                self.project_store.link_run(context.project.project_id, linked_run_id, msg.external_id)
            linked_meta_session_id = result.project_update.get("linked_meta_session_id")
            if linked_meta_session_id:
                self.project_store.link_meta_session(context.project.project_id, linked_meta_session_id, msg.external_id)

        self.project_store.append_turn(
            context.project.project_id,
            context.task.task_id,
            TaskTurn(role="user", content=msg.text, intent=classification.intent.value),
            msg.external_id,
        )

        if result.events is not None:
            final_content = ""
            stream_channel_id: str | None = None
            saw_tool_call = False
            async for event in result.events:
                evt_type = getattr(event, "type", "")
                if evt_type == "chat_tool_call_start":
                    saw_tool_call = True
                if evt_type == "chat_complete":
                    raw_content = getattr(event, "content", "") or ""
                    final_content = self._check_unsourced_claims(raw_content, saw_tool_call)
                    final_content = await self._post_process_response(final_content, msg.text, msg=msg, entity_ctx=_entity_ctx)
                    stream_channel_id = getattr(event, "stream_channel_id", None)
                    if hasattr(event, "model_copy"):
                        event = event.model_copy(update={"content": final_content})
                yield event
            if final_content:
                self._record_assistant_turn(context, msg, final_content)
                self._store_memory_candidates(msg.text, final_content, None, project_id=context.project.project_id)
            self._finalize_task(
                context,
                msg,
                classification.intent,
                bool(final_content),
                task_status_override=(result.task_update or {}).get("status"),
            )
            if stream_channel_id and stream_channel_id.startswith("run-"):
                self.project_store.link_run(context.project.project_id, stream_channel_id[4:], msg.external_id)
            return

        content = result.content
        label_prefix = self._format_reply_label(context, msg)
        if label_prefix and content and not starts_with_prefix(content):
            content = f"{label_prefix} {content}"
        auto_note = str(msg.metadata.get("clarification_auto_note") or "").strip()
        if auto_note and content:
            content = f"{auto_note}\n\n{content}"
        had_tool_call = classification.intent not in (
            IntentCategory.CONVERSATION, IntentCategory.DIRECT_TASK,
        )
        content = self._check_unsourced_claims(content, had_tool_call)
        content = await self._post_process_response(content, msg.text, msg=msg, entity_ctx=_entity_ctx)
        if self.promoter and self.promoter.should_propose(context.project, context.task):
            proposal = self.promoter.build_proposal(context.project, context.task)
            content = f"{content}\n\nSave as reusable workflow? -> {proposal.save_command}"
        pref_prompt = self._maybe_surface_preferences(msg.external_id, activate=True)
        if pref_prompt:
            content = f"{content}\n\n{pref_prompt}" if content else pref_prompt
        self._record_assistant_turn(context, msg, content)
        self._store_memory_candidates(msg.text, content, None, project_id=context.project.project_id)
        self._finalize_task(
            context,
            msg,
            classification.intent,
            bool(content),
            task_status_override=(result.task_update or {}).get("status"),
        )
        yield self._complete_event(content=content, stream_channel_id=result.stream_channel_id)

    async def _solver_path(
        self,
        msg: SurfaceMessage,
        context: ResolvedContext,
        classification: ClassificationResult,
        *,
        entity_ctx: EntityContext | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        workflow_candidates = []
        experience_context_str = None
        if self.memory_index is not None:
            try:
                candidates = await self.memory_index.retrieve_candidates(msg.text, top_k=5)
                exp_ctx = await self.memory_index.retrieve_experience_context(msg.text, top_k=3)
                enriched = enrich_planning_context(candidates, exp_ctx)
                workflow_candidates = enriched.get("similar_workflows", [])
                parts = enriched.get("learned_principles", []) + enriched.get("related_runs", [])
                experience_context_str = "; ".join(parts) if parts else None
            except Exception:
                logger.debug("Memory retrieval failed", exc_info=True)

        _entity_ctx_dict = None
        if entity_ctx and entity_ctx.matched_projects:
            _entity_ctx_dict = {
                "matched_projects": [
                    {"label": p.label, "status": p.status, "tasks": len(p.tasks),
                     "active_tasks": sum(1 for t in p.tasks if t.status == "active")}
                    for p in entity_ctx.matched_projects[:5]
                ],
                "is_about_project": entity_ctx.is_about_project,
            }

        decision = await self.goal_resolver.resolve(
            msg, context, classification,
            workflow_candidates=workflow_candidates,
            experience_context=experience_context_str,
            entity_context=_entity_ctx_dict,
        )

        decision = self.plan_builder.build_plan(decision)

        # ── UNDERSTAND phase: Guard 2 — understanding coherence ──
        if guards_enabled() and entity_ctx is not None:
            try:
                _g2_ctx = GuardContext(
                    message=msg, entity_ctx=entity_ctx,
                    classification=classification, solver_decision=decision,
                )
                _g2_start = time.monotonic()
                _g2 = guard_understanding(_g2_ctx)
                await self._emit_telemetry_event(
                    "guard_check",
                    duration_ms=(time.monotonic() - _g2_start) * 1000,
                    guard_action=_g2.action,
                    success=_g2.passed,
                    metadata={"guard": "understanding", "notes": _g2.notes},
                )
                for _note in _g2.notes:
                    logger.info("Guard 2: %s", _note)
                if not _g2.passed and _g2.action == "clarify" and _g2.clarification_question:
                    decision.clarification_question = _g2.clarification_question
            except Exception:
                logger.debug("Guard 2 (understanding) failed", exc_info=True)

        # 31-14 §3-3: Plan disclosure for multi-step plans
        if len(decision.plan_steps) > 1:
            try:
                from .progress_ux import format_plan_disclosure
                _plan_session = self._progress_sessions.get(msg.external_id)
                _step_descs = [s.description for s in decision.plan_steps]
                if _plan_session is not None:
                    _plan_text = format_plan_disclosure(_step_descs)
                    _offer_review = len(decision.plan_steps) > 3
                    if _offer_review:
                        _plan_text += "\nReview before I start?"
                    yield ChatCompleteEvent(
                        message_id=uuid.uuid4().hex[:12],
                        content=_plan_text,
                        token_usage={},
                        context_window=0,
                        graph_revision="",
                        detected_mode="progress_ack",
                    )
            except Exception:
                logger.debug("Plan disclosure failed", exc_info=True)

        if decision.clarification_question:
            self.project_store.set_pending_action(
                context.project.project_id,
                PendingAction(
                    kind="clarify",
                    intent=classification.intent.value,
                    original_text=msg.text,
                    metadata={**msg.metadata, "solver_decision": True},
                ),
                msg.external_id,
            )
            self.project_store.append_turn(
                context.project.project_id,
                context.task.task_id,
                TaskTurn(role="user", content=msg.text, intent=classification.intent.value),
                msg.external_id,
            )
            self.project_store.append_turn(
                context.project.project_id,
                context.task.task_id,
                TaskTurn(role="assistant", content=decision.clarification_question),
                msg.external_id,
            )
            yield self._complete_event(content=decision.clarification_question)
            return

        # 31-14 §3-4: phase transition before capability dispatch
        _exec_label = getattr(decision, "handler_hint", None) or classification.intent.value
        _phase_evt = self._make_phase_event(msg.external_id, "execution", "Executing", _exec_label)
        if _phase_evt is not None:
            yield _phase_evt

        if self.execution_selector is not None:
            exec_result = await self.execution_selector.execute(
                decision, msg, context, classification,
            )
            content = exec_result.content
            handler_result = exec_result.handler_result
        else:
            handler = self.handlers.get(decision.handler_hint or classification.intent)
            handler_result = await handler.handle(msg, context, classification)
            content = handler_result.content
            exec_result = None

        self.project_store.append_turn(
            context.project.project_id,
            context.task.task_id,
            TaskTurn(role="user", content=msg.text, intent=classification.intent.value),
            msg.external_id,
        )

        if handler_result is not None and handler_result.project_update:
            linked_workflow_id = handler_result.project_update.get("linked_workflow_id")
            if linked_workflow_id:
                self.project_store.link_workflow(context.project.project_id, linked_workflow_id, msg.external_id)
            linked_run_id = handler_result.project_update.get("linked_run_id")
            if linked_run_id:
                self.project_store.link_run(context.project.project_id, linked_run_id, msg.external_id)
            linked_meta_session_id = handler_result.project_update.get("linked_meta_session_id")
            if linked_meta_session_id:
                self.project_store.link_meta_session(context.project.project_id, linked_meta_session_id, msg.external_id)

        if handler_result is not None and handler_result.events is not None:
            final_content = ""
            stream_channel_id = None
            saw_tool_call = False
            async for event in handler_result.events:
                evt_type = getattr(event, "type", "")
                if evt_type == "chat_tool_call_start":
                    saw_tool_call = True
                if evt_type == "chat_complete":
                    raw_content = getattr(event, "content", "") or ""
                    final_content = self._check_unsourced_claims(raw_content, saw_tool_call)
                    final_content = await self._post_process_response(final_content, msg.text, msg=msg, entity_ctx=entity_ctx)
                    stream_channel_id = getattr(event, "stream_channel_id", None)
                    if hasattr(event, "model_copy"):
                        event = event.model_copy(update={"content": final_content})
                yield event
            # 31-14 §3-5: Result checkpoint for large outputs (events path)
            if final_content:
                try:
                    from .progress_ux import (
                        format_result_checkpoint,
                        result_checkpoint_enabled,
                        should_checkpoint_result,
                    )
                    if result_checkpoint_enabled() and should_checkpoint_result(final_content, threshold=4000):
                        _rc_session = self._progress_sessions.get(msg.external_id)
                        if _rc_session is not None and getattr(_rc_session, "verbosity", "full") != "minimal":
                            _rc_opts = format_result_checkpoint(final_content, user_focus=msg.text[:100])
                            _rc_labels = " ".join(f"[{o.label}]" for o in _rc_opts.options)
                            yield ChatCompleteEvent(
                                message_id=uuid.uuid4().hex[:12],
                                content=f"{_rc_opts.summary}\n{_rc_labels}",
                                token_usage={},
                                context_window=0,
                                graph_revision="",
                                detected_mode="result_checkpoint",
                            )
                except Exception:
                    logger.debug("Result checkpoint (events path) failed", exc_info=True)
            if final_content:
                self._record_assistant_turn(context, msg, final_content)
                self._store_memory_candidates(msg.text, final_content, None, project_id=context.project.project_id)
            self._finalize_task(
                context, msg, classification.intent, bool(final_content),
                task_status_override=(handler_result.task_update or {}).get("status"),
            )
            if stream_channel_id and stream_channel_id.startswith("run-"):
                self.project_store.link_run(context.project.project_id, stream_channel_id[4:], msg.external_id)
            return

        auto_note = str(msg.metadata.get("clarification_auto_note") or "").strip()
        if auto_note and content:
            content = f"{auto_note}\n\n{content}"
        label_prefix = self._format_reply_label(context, msg)
        if label_prefix and content and not starts_with_prefix(content):
            content = f"{label_prefix} {content}"

        had_tool_call = (
            exec_result is not None
            and exec_result.metadata.get("execution_mode") not in ("conversation_synthesis",)
        ) or (
            handler_result is not None
            and handler_result.content
            and handler_result.content != content
        )
        content = self._check_unsourced_claims(content, had_tool_call)
        content = await self._post_process_response(content, msg.text, msg=msg, entity_ctx=entity_ctx)

        if self.promoter and decision.save_candidate:
            if self.promoter.should_propose(context.project, context.task):
                proposal = self.promoter.build_proposal(context.project, context.task)
                content = f"{content}\n\nSave as reusable workflow? -> {proposal.save_command}"

        # 31-14 §3-5: Result checkpoint for large outputs
        try:
            from .progress_ux import (
                format_result_checkpoint,
                result_checkpoint_enabled,
                should_checkpoint_result,
            )
            if result_checkpoint_enabled() and content and should_checkpoint_result(content, threshold=4000):
                _rc_session = self._progress_sessions.get(msg.external_id)
                if _rc_session is not None and getattr(_rc_session, "verbosity", "full") != "minimal":
                    _rc_opts = format_result_checkpoint(content, user_focus=msg.text[:100])
                    _rc_summary = _rc_opts.summary
                    _rc_labels = " ".join(f"[{o.label}]" for o in _rc_opts.options)
                    yield ChatCompleteEvent(
                        message_id=uuid.uuid4().hex[:12],
                        content=f"{_rc_summary}\n{_rc_labels}",
                        token_usage={},
                        context_window=0,
                        graph_revision="",
                        detected_mode="result_checkpoint",
                    )
        except Exception:
            logger.debug("Result checkpoint failed", exc_info=True)

        self._record_assistant_turn(context, msg, content)
        self._store_memory_candidates(msg.text, content, None, project_id=context.project.project_id)
        self._finalize_task(
            context, msg, classification.intent, bool(content),
        )
        yield self._complete_event(content=content)

    def _resolve_pending_follow_up(
        self,
        msg: SurfaceMessage,
    ) -> tuple[ChatCompleteEvent | None, ResolvedContext, ClassificationResult, SurfaceMessage] | None:
        pending_projects = self.project_store.list_pending_projects(msg.external_id)
        if not pending_projects:
            return None
        project = self._select_pending_project(msg, pending_projects)
        if isinstance(project, ChatCompleteEvent):
            return project, self._pending_context_fallback(msg.external_id, pending_projects), ClassificationResult(
                intent=IntentCategory.CONVERSATION,
                confidence=1.0,
                raw_text=msg.text,
            ), msg
        if project is None or project.pending_action is None:
            return None
        task = self.project_store.get_current_task(project.project_id, msg.external_id)
        if task is None:
            return None
        context = ResolvedContext(
            project=project,
            task=task,
            is_new_project=False,
            is_new_task=False,
            confidence=1.0,
        )
        pending = project.pending_action
        reply = msg.text.strip().lower()

        if pending.kind == "confirm":
            if reply in {"no", "n", "cancel", "stop"}:
                self.project_store.clear_pending_action(project.project_id, msg.external_id)
                self.project_store.update_task_status(project.project_id, task.task_id, "paused", msg.external_id)
                return self._complete_event(content=f"{format_prefix(project.label)} Cancelled."), context, ClassificationResult(
                    intent=IntentCategory(pending.intent),
                    confidence=1.0,
                    raw_text=pending.original_text,
                ), msg
            if reply in {"yes", "y", "ok", "okay", "do it", "go ahead", "sure"}:
                self.project_store.clear_pending_action(project.project_id, msg.external_id)
                replay_msg = msg.model_copy(
                    update={
                        "text": pending.original_text,
                        "metadata": {
                            **pending.metadata,
                            **msg.metadata,
                            "skip_confirm": True,
                        },
                    }
                )
                return None, context, ClassificationResult(
                    intent=IntentCategory(pending.intent),
                    confidence=1.0,
                    raw_text=pending.original_text,
                ), replay_msg
            return self._complete_event(content=f"{format_prefix(project.label)} Please answer yes or no."), context, ClassificationResult(
                intent=IntentCategory(pending.intent),
                confidence=1.0,
                raw_text=pending.original_text,
            ), msg

        if pending.kind == "clarify":
            if pending.metadata.get("solver_decision") and not pending.options:
                self.project_store.clear_pending_action(project.project_id, msg.external_id)
                replay_msg = msg.model_copy(
                    update={
                        "text": f"{pending.original_text}\n[User clarification: {msg.text}]",
                        "metadata": {
                            **pending.metadata,
                            **msg.metadata,
                            "clarification_answer": msg.text,
                        },
                    }
                )
                return None, context, ClassificationResult(
                    intent=IntentCategory(pending.intent),
                    confidence=1.0,
                    raw_text=pending.original_text,
                ), replay_msg
            if reply.isdigit():
                idx = int(reply) - 1
                if 0 <= idx < len(pending.options):
                    result = self._build_clarification_resume(project, msg, pending, idx, context)
                    return self._maybe_add_reuse_choice(result, pending, idx)
            reply_norm = reply.strip()
            reuse_idx = self._match_reuse_choice_reply(reply_norm, pending)
            if reuse_idx is not None:
                result = self._build_clarification_resume(project, msg, pending, reuse_idx, context)
                return self._maybe_add_reuse_choice(result, pending, reuse_idx)
            for idx, option in enumerate(pending.options):
                option_lower = option.lower()
                basename = option_lower.rsplit("/", 1)[-1]
                if reply_norm and (reply_norm in option_lower or reply_norm in basename):
                    result = self._build_clarification_resume(project, msg, pending, idx, context)
                    return self._maybe_add_reuse_choice(result, pending, idx)
            if pending.options:
                fallback_idx = 2 if "reuse_candidate" in pending.metadata and len(pending.options) >= 3 else 0
                replay = self._build_clarification_resume(project, msg, pending, fallback_idx, context)
                replay = self._maybe_add_reuse_choice(replay, pending, fallback_idx)
                replay_msg = replay[3].model_copy(
                    update={
                        "metadata": {
                            **replay[3].metadata,
                            "clarification_auto_note": (
                                f"Proceeding with {pending.options[fallback_idx]} after one clarification round."
                            ),
                        }
                    }
                )
                return replay[0], replay[1], replay[2], replay_msg
            return self._complete_event(content=f"{format_prefix(project.label)} I couldn't resolve that choice."), context, ClassificationResult(
                intent=IntentCategory(pending.intent),
                confidence=1.0,
                raw_text=pending.original_text,
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
            project=project,
            task=task,
            is_new_project=False,
            is_new_task=False,
            confidence=1.0,
        )

    @staticmethod
    def _check_unsourced_claims(content: str, had_tool_call: bool) -> str:
        """Append a disclaimer if the response contains numeric claims
        that were not sourced from a tool call (web_search, etc.)."""
        global _unsourced_claim_warnings
        if had_tool_call or not content:
            return content
        if not _NUMERIC_CLAIM_RE_LEGACY.search(content):
            return content
        _unsourced_claim_warnings += 1
        logger.debug(
            "Unsourced numeric claim detected (total warnings: %d)",
            _unsourced_claim_warnings,
        )
        return (
            f"{content}\n\n"
            "_Note: This response may contain data from my training "
            "rather than a live source. For current prices or stats, "
            "ask me to search the web._"
        )

    def _record_assistant_turn(self, context, msg: SurfaceMessage, content: str) -> None:
        self.project_store.append_turn(
            context.project.project_id,
            context.task.task_id,
            TaskTurn(role="assistant", content=content, intent=None),
            msg.external_id,
        )
        if self.conversation_memory is not None and content:
            summary = f"{context.project.label}: {content[:160]}"
            self.conversation_memory.add_summary(
                summary=summary,
                workflow_id=(context.project.linked_workflow_ids[-1] if context.project.linked_workflow_ids else ""),
                topic_tags=[context.project.label, context.task.label],
            )
        summary_text = self._build_project_summary(context, content)
        if summary_text:
            self.project_store.update_project_summary(
                context.project.project_id,
                summary_text,
                msg.external_id,
            )
        self._maybe_auto_summarize(context, msg, on_complete=False)

    def _build_project_summary(self, context, content: str) -> str:
        parts = [context.project.summary.strip(), f"Latest task {context.task.label}: {content[:200].strip()}"]
        summary = " ".join(part for part in parts if part).strip()
        return summary[:400]

    def _maybe_add_reuse_choice(
        self,
        result: tuple[None, ResolvedContext, ClassificationResult, SurfaceMessage],
        pending: PendingAction,
        idx: int,
    ) -> tuple[None, ResolvedContext, ClassificationResult, SurfaceMessage]:
        """Add reuse_choice to replay metadata when resolving a reuse clarification (29-4)."""
        if "reuse_candidate" not in pending.metadata:
            return result
        choice = ["reuse", "adapt", "generate"][min(idx, 2)]
        _, ctx, cls, replay = result
        replay = replay.model_copy(
            update={
                "metadata": {
                    **replay.metadata,
                    "reuse_choice": choice,
                    "reuse_candidate": pending.metadata.get("reuse_candidate"),
                }
            }
        )
        return None, ctx, cls, replay

    @staticmethod
    def _match_reuse_choice_reply(reply: str, pending: PendingAction) -> int | None:
        if "reuse_candidate" not in pending.metadata:
            return None
        normalized = reply.strip().lower()
        if not normalized:
            return None
        if "don't reuse" in normalized or "do not reuse" in normalized:
            return 2
        if normalized in {"reuse", "use it", "same one"} or "reuse" in normalized:
            return 0
        if normalized in {"adapt", "modify it"} or "adapt" in normalized:
            return 1
        if normalized in {
            "no",
            "n",
            "new",
            "start fresh",
            "fresh",
            "from scratch",
        }:
            return 2
        return None

    def _build_clarification_resume(
        self,
        project: Project,
        msg: SurfaceMessage,
        pending: PendingAction,
        idx: int,
        context: ResolvedContext,
    ) -> tuple[None, ResolvedContext, ClassificationResult, SurfaceMessage]:
        self.project_store.clear_pending_action(project.project_id, msg.external_id)
        replay_msg = msg.model_copy(
            update={
                "text": pending.original_text,
                "metadata": {
                    **pending.metadata,
                    **msg.metadata,
                    "selected_option": idx,
                    "selected_path": pending.options[idx],
                }
            }
        )
        return None, context, ClassificationResult(
            intent=IntentCategory(pending.intent),
            confidence=1.0,
            raw_text=pending.original_text,
        ), replay_msg

    def _finalize_task(
        self,
        context,
        msg: SurfaceMessage,
        intent: IntentCategory,
        has_content: bool,
        task_status_override: str | None = None,
    ) -> None:
        if not has_content:
            return
        status = task_status_override or (
            "completed" if intent in (IntentCategory.WORKFLOW_BUILD, IntentCategory.META_GOAL) else "paused"
        )
        self.project_store.update_task_status(
            context.project.project_id,
            context.task.task_id,
            status,
            msg.external_id,
        )

        if status in ("completed", "paused"):
            try:
                from .resume import (
                    ResumeProtocol,
                    auto_populate_task_state,
                    compact_task_history,
                    persist_task_state,
                )
                conversation_text = "\n".join(
                    t.content for t in context.task.turns if t.content
                )
                state = auto_populate_task_state(context.task, conversation_text)
                state["completed_steps"] = compact_task_history(state["completed_steps"])
                ResumeProtocol().update_task_state(
                    context.task,
                    completed_steps=state["completed_steps"],
                    pending_steps=state["pending_steps"],
                    blocker=state.get("current_blocker"),
                    artifacts=state.get("artifacts"),
                )
                persist_task_state(
                    self.project_store,
                    context.project.project_id,
                    context.task,
                )
            except Exception:
                logger.debug("Auto-populate task state failed", exc_info=True)

        if status == "completed":
            self._maybe_auto_summarize(context, msg, on_complete=True)

    def _find_project_with_workflow(self, surface_id: str) -> Project | None:
        for project in self.project_store.list_active(surface_id):
            if project.linked_workflow_ids:
                return project
        return None

    def _format_reply_label(
        self,
        context: ResolvedContext,
        msg: SurfaceMessage | None = None,
    ) -> str:
        surface_id = self._current_surface_id
        if surface_id is None:
            return ""
        active_projects = self.project_store.list_active(surface_id)
        active_tasks = [t for t in context.project.tasks if t.status == "active"]
        is_messaging = msg is not None and self._is_messaging_surface(msg)

        if len(active_tasks) > 1:
            return format_prefix(context.project.label, context.task.label, bot_name=self.bot_name)
        if len(active_projects) > 1 or is_messaging:
            return format_prefix(context.project.label, bot_name=self.bot_name)
        return ""

    def _handle_save_command(self, msg: SurfaceMessage) -> ChatCompleteEvent | None:
        text = msg.text.strip()
        if not text.lower().startswith("/save"):
            return None

        parts = text.split(maxsplit=1)
        name = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            return self._complete_event(content="Usage: /save <name>")

        project = self._find_project_with_workflow(msg.external_id)
        if project is None:
            return self._complete_event(content="No workflow linked to the current project.")

        workflow_id = project.linked_workflow_ids[-1]
        graph_store = getattr(self.capability_context, "graph_store", None)
        if graph_store is None:
            return self._complete_event(content="Graph store not available.")

        try:
            graph = graph_store.get_graph(workflow_id)
            if not isinstance(graph, dict):
                return self._complete_event(content=f"Could not load workflow '{workflow_id}'.")
            graph["name"] = name
            graph_store.save_graph(workflow_id, graph)
        except Exception:
            logger.debug("Failed to rename workflow %s", workflow_id, exc_info=True)
            return self._complete_event(content=f"Failed to save workflow as '{name}'.")

        experience_store = getattr(self.capability_context, "experience_store", None)
        if experience_store is not None:
            try:
                experience_store.record(
                    workflow_id=workflow_id,
                    name=name,
                    summary=project.summary or name,
                )
            except Exception:
                logger.debug("Failed to write experience for %s", workflow_id, exc_info=True)

        self.project_store.update_project_summary(
            project.project_id,
            f"Saved workflow as '{name}'.",
            msg.external_id,
        )
        task = self.project_store.get_current_task(project.project_id, msg.external_id)
        if task is not None:
            self.project_store.update_task_status(
                project.project_id,
                task.task_id,
                "completed",
                msg.external_id,
            )
        return self._complete_event(content=f"Saved workflow as '{name}'.")

    def _handle_build_command(self, msg: SurfaceMessage) -> ChatCompleteEvent | None:
        """Handle /build-status and /build-stop (29-3 §6-4, §6-5)."""
        text = msg.text.strip().lower()
        if not text.startswith("/build-"):
            return None
        if not self.memory_kernel:
            return self._complete_event(content="Build session commands require memory kernel.")

        build_mgr = BuildSessionManager(memory_kernel=self.memory_kernel)
        context = self.context_resolver.resolve(msg)
        candidates = [
            g for g in self._concierge_state.active_goals
            if g.status in ("active", "paused")
            and g.context.get("build_session_id")
            and g.context.get("project_id") == context.project.project_id
        ]
        if not candidates:
            fallback = [
                g for g in self._concierge_state.active_goals
                if g.status in ("active", "paused") and g.context.get("build_session_id")
            ]
            if len(fallback) == 1:
                candidates = fallback
        if not candidates:
            return self._complete_event(content="No build session found for the current surface.")
        goal = candidates[0]
        session_id = goal.context.get("build_session_id")
        if not session_id:
            return self._complete_event(content="Current goal has no build session.")

        session = build_mgr.load(session_id)
        if not session:
            return self._complete_event(content="Build session not found.")

        if text == "/build-stop":
            build_mgr.transition_to(session, "reviewing")
            build_mgr.save(session)
            goal.status = "paused"
            goal.paused_at_stage = "reviewing"
            goal.updated_at = time.time()
            self._upsert_goal(goal)
            self._save_concierge_state(msg.external_id, self._concierge_state)
            self.project_store.update_task_status(
                context.project.project_id,
                context.task.task_id,
                "paused",
                msg.external_id,
            )
            return self._complete_event(
                content=f"{format_bare_prefix()} Build stopped. Current draft saved. Say 'continue' to resume."
            )

        if text == "/build-status":
            lines = [
                f"**Build session** {session.id[:8]}",
                f"Status: {session.status}",
                f"Iteration: {session.iteration_count}/{session.max_iterations}",
                f"Workflow: {session.workflow_id or '—'}",
            ]
            if session.user_feedback:
                lines.append(f"Feedback: {len(session.user_feedback)} item(s)")
            if session.diagnosis_history:
                lines.append(f"Diagnoses: {len(session.diagnosis_history)}")
            return self._complete_event(content="\n".join(lines))

        return None

    def _handle_project_command(self, msg: SurfaceMessage) -> ChatCompleteEvent | None:
        """Handle /project list|info|set|memory|delete."""
        text = msg.text.strip()
        lower = text.lower()

        if not lower.startswith("/project"):
            return None

        args = text[len("/project"):].strip()
        args_lower = args.lower()

        if not args or args_lower == "list":
            return self._project_list_response(msg.external_id)
        if args_lower.startswith("info"):
            name = args[len("info"):].strip()
            return self._project_info_response(msg.external_id, name or None)
        if args_lower.startswith("set "):
            rest = args[len("set "):].strip()
            return self._project_set_response(msg.external_id, rest)
        if args_lower.startswith("memory"):
            name = args[len("memory"):].strip()
            return self._project_memory_response(msg.external_id, name or None)
        if args_lower.startswith("delete "):
            name = args[len("delete "):].strip()
            return self._project_delete_response(msg.external_id, name)

        return self._complete_event(
            content="Usage: /project [list|info [name]|set <key> <value>|memory [name]|delete <name>]",
        )

    def _project_list_response(self, surface_id: str) -> ChatCompleteEvent:
        all_projects = self.project_store.list_projects(surface_id)
        if not all_projects:
            return self._complete_event(content="No projects on this surface.")
        lines = [f"**Projects ({len(all_projects)}):**"]
        for p in all_projects:
            task_count = len(p.tasks)
            active_tasks = sum(1 for t in p.tasks if t.status == "active")
            status_icon = {"active": "+", "paused": "~", "completed": "x"}.get(p.status, "?")
            lines.append(
                f"  [{status_icon}] **{p.label}** — {task_count} tasks "
                f"({active_tasks} active), id: `{p.project_id}`"
            )
        return self._complete_event(content="\n".join(lines))

    def _project_info_response(
        self, surface_id: str, name: str | None,
    ) -> ChatCompleteEvent:
        project = self._resolve_project_by_name(surface_id, name)
        if project is None:
            if name:
                return self._complete_event(
                    content=f'Project "{name}" not found. Use `/project list` to see available projects.',
                )
            return self._complete_event(content="No active project. Use `/project list`.")

        lines = [f"**{project.label}** ({project.status})"]
        lines.append(f"ID: `{project.project_id}`")
        lines.append(f"Created: {project.created_at:%Y-%m-%d %H:%M}")
        lines.append(f"Updated: {project.updated_at:%Y-%m-%d %H:%M}")

        if project.summary:
            lines.append(f"Summary: {project.summary}")

        if project.tasks:
            lines.append(f"\n**Tasks ({len(project.tasks)}):**")
            for t in project.tasks:
                icon = {"active": "+", "paused": "~", "blocked": "!", "completed": "x"}.get(t.status, "?")
                turn_count = len(t.turns)
                lines.append(f"  [{icon}] {t.label} — {turn_count} turns")
                if t.current_blocker:
                    lines.append(f"      Blocker: {t.current_blocker}")

        if project.linked_workflow_ids:
            lines.append(f"\nLinked workflows: {', '.join(project.linked_workflow_ids[:5])}")
        if project.linked_run_ids:
            lines.append(f"Linked runs: {', '.join(project.linked_run_ids[:5])}")

        mem_count = self._count_project_memories(project.project_id)
        if mem_count > 0:
            lines.append(f"\nProject memories: {mem_count} items (use `/project memory` to view)")

        return self._complete_event(content="\n".join(lines))

    def _project_set_response(self, surface_id: str, rest: str) -> ChatCompleteEvent:
        parts = rest.split(None, 1)
        if len(parts) < 2:
            return self._complete_event(
                content="Usage: /project set <key> <value>\nExample: /project set data_path /Users/me/data",
            )
        key, value = parts[0], parts[1]

        project = self._resolve_project_by_name(surface_id, None)
        if project is None:
            return self._complete_event(content="No active project. Start a conversation first.")

        if not self.memory_kernel:
            return self._complete_event(content="Memory kernel not available.")

        self.memory_kernel.store_fact(
            content=f"{key}: {value}",
            project_id=project.project_id,
            tags=[key],
            importance=1.0,
        )
        return self._complete_event(
            content=f"Stored for **{project.label}**: `{key}` = `{value}`",
        )

    def _project_memory_response(
        self, surface_id: str, name: str | None,
    ) -> ChatCompleteEvent:
        project = self._resolve_project_by_name(surface_id, name)
        if project is None:
            if name:
                return self._complete_event(
                    content=f'Project "{name}" not found. Use `/project list`.',
                )
            return self._complete_event(content="No active project. Use `/project list`.")

        if not self.memory_kernel:
            return self._complete_event(content="Memory kernel not available.")

        from dan.engine.memory_kernel import MemoryScope

        items = []
        for mem_type_items in self.memory_kernel._type_index.values():
            for item_id in mem_type_items:
                item = self.memory_kernel._index.get(item_id)
                if item is None:
                    continue
                if (
                    item.scope == MemoryScope.PROJECT
                    and item.metadata.get("project_id") == project.project_id
                    and item.lifecycle.value != "archive"
                ):
                    items.append(item)

        if not items:
            return self._complete_event(
                content=f"No memories stored for **{project.label}**.\n"
                f"Use `/project set <key> <value>` to add project-specific facts.",
            )

        items.sort(key=lambda x: x.updated_at, reverse=True)
        lines = [f"**Memories for {project.label}** ({len(items)} items):"]
        for i, item in enumerate(items[:20], 1):
            tag = item.memory_type.value.upper()
            snippet = item.content[:100].replace("\n", " ")
            lines.append(f"  {i}. [{tag}] {snippet}")
        if len(items) > 20:
            lines.append(f"  ... and {len(items) - 20} more")
        return self._complete_event(content="\n".join(lines))

    def _project_delete_response(self, surface_id: str, name: str) -> ChatCompleteEvent:
        project = self._resolve_project_by_name(surface_id, name)
        if project is None:
            return self._complete_event(
                content=f'Project "{name}" not found. Use `/project list`.',
            )
        self.project_store.update_project_status(
            project.project_id, "completed", surface_id,
        )
        return self._complete_event(
            content=f"Project **{project.label}** marked as completed.",
        )

    def _resolve_project_by_name(
        self, surface_id: str, name: str | None,
    ) -> Project | None:
        projects = self.project_store.list_projects(surface_id)
        if not projects:
            return None
        if not name:
            active = [p for p in projects if p.status == "active"]
            return active[0] if active else projects[0]
        name_lower = name.lower().strip()
        for p in projects:
            if p.label.lower() == name_lower:
                return p
        for p in projects:
            if name_lower in p.label.lower():
                return p
        for p in projects:
            if p.project_id == name_lower:
                return p
        return None

    def _count_project_memories(self, project_id: str) -> int:
        if not self.memory_kernel:
            return 0
        from dan.engine.memory_kernel import MemoryScope

        count = 0
        for mem_type_items in self.memory_kernel._type_index.values():
            for item_id in mem_type_items:
                item = self.memory_kernel._index.get(item_id)
                if (
                    item is not None
                    and item.scope == MemoryScope.PROJECT
                    and item.metadata.get("project_id") == project_id
                    and item.lifecycle.value != "archive"
                ):
                    count += 1
        return count

    def _handle_memory_command(self, msg: SurfaceMessage) -> ChatCompleteEvent | None:
        """Handle /memory-stats, /memory-search, /memory-delete, /memory-forget, /memory-confirm, /memory-reject."""
        text = msg.text.strip()
        lower = text.lower()

        if lower == "/memory-stats":
            return self._memory_stats_response()
        if lower.startswith("/memory-search"):
            query = text[len("/memory-search"):].strip()
            if not query:
                return self._complete_event(content="Usage: /memory-search <query>")
            return self._memory_search_response(query)
            
        if lower.startswith("/memory-delete"):
            parts = text.split()
            if len(parts) < 2:
                return self._complete_event(content="Usage: /memory-delete <id> [--force]")
            item_id = parts[1]
            force = "--force" in parts
            return self._memory_delete_response(item_id, force)
            
        if lower.startswith("/memory-forget"):
            query = text[len("/memory-forget"):].strip()
            if not query:
                return self._complete_event(content="Usage: /memory-forget <query>")
            return self._memory_forget_response(query)
            
        if lower.startswith("/memory-confirm"):
            return self._memory_confirm_response(msg.external_id)

        if lower.startswith("/memory-reject"):
            parts = text.split()
            indices = [int(p) for p in parts[1:] if p.isdigit()]
            return self._memory_reject_response(msg.external_id, indices)
            
        return None

    def _memory_delete_response(self, item_id: str, force: bool) -> ChatCompleteEvent:
        if not self.memory_kernel:
            return self._complete_event(content="Memory kernel not available.")
        
        from dan.engine.memory_kernel import MemoryLifecycle
        
        item = self.memory_kernel.get(item_id)
        if not item:
            return self._complete_event(content=f"Item {item_id} not found.")
            
        if item.lifecycle == MemoryLifecycle.DURABLE and not force:
            return self._complete_event(
                content=f"Item {item_id} is DURABLE. Use `/memory-delete {item_id} --force` to delete it."
            )
            
        success = self.memory_kernel.delete(item_id, hard=True)
        if success:
            return self._complete_event(content=f"Deleted item {item_id}.")
        return self._complete_event(content=f"Failed to delete item {item_id}.")

    def _memory_forget_response(self, query: str) -> ChatCompleteEvent:
        if not self.memory_kernel:
            return self._complete_event(content="Memory kernel not available.")
            
        from dan.engine.memory_kernel import MemoryLifecycle
        
        scored = self.memory_kernel.retrieve(query, limit=10)
        if not scored:
            return self._complete_event(content="No matching memories found.")
            
        deleted_count = 0
        lines = [f'**Forgot memories matching "{query}":**']
        for si in scored:
            if si.item.lifecycle == MemoryLifecycle.DURABLE:
                continue
            success = self.memory_kernel.delete(si.item.id, hard=True)
            if success:
                deleted_count += 1
                snippet = si.item.content[:80].replace("\n", " ")
                lines.append(f"  - Deleted [{si.item.memory_type.value.upper()}] {snippet}")
                
        if deleted_count == 0:
            return self._complete_event(
                content="No matching ACTIVE/INFERRED memories found. (DURABLE items require explicit `/memory-delete <id> --force`)"
            )
            
        return self._complete_event(content="\n".join(lines))

    def _memory_confirm_response(self, surface_id: str) -> ChatCompleteEvent:
        result = self.handle_preference_confirmation(surface_id, "confirm")
        if not result:
            result = "No pending preferences to confirm."
        return self._complete_event(content=result)

    def _memory_reject_response(self, surface_id: str, indices: list[int]) -> ChatCompleteEvent:
        msg = "reject " + " ".join(str(i) for i in indices) if indices else "reject"
        result = self.handle_preference_confirmation(surface_id, msg)
        if not result:
            result = "No pending preferences to reject."
        return self._complete_event(content=result)

    def _memory_stats_response(self) -> ChatCompleteEvent:
        if not self.memory_kernel:
            return self._complete_event(content="Memory kernel not available.")
        stats = self.memory_kernel.stats()
        by_type = stats.get("by_type", {})
        total = stats.get("total", 0)
        storage = stats.get("storage_path", "~/.dan/memory_kernel")
        from dan.engine.memory_kernel import MemoryType

        type_order = [
            MemoryType.FACT, MemoryType.PREFERENCE, MemoryType.EPISODE,
            MemoryType.WORKFLOW_PATTERN, MemoryType.WORKFLOW_ASSET,
            MemoryType.FAILURE_PATTERN, MemoryType.PRINCIPLE,
            MemoryType.WORKING_STATE,
        ]
        lines = ["**Memory Kernel Stats:**"]
        for mt in type_order:
            count = by_type.get(mt.value, 0)
            lines.append(f"  {mt.value.upper()}: {count} items")
        lines.append(f"  **Total: {total} items** | Storage: {storage}")
        return self._complete_event(content="\n".join(lines))

    def _memory_search_response(self, query: str) -> ChatCompleteEvent:
        if not self.memory_kernel:
            return self._complete_event(content="Memory kernel not available.")
        scored = self.memory_kernel.retrieve(query, limit=10)
        if not scored:
            return self._complete_event(
                content=f'No memory items found for "{query}". The memory may be empty or the query too specific.'
            )
        lines = [f'**Memory search for "{query}":**']
        for i, si in enumerate(scored, 1):
            tag = si.item.memory_type.value.upper()
            snippet = si.item.content[:80].replace("\n", " ")
            lines.append(f"  {i}. [{tag}] {snippet} (score: {si.score:.2f})")
        return self._complete_event(content="\n".join(lines))

    # ------------------------------------------------------------------
    # /mcp commands (MCP bridge management)
    # ------------------------------------------------------------------

    async def _handle_mcp_command(self, msg: SurfaceMessage) -> ChatCompleteEvent | None:
        text = msg.text.strip()
        lower = text.lower()
        if not lower.startswith("/mcp"):
            return None

        parts = text[4:].strip().split(None, 1)
        subcmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if subcmd == "list":
            return self._mcp_list()
        if subcmd == "install" and arg:
            return await self._mcp_install(arg)
        if subcmd == "remove" and arg:
            return await self._mcp_remove(arg)
        if subcmd == "tools":
            return self._mcp_tools(arg if arg else None)

        return self._complete_event(
            content="Usage: /mcp list | /mcp install <name> | /mcp remove <name> | /mcp tools [name]"
        )

    def _mcp_list(self) -> ChatCompleteEvent:
        if self.mcp_bridge is None:
            return self._complete_event(content="MCP bridge not available.")
        from dan.mcp_bridge import load_mcp_config

        connected = self.mcp_bridge.list_servers()
        config = load_mcp_config()
        all_names = sorted(set(list(connected.keys()) + list(config.servers.keys())))

        if not all_names:
            return self._complete_event(
                content="No MCP servers configured. Use `/mcp install <name>` to add one."
            )

        lines = ["**MCP servers:**"]
        for name in all_names:
            status = connected.get(name)
            if status and status.connected:
                lines.append(
                    f"- \U0001f7e2 **{name}** \u2014 {len(status.tool_names)} tools \u2014 {status.description}"
                )
                if status.last_error:
                    lines.append(f"  Last error: {status.last_error}")
            else:
                cfg = config.servers.get(name)
                desc = cfg.description or cfg.command if cfg else "unknown"
                lines.append(f"- \U0001f534 **{name}** \u2014 not connected \u2014 {desc}")
        return self._complete_event(content="\n".join(lines))

    async def _mcp_install(self, name: str) -> ChatCompleteEvent:
        if self.mcp_bridge is None:
            return self._complete_event(content="MCP bridge not available.")

        from dan.mcp_bridge import (
            MCPServerConfig,
            install_mcp_package,
            load_mcp_config,
            register_mcp_tools,
            resolve_known_server,
            save_mcp_config,
        )

        known = resolve_known_server(name)
        pip_pkg = known["pip"] if known else name
        command = known.get("command", name) if known else name
        description = known.get("description", "") if known else ""

        try:
            success, output = await install_mcp_package(pip_pkg)
        except Exception as exc:
            return self._complete_event(
                content=f"Cannot install `{pip_pkg}`: {exc}\n"
                f"Install manually: `pip install {pip_pkg}`"
            )

        if not success:
            return self._complete_event(
                content=f"Failed to install `{pip_pkg}`:\n```\n{output[:500]}\n```"
            )

        config = MCPServerConfig(command=command, description=description)
        mcp_config = load_mcp_config()
        mcp_config.servers[name] = config
        save_mcp_config(mcp_config)

        try:
            tools = await self.mcp_bridge.connect(name, config)
        except Exception as exc:
            return self._complete_event(
                content=f"Installed `{pip_pkg}` but failed to connect: {exc}\n"
                f"Server added to config \u2014 try `/mcp list` or restart."
            )

        if self._capability_registry is not None:
            register_mcp_tools(
                self._capability_registry, self._tool_registry, self.mcp_bridge, name
            )

        tool_names = [t["name"] for t in tools]
        return self._complete_event(
            content=f"Installed and connected **{name}** ({len(tools)} tools):\n"
            + "\n".join(f"- `{t}`" for t in tool_names)
        )

    async def _mcp_remove(self, name: str) -> ChatCompleteEvent:
        if self.mcp_bridge is None:
            return self._complete_event(content="MCP bridge not available.")

        from dan.mcp_bridge import load_mcp_config, save_mcp_config, unregister_mcp_tools

        if self._capability_registry is not None:
            unregister_mcp_tools(self._capability_registry, self._tool_registry, name)

        try:
            await self.mcp_bridge.disconnect(name)
        except Exception:
            pass

        config = load_mcp_config()
        removed = config.servers.pop(name, None)
        save_mcp_config(config)

        if removed:
            return self._complete_event(content=f"Removed MCP server **{name}**.")
        return self._complete_event(content=f"MCP server **{name}** was not configured.")

    def _mcp_tools(self, name: str | None) -> ChatCompleteEvent:
        if self.mcp_bridge is None:
            return self._complete_event(content="MCP bridge not available.")

        if name:
            tools = self.mcp_bridge.get_server_tools(name)
            if not tools:
                return self._complete_event(
                    content=f"No tools found for server **{name}** (not connected?)."
                )
            lines = [f"**{name}** tools:"]
            for t in tools:
                lines.append(f"- `{t['name']}` \u2014 {t.get('description', '')}")
            return self._complete_event(content="\n".join(lines))

        all_servers = self.mcp_bridge.list_servers()
        if not all_servers:
            return self._complete_event(content="No MCP servers connected.")
        lines: list[str] = []
        for srv_name, status in all_servers.items():
            tools = self.mcp_bridge.get_server_tools(srv_name)
            lines.append(f"**{srv_name}:**")
            for t in tools:
                lines.append(f"- `{t['name']}` \u2014 {t.get('description', '')}")
        return self._complete_event(content="\n".join(lines))

    def _maybe_auto_summarize(
        self,
        context: ResolvedContext,
        msg: SurfaceMessage,
        *,
        on_complete: bool = False,
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
        summary = summary[:400]

        try:
            self.project_store.update_project_summary(
                context.project.project_id,
                summary,
                msg.external_id,
            )
        except Exception:
            logger.debug("Auto-summarize failed for project %s", context.project.project_id, exc_info=True)

    @staticmethod
    def _should_skip_completion_guard(msg: SurfaceMessage) -> bool:
        """Return True when the completion guard should be bypassed.

        Skips for: slash commands, simple greetings, follow-up answers to DAN's
        own questions, short confirmations, and numeric option selections.
        """
        meta = msg.metadata
        if meta.get("clarification_answer") or meta.get("clarification_auto_note"):
            return True
        if meta.get("resume_context") or meta.get("handoff_context"):
            return True

        text = msg.text.strip()
        if not text:
            return True

        if text.startswith("/"):
            return True

        if text.isdigit():
            return True

        cleaned = re.sub(r"[!?.,:;]+$", "", text.lower()).strip()

        if cleaned in _FOLLOW_UP_PHRASES:
            return True

        words = cleaned.split()
        if len(words) <= 3:
            if all(w in _GREETING_TOKENS for w in words):
                return True
            if len(cleaned) <= 4:
                return True

        return False

    async def _post_process_response(
        self,
        content: str,
        user_text: str,
        *,
        msg: SurfaceMessage | None = None,
        entity_ctx: EntityContext | None = None,
    ) -> str:
        """ResponsePostProcessor pipeline: Guard 3 (relevance) + completion guard.

        PII detokenization is handled at the provider boundary via
        ``TokenizingProviderWrapper`` (31-10), not in this method.
        """
        if not content:
            return content

        if len(content) < _COMPLETION_GUARD_MIN_RESPONSE_LEN:
            return content

        # ── VERIFY phase: Guard 3 — response relevance ──
        if guards_enabled() and entity_ctx is not None and msg is not None:
            try:
                _g3_ctx = GuardContext(
                    message=msg, entity_ctx=entity_ctx,
                    response_content=content,
                )
                _g3_start = time.monotonic()
                _g3 = guard_response_relevance(_g3_ctx)
                await self._emit_telemetry_event(
                    "guard_check",
                    duration_ms=(time.monotonic() - _g3_start) * 1000,
                    guard_action=_g3.action,
                    success=_g3.passed,
                    metadata={"guard": "response_relevance", "notes": _g3.notes},
                )
                for _note in _g3.notes:
                    logger.info("Guard 3: %s", _note)
                if not _g3.passed and _g3.short_circuit_response:
                    content = f"{content.rstrip()}\n\n{_g3.short_circuit_response}"
            except Exception:
                logger.debug("Guard 3 (relevance) failed", exc_info=True)

        # 31-9 §4-2: skip completion guard for greetings, follow-ups, confirmations
        skip_completion = msg is not None and self._should_skip_completion_guard(msg)

        if not skip_completion:
            try:
                from .completion_guard import run_completion_check
                augmented, follow_up, report = await run_completion_check(user_text, content)
                if report and not report.all_met:
                    content = augmented
                    if follow_up:
                        content = f"{content.rstrip()}\n\n{follow_up}"
            except Exception:
                self._completion_guard_failures = getattr(self, "_completion_guard_failures", 0) + 1
                if self._completion_guard_failures == 1:
                    logger.warning("Completion guard check failed (first occurrence)", exc_info=True)
                else:
                    logger.debug("Completion guard check failed (occurrence %d)", self._completion_guard_failures)

        return content

    def _complete_event(self, *, content: str, stream_channel_id: str | None = None) -> ChatCompleteEvent:
        return ChatCompleteEvent(
            message_id=uuid.uuid4().hex[:12],
            content=content,
            token_usage={},
            context_window=0,
            graph_revision="",
            stream_channel_id=stream_channel_id,
        )

    def _load_concierge_state(self, surface_id: str) -> ConciergeState:
        """Load concierge working state from memory kernel (scope=USER)."""
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
        """Persist concierge working state to memory kernel as WORKING_STATE."""
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

    def _retrieve_memory_context(
        self,
        message: str,
        *,
        has_active_build: bool = False,
        project_id: str | None = None,
    ) -> str:
        """Retrieve relevant memory for this message (task-type-aware). Used for goal detection and planning."""
        if not self.memory_kernel:
            return ""
        try:
            from dan.engine.memory_kernel import classify_task_type

            scored = self.memory_kernel.retrieve_by_task(
                message,
                task_type=classify_task_type(message, has_active_build=has_active_build),
                limit=10,
                project_id=project_id,
            )
            if not scored:
                return ""
            lines = ["Relevant context from memory:"]
            for si in scored[:8]:
                tag = si.item.memory_type.value.upper()
                scope_hint = ""
                if si.item.scope.value == "project":
                    scope_hint = " (project)"
                lines.append(f"- [{tag}{scope_hint}] {si.item.content[:200]}")
            block = "\n".join(lines)
            return block[:800].rstrip() + ("..." if len(block) > 800 else "")
        except Exception:
            logger.debug("Memory retrieval failed in concierge", exc_info=True)
            return ""

    def _extract_memory_candidates(
        self,
        message: str,
        response: str,
        goal_context: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Extract memory items to store after an interaction (facts, episode, failure pattern)."""
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
    ) -> None:
        """Schedule concurrent memory extraction as a fire-and-forget background task.

        Three independent extraction steps (episode/failure, preferences,
        heuristic facts) are fanned out concurrently so they never block the
        response path.
        """
        if not self.memory_kernel:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._store_memory_candidates_sync(message, response, goal_context, project_id)
            return
        task = loop.create_task(
            self._store_memory_candidates_async(message, response, goal_context, project_id),
        )
        self._bg_memory_tasks.add(task)
        task.add_done_callback(self._bg_memory_tasks.discard)

    def _store_memory_candidates_sync(
        self,
        message: str,
        response: str,
        goal_context: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> None:
        """Fallback synchronous path when no event loop is running."""
        self._store_episode_candidates(message, response, goal_context)
        self._try_extract_preferences(message, response, project_id=project_id)
        self._try_memory_extraction(message, response, goal_context, project_id=project_id)

    async def _store_memory_candidates_async(
        self,
        message: str,
        response: str,
        goal_context: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> None:
        """Fan out episode, preference, and LLM-backed memory extraction concurrently."""
        from .fan_out import fan_out_dict

        tasks = {
            "episode": lambda: asyncio.to_thread(
                self._store_episode_candidates, message, response, goal_context,
            ),
            "preferences": lambda: asyncio.to_thread(
                self._try_extract_preferences, message, response, project_id=project_id,
            ),
            "memory_extraction": lambda: asyncio.to_thread(
                self._try_memory_extraction, message, response, goal_context, project_id=project_id,
            ),
        }
        results = await fan_out_dict(tasks)
        for name, result in results.items():
            if isinstance(result, Exception):
                logger.debug("Memory extraction step '%s' failed", name, exc_info=result)

    def _store_episode_candidates(
        self,
        message: str,
        response: str,
        goal_context: dict[str, Any] | None = None,
    ) -> None:
        """Extract and store episode/failure memory candidates."""
        candidates = self._extract_memory_candidates(message, response, goal_context)
        for item in candidates:
            try:
                self.memory_kernel.store(item)
            except Exception:
                logger.debug("Failed to store memory candidate", exc_info=True)

    def _try_extract_preferences(
        self,
        user_message: str,
        assistant_message: str,
        project_id: str | None = None,
    ) -> None:
        """Run preference extraction on every interaction, store results in memory kernel."""
        if self.memory_kernel is None:
            return
        if not os.environ.get("DAN_PREFERENCE_EXTRACTION", "1").strip() == "1":
            return
        try:
            from dan.engine.preference_extractor import PreferenceExtractor

            extractor = PreferenceExtractor()
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
                        self.memory_kernel.store_preference(
                            content=f"{key}: {entry}",
                            tags=[key],
                            project_id=project_id,
                        )
                elif isinstance(value, str) and value:
                    self.memory_kernel.store_preference(
                        content=f"{key}: {value}",
                        tags=[key],
                        project_id=project_id,
                    )
        except Exception:
            logger.debug("Preference extraction failed", exc_info=True)

    def _maybe_surface_preferences(self, surface_id: str, *, activate: bool = False) -> str:
        """Check if accumulated preferences should be surfaced for confirmation.

        Returns a suggestion message or empty string.  Non-blocking — simply
        stores candidates in ``_pending_preference_surface`` for the next response
        only when ``activate=True`` and the prompt is actually being surfaced.
        """
        if self.memory_kernel is None:
            return ""
        self._interaction_counter += 1
        try:
            candidates = self.memory_kernel.maybe_surface_preferences(
                session_count=self._interaction_counter,
            )
            if not candidates:
                return ""
            if activate:
                self._pending_preference_surface[surface_id] = candidates
            lines = ["I've noticed these preferences from our interactions:"]
            for i, item in enumerate(candidates, 1):
                lines.append(f"  {i}. {item.content}")
            lines.append(
                "Would you like to confirm any of these? "
                "(Reply 'confirm all' or 'reject N' where N is the number)"
            )
            return "\n".join(lines)
        except Exception:
            logger.debug("Preference surfacing check failed", exc_info=True)
            return ""

    def handle_preference_confirmation(self, surface_id: str, message: str) -> str:
        """Process user replies to preference surfacing suggestions.

        Returns a response message or empty string if the message isn't a
        preference confirmation/rejection.
        """
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

    def _try_memory_extraction(
        self,
        user_message: str,
        assistant_message: str,
        goal_context: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> None:
        """Run LLM-backed memory extraction with heuristic fallback."""
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
            for candidate in candidates:
                if candidate.memory_type == "fact":
                    self.memory_kernel.store_fact(
                        candidate.content, tags=candidate.tags,
                        project_id=project_id,
                    )
                elif candidate.memory_type == "preference":
                    self.memory_kernel.store_preference(
                        candidate.content, tags=candidate.tags,
                        project_id=project_id,
                    )
        except Exception:
            logger.debug("Memory extraction failed", exc_info=True)

    def _is_workflow_build_goal(self, goal: ConciergeGoal) -> bool:
        """True if goal involves building a workflow (29-3 §3-1)."""
        lower = (goal.description or "").lower()
        build_verbs = ("build", "create", "make", "automate")
        return any(kw in lower for kw in build_verbs)

    def _should_use_goal_orchestrator(self, classification: ClassificationResult) -> bool:
        """Route workflow/meta goals through the Phase 29 path only when fully wired."""
        if self.memory_kernel is None or self.meta_controller is None:
            return False
        return classification.intent in (
            IntentCategory.META_GOAL,
            IntentCategory.WORKFLOW_BUILD,
        )

    def _should_resume_goal_follow_up(
        self,
        message: str,
        classification: ClassificationResult,
        project_id: str | None = None,
    ) -> bool:
        if self.memory_kernel is None or self.meta_controller is None:
            return False
        goal = next(
            (
                g for g in self._concierge_state.active_goals
                if g.status in ("active", "paused")
                and (project_id is None or g.context.get("project_id") == project_id)
            ),
            None,
        )
        if goal is None:
            candidate_goals = [
                g for g in self._concierge_state.active_goals
                if g.status in ("active", "paused") and g.context.get("build_session_id")
            ]
            if len(candidate_goals) == 1:
                goal = candidate_goals[0]
        if goal is None:
            return False
        if classification.intent not in (
            IntentCategory.CONVERSATION,
            IntentCategory.RUN_CONTROL,
            IntentCategory.STATUS_CHECK,
        ):
            return False
        lower = message.strip().lower()
        if goal.context.get("build_session_id"):
            if self._looks_like_build_follow_up(lower):
                return True
        if goal.status == "paused" and lower in {"continue", "resume", "go ahead", "yes", "y"}:
            return True
        return False

    def _get_or_create_build_session(self, goal: ConciergeGoal):
        """Reuse the persisted build session across follow-up turns."""
        if self.memory_kernel is None:
            return None
        build_mgr = BuildSessionManager(memory_kernel=self.memory_kernel)
        session_id = goal.context.get("build_session_id")
        if session_id:
            session = build_mgr.load(session_id)
            if session is not None:
                if goal.description and not session.goal_description:
                    session.goal_description = goal.description
                    build_mgr.save(session)
                return session
        session = build_mgr.create(
            goal.id,
            max_iterations=goal.max_iterations,
            goal_description=goal.description,
        )
        build_mgr.save(session)
        goal.context["build_session_id"] = session.id
        return session

    @staticmethod
    def _message_requests_skip_smoke(message: str) -> bool:
        lower = message.strip().lower()
        return any(
            phrase in lower for phrase in (
                "don't test",
                "do not test",
                "skip smoke",
                "skip the smoke test",
                "don't run a smoke test",
            )
        )

    @staticmethod
    def _looks_like_build_follow_up(message: str) -> bool:
        lower = message.strip().lower()
        if lower in {
            "continue",
            "resume",
            "go",
            "go ahead",
            "do it",
            "fix it",
            "try again",
            "looks good",
            "looks great",
            "perfect",
            "accept",
            "approved",
            "run it",
            "run",
            "test it",
            "test",
            "start over",
            "start again",
            "reset",
            "from scratch",
            "pause",
            "stop",
            "hold on",
            "wait",
            "hold up",
        }:
            return True
        if Concierge._message_requests_skip_smoke(lower):
            return True
        return lower.startswith((
            "actually",
            "instead",
            "use ",
            "change ",
            "set ",
            "add ",
            "remove ",
        ))

    @staticmethod
    def _append_build_feedback_context(goal: ConciergeGoal, feedback: str) -> None:
        feedback = feedback.strip()
        if not feedback:
            return
        block = f"[Build feedback]\n- {feedback}"
        current = (goal.context.get("memory_context") or "").strip()
        if feedback in current:
            return
        goal.context["memory_context"] = f"{current}\n{block}".strip() if current else block

    def _upsert_goal(self, goal: ConciergeGoal) -> None:
        idx = next((i for i, g in enumerate(self._concierge_state.active_goals) if g.id == goal.id), None)
        if idx is None:
            self._concierge_state.active_goals.append(goal)
        else:
            self._concierge_state.active_goals[idx] = goal

    @staticmethod
    def _task_status_for_goal(goal: ConciergeGoal) -> str:
        if goal.status == "completed":
            return "completed"
        if goal.status == "paused":
            return "paused"
        return "active"

    def _detect_goal(
        self,
        message: str,
        memory_context: str,
        classification: ClassificationResult,
    ) -> ConciergeGoal | None:
        """Distinguish goal-bearing messages from simple questions/commands. Returns a new goal if goal-bearing."""
        if classification.intent == IntentCategory.META_GOAL:
            return ConciergeGoal(description=message.strip(), status="active")
        if classification.intent == IntentCategory.WORKFLOW_BUILD:
            lower = message.lower()
            if any(kw in lower for kw in ("build", "create", "make", "automate", "workflow", "pipeline")):
                return ConciergeGoal(description=message.strip(), status="active")
        return None

    def _continue_or_new(
        self,
        message: str,
        active_goals: list[ConciergeGoal],
        project_id: str | None = None,
    ) -> tuple[ConciergeGoal, Literal["continue", "new", "pause"]]:
        """If message relates to an active goal, return (goal, 'continue'); if explicit pause, (goal, 'pause'); else (new_goal, 'new')."""
        relevant_goals = [
            g for g in active_goals
            if project_id is None or g.context.get("project_id") == project_id
        ]
        if not relevant_goals:
            build_goals = [
                g for g in active_goals
                if g.status in ("active", "paused") and g.context.get("build_session_id")
            ]
            if len(build_goals) == 1:
                relevant_goals = build_goals
        active = [g for g in relevant_goals if g.status == "active"]
        paused = [g for g in relevant_goals if g.status == "paused"]
        reply = message.strip().lower()
        if not active:
            if paused:
                paused_goal = paused[0]
                if reply in {"continue", "resume", "go ahead", "yes", "y"} or (
                    paused_goal.context.get("build_session_id")
                    and self._looks_like_build_follow_up(reply)
                ):
                    paused_goal.status = "active"
                    paused_goal.updated_at = time.time()
                    return paused_goal, "continue"
            return ConciergeGoal(description=message.strip(), status="active"), "new"
        if reply in ("pause", "stop", "hold on", "wait", "hold up"):
            return active[0], "pause"
        if reply in ("start over", "start again", "reset", "from scratch"):
            goal = active[0]
            goal.context["build_start_over"] = True
            return goal, "continue"
        if reply in ("looks good", "looks great", "perfect", "accept", "approved"):
            goal = active[0]
            goal.context["build_accept"] = True
            return goal, "continue"
        if reply in ("run it", "run", "test it", "test"):
            goal = active[0]
            goal.context["build_run_test"] = True
            return goal, "continue"
        if reply in ("yes", "y", "continue", "go", "go ahead", "do it", "fix it", "try again"):
            return active[0], "continue"
        if reply in ("no", "n", "cancel"):
            return active[0], "continue"
        if active[0].context.get("build_session_id"):
            if reply.startswith(("build ", "create ", "make ", "automate ")):
                return ConciergeGoal(description=message.strip(), status="active"), "new"
            return active[0], "continue"
        if any(w in reply for w in ("same", "that one", "this one", "it ", "the workflow")):
            return active[0], "continue"
        return ConciergeGoal(description=message.strip(), status="active"), "new"

    def _pause_active_goals(self, surface_id: str) -> None:
        """Mark all active goals as paused and persist state."""
        for goal in self._concierge_state.active_goals:
            if goal.status == "active":
                goal.status = "paused"
                goal.updated_at = time.time()
        self._save_concierge_state(surface_id, self._concierge_state)

    def _effective_autonomy(self, surface: str) -> str:
        """Resolve autonomy level: surface-specific default or config (29-2 §4-3)."""
        key = (surface or "").strip().lower()
        if key in _SURFACE_AUTONOMY_DEFAULTS:
            return _SURFACE_AUTONOMY_DEFAULTS[key]
        return self._autonomy_level

    @staticmethod
    def _autonomy_override_from_message(message: str) -> str | None:
        """Detect per-goal override from message (29-2 §4-4): 'just do it' → autonomous, 'show me every step' → interactive."""
        lower = message.strip().lower()
        for phrases, level in _AUTONOMY_OVERRIDE_PHRASES:
            if any(p in lower for p in phrases):
                return level
        return None

    async def _execute_goal(
        self,
        goal: ConciergeGoal,
        msg: SurfaceMessage,
        context: ResolvedContext,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Run goal through plan → execute → diagnose → repair (delegates to MetaController when available)."""
        reuse_wf_id = goal.context.get("reuse_workflow_id")
        if reuse_wf_id:
            content = await self._handle_reuse_workflow(goal, reuse_wf_id, context)
            if content:
                yield self._complete_event(content=f"{format_prefix(context.project.label)} {content}")
                return
        if self.meta_controller is None:
            yield self._complete_event(
                content=f"{format_prefix(context.project.label)} Meta controller not available; goal not executed.",
            )
            return
        from dan.meta.controller import MetaControllerConfig, MetaSession, MetaSessionStatus

        # 29-2 §3-2: Planning decision — query memory before MetaController
        if self.memory_kernel and not goal.context.get("plan_action"):
            goal.context["plan_action"] = self._decide_plan_action(goal)
            logger.info("Goal %s plan_action=%s", goal.id[:8], goal.context["plan_action"])

        goal_str = goal.description
        mem_ctx = goal.context.get("memory_context")
        if mem_ctx and isinstance(mem_ctx, str):
            goal_str = f"{goal_str}\n\n[Relevant context from memory]\n{mem_ctx}"
        session = MetaSession(
            session_id=goal.id,
            goal=goal_str,
            status=MetaSessionStatus.PLANNING,
            max_iterations=goal.max_iterations,
            plan_result=goal.plan_result,
            workflow_id=goal.workflow_id,
            run_history=list(goal.run_history),
            iteration=goal.iteration,
            goal_context=dict(goal.context),
        )
        cfg = MetaControllerConfig(max_iterations=goal.max_iterations)
        try:
            session = await self.meta_controller.run_session(session, cfg)
        except Exception as exc:
            logger.exception("MetaController run_session failed for goal %s", goal.id)
            goal.status = "failed"
            goal.error_history.append(str(exc))
            goal.context["last_run_status"] = "failed"
            if self.memory_kernel:
                self.memory_kernel.store_failure_pattern(
                    f"Goal failed: {goal.description[:200]}. Error: {exc!s}",
                    metadata={"goal_id": goal.id},
                )
            self._sync_goal_from_session(goal, session)
            idx = next((i for i, g in enumerate(self._concierge_state.active_goals) if g.id == goal.id), None)
            if idx is not None:
                self._concierge_state.active_goals[idx] = goal
            self._save_concierge_state(msg.external_id, self._concierge_state)
            yield self._complete_event(
                content=f"{format_prefix(context.project.label)} Goal failed: {exc!s}",
            )
            return

        self._sync_goal_from_session(goal, session)

        # 29-2 §3-3: Track execution outcome
        goal.context["last_run_status"] = session.status.value

        if session.status == MetaSessionStatus.COMPLETED:
            goal.status = "completed"
        elif session.status == MetaSessionStatus.FAILED:
            goal.status = "failed"
            if session.error_context and self.memory_kernel:
                self.memory_kernel.store_failure_pattern(
                    f"Goal: {goal.description[:200]}. Outcome: {session.error_context[:500]}",
                    metadata={"goal_id": goal.id},
                )
        elif session.status == MetaSessionStatus.PAUSED:
            goal.status = "paused"
            goal.paused_at_stage = session.paused_at_stage

        # 29-2 §3-4: Diagnosis on failure — query memory with WORKFLOW_REPAIR policy
        if goal.status == "failed" and self.memory_kernel:
            diagnosis = self._diagnose_goal_failure(goal)
            if diagnosis:
                goal.context["last_diagnosis"] = diagnosis

        # 29-2 §3-5: Repair cap — if max iterations exhausted, ensure goal is marked failed
        if goal.iteration >= goal.max_iterations and goal.status not in ("completed", "failed"):
            goal.status = "failed"
            logger.info(
                "Goal %s: repair cap reached (%d/%d)",
                goal.id[:8], goal.iteration, goal.max_iterations,
            )

        # 29-3: Advance build session (validate → smoke test → diagnose) when we have a workflow
        build_status_msg = ""
        if (
            goal.context.get("build_session_id")
            and session.workflow_id
            and self.memory_kernel
            and session.status == MetaSessionStatus.COMPLETED
        ):
            build_status_msg = await self._advance_build_session(
                goal, session.workflow_id, context, msg,
            )

        idx = next((i for i, g in enumerate(self._concierge_state.active_goals) if g.id == goal.id), None)
        if idx is not None:
            self._concierge_state.active_goals[idx] = goal
        else:
            self._concierge_state.active_goals.append(goal)
        self._save_concierge_state(msg.external_id, self._concierge_state)

        summary = self._goal_outcome_summary(goal, session)
        # 29-2 §3-6: Autonomy-aware check-in detail
        check_in = self._compose_check_in(goal, build_status_msg)
        if check_in:
            summary = f"{summary}\n\n{check_in}"
        elif build_status_msg:
            summary = f"{summary}\n\n{build_status_msg}"
        yield self._complete_event(content=f"{format_prefix(context.project.label)} {summary}")

    async def _handle_reuse_workflow(
        self, goal: ConciergeGoal, workflow_id: str, context: ResolvedContext,
    ) -> str | None:
        """Handle REUSE path: load workflow, link to project, run smoke test (29-4 §2)."""
        graph_store = getattr(self.capability_context, "graph_store", None)
        if not graph_store:
            return None
        try:
            graph_dict = graph_store.get_graph(workflow_id)
            if not isinstance(graph_dict, dict):
                return None
        except Exception:
            return None
        surface_id = self._current_surface_id or context.project.surface_id or ""
        if surface_id:
            self.project_store.link_workflow(context.project.project_id, workflow_id, surface_id)
        goal.workflow_id = workflow_id
        goal.context.pop("reuse_workflow_id", None)
        build_session_id = goal.context.get("build_session_id")
        if build_session_id and self.memory_kernel:
            build_mgr = BuildSessionManager(memory_kernel=self.memory_kernel)
            session = build_mgr.load(build_session_id)
            if session:
                session.workflow_id = workflow_id
                build_mgr.save(session)
                run_manager = getattr(self.capability_context, "run_manager", None)
                if run_manager:
                    from dan.models.graph import Graph

                    graph = Graph.model_validate(graph_dict)
                    status, msg = await build_mgr.advance_after_execution(
                        session,
                        graph,
                        workflow_id,
                        run_manager,
                        skip_smoke=bool(goal.context.get("skip_smoke")),
                    )
                    build_mgr.save(session)
                    if self.memory_kernel:
                        self.memory_kernel.increment_workflow_asset_usage(
                            workflow_id, success=(status == "completed")
                        )
                        if status == "failed":
                            error_summary = msg[:300] if msg else "unknown"
                            self.memory_kernel.store_failure_pattern(
                                f"Reuse of workflow {workflow_id} failed: {error_summary}",
                                metadata={
                                    "workflow_id": workflow_id,
                                    "reuse_attempt": True,
                                    "error_category": "reuse_smoke_test_failure",
                                },
                            )
                    goal.updated_at = time.time()
                    if status == "completed":
                        goal.status = "completed"
                    elif status == "failed":
                        goal.status = "failed"
                    else:
                        goal.status = "active"
                    return f"Reused workflow: {workflow_id}. {msg}"
        return f"Reused workflow: {workflow_id}."

    async def _handle_build_accept(
        self, goal: ConciergeGoal, msg: SurfaceMessage, context: ResolvedContext,
    ) -> str:
        """Handle 'looks good' — transition build session to COMPLETED, store memory (29-3 §3-5)."""
        goal.context.pop("build_accept", None)
        build_mgr = BuildSessionManager(memory_kernel=self.memory_kernel)
        session = build_mgr.load(goal.context["build_session_id"])
        if not session:
            return "Build session not found."
        workflow_id = goal.workflow_id or session.workflow_id
        if not workflow_id:
            return "No workflow to accept yet. Run the build first."
        build_mgr.transition_to(session, BuildSessionStatus.COMPLETED.value)
        build_mgr.save(session)
        goal.status = "completed"
        goal.updated_at = time.time()
        graph_store = getattr(self.capability_context, "graph_store", None)
        graph_dict = graph_store.get_graph(workflow_id) if graph_store else None
        if isinstance(graph_dict, dict):
            await self._run_post_build_followups(
                goal,
                session,
                workflow_id,
                context,
                graph_dict,
                outcome="completed",
            )
        return "Accepted. Build session completed."

    async def _handle_build_run_test(
        self, goal: ConciergeGoal, msg: SurfaceMessage, context: ResolvedContext,
    ) -> str:
        """Handle 'run it' — run smoke test on current workflow (29-3 §3-5)."""
        goal.context.pop("build_run_test", None)
        goal.context.pop("skip_smoke", None)
        workflow_id = goal.workflow_id
        if not workflow_id:
            build_mgr = BuildSessionManager(memory_kernel=self.memory_kernel)
            session = build_mgr.load(goal.context["build_session_id"])
            workflow_id = session.workflow_id if session else None
        if not workflow_id:
            return "No workflow to test yet. Run the build first."
        return await self._advance_build_session(goal, workflow_id, context, msg) or "Build session advance failed."

    async def _advance_build_session(
        self,
        goal: ConciergeGoal,
        workflow_id: str,
        context: ResolvedContext,
        msg: SurfaceMessage,
    ) -> str:
        """Run validate → smoke test → diagnose for build session (29-3 §3-2). Returns status message."""
        from dan.models.graph import Graph

        graph_store = getattr(self.capability_context, "graph_store", None)
        run_manager = getattr(self.capability_context, "run_manager", None)
        if not graph_store or not run_manager:
            return ""
        try:
            graph_dict = graph_store.get_graph(workflow_id)
            if not isinstance(graph_dict, dict):
                return ""
            graph = Graph.model_validate(graph_dict)
        except Exception:
            logger.debug("Failed to load graph for build session", exc_info=True)
            return ""

        build_mgr = BuildSessionManager(memory_kernel=self.memory_kernel)
        session = build_mgr.load(goal.context["build_session_id"])
        if not session:
            return ""
        session.workflow_id = workflow_id
        build_mgr.save(session)

        status, message = await build_mgr.advance_after_execution(
            session,
            graph,
            workflow_id,
            run_manager,
            status_callback=lambda m: logger.info("[Build] %s", m),
            skip_smoke=bool(goal.context.get("skip_smoke")),
            goal_context=goal.context,
        )
        build_mgr.save(session)

        goal.updated_at = time.time()
        diagnosis = session.diagnosis_history[-1] if session.diagnosis_history else {}
        requires_review = bool(diagnosis.get("requires_user_review"))
        autonomy = goal.context.get("effective_autonomy", AutonomyLevel.SUPERVISED.value)
        if (
            status == BuildSessionStatus.MODIFYING.value
            and requires_review
            and autonomy != AutonomyLevel.AUTONOMOUS.value
        ):
            build_mgr.transition_to(session, BuildSessionStatus.REVIEWING.value)
            build_mgr.save(session)
            goal.status = "paused"
            goal.paused_at_stage = BuildSessionStatus.REVIEWING.value
            goal.context["pending_structural_review"] = diagnosis
            return "[Build] Structural changes are recommended before I continue. Review the diagnosis and reply 'continue' to proceed."
        if status == "completed":
            goal.status = "completed"
        elif status == "failed":
            goal.status = "failed"
        else:
            goal.status = "active"
            if requires_review:
                goal.context["pending_structural_review"] = diagnosis
            else:
                goal.context.pop("pending_structural_review", None)

        if status in ("completed", "failed"):
            await self._run_post_build_followups(
                goal,
                session,
                workflow_id,
                context,
                graph_dict,
                outcome="completed" if status == "completed" else "failed",
            )
        return f"[Build] {message}"

    async def _run_post_build_followups(
        self,
        goal: ConciergeGoal,
        session: Any,
        workflow_id: str,
        context: ResolvedContext,
        graph_dict: dict[str, Any],
        *,
        outcome: Literal["completed", "failed"],
    ) -> None:
        """Fan out independent post-build follow-up work."""
        from .fan_out import fan_out_dict

        build_mgr = BuildSessionManager(memory_kernel=self.memory_kernel)
        surface_id = self._current_surface_id or context.project.surface_id or ""
        tasks: dict[str, Any] = {
            "post_build_memory": lambda: asyncio.to_thread(
                build_mgr.store_post_build_memory,
                session,
                outcome,
                graph_dict=graph_dict,
            ),
        }
        if workflow_id and surface_id:
            tasks["link_workflow"] = lambda: asyncio.to_thread(
                self.project_store.link_workflow,
                context.project.project_id,
                workflow_id,
                surface_id,
            )
        parent_wf_id = goal.context.get("adapt_workflow_id")
        if outcome == "completed" and self.memory_kernel and parent_wf_id:
            goal_desc = goal.description or ""
            node_count = len(graph_dict.get("nodes", []))
            adapt_content = f"Goal: {goal_desc[:200]}. Adapted from {parent_wf_id}. Topology: {node_count} nodes."
        results = await fan_out_dict(tasks)
        for name, result in results.items():
            if isinstance(result, Exception):
                logger.debug("Post-build follow-up failed: %s", name, exc_info=result)
        if outcome == "completed" and self.memory_kernel and parent_wf_id:
            from dan.engine.memory_kernel import MemoryType

            matched = False
            for item in self.memory_kernel.list_by_type(MemoryType.WORKFLOW_ASSET):
                if item.metadata.get("workflow_id") != workflow_id:
                    continue
                matched = True
                related = list(item.related_ids or [])
                if parent_wf_id not in related:
                    related.append(parent_wf_id)
                self.memory_kernel.update(
                    item.id,
                    related_ids=related,
                    content=adapt_content,
                )
            if not matched:
                self.memory_kernel.store_workflow_asset(
                    adapt_content,
                    workflow_id=workflow_id,
                    success_rate=1.0,
                    related_ids=[parent_wf_id],
                )

    def _decide_plan_action(self, goal: ConciergeGoal) -> str:
        """Query memory with WORKFLOW_BUILD policy to decide reuse/adapt/generate (29-2 §3-2)."""
        if not self.memory_kernel:
            return "generate"
        try:
            from dan.engine.memory_kernel import MemoryType

            scored = self.memory_kernel.retrieve_by_task(
                goal.description,
                task_type="workflow_build",
                limit=10,
            )
            assets = [si for si in scored if si.item.memory_type == MemoryType.WORKFLOW_ASSET]
            if not assets:
                return "generate"
            best = assets[0]
            if best.score >= 0.8:
                return "reuse"
            if best.score >= 0.4:
                return "adapt"
            return "generate"
        except Exception:
            logger.debug("Plan action decision failed", exc_info=True)
            return "generate"

    def _diagnose_goal_failure(self, goal: ConciergeGoal) -> dict[str, Any] | None:
        """Diagnose failure via memory kernel and build session (29-2 §3-4)."""
        if not self.memory_kernel:
            return None
        last_error = goal.error_history[-1] if goal.error_history else "unknown failure"
        build_session_id = goal.context.get("build_session_id")
        if build_session_id:
            build_mgr = BuildSessionManager(memory_kernel=self.memory_kernel)
            session = build_mgr.load(build_session_id)
            if session:
                return build_mgr.diagnose_for_failure(session, last_error)
        try:
            scored = self.memory_kernel.retrieve_by_task(
                last_error,
                task_type="workflow_repair",
                limit=8,
            )
            lines = [f"[Memory] {si.item.content[:200]}" for si in scored[:5]]
            return {
                "error": last_error,
                "memory_context": "\n".join(lines),
                "items_retrieved": len(scored),
            }
        except Exception:
            logger.debug("Goal failure diagnosis failed", exc_info=True)
            return {"error": last_error, "memory_context": ""}

    def _compose_check_in(
        self, goal: ConciergeGoal, build_status_msg: str = "",
    ) -> str:
        """Compose check-in detail filtered by autonomy level (29-2 §3-6).

        INTERACTIVE: all step details.
        SUPERVISED:  failure/completion info only.
        AUTONOMOUS:  only on final completion or unrecoverable failure.
        """
        autonomy = goal.context.get("effective_autonomy", AutonomyLevel.SUPERVISED.value)
        parts: list[str] = []
        plan_action = goal.context.get("plan_action")
        last_diag = goal.context.get("last_diagnosis")

        if autonomy == AutonomyLevel.INTERACTIVE.value:
            if plan_action:
                parts.append(f"Plan: {plan_action}")
            if goal.run_history:
                parts.append(f"Runs: {len(goal.run_history)}")
            last_run = goal.context.get("last_run_status")
            if last_run:
                parts.append(f"Last run: {last_run}")
            if last_diag:
                parts.append(f"Diagnosis: {last_diag.get('error', '')[:100]}")
            if build_status_msg:
                parts.append(build_status_msg)
            return "\n".join(parts)

        if autonomy == AutonomyLevel.SUPERVISED.value:
            if goal.status in ("failed", "completed"):
                if last_diag:
                    parts.append(f"Diagnosis: {last_diag.get('error', '')[:100]}")
                if build_status_msg:
                    parts.append(build_status_msg)
                return "\n".join(parts)
            return ""

        # AUTONOMOUS: only on unrecoverable failure or final completion
        if goal.status == "failed":
            if last_diag:
                parts.append(f"Diagnosis: {last_diag.get('error', '')[:100]}")
            if build_status_msg and "fail" in build_status_msg.lower():
                parts.append(build_status_msg)
            return "\n".join(parts)
        return ""

    @staticmethod
    def _sync_goal_from_session(goal: ConciergeGoal, session: Any) -> None:
        """Update ConciergeGoal from a MetaSession after run_session."""
        goal.workflow_id = session.workflow_id
        goal.run_history = list(session.run_history)
        goal.plan_result = session.plan_result
        goal.iteration = session.iteration
        goal.updated_at = time.time()
        if session.error_context:
            goal.error_history.append(session.error_context)
        for entry in getattr(session, "repair_history", []) or []:
            goal.user_interventions.append(entry if isinstance(entry, dict) else {"repair": str(entry)})

    @staticmethod
    def _goal_outcome_summary(goal: ConciergeGoal, session: Any) -> str:
        """One-line outcome summary for the user."""
        if goal.status == "failed":
            last_error = goal.error_history[-1] if goal.error_history else getattr(session, "error_context", "unknown")
            return f"Goal failed: {last_error or 'unknown'}"[:400]
        if goal.status == "paused":
            return f"Paused at {goal.paused_at_stage or getattr(session, 'paused_at_stage', '?')}. Say 'continue' to resume."
        if goal.status == "completed":
            return f"Goal completed. Workflow: {goal.workflow_id or 'n/a'}."
        if goal.status == "active":
            return f"Goal in progress. Workflow: {goal.workflow_id or 'n/a'}."
        status = getattr(session, "status", None)
        if status and str(status).lower() == "completed":
            return f"Goal completed. Workflow: {goal.workflow_id or 'n/a'}."
        if status and str(status).lower() == "failed":
            return f"Goal failed: {getattr(session, 'error_context', 'unknown') or 'unknown'}"[:400]
        if status and str(status).lower() == "paused":
            return f"Paused at {getattr(session, 'paused_at_stage', '?')}. Say 'continue' to resume."
        return f"Goal status: {status}. Workflow: {goal.workflow_id or 'n/a'}."


def build_concierge(
    *,
    chat_manager: Any,
    capability_context: Any,
    user_profile: Any = None,
    conversation_memory: Any = None,
    meta_controller: Any = None,
    memory_kernel: Any = None,
    autonomy_level: str | None = None,
    use_solver: bool = True,
    enable_dispatcher: bool = True,
    max_concurrent_projects: int = 5,
    mcp_bridge: Any = None,
    capability_registry: Any = None,
    tool_registry: Any = None,
    telemetry_store: Any = None,
) -> "Concierge | tuple[Concierge, ConcurrentDispatcher]":
    project_store = ProjectStore()
    resolver = ProjectContextResolver(
        project_store=project_store,
        activity_tracker=getattr(capability_context, "activity_tracker", None),
        conversation_memory=conversation_memory,
    )
    progress_reporter = ProgressReporter(
        run_manager=getattr(capability_context, "run_manager", None),
        activity_tracker=getattr(capability_context, "activity_tracker", None),
    )
    promoter = WorkflowPromoter(
        graph_store=getattr(capability_context, "graph_store", None),
        experience_store=getattr(capability_context, "experience_store", None),
    )
    goal_resolver = None
    memory_index = None
    if use_solver:
        memory_index = WorkflowMemoryIndex(
            experience_store=getattr(capability_context, "experience_store", None),
            experience_index=getattr(capability_context, "experience_index", None),
            graph_store=getattr(capability_context, "graph_store", None),
        )
        goal_resolver = GoalResolver(
            llm=getattr(capability_context, "llm_provider", None),
        )
    concierge = Concierge(
        project_store=project_store,
        context_resolver=resolver,
        chat_manager=chat_manager,
        capability_context=capability_context,
        user_profile=user_profile,
        conversation_memory=conversation_memory,
        progress_reporter=progress_reporter,
        promoter=promoter,
        meta_controller=meta_controller,
        goal_resolver=goal_resolver,
        memory_index=memory_index,
        memory_kernel=memory_kernel,
        autonomy_level=autonomy_level,
        mcp_bridge=mcp_bridge,
        capability_registry=capability_registry,
        tool_registry=tool_registry,
        telemetry_store=telemetry_store,
    )
    try:
        from .scheduler import ScheduleHistoryStore, ScheduleStore

        concierge._schedule_store = ScheduleStore()
        concierge._schedule_history_store = ScheduleHistoryStore()
    except Exception:
        logger.debug("Schedule stores unavailable during concierge build", exc_info=True)
    try:
        from .follow_up import FollowUpQueue, load_follow_up_config

        concierge._follow_up_queue = FollowUpQueue()
        concierge._follow_up_config = load_follow_up_config()
    except Exception:
        logger.debug("Follow-up services unavailable during concierge build", exc_info=True)
    try:
        from .continuity import PresenceTracker

        concierge._presence_tracker = PresenceTracker()
    except Exception:
        logger.debug("Presence tracker unavailable during concierge build", exc_info=True)
    try:
        from dan.engine.correction_memory import CorrectionStore
        from dan.engine.adaptation_registry import AdaptationRegistry
        from dan.engine.learning_tiers import LearningHealthCounters

        concierge._correction_store = CorrectionStore()
        concierge._adaptation_registry = AdaptationRegistry()
        concierge._health_counters = LearningHealthCounters()
    except Exception:
        logger.debug("Learning stores unavailable during concierge build", exc_info=True)
    try:
        from .computer_policy import AuditLog, ComputerControlConfig
        from .computer_use import ComputerUseLeaseManager

        concierge._computer_config = ComputerControlConfig.load()
        concierge._computer_lease = ComputerUseLeaseManager()
        concierge._computer_audit = AuditLog()
    except Exception:
        logger.debug("Computer-control services unavailable during concierge build", exc_info=True)
    if not enable_dispatcher:
        return concierge
    from .dispatcher import ConcurrentDispatcher
    dispatcher = ConcurrentDispatcher(
        concierge, max_concurrent_projects=max_concurrent_projects,
    )
    return concierge, dispatcher
