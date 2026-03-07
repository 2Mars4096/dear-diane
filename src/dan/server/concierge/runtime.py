from __future__ import annotations

import logging
import re
import uuid
from typing import Any, AsyncIterator

from dan.server.chat_manager import ChatCompleteEvent, ChatErrorEvent, ChatStreamEvent

from .classifier import ClassificationResult, IntentCategory, classify_intent
from .context_resolver import ProjectContextResolver, ResolvedContext

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
from .models import PendingAction, Project, SurfaceMessage, TaskTurn
from .policy import ActionPolicy, resolve_policy
from .progress import ProgressReporter
from .project_store import ProjectStore
from .promotion import WorkflowPromoter
from .executor import ExecutionResult, ExecutionSelector
from .memory_bridge import WorkflowMemoryIndex, enrich_planning_context
from .queue import ProjectMessageQueue, QueueDecision
from .solver import GoalResolver, PlanBuilder, SolverDecision

_NUMERIC_CLAIM_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d+(?:\.\d+)?%"
    r"|\b(?:price|close|open|high|low|volume|cap)\b[^.]*?\$?\d",
    re.IGNORECASE,
)
_unsourced_claim_warnings: int = 0


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
        self._current_surface_id: str | None = None
        self.auto_summarize_turn_threshold: int = 10
        self.handlers = HandlerRegistry()
        self.handlers.register(IntentCategory.FILE_REQUEST, FileHandler(user_profile=user_profile, chat_manager=chat_manager))
        self.handlers.register(IntentCategory.DIRECT_TASK, DirectTaskHandler(chat_manager))
        self.handlers.register(IntentCategory.RUN_CONTROL, RunHandler(capability_context))
        self.handlers.register(IntentCategory.STATUS_CHECK, StatusHandler(capability_context, progress_reporter))
        self.handlers.register(IntentCategory.EXPERIENCE_QUERY, ExperienceHandler(capability_context))
        self.handlers.register(IntentCategory.PUBLISH_SHARE, PublishHandler(capability_context))
        self.handlers.register(IntentCategory.WORKFLOW_QUERY, WorkflowQueryHandler(capability_context))
        build_handler = WorkflowBuildHandler(chat_manager)
        self.handlers.register(IntentCategory.WORKFLOW_BUILD, build_handler)
        self.handlers.register(IntentCategory.META_GOAL, MetaGoalHandler(meta_controller, capability_context=capability_context))
        self.handlers.register(IntentCategory.CONVERSATION, ConversationHandler(chat_manager))
        self.execution_selector = execution_selector or ExecutionSelector(self.handlers)

    async def process(self, msg: SurfaceMessage) -> AsyncIterator[ChatStreamEvent]:
        self._current_surface_id = msg.external_id

        save_result = self._handle_save_command(msg)
        if save_result is not None:
            yield save_result
            return

        pending_resolution = self._resolve_pending_follow_up(msg)
        if pending_resolution is not None:
            immediate_event, context, classification, msg = pending_resolution
            if immediate_event is not None:
                yield immediate_event
                return
        else:
            context = self.context_resolver.resolve(msg)
            classification = classify_intent(msg.text, context)

        if not msg.metadata.get("skip_queue"):
            decision = self.queue.enqueue(msg, context, classification)
            if decision == QueueDecision.QUEUED:
                yield self._complete_event(
                    content=f"[DAN - {context.project.label}] Queued — I'll get to this after the current action.",
                )
                return

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
            yield self._complete_event(content=f"[DAN - {context.project.label}] Please confirm before I do that.")
            return

        self.queue.mark_active(msg.external_id, context.project.project_id)

        if self.goal_resolver is not None:
            async for event in self._solver_path(msg, context, classification):
                yield event
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
            async for event in result.events:
                if getattr(event, "type", "") == "chat_complete":
                    final_content = getattr(event, "content", "") or ""
                    stream_channel_id = getattr(event, "stream_channel_id", None)
                yield event
            if final_content:
                self._record_assistant_turn(context, msg, final_content)
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
        if label_prefix and content and not content.startswith("[DAN"):
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
        self._record_assistant_turn(context, msg, content)
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
            async for event in handler_result.events:
                if getattr(event, "type", "") == "chat_complete":
                    final_content = getattr(event, "content", "") or ""
                    stream_channel_id = getattr(event, "stream_channel_id", None)
                yield event
            if final_content:
                self._record_assistant_turn(context, msg, final_content)
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
        if label_prefix and content and not content.startswith("[DAN"):
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
                return self._complete_event(content=f"[DAN - {project.label}] Cancelled."), context, ClassificationResult(
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
                            "skip_queue": True,
                        },
                    }
                )
                return None, context, ClassificationResult(
                    intent=IntentCategory(pending.intent),
                    confidence=1.0,
                    raw_text=pending.original_text,
                ), replay_msg
            return self._complete_event(content=f"[DAN - {project.label}] Please answer yes or no."), context, ClassificationResult(
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
                            "skip_queue": True,
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
                    return self._build_clarification_resume(project, msg, pending, idx, context)
            reply_norm = reply.strip()
            for idx, option in enumerate(pending.options):
                option_lower = option.lower()
                basename = option_lower.rsplit("/", 1)[-1]
                if reply_norm and (reply_norm in option_lower or reply_norm in basename):
                    return self._build_clarification_resume(project, msg, pending, idx, context)
            if pending.options:
                replay = self._build_clarification_resume(project, msg, pending, 0, context)
                replay_msg = replay[3].model_copy(
                    update={
                        "metadata": {
                            **replay[3].metadata,
                            "clarification_auto_note": (
                                f"Proceeding with {pending.options[0]} after one clarification round."
                            ),
                        }
                    }
                )
                return replay[0], replay[1], replay[2], replay_msg
            return self._complete_event(content=f"[DAN - {project.label}] I couldn't resolve that choice."), context, ClassificationResult(
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
            content=f"[DAN] Multiple projects are waiting for a reply. Which project? Use /project <label>. Pending: {labels}"
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
        if not _NUMERIC_CLAIM_RE.search(content):
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
                    "skip_queue": True,
                }
            }
        )
        return None, context, ClassificationResult(
            intent=IntentCategory(pending.intent),
            confidence=1.0,
            raw_text=pending.original_text,
        ), replay_msg

    async def _drain_queued_messages(self, context, msg: SurfaceMessage) -> AsyncIterator[ChatStreamEvent]:
        queued = self.queue.drain(context.project.project_id, context.task.task_id, msg.external_id)
        for queued_msg in queued:
            replay = queued_msg.model_copy(
                update={
                    "metadata": {
                        **queued_msg.metadata,
                        "skip_queue": True,
                    }
                }
            )
            async for event in self.process(replay):
                yield event

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
            self.queue.mark_completed(msg.external_id, context.project.project_id)
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
            return f"[DAN - {context.project.label}]"
        active_tasks = [t for t in context.project.tasks if t.status == "active"]
        if len(active_tasks) > 1:
            return f"[DAN - {context.project.label} / {context.task.label}]"
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


def build_concierge(
    *,
    chat_manager: Any,
    capability_context: Any,
    user_profile: Any = None,
    conversation_memory: Any = None,
    meta_controller: Any = None,
    use_solver: bool = True,
) -> Concierge:
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
    return Concierge(
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
    )
