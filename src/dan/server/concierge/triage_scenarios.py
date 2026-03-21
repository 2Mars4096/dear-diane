from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .models import ResolvedContext

LexicalRouteStatus = Literal["matched_single", "ambiguous", "no_match"]

_SOCIAL_ONLY_RE = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|ok|okay|cool|great|sounds good|yes|no)[!.?]*$",
    re.IGNORECASE,
)
_WORKFLOW_WORD_RE = re.compile(
    r"\b(?:workflow|graph|node|edge|port|subgraph)\b",
    re.IGNORECASE,
)
_WORKFLOW_STATUS_RE = re.compile(
    r"\b(?:status|progress|state|how(?:'s| is)\s+it\s+going|what(?:'s| is)\s+the\s+status)\b",
    re.IGNORECASE,
)
_WORKFLOW_IDENTITY_RE = re.compile(
    r"\b(?:what workflow is this|which workflow|workflow (?:name|id)|current workflow)\b",
    re.IGNORECASE,
)
_WORKFLOW_APPLY_RE = re.compile(
    r"\b(?:apply (?:it|that|this)|go ahead and apply|use (?:it|that|this)|make it live)\b",
    re.IGNORECASE,
)
_WORKFLOW_RETRY_RE = re.compile(
    r"\b(?:retry|try again|regenerate|redo|rebuild)\b",
    re.IGNORECASE,
)
_WORKFLOW_RUN_RE = re.compile(
    r"\b(?:run it|run the workflow|execute it|start it|test it|will it run|is it gonna work|does it run)\b",
    re.IGNORECASE,
)
_PATH_HINT_RE = re.compile(r"(?:~?/|\.{1,2}/|[A-Za-z]:\\)")
_WEB_RE = re.compile(
    r"\b(?:web|online|internet|latest|current|news|recent|search|look up|browse)\b",
    re.IGNORECASE,
)
_FURNACE_RE = re.compile(
    r"(?:\bfurnace\b|\bdistill(?:ation)?\b|\brecipe\s+session\b|\bpill-\d+\b|\bstart\s+furnace\b|\bstart\s+session\b|蒸馏|熔炉|配方会话|炉子会话|启动会话|启动furnace|开始furnace|开始蒸馏)",
    re.IGNORECASE,
)
_WORKFLOW_ACTIVITY_INTENTS = frozenset({
    "workflow_edit", "workflow_build", "build", "mutate",
})
_WORKFLOW_ACTIVITY_LABEL_RE = re.compile(
    r"\b(?:workflow|graph|build|node)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LexicalScenario:
    id: str
    description: str
    positive_patterns: tuple[re.Pattern[str], ...]
    negative_patterns: tuple[re.Pattern[str], ...] = ()
    required_context: tuple[str, ...] = ()
    intent: Literal["ask", "agent", "plan"] = "agent"
    target: str = "general"
    action_hints: tuple[str, ...] = ()
    confidence: float = 0.9
    tier: int = 1
    examples: tuple[str, ...] = ()
    anti_examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class LexicalRouteResult:
    status: LexicalRouteStatus
    scenario: LexicalScenario | None = None
    matched_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    rationale: str = ""
    missing_context: tuple[str, ...] = ()


def _recent_workflow_activity(context: ResolvedContext, lookback: int = 4) -> bool:
    for turn in context.task.turns[-lookback:]:
        if turn.intent and turn.intent in _WORKFLOW_ACTIVITY_INTENTS:
            return True
        meta = turn.metadata or {}
        if meta.get("route_target") == "workflow":
            return True
        if meta.get("allow_mutation_tool"):
            return True
        if turn.role == "assistant" and _WORKFLOW_ACTIVITY_LABEL_RE.search(turn.content or ""):
            return True
    return False


def _recent_workflow_reference(context: ResolvedContext, lookback: int = 4) -> bool:
    parts = [
        context.project.label,
        context.project.summary,
        context.task.label,
        " ".join(context.task.pending_steps),
    ]
    if _WORKFLOW_WORD_RE.search(" ".join(part for part in parts if part)):
        return True
    for turn in context.task.turns[-lookback:]:
        if _WORKFLOW_WORD_RE.search(turn.content or ""):
            return True
    return False


def _explicit_workflow_reference(text: str) -> bool:
    return bool(_WORKFLOW_WORD_RE.search(text))


def _current_workflow_reference(text: str, context: ResolvedContext) -> bool:
    return _explicit_workflow_reference(text) or _recent_workflow_reference(context)


def _linked_workflow_reference(_text: str, context: ResolvedContext) -> bool:
    return bool(getattr(context.project, "linked_workflow_ids", None))


def _workflow_operation_subject_known(text: str, context: ResolvedContext) -> bool:
    if _explicit_workflow_reference(text):
        return True
    if _recent_workflow_activity(context):
        return True
    return _linked_workflow_reference(text, context) and _recent_workflow_reference(context)


_CONTEXT_PREDICATES = {
    "recent_workflow_activity": _recent_workflow_activity,
    "current_workflow_reference": _current_workflow_reference,
    "linked_workflow_reference": _linked_workflow_reference,
    "workflow_operation_subject_known": _workflow_operation_subject_known,
}


DEFAULT_LEXICAL_SCENARIOS: tuple[LexicalScenario, ...] = (
    LexicalScenario(
        id="social_ack",
        description="Exact social acknowledgements and one-word replies.",
        positive_patterns=(_SOCIAL_ONLY_RE,),
        intent="ask",
        target="general",
        confidence=0.99,
        tier=0,
        examples=("ok", "thanks", "sounds good"),
        anti_examples=("thanks, now run it", "hello, build the workflow"),
    ),
    LexicalScenario(
        id="explicit_furnace_run_control",
        description="Explicit furnace/distillation lifecycle request.",
        positive_patterns=(_FURNACE_RE,),
        intent="agent",
        target="run",
        action_hints=("run_control",),
        confidence=0.98,
        tier=2,
        examples=("start a furnace session", "distill this topic"),
        anti_examples=("summarize the furnace design doc",),
    ),
    LexicalScenario(
        id="explicit_file_read",
        description="Explicit read/review request anchored to a file/path.",
        positive_patterns=(
            _PATH_HINT_RE,
            re.compile(r"\b(?:check this file|review this file|read this file|open this file)\b", re.IGNORECASE),
            re.compile(r"\b(?:read|review|inspect|open|summarize)\b.*\b(?:file|document|paper|report|draft|readme)\b", re.IGNORECASE),
        ),
        intent="agent",
        target="file",
        action_hints=("read_file",),
        confidence=0.9,
        tier=1,
        examples=("check this file", "read ./notes.md"),
        anti_examples=("check this workflow", "status of the workflow"),
    ),
    LexicalScenario(
        id="explicit_file_write",
        description="Explicit write/edit request anchored to a file/path.",
        positive_patterns=(
            re.compile(r"\b(?:write|save|update|edit|patch|rewrite)\b.*\b(?:file|document|draft|report|readme)\b", re.IGNORECASE),
            re.compile(r"\b(?:write|save|update|edit|patch|rewrite)\b.*(?:~?/|\.{1,2}/|[A-Za-z]:\\)", re.IGNORECASE),
        ),
        intent="agent",
        target="file",
        action_hints=("write_file",),
        confidence=0.9,
        tier=2,
        examples=("update this file", "rewrite ./README.md"),
        anti_examples=("update the workflow", "retry the workflow"),
    ),
    LexicalScenario(
        id="explicit_web_lookup",
        description="Explicit web/latest/current lookup request.",
        positive_patterns=(
            re.compile(r"\b(?:search that online|look that up|browse the web)\b", re.IGNORECASE),
            _WEB_RE,
        ),
        negative_patterns=(_FURNACE_RE,),
        intent="agent",
        target="web",
        action_hints=("search_web",),
        confidence=0.88,
        tier=2,
        examples=("search that online", "look up the latest earnings"),
        anti_examples=("run the workflow", "read this file"),
    ),
    LexicalScenario(
        id="workflow_followup_apply",
        description="Apply the most recent workflow proposal or mutation.",
        positive_patterns=(_WORKFLOW_APPLY_RE,),
        required_context=("workflow_operation_subject_known",),
        intent="agent",
        target="workflow",
        action_hints=("workflow_edit",),
        confidence=0.96,
        tier=2,
        examples=("apply it", "go ahead and apply"),
        anti_examples=("apply this patch to the file",),
    ),
    LexicalScenario(
        id="workflow_followup_retry",
        description="Retry or regenerate the current workflow work.",
        positive_patterns=(_WORKFLOW_RETRY_RE,),
        required_context=("workflow_operation_subject_known",),
        intent="agent",
        target="workflow",
        action_hints=("workflow_edit",),
        confidence=0.95,
        tier=2,
        examples=("try again", "retry the workflow"),
        anti_examples=("retry the web search", "try this file again"),
    ),
    LexicalScenario(
        id="workflow_run_followup",
        description="Run or test the current workflow rather than editing it.",
        positive_patterns=(_WORKFLOW_RUN_RE,),
        required_context=("workflow_operation_subject_known",),
        intent="agent",
        target="run",
        action_hints=("workflow_run", "run_control"),
        confidence=0.94,
        tier=2,
        examples=("run it", "will it run"),
        anti_examples=("run a web search",),
    ),
    LexicalScenario(
        id="workflow_query_status",
        description="Ask for the current workflow status or progress.",
        positive_patterns=(
            re.compile(r"\b(?:status of the workflow|workflow status)\b", re.IGNORECASE),
            _WORKFLOW_STATUS_RE,
        ),
        required_context=("workflow_operation_subject_known",),
        intent="agent",
        target="workflow",
        action_hints=("workflow_query",),
        confidence=0.92,
        tier=1,
        examples=("status of the workflow", "how is it going"),
        anti_examples=("project status", "status of the latest news"),
    ),
    LexicalScenario(
        id="workflow_query_identity",
        description="Ask which workflow is current or referenced.",
        positive_patterns=(_WORKFLOW_IDENTITY_RE,),
        required_context=("workflow_operation_subject_known",),
        intent="agent",
        target="workflow",
        action_hints=("workflow_query",),
        confidence=0.93,
        tier=1,
        examples=("what workflow is this", "what is the current workflow id"),
        anti_examples=("what file is this",),
    ),
)


def evaluate_lexical_scenarios(
    text: str,
    context: ResolvedContext,
    *,
    scenarios: tuple[LexicalScenario, ...] = DEFAULT_LEXICAL_SCENARIOS,
) -> LexicalRouteResult:
    stripped = text.strip()
    if not stripped:
        return LexicalRouteResult(status="no_match")

    matched: list[LexicalScenario] = []
    gated: list[tuple[LexicalScenario, tuple[str, ...]]] = []

    for scenario in scenarios:
        if not any(pattern.search(stripped) for pattern in scenario.positive_patterns):
            continue
        if any(pattern.search(stripped) for pattern in scenario.negative_patterns):
            continue
        missing_context = tuple(
            name
            for name in scenario.required_context
            if not _CONTEXT_PREDICATES[name](stripped, context)
        )
        if missing_context:
            gated.append((scenario, missing_context))
            continue
        matched.append(scenario)

    if len(matched) == 1 and not gated:
        scenario = matched[0]
        return LexicalRouteResult(
            status="matched_single",
            scenario=scenario,
            matched_ids=(scenario.id,),
            confidence=scenario.confidence,
            rationale=scenario.description,
        )

    if len(matched) == 1 and gated:
        gated_targets = {scenario.target for scenario, _missing in gated}
        if any(target != matched[0].target for target in gated_targets):
            return LexicalRouteResult(
                status="ambiguous",
                matched_ids=(matched[0].id, *(scenario.id for scenario, _missing in gated)),
                confidence=0.0,
                rationale="Explicit lexical overlap with missing workflow context; escalate to LLM triage.",
                missing_context=tuple(
                    name for _scenario, missing in gated for name in missing
                ),
            )
        scenario = matched[0]
        return LexicalRouteResult(
            status="matched_single",
            scenario=scenario,
            matched_ids=(scenario.id,),
            confidence=scenario.confidence,
            rationale=scenario.description,
        )

    if matched or gated:
        return LexicalRouteResult(
            status="ambiguous",
            matched_ids=tuple(
                [scenario.id for scenario in matched]
                + [scenario.id for scenario, _missing in gated]
            ),
            confidence=0.0,
            rationale="Multiple lexical scenarios matched or required context is missing; escalate to LLM triage.",
            missing_context=tuple(
                name for _scenario, missing in gated for name in missing
            ),
        )

    return LexicalRouteResult(status="no_match")
