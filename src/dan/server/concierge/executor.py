from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .classifier import ClassificationResult, IntentCategory
from .context_resolver import ResolvedContext
from .handlers import HandlerRegistry, HandlerResult
from .models import SurfaceMessage

from .policy import validate_terminal_content
from .solver import ExecutionMode, TerminalOutcome

if TYPE_CHECKING:
    from .solver import (
        FallbackStep,
        PlanStep,
        SolverDecision,
    )

logger = logging.getLogger(__name__)

# Reuse = run an existing workflow as-is; maps to RunHandler which handles
# start/resume. Adapt = modify then run; maps to WorkflowBuildHandler.
EXECUTION_MODE_TO_INTENT: dict[ExecutionMode, IntentCategory] = {
    ExecutionMode.DIRECT_ACTION: IntentCategory.DIRECT_TASK,
    ExecutionMode.WORKFLOW_REUSE: IntentCategory.RUN_CONTROL,
    ExecutionMode.WORKFLOW_ADAPT: IntentCategory.WORKFLOW_BUILD,
    ExecutionMode.WORKFLOW_BUILD: IntentCategory.WORKFLOW_BUILD,
    ExecutionMode.RUN_CONTROL: IntentCategory.RUN_CONTROL,
    ExecutionMode.STATUS_PULL: IntentCategory.STATUS_CHECK,
    ExecutionMode.EXPERIENCE_LOOKUP: IntentCategory.EXPERIENCE_QUERY,
    ExecutionMode.PUBLISH_SHARE: IntentCategory.PUBLISH_SHARE,
    ExecutionMode.CONVERSATION_SYNTHESIS: IntentCategory.CONVERSATION,
    ExecutionMode.META_DELEGATE: IntentCategory.META_GOAL,
}


def _mode_key(mode: ExecutionMode | str) -> str:
    return mode.value if hasattr(mode, "value") else str(mode)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    outcome: TerminalOutcome
    content: str
    handler_result: HandlerResult | None = None
    assumptions_used: list[str] = field(default_factory=list)
    remaining_steps: list[str] = field(default_factory=list)
    fallback_used: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Selector + fallback ladder
# ---------------------------------------------------------------------------

