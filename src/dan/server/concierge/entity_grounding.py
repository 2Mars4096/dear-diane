from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .classifier import ClassificationResult
    from .models import Project, SurfaceMessage, Task
    from .project_store import ProjectStore
    from .solver import SolverDecision

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class EntityContext:
    matched_projects: list[Project]
    matched_tasks: list[Task]
    matched_workflows: list[str]
    unresolved_refs: list[str]
    is_about_project: bool


@dataclass
class GuardContext:
    message: SurfaceMessage
    entity_ctx: EntityContext
    classification: ClassificationResult | None = None
    solver_decision: SolverDecision | None = None
    response_content: str | None = None


@dataclass
class GuardResult:
    passed: bool
    action: Literal["proceed", "reclassify", "clarify", "short_circuit"] = "proceed"
    clarification_question: str | None = None
    short_circuit_response: str | None = None
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Environment knobs
# ---------------------------------------------------------------------------

_GUARD_PIPELINE_ENABLED = os.environ.get("DAN_GUARD_PIPELINE", "1") == "1"
_GUARD_CLASSIFICATION_ENABLED = os.environ.get("DAN_GUARD_CLASSIFICATION", "1") == "1"
_GUARD_UNDERSTANDING_ENABLED = os.environ.get("DAN_GUARD_UNDERSTANDING", "1") == "1"
_GUARD_RELEVANCE_ENABLED = os.environ.get("DAN_GUARD_RELEVANCE", "1") == "1"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ABOUT_PROJECT_PHRASES = frozenset({
    "update", "updates", "status", "progress", "how's", "how is",
    "what's happening", "any news", "review", "overview",
    "check on", "checking on", "where are we", "where is",
    "how far", "what's the status", "report",
})

