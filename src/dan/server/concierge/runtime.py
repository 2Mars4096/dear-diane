from __future__ import annotations

import asyncio
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

from .classifier import ClassificationResult, IntentCategory, classify_intent
from .context_resolver import ProjectContextResolver, ResolvedContext
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
from .queue import ProjectMessageQueue
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

_FAST_COMMAND_PREFIXES = ("/save", "/build-", "/memory-", "/mcp")
_PREF_CONFIRM_WORDS = frozenset({"confirm all", "yes", "confirm"})


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
        queue: ProjectMessageQueue | None = None,
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
    ) -> None:
        self.project_store = project_store
        self.context_resolver = context_resolver
        self.chat_manager = chat_manager
        self.capability_context = capability_context
        self.user_profile = user_profile
        self.conversation_memory = conversation_memory
        self.queue = queue or ProjectMessageQueue()
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
        self.handlers.register(IntentCategory.EXPERIENCE_QUERY, ExperienceHandler(capability_context))
        self.handlers.register(IntentCategory.PUBLISH_SHARE, PublishHandler(capability_context))
        self.handlers.register(IntentCategory.WORKFLOW_QUERY, WorkflowQueryHandler(capability_context))
        build_handler = WorkflowBuildHandler(chat_manager)
        self.handlers.register(IntentCategory.WORKFLOW_BUILD, build_handler)
        self.handlers.register(IntentCategory.META_GOAL, MetaGoalHandler(meta_controller, capability_context=capability_context))
        self.handlers.register(IntentCategory.CONVERSATION, ConversationHandler(chat_manager))
        self.execution_selector = execution_selector or ExecutionSelector(self.handlers)

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
        """Check if text is a known command that can skip LLM/memory prep."""
        lower = text.strip().lower()
        if any(lower.startswith(p) for p in _FAST_COMMAND_PREFIXES):
            return True
        pending = self._pending_preference_surface.get(self._current_surface_id)
        if pending and (lower in _PREF_CONFIRM_WORDS or lower.startswith("reject")):
            return True
        return False

    async def _try_fast_command(
        self, msg: SurfaceMessage,
    ) -> ChatCompleteEvent | None:
        """Dispatch known commands instantly, skipping expensive prep."""
        if not self._is_fast_command(msg.text):
            return None
        save_result = self._handle_save_command(msg)
        if save_result is not None:
            return save_result
        build_result = self._handle_build_command(msg)
        if build_result is not None:
            return build_result
        memory_result = self._handle_memory_command(msg)
        if memory_result is not None:
            return memory_result
        if msg.text.strip().lower().startswith("/mcp"):
            mcp_result = await self._handle_mcp_command(msg)
            if mcp_result is not None:
                return mcp_result
        pref_confirmation = self.handle_preference_confirmation(
            msg.external_id, msg.text,
        )
        if pref_confirmation:
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
        os.environ.get("DAN_CONCIERGE_REASSURANCE_DELAY", "5")
    )
    _REASSURANCE_REPEAT_INTERVAL: float = float(
        os.environ.get("DAN_CONCIERGE_REASSURANCE_INTERVAL", "15")
    )

    _REASSURANCE_MESSAGES = [
        "Working on your request — this might take a moment\u2026",
        "Still working on it \u2014 gathering information\u2026",
        "Hang tight \u2014 pulling things together\u2026",
        "Almost there \u2014 finishing up\u2026",
    ]

    async def process(self, msg: SurfaceMessage) -> AsyncIterator[ChatStreamEvent]:
        if self._REASSURANCE_INITIAL_DELAY <= 0:
            async for event in self._process_inner(msg):
                yield event
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
        reassurance_count = 0

        try:
            while True:
                if not first_event_received:
                    timeout = self._REASSURANCE_INITIAL_DELAY
                else:
                    timeout = self._REASSURANCE_REPEAT_INTERVAL

                try:
                    item = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    idx = min(reassurance_count, len(self._REASSURANCE_MESSAGES) - 1)
                    yield self._complete_event(
                        content=self._REASSURANCE_MESSAGES[idx],
                    )
                    reassurance_count += 1
                    continue

                if item is _sentinel:
                    break
                if isinstance(item, Exception):
                    raise item
                first_event_received = True
                yield item
        finally:
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

        # Fast-path: known commands respond instantly without LLM/memory prep.
        fast_event = await self._try_fast_command(msg)
        if fast_event is not None:
            self._save_concierge_state(msg.external_id, self._concierge_state)
            yield fast_event
            return

        has_active_build = any(
            goal.status in ("active", "paused") and goal.context.get("build_session_id")
            for goal in self._concierge_state.active_goals
        )

        _precomputed_context = None
        _speculative_reuse: tuple[str, Any] | None = None
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
            prep = await fan_out_dict(prep_tasks)
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

        pending_resolution = self._resolve_pending_follow_up(msg)
        if pending_resolution is not None:
            immediate_event, context, classification, msg = pending_resolution
            if immediate_event is not None:
                yield immediate_event
                return
        else:
            context = (
                _precomputed_context
                if _precomputed_context is not None
                else self.context_resolver.resolve(msg)
            )
            classification = classify_intent(msg.text, context)

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

        use_goal_orchestrator = (
            self._should_use_goal_orchestrator(classification)
            or self._should_resume_goal_follow_up(
                msg.text,
                classification,
                context.project.project_id,
            )
        )

        if self.goal_resolver is not None and not use_goal_orchestrator:
            async for event in self._solver_path(msg, context, classification):
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
                async for queued_event in self._drain_queued_messages(context, msg):
                    yield queued_event
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
                    async for queued_event in self._drain_queued_messages(context, msg):
                        yield queued_event
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
                    async for queued_event in self._drain_queued_messages(context, msg):
                        yield queued_event
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
                    async for queued_event in self._drain_queued_messages(context, msg):
                        yield queued_event
                    return
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
            )
            self._finalize_task(
                context,
                msg,
                classification.intent,
                True,
                task_status_override=self._task_status_for_goal(goal),
            )
            async for queued_event in self._drain_queued_messages(context, msg):
                yield queued_event
            return

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
                if evt_type == "chat_complete":
                    final_content = getattr(event, "content", "") or ""
                    stream_channel_id = getattr(event, "stream_channel_id", None)
                elif evt_type == "chat_tool_call_start":
                    saw_tool_call = True
                yield event
            if final_content:
                checked = self._check_unsourced_claims(final_content, saw_tool_call)
                self._record_assistant_turn(context, msg, checked)
                self._store_memory_candidates(msg.text, checked, None)
            self._finalize_task(
                context,
                msg,
                classification.intent,
                bool(final_content),
                task_status_override=(result.task_update or {}).get("status"),
            )
            if stream_channel_id and stream_channel_id.startswith("run-"):
                self.project_store.link_run(context.project.project_id, stream_channel_id[4:], msg.external_id)
            async for queued_event in self._drain_queued_messages(context, msg):
                yield queued_event
            return

        content = result.content
        label_prefix = self._format_reply_label(context)
        if label_prefix and content and not starts_with_prefix(content):
            content = f"{label_prefix} {content}"
        auto_note = str(msg.metadata.get("clarification_auto_note") or "").strip()
        if auto_note and content:
            content = f"{auto_note}\n\n{content}"
        had_tool_call = classification.intent not in (
            IntentCategory.CONVERSATION, IntentCategory.DIRECT_TASK,
        )
        content = self._check_unsourced_claims(content, had_tool_call)
        if self.promoter and self.promoter.should_propose(context.project, context.task):
            proposal = self.promoter.build_proposal(context.project, context.task)
            content = f"{content}\n\nSave as reusable workflow? -> {proposal.save_command}"
        pref_prompt = self._maybe_surface_preferences(msg.external_id, activate=True)
        if pref_prompt:
            content = f"{content}\n\n{pref_prompt}" if content else pref_prompt
        self._record_assistant_turn(context, msg, content)
        self._store_memory_candidates(msg.text, content, None)
        self._finalize_task(
            context,
            msg,
            classification.intent,
            bool(content),
            task_status_override=(result.task_update or {}).get("status"),
        )
        yield self._complete_event(content=content, stream_channel_id=result.stream_channel_id)
        async for queued_event in self._drain_queued_messages(context, msg):
            yield queued_event

    async def _solver_path(
        self,
        msg: SurfaceMessage,
        context: ResolvedContext,
        classification: ClassificationResult,
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

        decision = await self.goal_resolver.resolve(
            msg, context, classification,
            workflow_candidates=workflow_candidates,
            experience_context=experience_context_str,
        )

        decision = self.plan_builder.build_plan(decision, context)

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
                if evt_type == "chat_complete":
                    final_content = getattr(event, "content", "") or ""
                    stream_channel_id = getattr(event, "stream_channel_id", None)
                elif evt_type == "chat_tool_call_start":
                    saw_tool_call = True
                yield event
            if final_content:
                checked = self._check_unsourced_claims(final_content, saw_tool_call)
                self._record_assistant_turn(context, msg, checked)
                self._store_memory_candidates(msg.text, checked, None)
            self._finalize_task(
                context, msg, classification.intent, bool(final_content),
                task_status_override=(handler_result.task_update or {}).get("status"),
            )
            if stream_channel_id and stream_channel_id.startswith("run-"):
                self.project_store.link_run(context.project.project_id, stream_channel_id[4:], msg.external_id)
            async for queued_event in self._drain_queued_messages(context, msg):
                yield queued_event
            return

        auto_note = str(msg.metadata.get("clarification_auto_note") or "").strip()
        if auto_note and content:
            content = f"{auto_note}\n\n{content}"
        label_prefix = self._format_reply_label(context)
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

        if self.promoter and decision.save_candidate:
            if self.promoter.should_propose(context.project, context.task):
                proposal = self.promoter.build_proposal(context.project, context.task)
                content = f"{content}\n\nSave as reusable workflow? -> {proposal.save_command}"

        self._record_assistant_turn(context, msg, content)
        self._store_memory_candidates(msg.text, content, None)
        self._finalize_task(
            context, msg, classification.intent, bool(content),
        )
        yield self._complete_event(content=content)
        async for queued_event in self._drain_queued_messages(context, msg):
            yield queued_event

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

    async def _drain_queued_messages(self, context, msg: SurfaceMessage) -> AsyncIterator[ChatStreamEvent]:
        """No-op — queueing is now handled entirely by ConcurrentDispatcher."""
        return
        yield  # pragma: no cover — makes this a valid async generator

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
        if status == "completed":
            self._maybe_auto_summarize(context, msg, on_complete=True)

    def _find_project_with_workflow(self, surface_id: str) -> Project | None:
        for project in self.project_store.list_active(surface_id):
            if project.linked_workflow_ids:
                return project
        return None

    def _format_reply_label(self, context: ResolvedContext) -> str:
        surface_id = self._current_surface_id
        if surface_id is None:
            return ""
        active_projects = self.project_store.list_active(surface_id)
        if len(active_projects) > 1:
            return format_prefix(context.project.label, bot_name=self.bot_name)
        active_tasks = [t for t in context.project.tasks if t.status == "active"]
        if len(active_tasks) > 1:
            return format_prefix(context.project.label, context.task.label, bot_name=self.bot_name)
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

    def _handle_memory_command(self, msg: SurfaceMessage) -> ChatCompleteEvent | None:
        """Handle /memory-stats and /memory-search <query> (29-6 §11-1, §11-2)."""
        text = msg.text.strip()
        lower = text.lower()

        if lower == "/memory-stats":
            return self._memory_stats_response()
        if lower.startswith("/memory-search"):
            query = text[len("/memory-search"):].strip()
            if not query:
                return self._complete_event(content="Usage: /memory-search <query>")
            return self._memory_search_response(query)
        return None

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

    def _retrieve_memory_context(self, message: str, *, has_active_build: bool = False) -> str:
        """Retrieve relevant memory for this message (task-type-aware). Used for goal detection and planning."""
        if not self.memory_kernel:
            return ""
        try:
            from dan.engine.memory_kernel import classify_task_type

            scored = self.memory_kernel.retrieve_by_task(
                message,
                task_type=classify_task_type(message, has_active_build=has_active_build),
                limit=10,
            )
            if not scored:
                return ""
            lines = ["Relevant context from memory:"]
            for si in scored[:8]:
                tag = si.item.memory_type.value.upper()
                lines.append(f"- [{tag}] {si.item.content[:200]}")
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
            self._store_memory_candidates_sync(message, response, goal_context)
            return
        task = loop.create_task(
            self._store_memory_candidates_async(message, response, goal_context),
        )
        self._bg_memory_tasks.add(task)
        task.add_done_callback(self._bg_memory_tasks.discard)

    def _store_memory_candidates_sync(
        self,
        message: str,
        response: str,
        goal_context: dict[str, Any] | None = None,
    ) -> None:
        """Fallback synchronous path when no event loop is running."""
        self._store_episode_candidates(message, response, goal_context)
        self._try_extract_preferences(message, response)
        self._try_heuristic_extraction(message, response, goal_context)

    async def _store_memory_candidates_async(
        self,
        message: str,
        response: str,
        goal_context: dict[str, Any] | None = None,
    ) -> None:
        """Fan out episode, preference, and heuristic extraction concurrently."""
        from .fan_out import fan_out_dict

        tasks = {
            "episode": lambda: asyncio.to_thread(
                self._store_episode_candidates, message, response, goal_context,
            ),
            "preferences": lambda: asyncio.to_thread(
                self._try_extract_preferences, message, response,
            ),
            "heuristic": lambda: asyncio.to_thread(
                self._try_heuristic_extraction, message, response, goal_context,
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

    def _try_extract_preferences(self, user_message: str, assistant_message: str) -> None:
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
                        )
                elif isinstance(value, list):
                    for entry in value:
                        self.memory_kernel.store_preference(
                            content=f"{key}: {entry}",
                            tags=[key],
                        )
                elif isinstance(value, str) and value:
                    self.memory_kernel.store_preference(
                        content=f"{key}: {value}",
                        tags=[key],
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

    def _try_heuristic_extraction(
        self,
        user_message: str,
        assistant_message: str,
        goal_context: dict[str, Any] | None = None,
    ) -> None:
        """Run MemoryExtractor heuristics to capture facts and preferences the other extractors miss."""
        if self.memory_kernel is None:
            return
        if os.environ.get("DAN_MEMORY_EXTRACTION", "1").strip() != "1":
            return
        try:
            from dan.engine.memory_extractor import MemoryExtractor

            extractor = MemoryExtractor()
            tool_activity = (goal_context or {}).get("metadata", {}).get("tool_calls")
            candidates = extractor.extract(
                user_message=user_message,
                assistant_message=assistant_message,
                tool_calls=tool_activity if isinstance(tool_activity, list) else None,
                goal_context=goal_context,
            )
            for candidate in candidates:
                if candidate.memory_type == "fact":
                    self.memory_kernel.store_fact(
                        candidate.content, tags=candidate.tags,
                    )
                elif candidate.memory_type == "preference":
                    self.memory_kernel.store_preference(
                        candidate.content, tags=candidate.tags,
                    )
                # episodes already stored by _extract_memory_candidates
        except Exception:
            logger.debug("Heuristic memory extraction failed", exc_info=True)

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
        queue=ProjectMessageQueue(),
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
    )
    if not enable_dispatcher:
        return concierge
    from .dispatcher import ConcurrentDispatcher
    dispatcher = ConcurrentDispatcher(
        concierge, max_concurrent_projects=max_concurrent_projects,
    )
    return concierge, dispatcher