class ExecutionSelector:
    """Maps a SolverDecision to the right handler backend and walks the
    fallback chain when the primary path fails."""

    def __init__(self, handler_registry: HandlerRegistry) -> None:
        self._handlers = handler_registry

    # -- public API ---------------------------------------------------------

    def select_handler(self, decision: SolverDecision) -> IntentCategory:
        if decision.handler_hint is not None:
            return decision.handler_hint
        return EXECUTION_MODE_TO_INTENT.get(
            _mode_key(decision.execution_mode),
            IntentCategory.CONVERSATION,
        )

    async def execute(
        self,
        decision: SolverDecision,
        msg: SurfaceMessage,
        context: ResolvedContext,
        classification: ClassificationResult,
    ) -> ExecutionResult:
        if decision.clarification_question:
            return ExecutionResult(
                outcome=TerminalOutcome.WAITING_ON_SINGLE_USER_ACTION,
                content=decision.clarification_question,
            )
        result = await self._execute_with_fallback(
            decision, msg, context, classification,
        )
        return validate_terminal_outcome(result)

    async def _execute_with_fallback(
        self,
        decision: SolverDecision,
        msg: SurfaceMessage,
        context: ResolvedContext,
        classification: ClassificationResult,
    ) -> ExecutionResult:
        intent = self.select_handler(decision)
        logger.debug(
            "executor: primary mode=%s handler=%s",
            decision.execution_mode, intent,
        )

        primary = await self._try_handler(intent, msg, context, classification)
        if primary is not None:
            return self._synthesize_result(primary, decision)

        for step in decision.fallback_chain:
            fb_intent = EXECUTION_MODE_TO_INTENT.get(
                _mode_key(step.execution_mode),
                IntentCategory.CONVERSATION,
            )
            logger.debug(
                "executor: fallback step=%s handler=%s reason=%s",
                step.description, fb_intent, step.reason,
            )
            fb_result = await self._try_handler(
                fb_intent, msg, context, classification,
            )
            if fb_result is not None:
                return self._synthesize_result(
                    fb_result, decision, fallback_used=step.description,
                )

        remaining = [s.description for s in decision.plan_steps]
        return ExecutionResult(
            outcome=TerminalOutcome.PARTIAL_DONE_WITH_NEXT_UNLOCK,
            content=format_partial_result("", decision.assumptions, remaining),
            assumptions_used=list(decision.assumptions),
            remaining_steps=remaining,
        )

    async def _try_handler(
        self,
        intent: IntentCategory,
        msg: SurfaceMessage,
        context: ResolvedContext,
        classification: ClassificationResult,
    ) -> HandlerResult | None:
        try:
            handler = self._handlers.get(intent)
        except KeyError:
            logger.warning("executor: no handler registered for %s", intent)
            return None
        try:
            return await handler.handle(msg, context, classification)
        except Exception:
            logger.exception("executor: handler %s raised", intent)
            return None

    def _synthesize_result(
        self,
        handler_result: HandlerResult,
        decision: SolverDecision,
        fallback_used: str | None = None,
    ) -> ExecutionResult:
        if handler_result.clarification is not None:
            return ExecutionResult(
                outcome=TerminalOutcome.WAITING_ON_SINGLE_USER_ACTION,
                content=handler_result.clarification.question,
                handler_result=handler_result,
            )

        content = handler_result.content or ""
        remaining: list[str] = []

        if fallback_used:
            outcome = TerminalOutcome.PARTIAL_DONE_WITH_NEXT_UNLOCK
            remaining = [s.description for s in decision.plan_steps]
        elif decision.assumptions:
            outcome = TerminalOutcome.DONE_WITH_ASSUMPTIONS
        else:
            outcome = TerminalOutcome.DONE

        return ExecutionResult(
            outcome=outcome,
            content=content,
            handler_result=handler_result,
            assumptions_used=list(decision.assumptions),
            remaining_steps=remaining,
            fallback_used=fallback_used,
            metadata=_execution_metadata(decision, fallback_used),
        )


def validate_terminal_outcome(result: ExecutionResult) -> ExecutionResult:
    """Enforce the 'never just stop' policy: no empty DONE, no apology-only."""
    content = result.content.strip()

    if result.outcome == TerminalOutcome.DONE and not content:
        result.outcome = TerminalOutcome.PARTIAL_DONE_WITH_NEXT_UNLOCK
        result.content = format_partial_result(
            "", result.assumptions_used, result.remaining_steps,
        )
        return result

    is_valid, _reason = validate_terminal_content(content)
    if not is_valid and content:
        result.outcome = TerminalOutcome.PARTIAL_DONE_WITH_NEXT_UNLOCK
        result.content = format_partial_result(
            content, result.assumptions_used, result.remaining_steps,
        )

    return result


def format_partial_result(
    content: str,
    assumptions: list[str],
    remaining: list[str],
) -> str:
    """Format a partial result stating what was delivered, assumptions made,
    and remaining steps the user needs to unblock."""
    parts: list[str] = []
    if content:
        parts.append(content.rstrip())
    if assumptions:
        items = "\n".join(f"- {a}" for a in assumptions)
        parts.append(f"\n**Assumptions made:**\n{items}")
    if remaining:
        items = "\n".join(f"- {s}" for s in remaining)
        parts.append(f"\n**Remaining steps:**\n{items}")
    if not parts:
        parts.append(
            "I wasn't able to complete the task directly. "
            "Here's what's needed to move forward:"
        )
    return "\n".join(parts)


def _execution_metadata(
    decision: SolverDecision,
    fallback_used: str | None,
) -> dict[str, Any]:
    return {
        "execution_mode": _mode_key(decision.execution_mode),
        "confidence": decision.confidence,
        "fallback_used": fallback_used,
        "save_candidate": decision.save_candidate,
        "plan_step_count": len(decision.plan_steps),
    }