_EXTERNAL_ACTION_PHRASES = frozenset({
    "search online", "look up online", "check github", "check on github",
    "google", "search for", "look up on the web", "find on the web",
    "search the web", "browse", "go to",
})

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "it", "its", "this", "that", "these", "those", "i", "me",
    "my", "we", "our", "you", "your", "he", "she", "they", "them",
    "and", "or", "but", "if", "then", "so", "as", "not", "no",
    "than", "too", "very", "just", "about", "up", "out", "how",
    "what", "which", "who", "when", "where", "why", "all", "each",
    "every", "some", "any", "most", "other", "into", "through",
    "please",
})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_for_match(text: str) -> str:
    """Lowercase, strip non-alphanumeric except spaces."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _extract_content_words(text: str) -> set[str]:
    """Extract content words (non-stopwords, length >= 2) from text."""
    words = re.findall(r"[a-zA-Z]{2,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _format_project_info_short(projects: list[Project]) -> str:
    lines: list[str] = []
    for p in projects[:3]:
        task_count = len(p.tasks)
        active = sum(1 for t in p.tasks if t.status == "active")
        wf_count = len(p.linked_workflow_ids)
        run_count = len(p.linked_run_ids)
        lines.append(f"**{p.label}** ({p.status})")
        lines.append(f"  Tasks: {task_count} ({active} active)")
        if wf_count:
            lines.append(f"  Workflows: {wf_count}")
        if run_count:
            lines.append(f"  Runs: {run_count}")
        if p.summary:
            lines.append(f"  Summary: {p.summary}")
        recent_tasks = sorted(p.tasks, key=lambda t: t.updated_at, reverse=True)[:3]
        if recent_tasks:
            lines.append("  Recent activity:")
            for t in recent_tasks:
                lines.append(f"    [{t.status}] {t.label} — {len(t.turns)} turns")
    tip = "\nUse `/project info <name>` for details or `/project memory <name>` for stored facts."
    return "\n".join(lines) + tip


def guards_enabled() -> bool:
    """Check if the guard pipeline is enabled globally."""
    return _GUARD_PIPELINE_ENABLED


# ---------------------------------------------------------------------------
# Entity grounding
# ---------------------------------------------------------------------------


def ground_entities(
    text: str,
    project_store: ProjectStore,
    surface_id: str,
) -> EntityContext:
    projects = project_store.list_active(surface_id)
    if not projects:
        projects = project_store.list_projects(surface_id)[:20]

    norm_text = _normalize_for_match(text)
    matched_projects: list[Any] = []

    for p in projects:
        norm_label = _normalize_for_match(p.label)
        if len(norm_label) < 3:
            if norm_label and norm_label in norm_text.split():
                matched_projects.append(p)
        elif norm_label in norm_text:
            matched_projects.append(p)

    matched_tasks: list[Any] = []
    matched_workflows: list[str] = []
    for p in matched_projects:
        matched_tasks.extend(t for t in p.tasks if t.status == "active")
        matched_workflows.extend(p.linked_workflow_ids)

    text_lower = text.lower()
    has_about_phrase = any(phrase in text_lower for phrase in _ABOUT_PROJECT_PHRASES)
    has_external_phrase = any(phrase in text_lower for phrase in _EXTERNAL_ACTION_PHRASES)
    is_about_project = bool(matched_projects) and has_about_phrase and not has_external_phrase

    return EntityContext(
        matched_projects=matched_projects,
        matched_tasks=matched_tasks,
        matched_workflows=matched_workflows,
        unresolved_refs=[],
        is_about_project=is_about_project,
    )


# ---------------------------------------------------------------------------
# Guard 1: classification coherence
# ---------------------------------------------------------------------------


def guard_classification(ctx: GuardContext) -> GuardResult:
    if not _GUARD_PIPELINE_ENABLED or not _GUARD_CLASSIFICATION_ENABLED:
        return GuardResult(passed=True)
    if ctx.classification is None:
        return GuardResult(passed=True)

    msg_lower = ctx.message.text.lower()

    if any(phrase in msg_lower for phrase in _EXTERNAL_ACTION_PHRASES):
        return GuardResult(passed=True, notes=["external action phrase detected"])

    if ctx.entity_ctx.is_about_project and ctx.entity_ctx.matched_projects:
        summary = _format_project_info_short(ctx.entity_ctx.matched_projects)
        return GuardResult(
            passed=False,
            action="short_circuit",
            short_circuit_response=summary,
            notes=["project-about query short-circuited to project info"],
        )

    if ctx.classification.intent.value == "file_request":
        has_path = bool(re.search(r"(?:~|/)[A-Za-z0-9._~/-]+", ctx.message.text))
        if not has_path and ctx.entity_ctx.matched_projects:
            return GuardResult(
                passed=False,
                action="reclassify",
                notes=["file_request classification but no path found, project entity matched"],
            )

    if ctx.classification.confidence < 0.6 and ctx.entity_ctx.matched_projects:
        return GuardResult(
            passed=False,
            action="clarify",
            clarification_question="I'm not sure I understand. Could you clarify what you'd like me to do?",
            notes=[f"classification confidence {ctx.classification.confidence:.2f} below 0.6 with entity matches"],
        )

    return GuardResult(passed=True)


# ---------------------------------------------------------------------------
# Guard 2: understanding coherence
# ---------------------------------------------------------------------------


def guard_understanding(ctx: GuardContext) -> GuardResult:
    if not _GUARD_PIPELINE_ENABLED or not _GUARD_UNDERSTANDING_ENABLED:
        return GuardResult(passed=True)
    if ctx.solver_decision is None:
        return GuardResult(passed=True)

    user_words = _extract_content_words(ctx.message.text)
    entity_names = {_normalize_for_match(p.label) for p in ctx.entity_ctx.matched_projects}

    # Check 1 — assumption grounding
    assumptions = ctx.solver_decision.assumptions
    if assumptions:
        ungrounded = 0
        for assumption in assumptions:
            a_words = _extract_content_words(assumption)
            if not (a_words & user_words) and not (a_words & entity_names):
                ungrounded += 1
        if ungrounded > len(assumptions) / 2:
            return GuardResult(
                passed=False,
                action="clarify",
                clarification_question=(
                    "Before I proceed: I'm making some assumptions that may not match "
                    "what you said. Is that what you meant?"
                ),
                notes=[f"{ungrounded}/{len(assumptions)} assumptions ungrounded"],
            )

    # Check 2 — goal paraphrase similarity
    goal_words = _extract_content_words(ctx.solver_decision.user_goal)
    union = user_words | goal_words
    intersection = user_words & goal_words
    similarity = len(intersection) / len(union) if union else 1.0
    if similarity < 0.15:
        return GuardResult(
            passed=False,
            action="clarify",
            clarification_question=(
                "Before I proceed: my understanding of your goal seems quite different "
                "from what you said. Is that what you meant?"
            ),
            notes=[f"goal paraphrase Jaccard {similarity:.2f} below 0.15"],
        )

    # Check 3 — entity cross-check
    if ctx.entity_ctx.matched_projects:
        goal_lower = ctx.solver_decision.user_goal.lower()
        any_mentioned = any(
            p.label.lower() in goal_lower for p in ctx.entity_ctx.matched_projects
        )
        if not any_mentioned and ctx.solver_decision.execution_mode.value == "direct_action":
            return GuardResult(
                passed=False,
                action="clarify",
                clarification_question=(
                    "Before I proceed: you mentioned a project but my plan doesn't "
                    "reference it. Is that what you meant?"
                ),
                notes=["entity cross-check failed: matched projects not in solver goal"],
            )

    # Check 4 — confidence
    if ctx.solver_decision.confidence < 0.7:
        return GuardResult(
            passed=False,
            action="clarify",
            clarification_question=(
                "Before I proceed: I'm not fully confident I understood correctly. "
                "Is that what you meant?"
            ),
            notes=[f"solver confidence {ctx.solver_decision.confidence:.2f} below 0.7"],
        )

    return GuardResult(passed=True, notes=["all understanding checks passed"])


# ---------------------------------------------------------------------------
# Guard 3: response relevance
# ---------------------------------------------------------------------------


def guard_response_relevance(ctx: GuardContext) -> GuardResult:
    if not _GUARD_PIPELINE_ENABLED or not _GUARD_RELEVANCE_ENABLED:
        return GuardResult(passed=True)
    if ctx.response_content is None or len(ctx.response_content) < 50:
        return GuardResult(passed=True)

    user_words = _extract_content_words(ctx.message.text)
    response_words = _extract_content_words(ctx.response_content)

    flagged = False
    notes: list[str] = []

    if len(user_words) >= 3 and response_words:
        overlap = len(user_words & response_words)
        fraction = overlap / len(user_words)
        if fraction < 0.25:
            flagged = True
            notes.append(f"topic match {fraction:.2f} below 0.25")

    if ctx.entity_ctx.matched_projects:
        response_lower = ctx.response_content.lower()
        entity_mentioned = any(
            p.label.lower() in response_lower for p in ctx.entity_ctx.matched_projects
        )
        if not entity_mentioned:
            flagged = True
            notes.append("matched project entities absent from response")

    if flagged:
        note = "I may have misunderstood your question."
        if ctx.entity_ctx.matched_projects:
            labels = ", ".join(p.label for p in ctx.entity_ctx.matched_projects[:2])
            first_label = ctx.entity_ctx.matched_projects[0].label
            note += (
                f" Did you mean to ask about your {labels} project?"
                f" Try `/project info {first_label}`."
            )
        return GuardResult(
            passed=False,
            action="proceed",
            notes=notes,
            short_circuit_response=note,
        )

    return GuardResult(passed=True)
