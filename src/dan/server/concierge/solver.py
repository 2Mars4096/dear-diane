from __future__ import annotations

import json
import logging
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .classifier import ClassificationResult, IntentCategory, LLMProvider
from .context_resolver import ResolvedContext
from .models import SurfaceMessage

logger = logging.getLogger(__name__)


class ExecutionMode(str, Enum):
    DIRECT_ACTION = "direct_action"
    WORKFLOW_REUSE = "workflow_reuse"
    WORKFLOW_ADAPT = "workflow_adapt"
    WORKFLOW_BUILD = "workflow_build"
    RUN_CONTROL = "run_control"
    STATUS_PULL = "status_pull"
    EXPERIENCE_LOOKUP = "experience_lookup"
    PUBLISH_SHARE = "publish_share"
    CONVERSATION_SYNTHESIS = "conversation_synthesis"
    META_DELEGATE = "meta_delegate"


class TerminalOutcome(str, Enum):
    DONE = "done"
    DONE_WITH_ASSUMPTIONS = "done_with_assumptions"
    PARTIAL_DONE_WITH_NEXT_UNLOCK = "partial_done_with_next_unlock"
    WAITING_ON_SINGLE_USER_ACTION = "waiting_on_single_user_action"


class FallbackStep(BaseModel):
    description: str
    execution_mode: ExecutionMode
    reason: str = ""


class PlanStep(BaseModel):
    description: str
    execution_mode: ExecutionMode
    completion_criteria: str
    handler_hint: IntentCategory | None = None


class SolverDecision(BaseModel):
    user_goal: str
    requested_deliverable: str
    execution_mode: ExecutionMode
    plan_steps: list[PlanStep] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    fallback_chain: list[FallbackStep] = Field(default_factory=list)
    save_candidate: bool = False
    handler_hint: IntentCategory | None = None
    workflow_candidates: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.8
    plan_dag: Any | None = None


_INTENT_TO_MODE: dict[IntentCategory, ExecutionMode] = {
    IntentCategory.ASK: ExecutionMode.DIRECT_ACTION,
    IntentCategory.AGENT: ExecutionMode.DIRECT_ACTION,
    IntentCategory.PLAN: ExecutionMode.WORKFLOW_BUILD,
}

_FAST_PATH_INTENTS = frozenset({
    IntentCategory.ASK,
    IntentCategory.AGENT,
})

_FAST_PATH_CONFIDENCE = 0.85

_SOLVER_SYSTEM_PROMPT = (
    "Resolve the user's goal. Return JSON with:\n"
    "- user_goal: what they actually need\n"
    "- requested_deliverable: concrete output\n"
    "- execution_mode: direct_action | workflow_reuse | workflow_adapt | "
    "workflow_build | run_control | status_pull | experience_lookup | "
    "publish_share | conversation_synthesis | meta_delegate\n"
    "- assumptions: list of assumptions made\n"
    "- confidence: float 0.0-1.0 indicating how confident you are in this routing\n"
    "- clarification_question: set if the user's intent is unclear, multiple "
    "interpretations exist, or you need to make non-obvious assumptions. "
    "Prefer asking over guessing.\n\n"
    "ROUTING RULE: Time-sensitive queries (prices, rates, weather, scores, "
    "news, market data) → direct_action. Never guess live data.\n\n"
    '{"user_goal":"...","requested_deliverable":"...","execution_mode":"...",'
    '"assumptions":[...],"confidence":0.85,"clarification_question":null}'
)


_LIVE_DATA_PATTERNS = (
    "stock price", "share price", "close price", "closing price",
    "open price", "opening price", "market cap", "trading at",
    "exchange rate", "weather", "temperature", "forecast",
    "sports score", "game score", "election result",
    "current price", "latest price", "price of",
    "how much is", "what is .{1,40} trading at",
)

_LIVE_DATA_RE = re.compile(
    "|".join(_LIVE_DATA_PATTERNS), re.IGNORECASE,
)


def _needs_live_data(text: str) -> bool:
    return bool(_LIVE_DATA_RE.search(text))


def execution_mode_from_intent(intent: IntentCategory) -> ExecutionMode:
    return _INTENT_TO_MODE.get(intent, ExecutionMode.DIRECT_ACTION)


class GoalResolver:
    def __init__(self, llm: LLMProvider | None = None) -> None:
        self._llm = llm

    def build_planning_context(
        self,
        msg: SurfaceMessage,
        context: ResolvedContext,
        classification: ClassificationResult,
        *,
        workflow_candidates: list[dict[str, Any]] | None = None,
        experience_context: str | None = None,
        entity_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        recent_turns = [
            {"role": t.role, "content": t.content, "intent": t.intent}
            for t in context.task.turns[-6:]
        ]
        pending = None
        if context.project.pending_action:
            pa = context.project.pending_action
            pending = {"kind": pa.kind, "intent": pa.intent, "options": pa.options}
        ctx = {
            "message": msg.text,
            "project_summary": context.project.summary,
            "project_label": context.project.label,
            "task_label": context.task.label,
            "recent_turns": recent_turns,
            "active_runs": context.project.linked_run_ids,
            "pending_action": pending,
            "classifier_hint": classification.intent.value,
            "classifier_confidence": classification.confidence,
            "workflow_candidates": workflow_candidates or [],
            "experience_context": experience_context or "",
        }
        if entity_context:
            ctx["known_projects"] = entity_context
        return ctx

    async def resolve(
        self,
        msg: SurfaceMessage,
        context: ResolvedContext,
        classification: ClassificationResult,
        *,
        workflow_candidates: list[dict[str, Any]] | None = None,
        experience_context: str | None = None,
        entity_context: dict[str, Any] | None = None,
    ) -> SolverDecision:
        if self._is_fast_path(classification):
            logger.debug(
                "Fast-path: %s (%.2f)", classification.intent.value, classification.confidence,
            )
            return self._decision_from_classification(classification, msg.text)

        planning_ctx = self.build_planning_context(
            msg, context, classification,
            workflow_candidates=workflow_candidates,
            experience_context=experience_context,
            entity_context=entity_context,
        )
        if self._llm is None:
            return self._heuristic_decision(classification, msg.text, planning_ctx)
        return await self._llm_resolve(classification, msg.text, planning_ctx)

    def _is_fast_path(self, classification: ClassificationResult) -> bool:
        return (
            classification.intent in _FAST_PATH_INTENTS
            and classification.confidence >= _FAST_PATH_CONFIDENCE
        )

    def _decision_from_classification(
        self, classification: ClassificationResult, text: str,
    ) -> SolverDecision:
        mode = execution_mode_from_intent(classification.intent)
        return SolverDecision(
            user_goal=text,
            requested_deliverable=classification.param or text,
            execution_mode=mode,
            handler_hint=classification.intent,
            confidence=classification.confidence,
        )

    def _heuristic_decision(
        self,
        classification: ClassificationResult,
        text: str,
        planning_ctx: dict[str, Any],
    ) -> SolverDecision:
        mode = execution_mode_from_intent(classification.intent)
        if _needs_live_data(text) and mode == ExecutionMode.CONVERSATION_SYNTHESIS:
            mode = ExecutionMode.DIRECT_ACTION
        candidates = planning_ctx.get("workflow_candidates") or []
        # 33-9 E.11: Don't override WORKFLOW_BUILD to WORKFLOW_REUSE when the
        # classifier is confident the user wants a fresh build. This was
        # causing the agent lane to intercept build requests with stale reuse.
        _confident_build = (
            classification.intent == IntentCategory.PLAN
            and classification.route is not None
            and "workflow_edit" in classification.route.action_hints
            and classification.confidence >= 0.7
        )
        if candidates and mode in (ExecutionMode.DIRECT_ACTION, ExecutionMode.WORKFLOW_BUILD) and not _confident_build:
            mode = ExecutionMode.WORKFLOW_REUSE
        return SolverDecision(
            user_goal=text,
            requested_deliverable=classification.param or text,
            execution_mode=mode,
            handler_hint=classification.intent,
            workflow_candidates=candidates,
            confidence=max(classification.confidence - 0.1, 0.3),
            assumptions=[],
        )

    async def _llm_resolve(
        self,
        classification: ClassificationResult,
        text: str,
        planning_ctx: dict[str, Any],
    ) -> SolverDecision:
        if self._llm is None:
            return self._heuristic_decision(classification, text, planning_ctx)
        messages = [
            {"role": "system", "content": _SOLVER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(planning_ctx, default=str)},
        ]
        try:
            raw = await self._llm.complete(messages, model="")
            return self._parse_llm_response(raw, classification, planning_ctx)
        except Exception:
            logger.debug("LLM solver call failed, falling back to heuristic", exc_info=True)
            return self._heuristic_decision(classification, text, planning_ctx)

    def _parse_llm_response(
        self,
        raw: str,
        classification: ClassificationResult,
        planning_ctx: dict[str, Any],
    ) -> SolverDecision:
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
            data = json.loads(cleaned)
        except (json.JSONDecodeError, IndexError):
            logger.debug("Failed to parse solver JSON: %s", raw[:200])
            return self._heuristic_decision(
                classification, planning_ctx.get("message", ""), planning_ctx,
            )

        try:
            mode = ExecutionMode(data.get("execution_mode", "direct_action"))
        except ValueError:
            mode = execution_mode_from_intent(classification.intent)

        clarification = data.get("clarification_question")
        if not (isinstance(clarification, str) and clarification.strip()):
            clarification = None

        # Use the LLM's own confidence when available; fall back to a
        # moderate default so downstream guards (Guard 2) have a real signal.
        raw_confidence = data.get("confidence")
        if isinstance(raw_confidence, (int, float)) and 0.0 <= raw_confidence <= 1.0:
            confidence = float(raw_confidence)
        else:
            confidence = 0.7  # conservative default when LLM omits confidence

        return SolverDecision(
            user_goal=data.get("user_goal", planning_ctx.get("message", "")),
            requested_deliverable=data.get("requested_deliverable", ""),
            execution_mode=mode,
            assumptions=data.get("assumptions") or [],
            clarification_question=clarification,
            handler_hint=classification.intent,
            workflow_candidates=planning_ctx.get("workflow_candidates") or [],
            confidence=confidence,
        )


def _ps(
    desc: str, mode: ExecutionMode, criteria: str, hint: IntentCategory | None = None,
) -> PlanStep:
    return PlanStep(
        description=desc, execution_mode=mode,
        completion_criteria=criteria, handler_hint=hint,
    )


def _build_steps(d: SolverDecision) -> list[PlanStep]:
    M = ExecutionMode
    I = IntentCategory
    goal, deliv, hint = d.user_goal, d.requested_deliverable, d.handler_hint
    m = d.execution_mode

    if m == M.WORKFLOW_REUSE:
        return [
            _ps("Load matching workflow from candidates", M.WORKFLOW_REUSE, "Workflow loaded and parameters bound"),
            _ps("Run workflow and collect output", M.WORKFLOW_REUSE, f"Deliverable produced: {deliv}"),
        ]
    if m == M.WORKFLOW_ADAPT:
        return [
            _ps("Load closest matching workflow", M.WORKFLOW_REUSE, "Base workflow loaded"),
            _ps("Adapt workflow to match current goal", M.WORKFLOW_ADAPT, "Workflow modified and validated"),
            _ps("Run adapted workflow", M.WORKFLOW_ADAPT, f"Deliverable produced: {deliv}"),
        ]
    if m == M.WORKFLOW_BUILD:
        return [
            _ps("Plan workflow graph for goal", M.WORKFLOW_BUILD, "Graph structure determined with node types and edges"),
            _ps("Build and configure workflow", M.WORKFLOW_BUILD, "Workflow created and persisted", I.WORKFLOW_BUILD),
            _ps("Validate workflow can execute", M.WORKFLOW_BUILD, f"Dry-run passes; deliverable path: {deliv}"),
        ]
    if m == M.META_DELEGATE:
        return [
            _ps("Decompose meta-goal into sub-goals", M.META_DELEGATE, "Sub-goals identified with execution modes", I.META_GOAL),
            _ps("Build pipeline workflow for sub-goals", M.WORKFLOW_BUILD, "End-to-end workflow created", I.WORKFLOW_BUILD),
            _ps("Execute pipeline and collect outputs", M.META_DELEGATE, f"Deliverable produced: {deliv}"),
        ]
    if m == M.RUN_CONTROL:
        return [_ps(f"Apply run control: {goal}", M.RUN_CONTROL, "Run state changed and confirmed", I.RUN_CONTROL)]
    if m == M.STATUS_PULL:
        return [_ps("Retrieve and summarize current status", M.STATUS_PULL, "Status summary delivered to user", I.STATUS_CHECK)]
    if m == M.EXPERIENCE_LOOKUP:
        return [_ps(f"Look up: {goal}", M.EXPERIENCE_LOOKUP, "Relevant experience or workflow info delivered", hint)]
    if m == M.PUBLISH_SHARE:
        return [_ps(f"Publish/share: {goal}", M.PUBLISH_SHARE, "Content published or shared to target", I.PUBLISH_SHARE)]
    if m == M.CONVERSATION_SYNTHESIS:
        return [_ps("Synthesize conversational response", M.CONVERSATION_SYNTHESIS, "Response addresses user's message", I.CONVERSATION)]
    return [_ps(f"Execute: {goal}", M.DIRECT_ACTION, f"Deliverable produced: {deliv}", hint)]


_FALLBACK_CHAINS: dict[ExecutionMode, list[FallbackStep]] = {
    ExecutionMode.WORKFLOW_REUSE: [
        FallbackStep(description="Adapt existing workflow instead", execution_mode=ExecutionMode.WORKFLOW_ADAPT,
                     reason="Exact reuse failed; parameters may need adjustment"),
        FallbackStep(description="Build new workflow from scratch", execution_mode=ExecutionMode.WORKFLOW_BUILD,
                     reason="No suitable workflow to adapt"),
    ],
    ExecutionMode.WORKFLOW_ADAPT: [
        FallbackStep(description="Build new workflow from scratch", execution_mode=ExecutionMode.WORKFLOW_BUILD,
                     reason="Adaptation too complex; fresh build is cleaner"),
    ],
    ExecutionMode.META_DELEGATE: [
        FallbackStep(description="Build single workflow instead of pipeline", execution_mode=ExecutionMode.WORKFLOW_BUILD,
                     reason="Meta-decomposition too complex; simplify to one workflow"),
        FallbackStep(description="Execute as direct action", execution_mode=ExecutionMode.DIRECT_ACTION,
                     reason="Workflow approach infeasible for this request"),
    ],
}


class PlanBuilder:
    def build_plan(
        self, decision: SolverDecision,
    ) -> SolverDecision:
        steps = _build_steps(decision)
        fallback = _FALLBACK_CHAINS.get(decision.execution_mode, [])
        save = decision.execution_mode in (
            ExecutionMode.WORKFLOW_BUILD, ExecutionMode.WORKFLOW_ADAPT,
        )

        # 31-8: Build dependency DAG for parallel scheduling
        plan_dag = None
        if len(steps) > 1:
            try:
                from dan.engine.plan_scheduler import PlanTask, PlanDAG, infer_dependencies
                dag_tasks = [
                    PlanTask(
                        id=f"step-{i}",
                        name=step.description or f"Step {i}",
                        estimated_duration_minutes=30.0,
                    )
                    for i, step in enumerate(steps)
                ]
                plan_dag = PlanDAG(tasks=dag_tasks)
                plan_dag = infer_dependencies(plan_dag)
            except Exception:
                pass

        updates: dict = {
            "plan_steps": steps, "fallback_chain": fallback, "save_candidate": save,
        }
        if plan_dag is not None:
            updates["plan_dag"] = plan_dag
        return decision.model_copy(update=updates)
