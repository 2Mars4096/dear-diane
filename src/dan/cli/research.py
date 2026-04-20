"""dan-research — local deep-research product CLI built on the universal worker."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Sequence, TypeVar

from pydantic import BaseModel, Field

from dan.cli import load_env, normalize_workspace_root, resolve_config
from dan.cli.research_product import (
    RESEARCH_PRODUCT_NAME,
    ResearchAuditIssue,
    ResearchCliSession,
    ResearchOrganismReport,
    ResearchProductConfig,
    ResearchProductPaths,
    ResearchQualityGate,
    append_research_product_transcript,
    load_research_product_config,
    load_research_product_session,
    resolve_research_product_paths,
    save_research_product_session,
    write_research_product_config,
)
from dan.providers import LLMProvider
from dan.providers.factory import build_provider_registry
from dan.server.runtime_config import build_engine_config_from_env
from dan.worker.organism_log import (
    ORGANISM_LOG_SCHEMA_VERSION,
    OrganismLogContext,
    OrganismLogWriter,
    new_trace_id,
)
from dan.worker.organisms import (
    MAX_DEEP_RESEARCH_READERS,
    ProjectExecutionTask,
    ResearchConversationContext,
    ResearchConversationController,
    ResearchConversationEvidenceTarget,
    ResearchConversationFacts,
    ResearchConversationIntentionPlan,
    ResearchConversationMessage,
    ResearchConversationReportSummary,
    ResearchConversationReviewDecision,
    ResearchConversationSubproblem,
    ResearchConversationWorkstream,
    ResearchConversationTurnDecision,
    run_deep_research_organ_live,
)
from dan.worker.organisms.research_conversation import (
    _allows_best_effort_final_closure,
    _fallback_intention_plan,
    _fallback_review_decision,
    _fallback_turn_decision,
)
from dan.worker.signaling import EvidenceRef

DEFAULT_RESEARCH_OBJECTIVE = (
    "Investigate the objective, ground it in available evidence, and return a bounded "
    "deep-research report."
)
DEFAULT_RESEARCH_ACCEPTANCE_CRITERIA = [
    "Ground the report in the strongest available evidence.",
    "Return explicit evidence refs, contradictions, open questions, confidence, and a recommended next action.",
    "When concrete external entities materially affect the conclusion, verify their current identity, status, version, or availability from an authoritative source or mark them explicitly unverified.",
    "Include a compact verification appendix for the critical facts, marking each one as verified, unverified, or conflicted.",
    "Include a compact audit appendix for unresolved logic, freshness, source-authority, methodology, or scope-fit gaps, and mark the overall report readiness explicitly.",
    "Before broad search, choose a temporal frame (current-as-of-runtime, historical snapshot, trend over time, or timeless/default), anchor relative-time language to the runtime date/timezone when relevant, and keep the report consistent with that frame.",
    "Exclusivity, absence, availability, or status claims must state the searched scope/universe and use primary/authoritative sources when possible.",
    "Material numeric conflicts should be reconciled with an explicit method when possible, such as deriving totals from components before accepting vendor aggregates.",
    "When important facts remain available only through proxies or cannot be verified in public sources, say so directly and make the recommendation provisional instead of launching endless follow-up passes.",
    "Keep the report bounded and inspectable.",
]
DEFAULT_RESEARCH_MAX_SUPERVISION_LOOPS = 3
DEFAULT_RESEARCH_HARD_CONSTRAINTS = [
    "Stay bounded and inspectable.",
    "Prefer direct evidence over speculation.",
]
DEFAULT_RESEARCH_SOFT_CONSTRAINTS = [
    "Prefer the smallest sufficient search surface that still grounds the answer.",
]
DEFAULT_RESEARCH_TOOL_IDS = [
    "web_search",
    "file_read",
    "list_directory",
]
_RESEARCH_TOOL_EXCLUSIONS = frozenset(
    {
        "file_edit",
        "file_write",
        "file_delete",
        "file_move",
        "file_copy",
        "shell_command",
        "python_eval",
        "git_commit",
        "git_branch",
        "git_worktree",
    }
)
_DEPTH_PROFILES: dict[str, dict[str, int | None]] = {
    "shallow": {
        "max_tool_rounds": 4,
        "max_tool_calls": 12,
        "max_runtime_seconds": 60,
    },
    "standard": {
        "max_tool_rounds": 8,
        "max_tool_calls": 24,
        "max_runtime_seconds": 120,
    },
    "deep": {
        "max_tool_rounds": 12,
        "max_tool_calls": 48,
        "max_runtime_seconds": 180,
    },
}
_TIME_AWARENESS_POLICY = (
    "Before launching research, classify the request into current-as-of-runtime, "
    "historical snapshot, trend over time, or timeless/default. Anchor relative "
    "terms like current/latest/recent/today to the supplied current_date and "
    "timezone, and do not silently mix current and historical context."
)
_TEMPORAL_RELATIVE_HINTS = (
    "current",
    "currently",
    "latest",
    "today",
    "right now",
    "recent",
    "recently",
    "now",
    "up to date",
)
_TEMPORAL_TREND_HINTS = (
    "trend",
    "over time",
    "recent change",
    "change over",
    "timeline",
    "year over year",
    "month over month",
    "quarter over quarter",
)
_CONTROL_STAGE_RESULT = TypeVar("_CONTROL_STAGE_RESULT")
_TEMPORAL_TREND_PATTERNS = (
    r"\blast\s+\d+",
    r"\bpast\s+\d+",
    r"\bover\s+the\s+past\b",
    r"\bfrom\s+.+\bto\s+.+",
    r"\bbetween\s+.+\band\b.+",
)
_TEMPORAL_SNAPSHOT_HINTS = (
    "as of",
    "during ",
    "at the time",
    "back in",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dan-research",
        description=(
            "Run DAN Research as a local deep-research product on top of the universal "
            "worker surface. Omit the objective to start an interactive session. "
            "Per-workspace state is kept under .dan-research/."
        ),
    )
    parser.add_argument(
        "objective",
        nargs="?",
        default=None,
        help="Bounded research objective. If omitted, start the interactive research CLI.",
    )
    parser.add_argument(
        "--task-id",
        default="deep-research-task",
        help="Task identifier recorded in the research trace.",
    )
    parser.add_argument(
        "--organism-id",
        default="reference-project-execution",
        help="Internal organism identifier stamped into the worker trace.",
    )
    parser.add_argument(
        "--delivery-target",
        default="research memo",
        help="Delivery target attached to the research task.",
    )
    parser.add_argument(
        "--acceptance-criterion",
        dest="acceptance_criteria",
        action="append",
        default=[],
        help="Append one acceptance criterion.",
    )
    parser.add_argument(
        "--hard-constraint",
        dest="hard_constraints",
        action="append",
        default=[],
        help="Append one hard constraint.",
    )
    parser.add_argument(
        "--soft-constraint",
        dest="soft_constraints",
        action="append",
        default=[],
        help="Append one soft constraint.",
    )
    parser.add_argument(
        "--evidence-summary",
        dest="evidence_summaries",
        action="append",
        default=[],
        help="Append one compact evidence summary note.",
    )
    parser.add_argument(
        "--reader-brief",
        dest="reader_briefs",
        action="append",
        default=[],
        help="Optional explicit reader brief. Repeat to seed multiple reader roles.",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="Directory for generated evidence note files. Defaults to .dan-research/runs inside the workspace.",
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root for local tool calls. Defaults to DAN_WORKSPACE_ROOT or the current directory.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Live model. Falls back to workspace product config, .env, then runtime defaults.",
    )
    parser.add_argument(
        "--api-key",
        help="Optional default-provider API key override.",
    )
    parser.add_argument(
        "--base-url",
        help="Optional default-provider base URL override.",
    )
    parser.add_argument(
        "--tool",
        dest="tool_ids",
        action="append",
        default=[],
        help="Enable one read-only local tool. Repeat to override the default research basket.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List the available local research tools and exit.",
    )
    parser.add_argument(
        "--research-readers",
        type=int,
        default=None,
        help=(
            f"Override research reader fan-out (1-{MAX_DEEP_RESEARCH_READERS}). "
            "By default the product auto-sizes reader count from task breadth."
        ),
    )
    parser.add_argument(
        "--depth",
        choices=sorted(_DEPTH_PROFILES),
        default=None,
        help=(
            "Research depth profile. Maps onto tool-loop budgets and falls back to "
            "workspace config, DAN_RESEARCH_DEPTH, then standard."
        ),
    )
    parser.add_argument(
        "--thinking-mode",
        choices=["auto", "enabled", "disabled"],
        default=None,
        help=(
            "Provider thinking mode for live model calls. Falls back to workspace "
            "product config, DAN_RESEARCH_THINKING_MODE, then auto."
        ),
    )
    parser.add_argument(
        "--quiet-progress",
        action="store_true",
        help="Disable live tool/progress rendering for non-JSON runs.",
    )
    parser.add_argument(
        "--show-model-trace",
        action="store_true",
        help=(
            "Show public model request/response previews in CLI progress output. "
            "This does not expose hidden chain-of-thought."
        ),
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize or refresh the workspace-local .dan-research/config.json and exit.",
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Show the resolved DAN Research product configuration and exit.",
    )
    parser.add_argument(
        "--session-file",
        help="Optional session file override. Relative paths resolve from the workspace root.",
    )
    parser.add_argument(
        "--new-session",
        action="store_true",
        help="Ignore any saved session and start from a fresh one.",
    )
    parser.add_argument(
        "--no-session-persist",
        action="store_true",
        help="Do not load or save workspace-local session state.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=None,
        help="Optional maximum provider tool rounds per worker completion. Overrides --depth when set.",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=None,
        help="Optional maximum provider tool calls per worker completion. Overrides --depth when set.",
    )
    parser.add_argument(
        "--max-supervision-loops",
        type=int,
        default=None,
        help=(
            "Optional cap on bounded research passes per orchestrated turn. "
            "Use 0 for unbounded; by default DAN Research keeps looping until "
            "the report is ready, clarification is needed, or a run fails."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full structured research report or config payload as JSON.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the JSON report.",
    )
    return parser


def _dedupe(values: Sequence[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _non_empty(values: Sequence[str], defaults: Sequence[str]) -> list[str]:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return cleaned or list(defaults)


def _plan_evidence_target_note(
    target: ResearchConversationEvidenceTarget,
    *,
    prefix: str = "Target",
) -> str:
    parts = [f"{prefix} {str(target.target_id or '').strip() or 'target'}: {str(target.claim).strip()}"]
    if str(target.entity_type or "").strip():
        parts.append(f"Entity type: {str(target.entity_type).strip()}")
    if str(target.metric_kind or "").strip():
        parts.append(f"Metric kind: {str(target.metric_kind).strip()}")
    if str(target.series_kind or "").strip():
        parts.append(f"Series kind: {str(target.series_kind).strip()}")
    if str(target.comparison_basis or "").strip():
        parts.append(f"Comparison basis: {str(target.comparison_basis).strip()}")
    if str(target.as_of or "").strip():
        parts.append(f"As of: {str(target.as_of).strip()}")
    if str(target.unit_or_format or "").strip():
        parts.append(f"Format/unit: {str(target.unit_or_format).strip()}")
    if str(target.geography_or_scope or "").strip():
        parts.append(f"Scope: {str(target.geography_or_scope).strip()}")
    families = [str(item).strip() for item in list(target.accepted_source_families or []) if str(item).strip()]
    if families:
        parts.append(f"Accepted sources: {', '.join(families[:3])}")
    sites = [str(item).strip() for item in list(target.preferred_sites or []) if str(item).strip()]
    if sites:
        parts.append(f"Preferred sites: {', '.join(sites[:4])}")
    aliases = [str(item).strip() for item in list(target.aliases or []) if str(item).strip()]
    if aliases:
        parts.append(f"Aliases: {', '.join(aliases[:5])}")
    if str(target.acceptable_proxy or "").strip():
        parts.append(f"Proxy rule: {str(target.acceptable_proxy).strip()}")
    if str(target.stop_condition or "").strip():
        parts.append(f"Stop when: {str(target.stop_condition).strip()}")
    if str(target.not_found_guidance or "").strip():
        parts.append(f"If still missing: {str(target.not_found_guidance).strip()}")
    if str(target.not_available_guidance or "").strip():
        parts.append(f"If unavailable: {str(target.not_available_guidance).strip()}")
    return " ".join(parts)


def _evidence_targets_by_problem_id(
    plan: ResearchConversationIntentionPlan | None,
) -> dict[str, list[ResearchConversationEvidenceTarget]]:
    mapping: dict[str, list[ResearchConversationEvidenceTarget]] = {}
    if plan is None:
        return mapping
    for target in plan.evidence_targets:
        for problem_id in list(target.related_subproblem_ids or []):
            key = str(problem_id or "").strip()
            if not key:
                continue
            mapping.setdefault(key, []).append(target)
    return mapping


def _plan_subproblem_brief(
    subproblem: ResearchConversationSubproblem,
    *,
    index: int,
    related_targets: Sequence[ResearchConversationEvidenceTarget] = (),
) -> str:
    parts = [
        f"Subproblem {str(subproblem.problem_id or f'problem-{index}').strip()}: {str(subproblem.question).strip()}",
        f"Why it matters: {str(subproblem.why_it_matters).strip()}",
        f"Evidence to seek: {str(subproblem.evidence_to_seek).strip()}",
    ]
    search_hint = str(subproblem.search_hint or "").strip()
    if search_hint:
        parts.append(f"Search hint: {search_hint}")
    depends_on = [str(item).strip() for item in list(subproblem.depends_on or []) if str(item).strip()]
    if depends_on:
        parts.append(f"Depends on: {', '.join(depends_on)}")
    for target in list(related_targets or [])[:2]:
        parts.append(_plan_evidence_target_note(target))
    if len(list(related_targets or [])) > 2:
        parts.append(f"More targets: {len(list(related_targets or [])) - 2}")
    return " ".join(parts)


def _plan_workstream_brief(
    workstream: ResearchConversationWorkstream,
    *,
    index: int,
    subproblems_by_id: dict[str, ResearchConversationSubproblem],
    evidence_targets_by_problem_id: dict[str, list[ResearchConversationEvidenceTarget]],
) -> str:
    related = [
        subproblems_by_id[problem_id]
        for problem_id in list(workstream.subproblem_ids or [])
        if problem_id in subproblems_by_id
    ]
    related_targets: list[ResearchConversationEvidenceTarget] = []
    seen_target_ids: set[str] = set()
    for problem_id in list(workstream.subproblem_ids or []):
        for target in evidence_targets_by_problem_id.get(problem_id, []):
            target_id = str(target.target_id or "").strip() or str(target.claim or "").strip()
            if not target_id or target_id in seen_target_ids:
                continue
            seen_target_ids.add(target_id)
            related_targets.append(target)
    parts = [
        f"Workstream {str(workstream.stream_id or f'stream-{index}').strip()}: {str(workstream.goal or workstream.title).strip()}",
        f"Why it matters: {str(workstream.why_it_matters).strip()}",
    ]
    title = str(workstream.title or "").strip()
    if title and title != str(workstream.goal or "").strip():
        parts.append(f"Title: {title}")
    for related_index, subproblem in enumerate(related, start=1):
        parts.append(
            f"Stream task {related_index} ({str(subproblem.problem_id or '').strip() or f'problem-{related_index}'}): {str(subproblem.question).strip()}"
        )
        parts.append(f"Evidence to seek: {str(subproblem.evidence_to_seek).strip()}")
        search_hint = str(subproblem.search_hint or "").strip()
        if search_hint:
            parts.append(f"Search hint: {search_hint}")
        for target in evidence_targets_by_problem_id.get(str(subproblem.problem_id or "").strip(), [])[:1]:
            parts.append(_plan_evidence_target_note(target))
    aggregation_hint = str(workstream.aggregation_hint or "").strip()
    if aggregation_hint:
        parts.append(f"Aggregate by: {aggregation_hint}")
    if related_targets and not related:
        for target in related_targets[:2]:
            parts.append(_plan_evidence_target_note(target))
    return " ".join(parts)


def _plan_workstream_summary(
    workstream: ResearchConversationWorkstream,
    *,
    index: int,
    subproblems_by_id: dict[str, ResearchConversationSubproblem],
    evidence_targets_by_problem_id: dict[str, list[ResearchConversationEvidenceTarget]],
) -> str:
    related_questions = [
        str(subproblems_by_id[problem_id].question).strip()
        for problem_id in list(workstream.subproblem_ids or [])
        if problem_id in subproblems_by_id
    ]
    covers = "; ".join(question for question in related_questions if question)
    parts = [
        f"Workstream {str(workstream.stream_id or f'stream-{index}').strip()}: {str(workstream.goal or workstream.title).strip()}",
        f"Why: {str(workstream.why_it_matters).strip()}",
    ]
    if covers:
        parts.append(f"Covers: {covers}")
    aggregation_hint = str(workstream.aggregation_hint or "").strip()
    if aggregation_hint:
        parts.append(f"Aggregate by: {aggregation_hint}")
    stream_targets: list[str] = []
    seen_target_ids: set[str] = set()
    for problem_id in list(workstream.subproblem_ids or []):
        for target in evidence_targets_by_problem_id.get(problem_id, []):
            target_key = str(target.target_id or "").strip() or str(target.claim or "").strip()
            if not target_key or target_key in seen_target_ids:
                continue
            seen_target_ids.add(target_key)
            stream_targets.append(_plan_evidence_target_note(target, prefix="Fact target"))
    parts.extend(stream_targets[:2])
    if len(stream_targets) > 2:
        parts.append(f"More fact targets: {len(stream_targets) - 2}")
    return " ".join(parts)


def _plan_reader_briefs(
    plan: ResearchConversationIntentionPlan | None,
    *,
    fallback_reader_briefs: Sequence[str],
) -> list[str]:
    briefs: list[str] = []
    if plan is not None:
        targets_by_problem_id = _evidence_targets_by_problem_id(plan)
        subproblems_by_id = {
            str(subproblem.problem_id or "").strip(): subproblem
            for subproblem in plan.subproblems
            if str(subproblem.problem_id or "").strip()
        }
        if plan.workstreams:
            for index, workstream in enumerate(plan.workstreams, start=1):
                brief = _plan_workstream_brief(
                    workstream,
                    index=index,
                    subproblems_by_id=subproblems_by_id,
                    evidence_targets_by_problem_id=targets_by_problem_id,
                )
                if brief:
                    briefs.append(brief)
        elif plan.subproblems:
            for index, subproblem in enumerate(plan.subproblems, start=1):
                brief = _plan_subproblem_brief(
                    subproblem,
                    index=index,
                    related_targets=targets_by_problem_id.get(
                        str(subproblem.problem_id or "").strip(),
                        [],
                    ),
                )
                if brief:
                    briefs.append(brief)
    if briefs:
        return _dedupe(briefs)
    briefs.extend(str(brief).strip() for brief in fallback_reader_briefs if str(brief).strip())
    return _dedupe(briefs)


def _plan_evidence_summaries(
    plan: ResearchConversationIntentionPlan | None,
) -> list[str]:
    if plan is None:
        return []
    summaries: list[str] = []
    targets_by_problem_id = _evidence_targets_by_problem_id(plan)
    if str(plan.answer_goal or "").strip():
        summaries.append(f"Answer goal: {str(plan.answer_goal).strip()}")
    if str(plan.plan_summary or "").strip():
        summaries.append(f"Plan summary: {str(plan.plan_summary).strip()}")
    subproblems_by_id = {
        str(subproblem.problem_id or "").strip(): subproblem
        for subproblem in plan.subproblems
        if str(subproblem.problem_id or "").strip()
    }
    for index, workstream in enumerate(plan.workstreams, start=1):
        summaries.append(
            _plan_workstream_summary(
                workstream,
                index=index,
                subproblems_by_id=subproblems_by_id,
                evidence_targets_by_problem_id=targets_by_problem_id,
            )
        )
    for index, subproblem in enumerate(plan.subproblems, start=1):
        summaries.append(
            _plan_subproblem_brief(
                subproblem,
                index=index,
                related_targets=targets_by_problem_id.get(
                    str(subproblem.problem_id or "").strip(),
                    [],
                ),
            )
        )
    for target in plan.evidence_targets:
        summaries.append(_plan_evidence_target_note(target, prefix="Fact target"))
    return _dedupe(summaries)


def _plan_payload(
    plan: ResearchConversationIntentionPlan | None,
) -> dict[str, Any]:
    if plan is None:
        return {}
    return {
        "answer_goal": str(plan.answer_goal or "").strip(),
        "refined_objective": str(plan.refined_objective or "").strip(),
        "plan_summary": str(plan.plan_summary or "").strip(),
        "subproblem_count": len(plan.subproblems),
        "workstream_count": len(plan.workstreams),
        "subproblems": [
            {
                "problem_id": str(item.problem_id or "").strip(),
                "question": str(item.question or "").strip(),
                "why_it_matters": str(item.why_it_matters or "").strip(),
                "evidence_to_seek": str(item.evidence_to_seek or "").strip(),
                "search_hint": str(item.search_hint or "").strip(),
                "depends_on": [
                    str(dep).strip()
                    for dep in list(item.depends_on or [])
                    if str(dep).strip()
                ],
            }
            for item in plan.subproblems
        ],
        "workstreams": [
            {
                "stream_id": str(item.stream_id or "").strip(),
                "title": str(item.title or "").strip(),
                "goal": str(item.goal or "").strip(),
                "why_it_matters": str(item.why_it_matters or "").strip(),
                "subproblem_ids": [
                    str(problem_id).strip()
                    for problem_id in list(item.subproblem_ids or [])
                    if str(problem_id).strip()
                ],
                "aggregation_hint": str(item.aggregation_hint or "").strip(),
            }
            for item in plan.workstreams
        ],
        "evidence_target_count": len(plan.evidence_targets),
        "evidence_targets": [
            {
                "target_id": str(item.target_id or "").strip(),
                "claim": str(item.claim or "").strip(),
                "entity_type": str(item.entity_type or "").strip(),
                "metric_kind": str(item.metric_kind or "").strip(),
                "series_kind": str(item.series_kind or "").strip(),
                "comparison_basis": str(item.comparison_basis or "").strip(),
                "why_it_matters": str(item.why_it_matters or "").strip(),
                "related_subproblem_ids": [
                    str(problem_id).strip()
                    for problem_id in list(item.related_subproblem_ids or [])
                    if str(problem_id).strip()
                ],
                "as_of": str(item.as_of or "").strip(),
                "unit_or_format": str(item.unit_or_format or "").strip(),
                "geography_or_scope": str(item.geography_or_scope or "").strip(),
                "accepted_source_families": [
                    str(source).strip()
                    for source in list(item.accepted_source_families or [])
                    if str(source).strip()
                ],
                "preferred_sites": [
                    str(site).strip()
                    for site in list(item.preferred_sites or [])
                    if str(site).strip()
                ],
                "aliases": [
                    str(alias).strip()
                    for alias in list(item.aliases or [])
                    if str(alias).strip()
                ],
                "acceptable_proxy": str(item.acceptable_proxy or "").strip(),
                "stop_condition": str(item.stop_condition or "").strip(),
                "not_found_guidance": str(item.not_found_guidance or "").strip(),
                "not_available_guidance": str(item.not_available_guidance or "").strip(),
            }
            for item in plan.evidence_targets
        ],
    }


def _planned_reader_count(
    plan: ResearchConversationIntentionPlan | None,
    *,
    base_requested_count: int | None,
    continuation_index: int,
    previous_selected_count: int | None,
) -> int | None:
    provisional = _continuation_reader_count(
        base_requested_count=base_requested_count,
        continuation_index=continuation_index,
        previous_selected_count=previous_selected_count,
    )
    if base_requested_count is not None:
        return provisional
    planned_width = 0
    if plan is not None:
        if plan.workstreams:
            planned_width = len(plan.workstreams)
        elif plan.subproblems:
            planned_width = len(plan.subproblems)
    if planned_width <= 0:
        return provisional
    capped_width = min(MAX_DEEP_RESEARCH_READERS, max(1, planned_width))
    if provisional is None:
        return capped_width
    return max(1, min(provisional, capped_width))


def _resolve_user_path(value: str | Path, *, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _now_context() -> dict[str, str]:
    now = datetime.now().astimezone()
    return {
        "current_timestamp": now.isoformat(),
        "current_date": now.date().isoformat(),
        "timezone": str(now.tzinfo or ""),
    }


def _temporal_frame(
    *,
    objective: str,
    current_date: str,
    timezone_name: str,
) -> dict[str, str]:
    text = " ".join(str(objective or "").strip().split())
    lowered = text.lower()
    years = sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", text)))
    iso_dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    explicit_points = [*iso_dates, *years]
    has_relative = any(hint in lowered for hint in _TEMPORAL_RELATIVE_HINTS)
    has_trend = any(hint in lowered for hint in _TEMPORAL_TREND_HINTS) or any(
        re.search(pattern, lowered) for pattern in _TEMPORAL_TREND_PATTERNS
    )
    if len(explicit_points) >= 2 and any(
        token in lowered for token in ("compare", "change", "difference", "vs", "versus")
    ):
        has_trend = True
    has_snapshot = any(hint in lowered for hint in _TEMPORAL_SNAPSHOT_HINTS) or bool(
        explicit_points
    )

    mode = "timeless"
    anchor = ""
    window = ""
    if has_trend:
        mode = "trend"
        anchor = current_date if has_relative or not explicit_points else explicit_points[-1]
        if len(explicit_points) >= 2:
            window = f"{explicit_points[0]} -> {explicit_points[-1]}"
        elif explicit_points:
            window = explicit_points[0]
        elif has_relative:
            window = f"runtime -> {current_date}"
    elif has_relative:
        mode = "current"
        anchor = current_date
    elif has_snapshot:
        mode = "historical_snapshot"
        anchor = explicit_points[-1] if explicit_points else ""

    if mode == "current":
        guidance = (
            f"Temporal frame: current-as-of-runtime. Anchor relative-time language to "
            f"{current_date} ({timezone_name}). Prefer the freshest authoritative sources "
            "available and carry explicit as-of dates for material claims."
        )
    elif mode == "historical_snapshot":
        guidance = (
            "Temporal frame: historical snapshot. Stay centered on the requested period "
            f"({anchor or 'target date not explicit'}), do not silently replace it with "
            "newer data, and label any later context as later context."
        )
    elif mode == "trend":
        guidance = (
            f"Temporal frame: trend over time. Use dated observations across the requested "
            f"window ({window or 'window inferred from the prompt'}) and treat "
            f"{anchor or current_date} ({timezone_name}) as the endpoint unless the prompt "
            "explicitly names another endpoint."
        )
    else:
        guidance = (
            "Temporal frame: timeless/default. If a claim turns out to depend on current "
            "state, promote that claim to current-as-of-runtime and attach an explicit "
            "as-of date instead of leaving it implicit."
        )
    return {
        "mode": mode,
        "anchor": anchor,
        "window": window,
        "guidance": guidance,
    }


def _truncate_text(value: str, *, limit: int = 160) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _research_tool_ids(values: Sequence[str]) -> list[str]:
    return [
        tool_id
        for tool_id in _dedupe(values)
        if str(tool_id or "").strip() and str(tool_id).strip() not in _RESEARCH_TOOL_EXCLUSIONS
    ]


def _effective_research_tool_ids(
    explicit: Sequence[str],
    *,
    product_config: ResearchProductConfig | None = None,
) -> list[str]:
    if any(str(value or "").strip() for value in explicit):
        return _research_tool_ids(explicit)
    if product_config and product_config.default_tool_ids:
        return _research_tool_ids(product_config.default_tool_ids)
    return list(DEFAULT_RESEARCH_TOOL_IDS)


def _effective_acceptance_criteria(
    explicit: Sequence[str],
    *,
    product_config: ResearchProductConfig | None = None,
) -> list[str]:
    if any(str(value or "").strip() for value in explicit):
        return _non_empty(explicit, DEFAULT_RESEARCH_ACCEPTANCE_CRITERIA)
    if product_config and product_config.acceptance_criteria:
        return _non_empty(
            product_config.acceptance_criteria,
            DEFAULT_RESEARCH_ACCEPTANCE_CRITERIA,
        )
    return list(DEFAULT_RESEARCH_ACCEPTANCE_CRITERIA)


def _effective_delivery_target(
    explicit: str | None,
    *,
    product_config: ResearchProductConfig | None = None,
) -> str:
    text = str(explicit or "").strip()
    if text:
        return text
    if product_config and str(product_config.delivery_target or "").strip():
        return str(product_config.delivery_target).strip()
    return "research memo"


def _effective_thinking_mode(
    requested: str | None,
    *,
    product_config: ResearchProductConfig | None = None,
) -> str:
    candidate = str(requested or "").strip()
    if not candidate and product_config is not None:
        candidate = str(product_config.thinking_mode or "").strip()
    if not candidate:
        candidate = str(os.environ.get("DAN_RESEARCH_THINKING_MODE") or "").strip()
    normalized = (candidate or "auto").lower()
    if normalized not in {"auto", "enabled", "disabled"}:
        raise ValueError(
            "DAN Research thinking mode must be one of: auto, enabled, disabled"
        )
    return normalized


def _effective_depth_profile(
    requested: str | None,
    *,
    product_config: ResearchProductConfig | None = None,
) -> str:
    candidate = str(requested or "").strip().lower()
    if not candidate and product_config is not None:
        candidate = str(product_config.depth_profile or "").strip().lower()
    if not candidate:
        candidate = str(os.environ.get("DAN_RESEARCH_DEPTH") or "").strip().lower()
    normalized = candidate or "standard"
    if normalized not in _DEPTH_PROFILES:
        raise ValueError(
            "DAN Research depth must be one of: shallow, standard, deep"
        )
    return normalized


def _depth_budget(
    depth_profile: str,
    *,
    requested_depth: str | None,
    explicit_rounds: int | None,
    explicit_calls: int | None,
    product_config: ResearchProductConfig | None = None,
) -> tuple[int | None, int]:
    profile = _DEPTH_PROFILES[depth_profile]
    if explicit_rounds is not None:
        max_tool_rounds = None if int(explicit_rounds) <= 0 else int(explicit_rounds)
    elif (
        not str(requested_depth or "").strip()
        and product_config
        and product_config.max_tool_rounds is not None
        and explicit_calls is None
    ):
        max_tool_rounds = (
            None
            if int(product_config.max_tool_rounds) <= 0
            else int(product_config.max_tool_rounds)
        )
    else:
        max_tool_rounds = profile["max_tool_rounds"]

    if explicit_calls is not None:
        max_tool_calls = int(explicit_calls)
    elif (
        not str(requested_depth or "").strip()
        and product_config
        and explicit_rounds is None
    ):
        max_tool_calls = int(product_config.max_tool_calls)
    else:
        max_tool_calls = int(profile["max_tool_calls"] or 24)
    return max_tool_rounds, max_tool_calls


def _depth_runtime_budget(depth_profile: str) -> int | None:
    raw = str(os.environ.get("DAN_RESEARCH_MAX_RUNTIME_SECONDS") or "").strip()
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            parsed = 0
        if parsed <= 0:
            return None
        return parsed
    value = _DEPTH_PROFILES[depth_profile].get("max_runtime_seconds")
    return None if value is None else int(value)


def _effective_research_reader_count(
    requested_count: int | None,
    *,
    product_config: ResearchProductConfig | None = None,
) -> int | None:
    if requested_count is not None:
        return int(requested_count)
    if product_config and product_config.research_reader_count is not None:
        return int(product_config.research_reader_count)
    return None


def _effective_max_supervision_loops(
    requested_count: int | None,
    *,
    product_config: ResearchProductConfig | None = None,
) -> int | None:
    candidate: Any = requested_count
    if candidate is None and product_config is not None:
        candidate = product_config.max_supervision_loops
    if candidate is None:
        candidate = os.environ.get("DAN_RESEARCH_MAX_SUPERVISION_LOOPS")
    if candidate is None:
        return DEFAULT_RESEARCH_MAX_SUPERVISION_LOOPS
    if isinstance(candidate, int):
        value = candidate
    else:
        text = str(candidate).strip().lower()
        if not text or text in {"none", "null", "unbounded", "unlimited", "inf", "infinite"}:
            return None
        try:
            value = int(text)
        except ValueError as exc:
            raise ValueError(
                "DAN Research max supervision loops must be a positive integer or 0 for unbounded"
            ) from exc
    if value == 0:
        return None
    if value < 0:
        raise ValueError(
            "DAN Research max supervision loops must be a positive integer or 0 for unbounded"
        )
    return value


def _continuation_reader_count(
    *,
    base_requested_count: int | None,
    continuation_index: int,
    previous_selected_count: int | None,
) -> int | None:
    if base_requested_count is not None or continuation_index <= 0:
        return base_requested_count
    try:
        configured_cap = int(
            str(
                os.environ.get("DAN_RESEARCH_CONTINUATION_MAX_READERS")
                or min(4, MAX_DEEP_RESEARCH_READERS)
            ).strip()
        )
    except ValueError:
        configured_cap = min(4, MAX_DEEP_RESEARCH_READERS)
    cap = min(MAX_DEEP_RESEARCH_READERS, max(1, configured_cap))
    previous = previous_selected_count or MAX_DEEP_RESEARCH_READERS
    return max(1, min(previous, cap))


def _resolve_live_model(
    requested_model: str | None,
    *,
    product_config: ResearchProductConfig | None = None,
) -> str:
    text = str(requested_model or "").strip()
    if text:
        return text
    if product_config:
        configured = str(product_config.default_model or "").strip()
        if configured:
            return configured
    config = resolve_config()
    env_model = str(config.get("model") or "").strip()
    if env_model:
        return env_model
    engine_config = build_engine_config_from_env()
    fallback = str(engine_config.llm_default_model or "").strip()
    if fallback and fallback != "stub-model":
        return fallback
    raise ValueError(
        "DAN Research requires --model, a .dan-research default_model, or a configured DAN_MODEL/DAN_LLM_MODEL"
    )


def _maybe_resolve_live_model(
    requested_model: str | None,
    *,
    product_config: ResearchProductConfig | None = None,
) -> str | None:
    try:
        return _resolve_live_model(requested_model, product_config=product_config)
    except Exception:
        return None


def _build_live_provider(model: str, *, api_key: str | None, base_url: str | None) -> LLMProvider:
    engine_config = build_engine_config_from_env()
    if api_key:
        engine_config.llm_api_key = api_key
    if base_url:
        engine_config.llm_base_url = base_url
    registry = build_provider_registry(engine_config)
    return registry.resolve(model)


def _provider_request_overrides_for_thinking_mode(
    thinking_mode: str,
) -> dict[str, Any]:
    normalized = str(thinking_mode or "auto").strip().lower()
    if normalized == "auto":
        return {}
    return {"thinking": {"type": normalized}}


def _build_evidence_refs(
    workdir: Path,
    evidence_summaries: Sequence[str],
) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    workdir.mkdir(parents=True, exist_ok=True)
    for index, summary in enumerate(evidence_summaries, start=1):
        text = str(summary or "").strip()
        if not text:
            continue
        locator = workdir / f"evidence-{index}.md"
        locator.write_text(f"# evidence-{index}\n\n{text}\n", encoding="utf-8")
        refs.append(
            EvidenceRef(
                ref_id=f"research-brief:evidence-{index}",
                label=f"Evidence {index}",
                summary=text,
                source="research-cli",
                locator=str(locator),
            )
        )
    return refs


def _build_research_task(
    workdir: Path,
    *,
    task_id: str,
    objective: str,
    temporal_mode: str,
    temporal_anchor: str,
    temporal_window: str,
    temporal_guidance: str,
    acceptance_criteria: Sequence[str],
    delivery_target: str,
    hard_constraints: Sequence[str],
    soft_constraints: Sequence[str],
    evidence_summaries: Sequence[str],
) -> ProjectExecutionTask:
    return ProjectExecutionTask(
        task_id=task_id,
        objective=objective,
        temporal_mode=temporal_mode,
        temporal_anchor=temporal_anchor,
        temporal_window=temporal_window,
        temporal_guidance=temporal_guidance,
        acceptance_criteria=list(acceptance_criteria),
        delivery_target=delivery_target,
        hard_constraints=list(hard_constraints),
        soft_constraints=list(soft_constraints),
        evidence_refs=_build_evidence_refs(workdir, evidence_summaries),
    )


def _print_tool_catalog(*, as_json: bool) -> None:
    rows = [
        {
            "tool_id": tool_id,
            "category": (
                "web"
                if tool_id == "web_search"
                else "git"
                if tool_id.startswith("git_")
                else "file"
            ),
            "description": {
                "list_directory": "List files under the workspace root.",
                "file_read": "Read local files from the workspace.",
                "web_search": "Search the web and, when needed, ground the answer by fetching authoritative result pages.",
                "git_status": "Inspect git status for grounded repo context.",
                "git_diff": "Inspect repo diffs for grounded repo context.",
                "git_log": "Inspect recent commit history for grounded repo context.",
            }.get(tool_id, ""),
        }
        for tool_id in DEFAULT_RESEARCH_TOOL_IDS
    ]
    if as_json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    for row in rows:
        print(f"{row['tool_id']} [{row['category']}]")
        if row["description"]:
            print(f"  {row['description']}")


class ResearchProgressRenderer:
    """Render concise live progress for the research CLI."""

    def __init__(self, *, enabled: bool, show_model_trace: bool = False) -> None:
        self._enabled = bool(enabled)
        self._show_model_trace = bool(show_model_trace)
        self._open_model_streams: dict[tuple[str, str], bool] = {}
        self._completed_model_streams: dict[tuple[str, str], bool] = {}

    @staticmethod
    def _scope_label(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if "." in text:
            return text.split(".", 1)[1]
        return text

    def _stream_prefix(self, channel: str, event: dict[str, Any]) -> str:
        scope = self._scope_label(event.get("worker_id"))
        if scope:
            return f"[{scope}][{channel}]"
        return f"[{channel}]"

    @staticmethod
    def _stream_key(event: dict[str, Any]) -> tuple[str, str]:
        return (
            str(event.get("worker_id") or ""),
            str(event.get("round") or ""),
        )

    def _flush_open_model_streams(self) -> None:
        if not self._open_model_streams:
            return
        if any(self._open_model_streams.values()):
            print()
        self._completed_model_streams.update(self._open_model_streams)
        self._open_model_streams.clear()

    @staticmethod
    def _format_timeout_seconds(value: Any) -> str | None:
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            return None
        if timeout <= 0:
            return None
        if timeout.is_integer():
            return f"{int(timeout)}s"
        return f"{timeout:.1f}s"

    def __call__(self, event: dict[str, Any]) -> None:
        if not self._enabled:
            return
        name = str(event.get("event") or "")
        if name == "model.stream.started":
            if not self._show_model_trace:
                return
            key = self._stream_key(event)
            if key in self._open_model_streams:
                return
            self._completed_model_streams.pop(key, None)
            self._open_model_streams[key] = False
            return
        if name == "model.stream.delta":
            if not self._show_model_trace:
                return
            key = self._stream_key(event)
            if key not in self._open_model_streams or not self._open_model_streams[key]:
                prefix = self._stream_prefix("model", event)
                print(f"{prefix} stream: ", end="", flush=True)
            self._open_model_streams[key] = True
            delta = str(event.get("delta") or "").replace("\r", "").replace("\n", "\\n")
            if delta:
                print(delta, end="", flush=True)
            return
        if name == "model.stream.completed":
            if not self._show_model_trace:
                return
            key = self._stream_key(event)
            had_visible_output = bool(self._open_model_streams.pop(key, False))
            self._completed_model_streams[key] = had_visible_output
            if had_visible_output:
                print()
            return
        if name not in {"model.stream.started", "model.stream.delta"}:
            self._flush_open_model_streams()
        if name == "assistant.message":
            text = str(event.get("message") or "").strip()
            if text:
                print(f"[assistant] {text}")
            return
        if name == "research.started":
            objective = _truncate_text(str(event.get("objective") or ""), limit=180)
            readers = event.get("requested_reader_count")
            depth = str(event.get("depth_profile") or "standard")
            suffix = []
            if readers is not None:
                suffix.append(f"readers={readers}")
            suffix.append(f"depth={depth}")
            print(f"[status] starting research run: {objective} ({', '.join(suffix)})")
            return
        if name == "research.completed":
            status = str(event.get("status") or "completed")
            confidence = event.get("confidence")
            readers = event.get("selected_reader_count")
            extras: list[str] = []
            if readers is not None:
                extras.append(f"readers={readers}")
            if confidence is not None:
                extras.append(f"confidence={float(confidence):.2f}")
            suffix = f" ({', '.join(extras)})" if extras else ""
            print(f"[status] research run {status}{suffix}")
            return
        if name == "research.heartbeat":
            scope = self._scope_label(event.get("worker_id"))
            elapsed = int(event.get("elapsed_seconds") or 0)
            phase = str(event.get("phase") or "waiting").strip() or "waiting"
            detail = _truncate_text(str(event.get("detail") or ""), limit=140)
            prefix = f"[{scope}][status]" if scope else "[status]"
            message = f"{prefix} still running: {phase}"
            if elapsed > 0:
                message += f" {elapsed}s"
            if detail:
                message += f" — {detail}"
            print(message)
            return
        if name == "model.requested":
            if not self._show_model_trace:
                return
            scope = self._scope_label(event.get("worker_id"))
            prefix = f"[{scope}][model]" if scope else "[model]"
            extras: list[str] = [f"tools={event.get('tool_count', 0)}"]
            if event.get("message_count") is not None:
                extras.append(f"msgs={event.get('message_count')}")
            if event.get("total_input_chars") is not None:
                extras.append(f"chars={event.get('total_input_chars')}")
            if event.get("max_tokens") is not None:
                extras.append(f"max_tokens={event.get('max_tokens')}")
            timeout = self._format_timeout_seconds(event.get("request_timeout_seconds"))
            if timeout:
                extras.append(f"timeout={timeout}")
            if event.get("request_mode"):
                extras.append(f"mode={event.get('request_mode')}")
            if event.get("hedged"):
                extras.append("hedged=yes")
            if event.get("override_reasoning_enabled") is not None:
                extras.append(
                    "reasoning="
                    + ("enabled" if event.get("override_reasoning_enabled") else "disabled")
                )
            override_thinking = str(event.get("override_thinking_type") or "").strip()
            if override_thinking:
                extras.append(f"thinking={override_thinking}")
            print(
                f"{prefix} request: "
                f"round={event.get('round', '?')} "
                f"model={event.get('model') or '(unknown)'} "
                f"{' '.join(extras)}"
            )
            return
        if name == "model.responded":
            if not self._show_model_trace:
                return
            scope = self._scope_label(event.get("worker_id"))
            prefix = f"[{scope}][model]" if scope else "[model]"
            tool_calls = [
                str(tool_id).strip()
                for tool_id in (event.get("tool_calls") or [])
                if str(tool_id).strip()
            ]
            summary = (
                f"{prefix} response: "
                f"round={event.get('round', '?')} "
                f"model={event.get('model') or '(unknown)'}"
            )
            if tool_calls:
                summary += f" tool_calls={', '.join(tool_calls)}"
            finish_reason = str(event.get("finish_reason") or "").strip()
            if finish_reason:
                summary += f" finish_reason={finish_reason}"
            if event.get("usage_total_tokens") is not None:
                summary += f" tokens={event.get('usage_total_tokens')}"
            if event.get("usage_cached_input_tokens") is not None:
                summary += f" cached={event.get('usage_cached_input_tokens')}"
            if event.get("text_chars") is not None:
                summary += f" chars={event.get('text_chars')}"
            if event.get("hedged"):
                summary += " hedged=yes"
            print(summary)
            stream_key = self._stream_key(event)
            if event.get("streamed") and self._completed_model_streams.pop(stream_key, False):
                return
            text = _truncate_text(str(event.get("text") or ""), limit=220)
            if text:
                print(f"{prefix} preview: {text}")
            return
        if name == "tool.started":
            scope = self._scope_label(event.get("worker_id"))
            prefix = f"[{scope}][tool]" if scope else "[tool]"
            tool_id = str(event.get("tool_id") or "tool")
            arguments = dict(event.get("arguments") or {})
            preview = _truncate_text(
                json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str),
                limit=160,
            )
            print(f"{prefix} {tool_id}: {preview}")
            return
        if name == "tool.completed":
            scope = self._scope_label(event.get("worker_id"))
            prefix = f"[{scope}][tool]" if scope else "[tool]"
            tool_id = str(event.get("tool_id") or "tool")
            print(f"{prefix} ok {tool_id}")
            return
        if name in {"tool.failed", "tool.denied"}:
            scope = self._scope_label(event.get("worker_id"))
            prefix = f"[{scope}][tool]" if scope else "[tool]"
            tool_id = str(event.get("tool_id") or "tool")
            message = str(event.get("error") or "denied")
            print(f"{prefix} {name.split('.')[-1]} {tool_id}: {message}")


def _event_timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _emit_research_event(event_callback, event: dict[str, Any]) -> None:
    if event_callback is None:
        return
    event_callback(dict(event))


class ResearchEventLogger:
    """Persist timestamped DAN Research events to one JSONL file."""

    def __init__(
        self,
        *,
        path: Path,
        stream_kind: Literal["bounded_run", "control_plane"] = "bounded_run",
        session_id: str = "",
        turn_id: str = "",
        task_id: str = "",
        organism_id: str = "",
        organ_id: str = "",
        trace_id: str = "",
    ) -> None:
        self._writer = OrganismLogWriter(
            path=path,
            context=OrganismLogContext(
                product="dan_research",
                stream_kind=stream_kind,
                session_id=session_id,
                turn_id=turn_id,
                task_id=task_id,
                trace_id=trace_id,
                organism_id=organism_id,
                organ_id=organ_id,
            ),
        )
        self.path = self._writer.path

    def emit(self, event: dict[str, Any]) -> None:
        self._writer.emit(event)

    def emit_stage_records(self, stage_records: Sequence[dict[str, Any]]) -> None:
        self._writer.emit_stage_records(stage_records)

    def emit_trace_rows(self, trace_rows: Sequence[dict[str, Any]]) -> None:
        self._writer.emit_trace_rows(trace_rows)

    def update_context(self, **updates: Any) -> None:
        self._writer.update_context(**updates)

    def close(self) -> None:
        self._writer.close()


def _log_event(logger: ResearchEventLogger | None, event: str, **payload: Any) -> None:
    if logger is None:
        return
    logger.emit({"event": event, **payload})


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(0.0, float(raw.strip()))
    except (TypeError, ValueError):
        return default


class ResearchHeartbeatMonitor:
    """Emit sparse heartbeat events when a bounded research run goes quiet."""

    def __init__(
        self,
        *,
        event_callback,
        enabled: bool = True,
        idle_seconds: float = 10.0,
        repeat_seconds: float = 15.0,
        poll_seconds: float = 2.0,
    ) -> None:
        self._event_callback = event_callback
        self._enabled = bool(enabled) and event_callback is not None
        self._idle_seconds = max(0.01, float(idle_seconds))
        self._repeat_seconds = max(self._idle_seconds, float(repeat_seconds))
        self._poll_seconds = max(0.01, float(poll_seconds))
        self._last_activity = 0.0
        self._last_heartbeat = 0.0
        self._phase = "starting"
        self._worker_id = ""
        self._detail = ""
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    @staticmethod
    def _tool_detail(event: dict[str, Any]) -> str:
        tool_id = str(event.get("tool_id") or "").strip()
        arguments = dict(event.get("arguments") or {})
        query = str(arguments.get("query") or "").strip()
        url = str(arguments.get("url") or "").strip()
        if query:
            return _truncate_text(f"{tool_id}: {query}", limit=160)
        if url:
            return _truncate_text(f"{tool_id}: {url}", limit=160)
        return _truncate_text(tool_id, limit=160)

    def observe(self, event: dict[str, Any]) -> None:
        if not self._enabled:
            return
        name = str(event.get("event") or "").strip()
        if not name or name == "research.heartbeat":
            return
        loop = asyncio.get_running_loop()
        self._last_activity = loop.time()
        self._worker_id = str(event.get("worker_id") or "").strip()
        if name == "research.started":
            objective = _truncate_text(str(event.get("objective") or ""), limit=160)
            self._phase = "research"
            self._detail = objective
            return
        if name == "model.requested":
            self._phase = "model"
            self._detail = (
                f"round={event.get('round', '?')} "
                f"tools={event.get('tool_count', 0)}"
            )
            return
        if name == "model.responded":
            finish = str(event.get("finish_reason") or "stop").strip() or "stop"
            self._phase = "model-response"
            self._detail = f"finish_reason={finish}"
            return
        if name == "tool.started":
            self._phase = "tool"
            self._detail = self._tool_detail(event)
            return
        if name == "tool.completed":
            self._phase = "post-tool"
            self._detail = self._tool_detail(event)
            return
        if name in {"tool.failed", "tool.denied"}:
            self._phase = "tool-error"
            self._detail = _truncate_text(
                f"{self._tool_detail(event)}: {event.get('error') or name}",
                limit=160,
            )
            return
        if name == "research.completed":
            self._phase = "completed"
            self._detail = str(event.get("status") or "completed")

    def note_control_stage(
        self,
        *,
        phase: str,
        detail: str = "",
        worker_id: str = "",
    ) -> None:
        if not self._enabled:
            return
        loop = asyncio.get_running_loop()
        self._last_activity = loop.time()
        self._worker_id = str(worker_id or "").strip()
        self._phase = _assistant_text(phase) or "control"
        self._detail = _truncate_text(detail, limit=160)

    async def start(self) -> None:
        if not self._enabled or self._task is not None:
            return
        now = asyncio.get_running_loop().time()
        if self._last_activity <= 0:
            self._last_activity = now
        self._last_heartbeat = now
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self._poll_seconds)
            now = asyncio.get_running_loop().time()
            if (now - self._last_activity) < self._idle_seconds:
                continue
            if (now - self._last_heartbeat) < self._repeat_seconds:
                continue
            self._last_heartbeat = now
            _emit_research_event(
                self._event_callback,
                {
                    "event": "research.heartbeat",
                    "worker_id": self._worker_id,
                    "phase": self._phase,
                    "detail": self._detail,
                    "elapsed_seconds": int(now - self._last_activity),
                },
            )


def _control_stage_stall_seconds() -> float:
    return _env_float("DAN_RESEARCH_CONTROL_STAGE_STALL_SECONDS", 20.0)


def _control_stage_max_seconds() -> float:
    return _env_float("DAN_RESEARCH_CONTROL_STAGE_MAX_SECONDS", 180.0)


def _hedged_orchestrator_session(
    controller: ResearchConversationController,
    session: Any,
    *,
    stage_name: str,
):
    metadata = dict(getattr(session, "metadata", {}) or {})
    metadata.setdefault("surface", "dan-research")
    metadata["hedged_control_stage"] = stage_name
    if session is not None:
        metadata["hedged_from_session_id"] = getattr(session, "session_id", "")
    return controller.create_session(metadata=metadata)


def _fresh_control_stage_session(
    controller: ResearchConversationController,
    session: Any,
    *,
    stage_name: str,
    reason: str,
):
    metadata = {
        "surface": "dan-research",
        "control_stage_reset": stage_name,
        "control_stage_reset_reason": reason,
    }
    if session is not None:
        metadata["reset_from_session_id"] = getattr(session, "session_id", "")
    return controller.create_session(metadata=metadata)


def _should_refresh_control_stage_session(
    session: Any,
    fallback_session: Any,
) -> bool:
    if fallback_session is None:
        return True
    session_id = getattr(session, "session_id", None)
    fallback_session_id = getattr(fallback_session, "session_id", None)
    if session_id and fallback_session_id and session_id == fallback_session_id:
        return True
    if getattr(fallback_session, "active_message_id", None) is not None:
        return True
    if getattr(fallback_session, "status", None) == "running":
        return True
    if getattr(fallback_session, "closed_at", None) is not None:
        return True
    return False


def _recover_control_stage_session(
    *,
    controller: ResearchConversationController,
    session: Any,
    fallback_session: Any,
    stage_name: str,
    reason: str,
    control_logger: ResearchEventLogger | None,
    task_id: str | None,
    trace_id: str | None,
) -> Any:
    if not _should_refresh_control_stage_session(session, fallback_session):
        return fallback_session
    refreshed_session = _fresh_control_stage_session(
        controller,
        session,
        stage_name=stage_name,
        reason=reason,
    )
    _log_event(
        control_logger,
        f"orchestrator.{stage_name}.session.reset",
        task_id=task_id,
        trace_id=trace_id,
        reason=reason,
        prior_session_id=getattr(fallback_session or session, "session_id", None),
        new_session_id=getattr(refreshed_session, "session_id", None),
    )
    return refreshed_session


async def _cancel_pending_task(task: asyncio.Task[Any]) -> None:
    if task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _run_control_stage_with_hedge(
    *,
    stage_name: str,
    heartbeat_phase: str,
    detail: str,
    controller: ResearchConversationController,
    session,
    primary_call: Callable[[Any], Awaitable[tuple[_CONTROL_STAGE_RESULT, Any]]],
    fallback_result: Callable[[], tuple[_CONTROL_STAGE_RESULT, Any]],
    control_logger: ResearchEventLogger | None,
    heartbeat: ResearchHeartbeatMonitor | None = None,
    task_id: str | None = None,
    trace_id: str | None = None,
) -> tuple[_CONTROL_STAGE_RESULT, Any]:
    stall_seconds = _control_stage_stall_seconds()
    max_seconds = _control_stage_max_seconds()
    if heartbeat is not None:
        heartbeat.note_control_stage(phase=heartbeat_phase, detail=detail)

    if stall_seconds <= 0 or max_seconds <= 0:
        return await primary_call(session)

    loop = asyncio.get_running_loop()
    stage_started = loop.time()
    primary_task = asyncio.create_task(primary_call(session))
    primary_exception: Exception | None = None

    try:
        return await asyncio.wait_for(asyncio.shield(primary_task), timeout=stall_seconds)
    except asyncio.TimeoutError:
        backup_session = _hedged_orchestrator_session(
            controller,
            session,
            stage_name=stage_name,
        )
        backup_task = asyncio.create_task(primary_call(backup_session))
        _log_event(
            control_logger,
            f"orchestrator.{stage_name}.hedge.launched",
            task_id=task_id,
            trace_id=trace_id,
            stall_seconds=stall_seconds,
            max_seconds=max_seconds,
            detail=detail,
            primary_session_id=getattr(session, "session_id", None),
            backup_session_id=getattr(backup_session, "session_id", None),
        )
        if heartbeat is not None:
            heartbeat.note_control_stage(
                phase=heartbeat_phase,
                detail=f"{detail} (backup attempt launched)",
            )
        tasks: dict[asyncio.Task[Any], str] = {
            primary_task: "primary",
            backup_task: "backup",
        }
        exceptions: list[tuple[str, str]] = []
        deadline = stage_started + max_seconds
        winner_task: asyncio.Task[Any] | None = None
        winner_role = ""
        winner_result: tuple[_CONTROL_STAGE_RESULT, Any] | None = None

        try:
            while tasks:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                done, _pending = await asyncio.wait(
                    tuple(tasks.keys()),
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    break
                for task in done:
                    role = tasks.pop(task)
                    try:
                        result = task.result()
                    except asyncio.CancelledError:
                        continue
                    except Exception as exc:  # pragma: no cover - rare branch
                        exceptions.append((role, f"{type(exc).__name__}: {exc}"))
                        _log_event(
                            control_logger,
                            f"orchestrator.{stage_name}.hedge.failed",
                            task_id=task_id,
                            trace_id=trace_id,
                            attempt=role,
                            error_type=type(exc).__name__,
                            error=str(exc),
                        )
                        if role == "primary" and primary_exception is None:
                            primary_exception = exc
                        continue
                    winner_task = task
                    winner_role = role
                    winner_result = result
                    break
                if winner_result is not None:
                    break

            if winner_result is not None:
                _log_event(
                    control_logger,
                    f"orchestrator.{stage_name}.hedge.accepted",
                    task_id=task_id,
                    trace_id=trace_id,
                    accepted_attempt=winner_role,
                    detail=detail,
                )
                for task, role in list(tasks.items()):
                    await _cancel_pending_task(task)
                    _log_event(
                        control_logger,
                        f"orchestrator.{stage_name}.hedge.cancelled",
                        task_id=task_id,
                        trace_id=trace_id,
                        cancelled_attempt=role,
                    )
                return winner_result
        finally:
            if winner_task is None:
                for task in list(tasks.keys()):
                    await _cancel_pending_task(task)

        _log_event(
            control_logger,
            f"orchestrator.{stage_name}.fallback",
            task_id=task_id,
            trace_id=trace_id,
            reason="control_stage_timeout_or_failure",
            detail=detail,
            exceptions=exceptions,
        )
        if heartbeat is not None:
            heartbeat.note_control_stage(
                phase=heartbeat_phase,
                detail=f"{detail} (timed out; using fallback)",
            )
        fallback_value, fallback_session = fallback_result()
        fallback_session = _recover_control_stage_session(
            controller=controller,
            session=session,
            fallback_session=fallback_session,
            stage_name=stage_name,
            reason="timeout_or_failure",
            control_logger=control_logger,
            task_id=task_id,
            trace_id=trace_id,
        )
        return fallback_value, fallback_session
    except Exception as exc:
        await _cancel_pending_task(primary_task)
        _log_event(
            control_logger,
            f"orchestrator.{stage_name}.fallback",
            task_id=task_id,
            trace_id=trace_id,
            reason="control_stage_exception",
            detail=detail,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        fallback_value, fallback_session = fallback_result()
        fallback_session = _recover_control_stage_session(
            controller=controller,
            session=session,
            fallback_session=fallback_session,
            stage_name=stage_name,
            reason=f"exception:{type(exc).__name__}",
            control_logger=control_logger,
            task_id=task_id,
            trace_id=trace_id,
        )
        return fallback_value, fallback_session


class ResearchConversationOutcome(BaseModel):
    """Rendered outcome of one orchestrated DAN Research turn."""

    status: str
    assistant_messages: list[str] = Field(default_factory=list)
    question: str | None = None
    reports: list[ResearchOrganismReport] = Field(default_factory=list)
    response_kind: Literal["chat_reply", "report_reply", "clarification"] = "chat_reply"
    artifact_title: str = ""
    artifact_markdown: str = ""
    artifact_path: str | None = None


def _assistant_block_text(message: str) -> str:
    text = str(message or "")
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned_lines = [line.rstrip() for line in lines]
    while cleaned_lines and not cleaned_lines[0].strip():
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()
    return "\n".join(cleaned_lines)


def _assistant_text(message: str, *, preserve_formatting: bool = False) -> str:
    if preserve_formatting:
        return _assistant_block_text(message)
    return " ".join(str(message or "").strip().split())


def _report_context(report: ResearchOrganismReport) -> ResearchConversationReportSummary:
    return ResearchConversationReportSummary(
        status=report.status,
        task_id=report.task_id,
        objective=report.objective,
        temporal_mode=report.temporal_mode,
        temporal_anchor=report.temporal_anchor,
        temporal_window=report.temporal_window,
        temporal_guidance=report.temporal_guidance,
        delivery_target=report.delivery_target,
        findings=list(report.findings),
        evidence_summary=list(report.evidence_summary),
        evidence_refs=list(report.evidence_refs),
        contradictions=list(report.contradictions),
        open_questions=list(report.open_questions),
        verification_facts=[
            fact.model_dump(mode="json")
            if hasattr(fact, "model_dump")
            else dict(fact)
            for fact in report.verification_facts
        ],
        evidence_integrity=[
            row.model_dump(mode="json")
            if hasattr(row, "model_dump")
            else dict(row)
            for row in report.evidence_integrity
        ],
        audit_issues=[
            issue.model_dump(mode="json")
            if hasattr(issue, "model_dump")
            else dict(issue)
            for issue in report.audit_issues
        ],
        quality_gates=[
            gate.model_dump(mode="json")
            if hasattr(gate, "model_dump")
            else dict(gate)
            for gate in report.quality_gates
        ],
        report_readiness=report.report_readiness,
        artifact_mode=report.artifact_mode,
        readiness_note=report.readiness_note,
        confidence=report.confidence,
        recommended_change=report.recommended_change,
        error=report.error,
    )


def _report_finding(report: ResearchOrganismReport) -> str:
    findings = "; ".join(report.findings[:2]) or "(none)"
    return (
        "Current bounded research pass: "
        f"objective={report.objective}; "
        f"temporal_mode={report.temporal_mode}; "
        f"status={report.status}; "
        f"readiness={report.report_readiness}; "
        f"confidence={report.confidence if report.confidence is not None else '(unset)'}; "
        f"findings={findings}"
    )


def _set_report_quality_gate(
    report: ResearchOrganismReport,
    *,
    gate: str,
    status: str,
    summary: str,
    required_follow_up: str = "",
    evidence_ref: str = "",
    allow_downgrade: bool = False,
) -> None:
    severity_rank = {
        "not_applicable": -1,
        "pass": 0,
        "warn": 1,
        "fail": 2,
    }
    normalized_status = str(status or "").strip().lower()
    for item in report.quality_gates:
        if str(getattr(item, "gate", "") or "").strip() != gate:
            continue
        existing_status = str(getattr(item, "status", "") or "").strip().lower()
        if (
            not allow_downgrade
            and severity_rank.get(existing_status, 0) > severity_rank.get(normalized_status, 0)
        ):
            if required_follow_up and not str(item.required_follow_up or "").strip():
                item.required_follow_up = required_follow_up
            if evidence_ref and not str(item.evidence_ref or "").strip():
                item.evidence_ref = evidence_ref
            return
        item.status = normalized_status
        item.summary = summary
        item.required_follow_up = required_follow_up
        item.evidence_ref = evidence_ref
        return
    report.quality_gates.append(
        ResearchQualityGate(
            gate=gate,
            status=normalized_status,
            summary=summary,
            required_follow_up=required_follow_up,
            evidence_ref=evidence_ref,
        )
    )


def _append_report_audit_issue(
    report: ResearchOrganismReport,
    *,
    kind: str,
    severity: str,
    issue: str,
    affected_claim: str,
    required_follow_up: str,
) -> None:
    normalized_issue = " ".join(str(issue or "").strip().split())
    if not normalized_issue:
        return
    for item in report.audit_issues:
        if " ".join(str(getattr(item, "issue", "") or "").strip().split()) == normalized_issue:
            return
    report.audit_issues.append(
        ResearchAuditIssue(
            kind=kind,
            severity=severity,
            issue=normalized_issue,
            affected_claim=affected_claim,
            required_follow_up=required_follow_up,
        )
    )


def _mark_report_incomplete(
    report: ResearchOrganismReport,
    *,
    reason: str,
) -> None:
    follow_up = "Run another bounded research pass and include that pass before treating the artifact as finished."
    report.status = "incomplete"
    report.report_readiness = "blocked"
    report.artifact_mode = "blocker_report"
    if reason not in str(report.readiness_note or ""):
        report.readiness_note = (
            f"{str(report.readiness_note).strip()} {reason}".strip()
            if str(report.readiness_note or "").strip()
            else reason
        )
    _set_report_quality_gate(
        report,
        gate="final_status",
        status="fail",
        summary=reason,
        required_follow_up=follow_up,
    )
    _append_report_audit_issue(
        report,
        kind="logic",
        severity="major",
        issue=reason,
        affected_claim="final report closure",
        required_follow_up=follow_up,
    )


def _mark_report_provisional_close(
    report: ResearchOrganismReport,
    *,
    reason: str,
) -> None:
    follow_up = (
        "Treat the memo as provisional only: refresh the remaining stale or disputed "
        "facts before relying on it as a final report."
    )
    report.status = "completed"
    report.report_readiness = "provisional"
    report.artifact_mode = "provisional_report"
    if reason not in str(report.readiness_note or ""):
        report.readiness_note = (
            f"{str(report.readiness_note).strip()} {reason}".strip()
            if str(report.readiness_note or "").strip()
            else reason
        )
    _set_report_quality_gate(
        report,
        gate="final_status",
        status="warn",
        summary=reason,
        required_follow_up=follow_up,
        allow_downgrade=True,
    )


def _summarize_evidence_integrity(report: ResearchOrganismReport) -> str:
    counts = {
        "accepted": 0,
        "accepted_with_proxy": 0,
        "conflicted": 0,
        "unverifiable": 0,
        "pending": 0,
    }
    for row in report.evidence_integrity:
        key = str(getattr(row, "status", "") or "").strip().lower()
        if key in counts:
            counts[key] += 1
    parts: list[str] = []
    if counts["accepted"]:
        parts.append(f"{counts['accepted']} accepted")
    if counts["accepted_with_proxy"]:
        parts.append(f"{counts['accepted_with_proxy']} proxy/caveated")
    if counts["unverifiable"]:
        parts.append(f"{counts['unverifiable']} unverifiable")
    if counts["pending"]:
        parts.append(f"{counts['pending']} still unresolved")
    if counts["conflicted"]:
        parts.append(f"{counts['conflicted']} conflicted")
    return ", ".join(parts)


def _set_readiness_if_weaker(
    report: ResearchOrganismReport,
    *,
    readiness: str,
) -> None:
    order = {
        "blocked": 0,
        "provisional": 1,
        "grounded": 2,
        "actionable": 3,
    }
    current = str(report.report_readiness or "").strip().lower()
    if order.get(readiness, 0) < order.get(current, 0):
        report.report_readiness = readiness
        if readiness == "blocked":
            report.artifact_mode = "blocker_report"
        elif readiness == "provisional" and report.artifact_mode == "final_report":
            report.artifact_mode = "provisional_report"


def _apply_evidence_integrity_pass(report: ResearchOrganismReport) -> None:
    if not report.evidence_integrity:
        return

    rows = list(report.evidence_integrity)
    conflicted_rows = [row for row in rows if row.status == "conflicted"]
    pending_rows = [row for row in rows if row.status == "pending"]
    caveated_rows = [
        row
        for row in rows
        if row.status in {"accepted_with_proxy", "unverifiable"}
    ]
    source_identity_gaps = [
        row
        for row in rows
        if row.status != "accepted" and row.source_identity in {"unclear", "missing"}
    ]
    metric_gaps = [
        row
        for row in rows
        if row.status != "accepted"
        and (
            row.metric_identity in {"unclear", "missing"}
            or row.unit_scale_consistency in {"unclear", "missing"}
            or row.same_source_consistency in {"unclear", "missing"}
        )
    ]
    time_gaps = [
        row
        for row in rows
        if row.status != "accepted" and row.time_alignment in {"unclear", "missing"}
    ]
    scope_gaps = [
        row
        for row in rows
        if row.status != "accepted" and row.scope_alignment in {"unclear", "missing"}
    ]

    if source_identity_gaps:
        _set_report_quality_gate(
            report,
            gate="source_authority",
            status="warn",
            summary=(
                "Evidence integrity pass found claim-level source-identity gaps that still "
                "need authoritative or better-matched sourcing."
            ),
            required_follow_up=(
                "Confirm the cited URL/title/date/entity for the unresolved claims before "
                "treating them as settled."
            ),
        )
    if metric_gaps:
        _set_report_quality_gate(
            report,
            gate="numeric_reconciliation",
            status="warn",
            summary=(
                "Evidence integrity pass found claim-level metric, unit/scale, or "
                "same-source consistency gaps."
            ),
            required_follow_up=(
                "Normalize the metric identity and units/scale for the unresolved claims "
                "before treating them as clean numeric facts."
            ),
        )
    if time_gaps:
        _set_report_quality_gate(
            report,
            gate="time_anchor",
            status="warn",
            summary=(
                "Evidence integrity pass found claims that still lack a clean as-of date "
                "or time alignment."
            ),
            required_follow_up=(
                "Add explicit as-of dates or explain the publication lag for the unresolved claims."
            ),
        )
    if scope_gaps:
        _set_report_quality_gate(
            report,
            gate="scope_boundary",
            status="warn",
            summary=(
                "Evidence integrity pass found claims whose geography, market, or scope "
                "boundary is still unclear."
            ),
            required_follow_up=(
                "Clarify the market/geography/scope for the unresolved claims before "
                "treating them as interchangeable."
            ),
        )

    if conflicted_rows:
        summary = _summarize_evidence_integrity(report)
        _set_readiness_if_weaker(report, readiness="blocked")
        _set_report_quality_gate(
            report,
            gate="final_status",
            status="fail",
            summary=(
                "Evidence integrity pass still has conflicted core claims. "
                f"Current integrity summary: {summary}."
            ),
            required_follow_up=(
                "Resolve or explicitly retire the conflicted claims before treating the artifact as finished."
            ),
        )
        for row in conflicted_rows[:3]:
            _append_report_audit_issue(
                report,
                kind="conflict",
                severity="major",
                issue=(
                    "Evidence integrity pass still sees this claim as conflicted: "
                    f"{row.claim}."
                ),
                affected_claim=row.claim,
                required_follow_up=(
                    row.rationale
                    or "Find stronger evidence or state explicitly that the claim remains conflicted."
                ),
            )
    elif pending_rows or caveated_rows:
        summary = _summarize_evidence_integrity(report)
        _set_readiness_if_weaker(report, readiness="provisional")
        _set_report_quality_gate(
            report,
            gate="final_status",
            status="warn",
            summary=(
                "Evidence integrity pass found unresolved or caveated claims, so the "
                f"artifact should stay provisional. Current integrity summary: {summary}."
            ),
            required_follow_up=(
                "Either resolve the remaining pending claims or keep them explicitly caveated in the final memo."
            ),
        )

    summary = _summarize_evidence_integrity(report)
    if summary and summary not in str(report.readiness_note or ""):
        prefix = str(report.readiness_note or "").strip()
        integrity_note = f"Evidence integrity: {summary}."
        report.readiness_note = f"{prefix} {integrity_note}".strip() if prefix else integrity_note


def _conversation_facts(
    *,
    session: ResearchCliSession,
    workspace_root: Path,
    model: str,
    thinking_mode: str,
    tool_ids: Sequence[str],
    delivery_target: str,
    depth_profile: str,
    requested_reader_count: int | None,
    additional_reports: Sequence[ResearchOrganismReport] | None = None,
) -> ResearchConversationFacts:
    latest_report = (
        list(additional_reports or [])[-1]
        if additional_reports
        else (session.turns[-1] if session.turns else None)
    )
    now_context = _now_context()
    shell_process_directory = str(
        Path(os.environ.get("PWD") or str(workspace_root)).expanduser()
    )
    return ResearchConversationFacts(
        product_name=RESEARCH_PRODUCT_NAME,
        workspace_root=str(workspace_root),
        effective_working_directory=str(workspace_root),
        shell_process_directory=shell_process_directory,
        session_id=session.session_id,
        active_model=str(model or "").strip(),
        thinking_mode=str(thinking_mode or "").strip(),
        enabled_tools=list(tool_ids),
        research_turn_count=len(session.turns) + len(list(additional_reports or [])),
        conversation_message_count=len(session.conversation),
        pending_clarification=session.pending_clarification,
        latest_report_status=str(getattr(latest_report, "status", "") or ""),
        latest_report_objective=str(getattr(latest_report, "objective", "") or ""),
        latest_report_confidence=getattr(latest_report, "confidence", None),
        latest_report_error=getattr(latest_report, "error", None),
        default_delivery_target=delivery_target,
        depth_profile=depth_profile,
        requested_reader_count=requested_reader_count,
        current_timestamp=now_context["current_timestamp"],
        current_date=now_context["current_date"],
        timezone=now_context["timezone"],
        time_awareness_policy=_TIME_AWARENESS_POLICY,
    )


def _conversation_context(
    *,
    session: ResearchCliSession,
    workspace_root: Path,
    model: str,
    thinking_mode: str,
    tool_ids: Sequence[str],
    acceptance_criteria: Sequence[str],
    delivery_target: str,
    depth_profile: str,
    requested_reader_count: int | None,
    additional_reports: Sequence[ResearchOrganismReport] | None = None,
) -> ResearchConversationContext:
    facts = _conversation_facts(
        session=session,
        workspace_root=workspace_root,
        model=model,
        thinking_mode=thinking_mode,
        tool_ids=tool_ids,
        delivery_target=delivery_target,
        depth_profile=depth_profile,
        requested_reader_count=requested_reader_count,
        additional_reports=additional_reports,
    )
    session_reports = session.context_reports(limit=4)
    additional_context_reports = [
        report for report in list(additional_reports or []) if not report.is_failed_no_output()
    ]
    recent_reports = [
        _report_context(report)
        for report in [*session_reports, *additional_context_reports]
    ]
    return ResearchConversationContext(
        workspace_root=str(workspace_root),
        model=model,
        thinking_mode=thinking_mode,
        tool_ids=list(tool_ids),
        acceptance_criteria=list(acceptance_criteria),
        default_delivery_target=delivery_target,
        depth_profile=depth_profile,
        requested_reader_count=requested_reader_count,
        pending_clarification=session.pending_clarification,
        facts=facts,
        recent_conversation=[
            ResearchConversationMessage(
                role=entry.role,
                text=entry.text,
                kind=entry.kind,
            )
            for entry in session.conversation[-8:]
        ],
        recent_reports=recent_reports[-6:],
        claim_ledger=[
            fact.model_dump(mode="json")
            if hasattr(fact, "model_dump")
            else dict(fact)
            for fact in session.claim_ledger
        ],
    )


def _load_orchestrator_session(
    *,
    session: ResearchCliSession,
    controller: ResearchConversationController,
):
    try:
        return controller.load_session(
            dict(session.orchestrator_state.get("durable_session") or {})
        )
    except Exception:
        return None


def _store_orchestrator_session(
    *,
    session: ResearchCliSession,
    controller: ResearchConversationController,
    durable_session,
) -> None:
    session.orchestrator_state = {
        **dict(session.orchestrator_state),
        "durable_session": controller.dump_session(durable_session),
    }


def _emit_assistant_message(event_callback, message: str) -> None:
    text = _assistant_text(message)
    if not text:
        return
    _emit_research_event(event_callback, {"event": "assistant.message", "message": text})


def _build_runtime_context(
    *,
    workspace_root: Path,
    model: str,
    tool_ids: Sequence[str],
    thinking_mode: str,
    task_id: str,
    delivery_target: str,
    depth_profile: str,
    requested_reader_count: int | None,
    max_runtime_seconds: int | None,
) -> dict[str, Any]:
    shell_process_directory = str(
        Path(os.environ.get("PWD") or str(workspace_root)).expanduser()
    )
    now_context = _now_context()
    return {
        "product_name": RESEARCH_PRODUCT_NAME,
        "workspace_root": str(workspace_root),
        "current_working_directory": str(workspace_root),
        "shell_process_directory": shell_process_directory,
        "model": model,
        "task_id": task_id,
        "tool_ids": list(tool_ids),
        "thinking_mode": thinking_mode,
        "delivery_target": delivery_target,
        "depth_profile": depth_profile,
        "requested_reader_count": requested_reader_count,
        "max_runtime_seconds": max_runtime_seconds,
        "time_awareness_policy": _TIME_AWARENESS_POLICY,
        **now_context,
    }


async def run_research_organism_live(
    workdir: Path,
    *,
    llm_provider: LLMProvider,
    objective: str,
    model: str,
    task_id: str = "deep-research-task",
    organism_id: str = "reference-project-execution",
    delivery_target: str = "research memo",
    acceptance_criteria: Sequence[str] | None = None,
    hard_constraints: Sequence[str] | None = None,
    soft_constraints: Sequence[str] | None = None,
    evidence_summaries: Sequence[str] | None = None,
    reader_briefs: Sequence[str] | None = None,
    tool_ids: Sequence[str] | None = None,
    workspace_root: str | Path | None = None,
    research_reader_count: int | None = None,
    depth_profile: str = "standard",
    max_tool_rounds: int | None = 8,
    max_tool_calls: int = 24,
    max_runtime_seconds: int | None = None,
    thinking_mode: str = "auto",
    event_callback=None,
    session_context: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> ResearchOrganismReport:
    now_context = _now_context()
    frame_current_date = str(
        (session_context or {}).get("current_date") or now_context["current_date"]
    ).strip() or now_context["current_date"]
    frame_timezone = str(
        (session_context or {}).get("timezone") or now_context["timezone"]
    ).strip() or now_context["timezone"]
    temporal_frame = _temporal_frame(
        objective=objective,
        current_date=frame_current_date,
        timezone_name=frame_timezone,
    )
    task = _build_research_task(
        workdir,
        task_id=task_id,
        objective=objective,
        temporal_mode=temporal_frame["mode"],
        temporal_anchor=temporal_frame["anchor"],
        temporal_window=temporal_frame["window"],
        temporal_guidance=temporal_frame["guidance"],
        acceptance_criteria=acceptance_criteria or DEFAULT_RESEARCH_ACCEPTANCE_CRITERIA,
        delivery_target=delivery_target,
        hard_constraints=hard_constraints or DEFAULT_RESEARCH_HARD_CONSTRAINTS,
        soft_constraints=soft_constraints or DEFAULT_RESEARCH_SOFT_CONSTRAINTS,
        evidence_summaries=evidence_summaries or [],
    )
    result = await run_deep_research_organ_live(
        workdir,
        llm_provider=llm_provider,
        task=task,
        model=model,
        organism_id=organism_id,
        tool_ids=list(tool_ids or DEFAULT_RESEARCH_TOOL_IDS),
        workspace_root=workspace_root,
        max_tool_rounds=max_tool_rounds,
        max_tool_calls=max_tool_calls,
        max_runtime_seconds=max_runtime_seconds,
        research_reader_count=research_reader_count,
        research_reader_briefs=list(reader_briefs or []),
        event_callback=event_callback,
        trace_id=trace_id,
    )
    if hasattr(result, "model_dump"):
        payload = result.model_dump(mode="json")
    elif isinstance(result, dict):
        payload = dict(result)
    else:
        raise TypeError(f"Unsupported research report result: {type(result).__name__}")
    final_output = dict(payload.get("final_output") or {})
    status = str(payload.get("status") or "")
    default_readiness = "grounded" if final_output and status == "completed" else "blocked"
    return ResearchOrganismReport(
        status=status,
        trace_id=str(payload.get("trace_id") or ""),
        organism_id=organism_id,
        organ_id="deep-research",
        task_id=task_id,
        objective=objective,
        temporal_mode=temporal_frame["mode"],
        temporal_anchor=temporal_frame["anchor"],
        temporal_window=temporal_frame["window"],
        temporal_guidance=temporal_frame["guidance"],
        delivery_target=delivery_target,
        findings=final_output.get("findings"),
        evidence_summary=final_output.get("evidence_summary"),
        evidence_refs=final_output.get("evidence_refs"),
        contradictions=final_output.get("contradictions"),
        open_questions=final_output.get("open_questions"),
        verification_facts=final_output.get("verification_facts"),
        audit_issues=final_output.get("audit_issues"),
        quality_gates=final_output.get("quality_gates"),
        report_readiness=final_output.get("report_readiness") or default_readiness,
        artifact_mode=final_output.get("artifact_mode"),
        readiness_note=final_output.get("readiness_note"),
        confidence=final_output.get("confidence"),
        recommended_change=str(final_output.get("recommended_change") or ""),
        selected_reader_count=payload.get("selected_reader_count"),
        selected_reader_briefs=payload.get("selected_reader_briefs"),
        depth_profile=depth_profile,
        max_tool_rounds=max_tool_rounds,
        max_tool_calls=max_tool_calls,
        outputs=final_output,
        handoff_count=int(payload.get("handoff_count") or 0),
        signal_count=int(payload.get("signal_count") or 0),
        error=str(payload.get("error") or "") or None,
        stage_records=payload.get("stage_records"),
        trace_rows=payload.get("trace_rows"),
    )


async def _run_research_turn(
    *,
    args,
    llm_provider: LLMProvider,
    workspace_root: Path,
    model: str,
    objective: str,
    task_id: str,
    delivery_target: str,
    acceptance_criteria: Sequence[str],
    hard_constraints: Sequence[str],
    soft_constraints: Sequence[str],
    evidence_summaries: Sequence[str],
    reader_briefs: Sequence[str],
    workdir: Path,
    tool_ids: Sequence[str],
    research_reader_count: int | None,
    depth_profile: str,
    max_tool_rounds: int | None,
    max_tool_calls: int,
    max_runtime_seconds: int | None,
    thinking_mode: str,
    event_callback=None,
    trace_id: str | None = None,
) -> ResearchOrganismReport:
    session_context = _build_runtime_context(
        workspace_root=workspace_root,
        model=model,
        tool_ids=tool_ids,
        thinking_mode=thinking_mode,
        task_id=task_id,
        delivery_target=delivery_target,
        depth_profile=depth_profile,
        requested_reader_count=research_reader_count,
        max_runtime_seconds=max_runtime_seconds,
    )
    result = await run_research_organism_live(
        workdir,
        llm_provider=llm_provider,
        objective=objective,
        model=model,
        task_id=task_id,
        organism_id=str(args.organism_id),
        delivery_target=delivery_target,
        acceptance_criteria=acceptance_criteria,
        hard_constraints=hard_constraints,
        soft_constraints=soft_constraints,
        evidence_summaries=evidence_summaries,
        reader_briefs=reader_briefs,
        tool_ids=tool_ids,
        workspace_root=workspace_root,
        research_reader_count=research_reader_count,
        depth_profile=depth_profile,
        max_tool_rounds=max_tool_rounds,
        max_tool_calls=max_tool_calls,
        max_runtime_seconds=max_runtime_seconds,
        thinking_mode=thinking_mode,
        event_callback=event_callback,
        session_context=session_context,
        trace_id=trace_id,
    )
    if isinstance(result, ResearchOrganismReport):
        report = result
    elif hasattr(result, "model_dump"):
        report = ResearchOrganismReport.model_validate(result.model_dump(mode="json"))
    elif isinstance(result, dict):
        report = ResearchOrganismReport.model_validate(result)
    else:
        raise TypeError(f"Unsupported research report result: {type(result).__name__}")
    _apply_evidence_integrity_pass(report)
    return report


async def _run_orchestrated_turn(
    *,
    args,
    controller: ResearchConversationController,
    llm_provider: LLMProvider,
    workspace_root: Path,
    model: str,
    objective: str,
    session: ResearchCliSession,
    tool_ids: Sequence[str],
    acceptance_criteria: Sequence[str],
    delivery_target: str,
    hard_constraints: Sequence[str],
    soft_constraints: Sequence[str],
    default_evidence_summaries: Sequence[str],
    default_reader_briefs: Sequence[str],
    research_reader_count: int | None,
    max_supervision_loops: int | None,
    depth_profile: str,
    max_tool_rounds: int | None,
    max_tool_calls: int,
    max_runtime_seconds: int | None,
    thinking_mode: str,
    run_root: Path,
    progress_renderer: ResearchProgressRenderer,
    control_logger: ResearchEventLogger | None,
) -> ResearchConversationOutcome:
    assistant_messages: list[str] = []
    reports: list[ResearchOrganismReport] = []
    question: str | None = None
    current_event_callback = progress_renderer
    current_control_heartbeat: ResearchHeartbeatMonitor | None = None
    session.record_message(role="user", text=objective)
    _log_event(
        control_logger,
        "orchestrator.turn.started",
        session_id=session.session_id,
        objective=objective,
        pending_clarification=session.pending_clarification,
        prior_turns=len(session.turns),
    )

    def _control_event_callback(event: dict[str, Any]) -> None:
        if current_control_heartbeat is not None:
            current_control_heartbeat.observe(event)
        _emit_research_event(progress_renderer, event)
        if control_logger is not None:
            control_logger.emit(event)

    controller.set_event_callback(_control_event_callback)

    orchestrator_session = _load_orchestrator_session(
        session=session,
        controller=controller,
    )
    turn_context = _conversation_context(
        session=session,
        workspace_root=workspace_root,
        model=model,
        thinking_mode=thinking_mode,
        tool_ids=tool_ids,
        acceptance_criteria=acceptance_criteria,
        delivery_target=delivery_target,
        depth_profile=depth_profile,
        requested_reader_count=research_reader_count,
    )
    _log_event(
        control_logger,
        "orchestrator.turn.decision.started",
        objective=objective,
        pending_clarification=session.pending_clarification,
    )
    decision, orchestrator_session = await _run_control_stage_with_hedge(
        stage_name="turn_decision",
        heartbeat_phase="turn-decision",
        detail=_truncate_text(objective, limit=160),
        controller=controller,
        session=orchestrator_session,
        primary_call=lambda stage_session: controller.decide_user_turn(
            session=stage_session,
            user_message=objective,
            pending_clarification=session.pending_clarification,
            context=turn_context,
        ),
        fallback_result=lambda: (
            _fallback_turn_decision(
                user_message=objective,
                pending_clarification=session.pending_clarification,
                context=turn_context,
            ),
            orchestrator_session,
        ),
        control_logger=control_logger,
    )
    _store_orchestrator_session(
        session=session,
        controller=controller,
        durable_session=orchestrator_session,
    )
    _log_event(
        control_logger,
        "orchestrator.turn.decision",
        action=decision.action,
        response_kind=decision.response_kind,
        public_response=decision.public_response,
        research_objective=decision.research_objective,
        delivery_target=decision.delivery_target,
        acceptance_criteria_count=len(decision.acceptance_criteria),
    )

    def _record_assistant(
        text: str,
        *,
        kind: str = "message",
        preserve_formatting: bool = False,
    ) -> None:
        cleaned = _assistant_text(text, preserve_formatting=preserve_formatting)
        if not cleaned:
            return
        assistant_messages.append(cleaned)
        session.record_message(role="assistant", text=cleaned, kind=kind)
        _emit_assistant_message(current_event_callback, cleaned)

    if decision.public_response:
        _record_assistant(
            decision.public_response,
            kind="report" if decision.response_kind == "report_reply" else "message",
            preserve_formatting=decision.response_kind == "report_reply",
        )

    if decision.action == "respond":
        session.pending_clarification = None
        _log_event(
            control_logger,
            "orchestrator.turn.completed",
            status="responded",
            report_count=0,
            response_kind=decision.response_kind,
        )
        return ResearchConversationOutcome(
            status="responded",
            assistant_messages=assistant_messages,
            question=None,
            reports=[],
            response_kind=decision.response_kind,
            artifact_title=decision.artifact_title,
            artifact_markdown=decision.artifact_markdown,
        )

    if decision.action == "clarify":
        question = _assistant_text(
            decision.clarifying_question or decision.public_response
        )
        session.pending_clarification = question or None
        if question and question not in assistant_messages:
            _record_assistant(question, kind="clarification")
        _log_event(
            control_logger,
            "orchestrator.turn.completed",
            status="clarify",
            question=question,
            report_count=0,
        )
        return ResearchConversationOutcome(
            status="clarify",
            assistant_messages=assistant_messages,
            question=question,
            reports=[],
            response_kind="clarification",
        )

    session.pending_clarification = None
    next_objective = _assistant_text(decision.research_objective or objective)
    next_delivery_target = _assistant_text(decision.delivery_target or delivery_target)
    effective_acceptance_criteria = _dedupe(
        [*list(acceptance_criteria), *list(decision.acceptance_criteria)]
    ) or list(acceptance_criteria)
    base_turn_number = session.next_turn_number()
    initial_plan_context = _conversation_context(
        session=session,
        workspace_root=workspace_root,
        model=model,
        thinking_mode=thinking_mode,
        tool_ids=tool_ids,
        acceptance_criteria=effective_acceptance_criteria,
        delivery_target=next_delivery_target,
        depth_profile=depth_profile,
        requested_reader_count=research_reader_count,
    )
    _log_event(
        control_logger,
        "orchestrator.plan.started",
        planning_mode="initial",
        objective=next_objective,
        delivery_target=next_delivery_target,
    )
    current_plan, orchestrator_session = await _run_control_stage_with_hedge(
        stage_name="plan",
        heartbeat_phase="planning",
        detail=f"initial: {_truncate_text(next_objective, limit=140)}",
        controller=controller,
        session=orchestrator_session,
        primary_call=lambda stage_session: controller.plan_research_intention(
            session=stage_session,
            objective=next_objective,
            delivery_target=next_delivery_target,
            acceptance_criteria=effective_acceptance_criteria,
            context=initial_plan_context,
            planning_mode="initial",
        ),
        fallback_result=lambda: (
            _fallback_intention_plan(
                objective=next_objective,
                delivery_target=next_delivery_target,
                acceptance_criteria=effective_acceptance_criteria,
                context=initial_plan_context,
                planning_mode="initial",
                previous_report=None,
            ),
            orchestrator_session,
        ),
        control_logger=control_logger,
    )
    _store_orchestrator_session(
        session=session,
        controller=controller,
        durable_session=orchestrator_session,
    )
    _log_event(
        control_logger,
        "orchestrator.plan.completed",
        planning_mode="initial",
        answer_goal=current_plan.answer_goal,
        refined_objective=current_plan.refined_objective,
        subproblem_count=len(current_plan.subproblems),
        workstream_count=len(current_plan.workstreams),
        plan_summary=current_plan.plan_summary,
    )
    next_objective = _assistant_text(current_plan.refined_objective or next_objective)
    effective_acceptance_criteria = _dedupe(
        [*list(effective_acceptance_criteria), *list(current_plan.acceptance_criteria)]
    ) or list(effective_acceptance_criteria)
    current_plan_mode = "initial"

    continuation_index = 0
    while True:
        turn_reader_count = _planned_reader_count(
            current_plan,
            base_requested_count=research_reader_count,
            continuation_index=continuation_index,
            previous_selected_count=reports[-1].selected_reader_count if reports else None,
        )
        turn_evidence_summaries = _dedupe(
            [
                *list(default_evidence_summaries),
                *session.carry_forward_findings(),
                *_plan_evidence_summaries(current_plan),
            ]
        )
        turn_reader_briefs = _plan_reader_briefs(
            current_plan,
            fallback_reader_briefs=default_reader_briefs,
        )
        report_turn_number = base_turn_number + len(reports)
        task_id = f"{args.task_id}:{report_turn_number}"
        workdir = run_root / f"turn-{report_turn_number:02d}"
        run_trace_id = new_trace_id()
        event_logger = ResearchEventLogger(
            path=workdir / "events.jsonl",
            stream_kind="bounded_run",
            session_id=session.session_id,
            turn_id=str(report_turn_number),
            task_id=task_id,
            organism_id=args.organism_id,
            organ_id="deep-research",
            trace_id=run_trace_id,
        )
        run_completed = False

        def _run_event_callback(event: dict[str, Any]) -> None:
            heartbeat.observe(event)
            _emit_research_event(progress_renderer, event)
            trace_row = event.get("trace_row")
            if (
                str(event.get("event") or "").strip() == "trace.row"
                and isinstance(trace_row, dict)
            ):
                event_logger.emit_trace_rows([trace_row])
                return
            event_logger.emit(event)

        heartbeat = ResearchHeartbeatMonitor(
            event_callback=_run_event_callback,
            enabled=True,
            idle_seconds=_env_float("DAN_RESEARCH_HEARTBEAT_IDLE_SECONDS", 10.0),
            repeat_seconds=_env_float("DAN_RESEARCH_HEARTBEAT_INTERVAL_SECONDS", 15.0),
            poll_seconds=_env_float("DAN_RESEARCH_HEARTBEAT_POLL_SECONDS", 2.0),
        )

        current_event_callback = _run_event_callback
        try:
            await heartbeat.start()
            current_control_heartbeat = heartbeat
            _log_event(
                control_logger,
                "run.turn.started",
                task_id=task_id,
                turn_number=report_turn_number,
                objective=next_objective,
                temporal_mode=_temporal_frame(
                    objective=next_objective,
                    current_date=_now_context()["current_date"],
                    timezone_name=_now_context()["timezone"],
                )["mode"],
                delivery_target=next_delivery_target,
                requested_reader_count=turn_reader_count,
                max_runtime_seconds=max_runtime_seconds,
                workdir=str(workdir),
            )
            event_logger.emit(
                {
                    "event": "run.log.started",
                    "trace_id": run_trace_id,
                    "task_id": task_id,
                    "turn_number": report_turn_number,
                    "objective": next_objective,
                    "delivery_target": next_delivery_target,
                    "depth_profile": depth_profile,
                    "requested_reader_count": turn_reader_count,
                    "max_runtime_seconds": max_runtime_seconds,
                    "workdir": str(workdir),
                }
            )
            event_logger.emit(
                {
                    "event": "research.plan",
                    "planning_mode": current_plan_mode,
                    **_plan_payload(current_plan),
                }
            )
            _emit_research_event(
                _run_event_callback,
                {
                    "event": "research.started",
                    "trace_id": run_trace_id,
                    "objective": next_objective,
                    "delivery_target": next_delivery_target,
                    "depth_profile": depth_profile,
                    "requested_reader_count": turn_reader_count,
                    "max_runtime_seconds": max_runtime_seconds,
                },
            )
            report = await _run_research_turn(
                args=args,
                llm_provider=llm_provider,
                workspace_root=workspace_root,
                model=model,
                objective=next_objective,
                task_id=task_id,
                delivery_target=next_delivery_target,
                acceptance_criteria=effective_acceptance_criteria,
                hard_constraints=hard_constraints,
                soft_constraints=soft_constraints,
                evidence_summaries=turn_evidence_summaries,
                reader_briefs=turn_reader_briefs,
                workdir=workdir,
                tool_ids=tool_ids,
                research_reader_count=turn_reader_count,
                depth_profile=depth_profile,
                max_tool_rounds=max_tool_rounds,
                max_tool_calls=max_tool_calls,
                max_runtime_seconds=max_runtime_seconds,
                thinking_mode=thinking_mode,
                event_callback=_run_event_callback,
                trace_id=run_trace_id,
            )
            report = report.model_copy(
                update={
                    "event_log_path": str(event_logger.path),
                    "event_log_schema": ORGANISM_LOG_SCHEMA_VERSION,
                }
            )
            event_logger.update_context(trace_id=report.trace_id)
            event_logger.emit_stage_records(report.stage_records)
            reports.append(report)
            _emit_research_event(
                _run_event_callback,
                {
                    "event": "research.completed",
                    "status": report.status,
                    "confidence": report.confidence,
                    "selected_reader_count": report.selected_reader_count,
                    "trace_id": report.trace_id,
                },
            )
            _log_event(
                control_logger,
                "run.turn.report.completed",
                task_id=task_id,
                trace_id=report.trace_id,
                status=report.status,
                confidence=report.confidence,
                event_log_path=report.event_log_path,
            )

            _log_event(
                control_logger,
                "orchestrator.review.started",
                task_id=task_id,
                trace_id=report.trace_id,
                report_status=report.status,
            )
            review_report_context = _report_context(report)
            review_context = _conversation_context(
                session=session,
                workspace_root=workspace_root,
                model=model,
                thinking_mode=thinking_mode,
                tool_ids=tool_ids,
                acceptance_criteria=effective_acceptance_criteria,
                delivery_target=next_delivery_target,
                depth_profile=depth_profile,
                requested_reader_count=turn_reader_count,
                additional_reports=reports,
            )
            review, orchestrator_session = await _run_control_stage_with_hedge(
                stage_name="review",
                heartbeat_phase="review",
                detail=f"task {task_id}: {_truncate_text(report.status, limit=60)}",
                controller=controller,
                session=orchestrator_session,
                primary_call=lambda stage_session: controller.review_research_result(
                    session=stage_session,
                    objective=next_objective,
                    report_summary=review_report_context,
                    context=review_context,
                ),
                fallback_result=lambda: (
                    _fallback_review_decision(
                        objective=next_objective,
                        report_summary=review_report_context,
                    ),
                    orchestrator_session,
                ),
                control_logger=control_logger,
                heartbeat=heartbeat,
                task_id=task_id,
                trace_id=report.trace_id,
            )
            _store_orchestrator_session(
                session=session,
                controller=controller,
                durable_session=orchestrator_session,
            )
            hit_supervision_cap = review.action == "continue" and not (
                max_supervision_loops is None
                or continuation_index + 1 < max_supervision_loops
            )
            if hit_supervision_cap and _allows_best_effort_final_closure(
                next_objective, review_report_context
            ):
                override_reason = (
                    "This was the explicitly final bounded research pass, so the artifact "
                    "is closing as a provisional report with explicit caveats instead of "
                    "asking for another pass."
                )
                _mark_report_provisional_close(report, reason=override_reason)
                review = ResearchConversationReviewDecision(
                    action="done",
                    public_response=override_reason,
                )
            _log_event(
                control_logger,
                "orchestrator.review.completed",
                task_id=task_id,
                trace_id=report.trace_id,
                action=review.action,
                public_response=review.public_response,
                next_objective=review.next_objective,
                clarifying_question=review.clarifying_question,
            )
            if review.public_response:
                _record_assistant(review.public_response)

            if review.action == "continue" and (
                max_supervision_loops is None
                or continuation_index + 1 < max_supervision_loops
            ):
                event_logger.emit(
                    {
                        "event": "run.log.completed",
                        "task_id": task_id,
                        "trace_id": report.trace_id,
                        "status": report.status,
                        "review_action": review.action,
                        "confidence": report.confidence,
                        "event_log_path": report.event_log_path,
                    }
                )
                _log_event(
                    control_logger,
                    "run.turn.completed",
                    task_id=task_id,
                    trace_id=report.trace_id,
                    status=report.status,
                    review_action=review.action,
                    next_objective=next_objective,
                    continue_index=continuation_index + 1,
                )
                run_completed = True
                next_objective = _assistant_text(review.next_objective or next_objective)
                default_evidence_summaries = _dedupe(
                    [*list(default_evidence_summaries), _report_finding(report)]
                )
                continuation_report_context = _report_context(report)
                continuation_plan_context = _conversation_context(
                    session=session,
                    workspace_root=workspace_root,
                    model=model,
                    thinking_mode=thinking_mode,
                    tool_ids=tool_ids,
                    acceptance_criteria=effective_acceptance_criteria,
                    delivery_target=next_delivery_target,
                    depth_profile=depth_profile,
                    requested_reader_count=turn_reader_count,
                    additional_reports=reports,
                )
                _log_event(
                    control_logger,
                    "orchestrator.plan.started",
                    planning_mode="continuation",
                    task_id=task_id,
                    trace_id=report.trace_id,
                    objective=next_objective,
                    delivery_target=next_delivery_target,
                    strategy="deterministic_follow_up",
                )
                current_plan = _fallback_intention_plan(
                    objective=next_objective,
                    delivery_target=next_delivery_target,
                    acceptance_criteria=effective_acceptance_criteria,
                    context=continuation_plan_context,
                    planning_mode="continuation",
                    previous_report=continuation_report_context,
                )
                _store_orchestrator_session(
                    session=session,
                    controller=controller,
                    durable_session=orchestrator_session,
                )
                _log_event(
                    control_logger,
                    "orchestrator.plan.completed",
                    planning_mode="continuation",
                    answer_goal=current_plan.answer_goal,
                    refined_objective=current_plan.refined_objective,
                    subproblem_count=len(current_plan.subproblems),
                    workstream_count=len(current_plan.workstreams),
                    plan_summary=current_plan.plan_summary,
                    prior_trace_id=report.trace_id,
                    strategy="deterministic_follow_up",
                )
                next_objective = _assistant_text(
                    current_plan.refined_objective or next_objective
                )
                effective_acceptance_criteria = _dedupe(
                    [*list(effective_acceptance_criteria), *list(current_plan.acceptance_criteria)]
                ) or list(effective_acceptance_criteria)
                current_plan_mode = "continuation"
                continuation_index += 1
                continue

            if review.action == "continue":
                reason = (
                    "The review still requires another bounded research pass, but this "
                    "turn hit the supervision limit before that pass was included, so the "
                    "artifact is returning incomplete."
                )
                _mark_report_incomplete(report, reason=reason)
                completion_note = (
                    "This turn is returning incomplete because another bounded research "
                    "pass is still required."
                )
                if completion_note not in assistant_messages:
                    _record_assistant(completion_note)

            if review.action == "clarify":
                question = _assistant_text(
                    review.clarifying_question or review.public_response
                )
                session.pending_clarification = question or None
                if question and question not in assistant_messages:
                    _record_assistant(question, kind="clarification")
                event_logger.emit(
                    {
                        "event": "run.log.completed",
                        "task_id": task_id,
                        "trace_id": report.trace_id,
                        "status": report.status,
                        "review_action": review.action,
                        "question": question,
                        "event_log_path": report.event_log_path,
                    }
                )
                _log_event(
                    control_logger,
                    "run.turn.completed",
                    task_id=task_id,
                    trace_id=report.trace_id,
                    status=report.status,
                    review_action=review.action,
                    question=question,
                )
                run_completed = True
                _log_event(
                    control_logger,
                    "orchestrator.turn.completed",
                    status="clarify",
                    question=question,
                    report_count=len(reports),
                )
                return ResearchConversationOutcome(
                    status="clarify",
                    assistant_messages=assistant_messages,
                    question=question,
                    reports=reports,
                    response_kind="clarification",
                )
            event_logger.emit(
                {
                    "event": "run.log.completed",
                    "task_id": task_id,
                    "trace_id": report.trace_id,
                    "status": report.status,
                    "review_action": review.action,
                    "confidence": report.confidence,
                    "event_log_path": report.event_log_path,
                }
            )
            _log_event(
                control_logger,
                "run.turn.completed",
                task_id=task_id,
                trace_id=report.trace_id,
                status=report.status,
                review_action=review.action,
            )
            run_completed = True
            break
        except Exception as exc:
            if not run_completed:
                _log_event(
                    control_logger,
                    "run.turn.failed",
                    task_id=task_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                event_logger.emit(
                    {
                        "event": "run.log.failed",
                        "task_id": task_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
            raise
        finally:
            await heartbeat.stop()
            current_control_heartbeat = None
            current_event_callback = progress_renderer
            event_logger.close()

    final_status = reports[-1].status if reports else "responded"
    _log_event(
        control_logger,
        "orchestrator.turn.completed",
        status=final_status,
        report_count=len(reports),
        final_trace_id=reports[-1].trace_id if reports else None,
    )
    return ResearchConversationOutcome(
        status=final_status,
        assistant_messages=assistant_messages,
        question=question,
        reports=reports,
    )


def _print_research_report(report: dict[str, object]) -> None:
    status = str(report.get("status") or "").strip().lower()
    readiness = str(report.get("report_readiness") or "").strip().lower()
    artifact_mode = str(report.get("artifact_mode") or "").strip().lower()
    if not artifact_mode:
        if status != "completed" or readiness == "blocked":
            artifact_mode = "blocker_report"
        elif readiness == "provisional":
            artifact_mode = "provisional_report"
        else:
            artifact_mode = "final_report"

    title = {
        "blocker_report": "Research Blocker Report",
        "provisional_report": "Provisional Research Report",
    }.get(artifact_mode, "Research Report")
    print(title)
    print()

    objective = str(report.get("objective") or "").strip()
    if objective:
        print(f"Objective: {objective}")
    temporal_mode = str(report.get("temporal_mode") or "").strip()
    temporal_anchor = str(report.get("temporal_anchor") or "").strip()
    temporal_window = str(report.get("temporal_window") or "").strip()
    temporal_guidance = str(report.get("temporal_guidance") or "").strip()
    if temporal_mode:
        detail = temporal_anchor or temporal_window or "-"
        print(f"Temporal frame: {temporal_mode} ({detail})")
    if temporal_guidance:
        print(f"Temporal note: {temporal_guidance}")
    if objective or temporal_mode or temporal_guidance:
        print()

    error = str(report.get("error") or "").strip()
    readiness_note = str(report.get("readiness_note") or "").strip()

    def _row_dict(item: object) -> dict[str, object]:
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        if isinstance(item, dict):
            return dict(item)
        return {}

    verification_rows = [_row_dict(item) for item in (report.get("verification_facts") or [])]
    integrity_rows = [_row_dict(item) for item in (report.get("evidence_integrity") or [])]
    audit_rows = [_row_dict(item) for item in (report.get("audit_issues") or [])]
    quality_gate_rows = [_row_dict(item) for item in (report.get("quality_gates") or [])]

    if artifact_mode == "blocker_report":
        blockers: list[str] = []
        if readiness_note:
            blockers.append(readiness_note)
        if error:
            blockers.append(error)
        for row in quality_gate_rows:
            if str(row.get("status") or "").strip().lower() != "fail":
                continue
            gate = str(row.get("gate") or "").strip()
            summary = str(row.get("summary") or "").strip()
            follow_up = str(row.get("required_follow_up") or "").strip()
            text = f"{gate}: {summary}" if gate and summary else summary or gate
            if follow_up:
                text = f"{text}. Follow-up: {follow_up}".strip()
            if text:
                blockers.append(text)
        for row in audit_rows:
            severity = str(row.get("severity") or "").strip().lower()
            if severity not in {"critical", "major", "blocking"}:
                continue
            issue = str(row.get("issue") or "").strip()
            follow_up = str(row.get("required_follow_up") or "").strip()
            text = issue
            if follow_up:
                text = f"{text}. Follow-up: {follow_up}".strip()
            if text:
                blockers.append(text)
        for row in verification_rows:
            status_label = str(row.get("status") or "").strip().lower()
            if status_label == "verified":
                continue
            fact = str(row.get("fact") or "").strip()
            note = str(row.get("note") or "").strip()
            if fact:
                blockers.append(f"{status_label or 'unverified'}: {fact}. {note}".strip())
        for row in integrity_rows:
            status_label = str(row.get("status") or "").strip().lower()
            if status_label not in {"conflicted", "pending"}:
                continue
            claim = str(row.get("claim") or "").strip()
            rationale = str(row.get("rationale") or "").strip()
            if claim:
                blockers.append(f"{status_label}: {claim}. {rationale}".strip())
        if blockers:
            print("Why it is blocked:")
            seen: set[str] = set()
            shown = 0
            for item in blockers:
                cleaned = " ".join(str(item).split()).strip()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                print(f"- {cleaned}")
                shown += 1
                if shown >= 8:
                    break
            print()
    else:
        if error:
            print("Report could not be completed:")
            print(error)
            print()

        recommended_change = str(report.get("recommended_change") or "").strip()
        if recommended_change:
            print("Recommendation:")
            print(recommended_change)
            print()

        if readiness_note:
            print("Scope note:")
            print(readiness_note)
            print()

    for label, key in (
        ("Findings", "findings"),
        ("Evidence Summary", "evidence_summary"),
        ("Evidence Refs", "evidence_refs"),
        ("Contradictions", "contradictions"),
        ("Open Questions", "open_questions"),
    ):
        values = [str(item).strip() for item in (report.get(key) or []) if str(item).strip()]
        if not values:
            continue
        print(f"{label}:")
        for item in values[:8]:
            print(f"- {item}")
        if len(values) > 8:
            print(f"- (+{len(values) - 8} more)")
        print()

    if artifact_mode == "blocker_report":
        next_steps: list[str] = []
        for row in [*quality_gate_rows, *audit_rows]:
            follow_up = str(row.get("required_follow_up") or "").strip()
            if follow_up:
                next_steps.append(follow_up)
        if next_steps:
            print("Next steps:")
            seen: set[str] = set()
            shown = 0
            for item in next_steps:
                cleaned = " ".join(str(item).split()).strip()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                print(f"- {cleaned}")
                shown += 1
                if shown >= 6:
                    break
            print()
        return

    caveats: list[str] = []
    if readiness in {"blocked", "provisional"} and not readiness_note:
        caveats.append(
            "The report should be treated as provisional because some material facts "
            "or source checks remain incomplete."
        )
    for row in verification_rows:
        status_label = str(row.get("status") or "").strip().lower() or "unverified"
        if status_label == "verified":
            continue
        fact = str(row.get("fact") or "").strip()
        if not fact:
            continue
        note = str(row.get("note") or "").strip()
        source = str(row.get("source") or "").strip()
        suffix = f" Source: {source}." if source else ""
        caveats.append(f"{status_label}: {fact}.{suffix} {note}".strip())
    for row in integrity_rows:
        status_label = str(row.get("status") or "").strip().lower()
        if status_label not in {"accepted_with_proxy", "unverifiable"}:
            continue
        claim = str(row.get("claim") or "").strip()
        rationale = str(row.get("rationale") or "").strip()
        source = str(row.get("source") or "").strip()
        suffix = f" Source: {source}." if source else ""
        if claim:
            caveats.append(f"{status_label}: {claim}.{suffix} {rationale}".strip())
    for row in audit_rows:
        severity = str(row.get("severity") or "").strip().lower() or "minor"
        if severity not in {"blocking", "major", "critical"}:
            continue
        issue = str(row.get("issue") or "").strip()
        if not issue:
            continue
        follow_up = str(row.get("required_follow_up") or "").strip()
        suffix = f" Follow-up: {follow_up}" if follow_up else ""
        caveats.append(f"{severity}: {issue}.{suffix}".strip())
    if caveats:
        print("Caveats:")
        for item in caveats[:8]:
            print(f"- {item}")
        if len(caveats) > 8:
            print(f"- (+{len(caveats) - 8} more)")


def _report_row_dict(item: object) -> dict[str, object]:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")
    if isinstance(item, dict):
        return dict(item)
    return {}


def _markdown_bullet_lines(values: Sequence[str]) -> list[str]:
    lines: list[str] = []
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip()
        if cleaned:
            lines.append(f"- {cleaned}")
    return lines


def _looks_like_markdown_block(value: str) -> bool:
    stripped = str(value or "").lstrip()
    return (
        "\n" in str(value or "")
        or stripped.startswith(("#", "- ", "* ", "> ", "```"))
        or bool(re.match(r"\d+\.\s", stripped))
    )


def _markdown_section_lines(
    title: str,
    values: Sequence[str],
    *,
    preserve_blocks: bool = False,
) -> list[str]:
    rendered: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if preserve_blocks and _looks_like_markdown_block(text):
            rendered.extend(line.rstrip() for line in text.splitlines())
        else:
            rendered.append(f"- {text}")
    if not rendered:
        return []
    return [f"## {title}", "", *rendered, ""]


def _unique_clean_values(values: Sequence[str], *, limit: int | None = None) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
        if limit is not None and len(unique) >= limit:
            break
    return unique


def _render_report_markdown(report: dict[str, object]) -> str:
    status = str(report.get("status") or "").strip().lower()
    readiness = str(report.get("report_readiness") or "").strip().lower()
    artifact_mode = str(report.get("artifact_mode") or "").strip().lower()
    if not artifact_mode:
        if status != "completed" or readiness == "blocked":
            artifact_mode = "blocker_report"
        elif readiness == "provisional":
            artifact_mode = "provisional_report"
        else:
            artifact_mode = "final_report"

    title = {
        "blocker_report": "Research Blocker Report",
        "provisional_report": "Provisional Research Report",
    }.get(artifact_mode, "Research Report")
    lines: list[str] = [f"# {title}", ""]

    objective = str(report.get("objective") or "").strip()
    if objective:
        lines.append(f"**Objective:** {objective}")
    temporal_mode = str(report.get("temporal_mode") or "").strip()
    temporal_anchor = str(report.get("temporal_anchor") or "").strip()
    temporal_window = str(report.get("temporal_window") or "").strip()
    temporal_guidance = str(report.get("temporal_guidance") or "").strip()
    if temporal_mode:
        detail = temporal_anchor or temporal_window or "-"
        lines.append(f"**Temporal Frame:** {temporal_mode} ({detail})")
    if temporal_guidance:
        lines.append(f"**Temporal Note:** {temporal_guidance}")
    lines.append("")

    error = str(report.get("error") or "").strip()
    readiness_note = str(report.get("readiness_note") or "").strip()
    recommended_change = str(report.get("recommended_change") or "").strip()

    verification_rows = [
        _report_row_dict(item) for item in (report.get("verification_facts") or [])
    ]
    integrity_rows = [
        _report_row_dict(item) for item in (report.get("evidence_integrity") or [])
    ]
    audit_rows = [_report_row_dict(item) for item in (report.get("audit_issues") or [])]
    quality_gate_rows = [
        _report_row_dict(item) for item in (report.get("quality_gates") or [])
    ]

    if artifact_mode == "blocker_report":
        blockers: list[str] = []
        if readiness_note:
            blockers.append(readiness_note)
        if error:
            blockers.append(error)
        for row in quality_gate_rows:
            if str(row.get("status") or "").strip().lower() != "fail":
                continue
            gate = str(row.get("gate") or "").strip()
            summary = str(row.get("summary") or "").strip()
            follow_up = str(row.get("required_follow_up") or "").strip()
            text = f"{gate}: {summary}" if gate and summary else summary or gate
            if follow_up:
                text = f"{text}. Follow-up: {follow_up}".strip()
            if text:
                blockers.append(text)
        for row in audit_rows:
            severity = str(row.get("severity") or "").strip().lower()
            if severity not in {"critical", "major", "blocking"}:
                continue
            issue = str(row.get("issue") or "").strip()
            follow_up = str(row.get("required_follow_up") or "").strip()
            text = issue
            if follow_up:
                text = f"{text}. Follow-up: {follow_up}".strip()
            if text:
                blockers.append(text)
        for row in verification_rows:
            status_label = str(row.get("status") or "").strip().lower()
            if status_label == "verified":
                continue
            fact = str(row.get("fact") or "").strip()
            note = str(row.get("note") or "").strip()
            if fact:
                blockers.append(f"{status_label or 'unverified'}: {fact}. {note}".strip())
        for row in integrity_rows:
            status_label = str(row.get("status") or "").strip().lower()
            if status_label not in {"conflicted", "pending"}:
                continue
            claim = str(row.get("claim") or "").strip()
            rationale = str(row.get("rationale") or "").strip()
            if claim:
                blockers.append(f"{status_label}: {claim}. {rationale}".strip())
        if blockers:
            lines.extend(
                [
                    "## Why It Is Blocked",
                    "",
                    *_markdown_bullet_lines(_unique_clean_values(blockers, limit=8)),
                    "",
                ]
            )
    else:
        if recommended_change:
            lines.extend(["## Recommendation", "", recommended_change, ""])
        if readiness_note:
            lines.extend(["## Scope Note", "", readiness_note, ""])
        if error:
            lines.extend(["## Error", "", error, ""])

    findings = [str(item).strip() for item in (report.get("findings") or []) if str(item).strip()]
    if findings:
        lines.extend(_markdown_section_lines("Findings", findings, preserve_blocks=True))

    evidence_summary = [
        str(item).strip() for item in (report.get("evidence_summary") or []) if str(item).strip()
    ]
    if evidence_summary:
        lines.extend(_markdown_section_lines("Evidence Summary", evidence_summary))

    evidence_refs = [
        str(item).strip() for item in (report.get("evidence_refs") or []) if str(item).strip()
    ]
    if evidence_refs:
        lines.extend(_markdown_section_lines("Sources", evidence_refs))

    contradictions = [
        str(item).strip() for item in (report.get("contradictions") or []) if str(item).strip()
    ]
    if contradictions:
        lines.extend(_markdown_section_lines("Contradictions", contradictions))

    open_questions = [
        str(item).strip() for item in (report.get("open_questions") or []) if str(item).strip()
    ]
    if open_questions:
        lines.extend(_markdown_section_lines("Open Questions", open_questions))

    if artifact_mode == "blocker_report":
        next_steps: list[str] = []
        for row in [*quality_gate_rows, *audit_rows]:
            follow_up = str(row.get("required_follow_up") or "").strip()
            if follow_up:
                next_steps.append(follow_up)
        if next_steps:
            lines.extend(_markdown_section_lines("Next Steps", _unique_clean_values(next_steps, limit=6)))
    else:
        caveats: list[str] = []
        if readiness in {"blocked", "provisional"} and not readiness_note:
            caveats.append(
                "The report should be treated as provisional because some material facts "
                "or source checks remain incomplete."
            )
        for row in verification_rows:
            status_label = str(row.get("status") or "").strip().lower() or "unverified"
            if status_label == "verified":
                continue
            fact = str(row.get("fact") or "").strip()
            if not fact:
                continue
            note = str(row.get("note") or "").strip()
            source = str(row.get("source") or "").strip()
            suffix = f" Source: {source}." if source else ""
            caveats.append(f"{status_label}: {fact}.{suffix} {note}".strip())
        for row in integrity_rows:
            status_label = str(row.get("status") or "").strip().lower()
            if status_label not in {"accepted_with_proxy", "unverifiable"}:
                continue
            claim = str(row.get("claim") or "").strip()
            rationale = str(row.get("rationale") or "").strip()
            source = str(row.get("source") or "").strip()
            suffix = f" Source: {source}." if source else ""
            if claim:
                caveats.append(f"{status_label}: {claim}.{suffix} {rationale}".strip())
        for row in audit_rows:
            severity = str(row.get("severity") or "").strip().lower() or "minor"
            if severity not in {"blocking", "major", "critical"}:
                continue
            issue = str(row.get("issue") or "").strip()
            if not issue:
                continue
            follow_up = str(row.get("required_follow_up") or "").strip()
            suffix = f" Follow-up: {follow_up}" if follow_up else ""
            caveats.append(f"{severity}: {issue}.{suffix}".strip())
        if caveats:
            lines.extend(_markdown_section_lines("Caveats", _unique_clean_values(caveats, limit=8)))

    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def _write_text_output(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _default_markdown_report_path(report: ResearchOrganismReport) -> Path | None:
    event_log_path = str(report.event_log_path or "").strip()
    if not event_log_path:
        return None
    return Path(event_log_path).expanduser().resolve().with_name("report.md")


def _attach_default_markdown_report(
    report: ResearchOrganismReport,
) -> ResearchOrganismReport:
    destination = _default_markdown_report_path(report)
    if destination is None:
        return report
    markdown = _render_report_markdown(report.model_dump(mode="json"))
    _write_text_output(destination, markdown)
    report_path = str(destination)
    if str(report.markdown_report_path or "").strip() == report_path:
        return report
    return report.model_copy(update={"markdown_report_path": report_path})


def _default_reply_markdown_artifact_path(
    *,
    product_paths: ResearchProductPaths,
    session: ResearchCliSession,
) -> Path:
    reply_index = sum(
        1
        for entry in list(session.conversation or [])
        if str(getattr(entry, "role", "") or "").strip() == "user"
    )
    if reply_index <= 0:
        reply_index = len(list(session.turns or [])) + 1
    return (
        Path(product_paths.runs_dir).expanduser().resolve() / f"reply-{reply_index:02d}" / "report.md"
    )


def _attach_orchestrator_report_artifact(
    outcome: ResearchConversationOutcome,
    *,
    product_paths: ResearchProductPaths,
    session: ResearchCliSession,
) -> ResearchConversationOutcome:
    if outcome.response_kind != "report_reply":
        return outcome
    artifact_source = str(outcome.artifact_markdown or "").strip()
    if not artifact_source and outcome.assistant_messages:
        artifact_source = outcome.assistant_messages[-1]
    markdown = _assistant_block_text(artifact_source)
    if not markdown:
        return outcome
    destination = _default_reply_markdown_artifact_path(
        product_paths=product_paths,
        session=session,
    )
    _write_text_output(destination, markdown)
    report_path = str(destination)
    session.orchestrator_state = {
        **dict(session.orchestrator_state),
        "latest_response_artifact": {
            "kind": "report_reply",
            "title": str(outcome.artifact_title or "").strip(),
            "path": report_path,
            "conversation_turn_count": sum(
                1
                for entry in list(session.conversation or [])
                if str(getattr(entry, "role", "") or "").strip() == "user"
            ),
        },
    }
    return outcome.model_copy(
        update={
            "artifact_markdown": markdown,
            "artifact_path": report_path,
        }
    )


def _render_outcome_payload(
    outcome: ResearchConversationOutcome,
    *,
    as_json: bool,
    output_path: str | None,
    workspace_root: Path,
) -> None:
    payload = outcome.model_dump(mode="json")
    if output_path:
        destination = _resolve_user_path(output_path, base_dir=workspace_root)
        if (
            outcome.response_kind == "report_reply"
            and str(outcome.artifact_markdown or "").strip()
            and destination.suffix.lower() in {".md", ".markdown"}
        ):
            _write_text_output(destination, outcome.artifact_markdown)
        else:
            _write_text_output(
                destination,
                json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
            )
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


def _render_report(
    report: ResearchOrganismReport,
    *,
    as_json: bool,
    output_path: str | None,
    workspace_root: Path,
) -> None:
    if output_path:
        destination = _resolve_user_path(output_path, base_dir=workspace_root)
        if destination.suffix.lower() in {".md", ".markdown"}:
            _write_text_output(
                destination,
                _render_report_markdown(report.model_dump(mode="json")),
            )
        else:
            _write_text_output(destination, report.model_dump_json(indent=2))
    if as_json:
        print(report.model_dump_json(indent=2))
    else:
        _print_research_report(report.model_dump(mode="json"))


def _print_repl_help() -> None:
    print("Commands:")
    print("/help    Show this help")
    print("/tools   List available local research tools")
    print("/status  Show workspace/session status")
    print("/history Show recent research turns")
    print("/summary Show session-level research summary")
    print("/reset   Clear carried-forward conversation and research context")
    print("/clear   Alias for /reset")
    print("/exit    Exit the research CLI")
    print()
    print("Notes:")
    print("- natural-language turns go through the durable orchestrator first")
    print("- only explicit slash commands stay local to the shell")
    print("- concrete investigation requests launch the bounded deep-research organ")


def _print_session_status(
    *,
    session: ResearchCliSession,
    workspace_root: Path,
    product_paths: ResearchProductPaths,
    model: str,
    thinking_mode: str,
    tool_ids: Sequence[str],
    delivery_target: str,
    depth_profile: str,
    research_reader_count: int | None,
    max_supervision_loops: int | None,
    max_tool_rounds: int | None,
    max_tool_calls: int,
    max_runtime_seconds: int | None,
    persist_session: bool,
) -> None:
    print(f"product: {RESEARCH_PRODUCT_NAME}")
    print(f"workspace: {workspace_root}")
    print(f"effective working directory: {workspace_root}")
    print(
        "shell process directory: "
        f"{Path(os.environ.get('PWD') or str(workspace_root)).expanduser()}"
    )
    print(f"session: {session.session_id}")
    print(f"turns: {len(session.turns)}")
    print(f"conversation messages: {len(session.conversation)}")
    print(f"model: {model}")
    print(f"thinking mode: {thinking_mode}")
    print(f"tools: {', '.join(tool_ids) or '(none)'}")
    print(f"delivery target: {delivery_target}")
    print(f"depth: {depth_profile}")
    print(f"research readers: {research_reader_count if research_reader_count is not None else 'auto'}")
    print(
        "max supervision loops: "
        + (str(max_supervision_loops) if max_supervision_loops is not None else "unbounded")
    )
    print(
        "max tool rounds: "
        + (str(max_tool_rounds) if max_tool_rounds is not None else "unbounded")
    )
    print(f"max tool calls: {max_tool_calls}")
    print(
        "max runtime seconds per cell: "
        + (str(max_runtime_seconds) if max_runtime_seconds is not None else "unbounded")
    )
    if session.pending_clarification:
        print(f"pending clarification: {session.pending_clarification}")
    print(f"session persistence: {'enabled' if persist_session else 'disabled'}")
    if persist_session:
        print(f"session file: {product_paths.session}")
        print(f"transcript: {product_paths.transcript}")
        print(f"control log: {product_paths.control_log}")


def _print_session_history(session: ResearchCliSession, *, limit: int = 10) -> None:
    if not session.turns:
        print("No saved research turns.")
        return
    print("Recent turns:")
    start_index = max(0, len(session.turns) - limit)
    for offset, report in enumerate(session.turns[start_index:], start=start_index + 1):
        print(
            f"{offset}. [{report.status}] {report.objective} "
            f"(confidence: {report.confidence if report.confidence is not None else '(unset)'})"
        )


def _print_session_rollup(session: ResearchCliSession) -> None:
    if not session.turns:
        print("Session summary: no completed turns yet.")
        return
    evidence_refs: list[str] = []
    open_questions: list[str] = []
    for report in session.turns:
        evidence_refs.extend(report.evidence_refs)
        open_questions.extend(report.open_questions)
    unique_refs = _dedupe(evidence_refs)
    unique_questions = _dedupe(open_questions)
    print(
        "Session summary: "
        f"{len(session.turns)} turns, "
        f"{len(unique_refs)} evidence refs, "
        f"{len(unique_questions)} open questions"
    )
    if unique_refs:
        print(f"Evidence refs: {', '.join(unique_refs[:8])}")
    if len(unique_refs) > 8:
        print(f"More refs: +{len(unique_refs) - 8}")


def _load_or_create_session(
    *,
    product_paths: ResearchProductPaths,
    workspace_root: Path,
    new_session: bool,
    persist_session: bool,
) -> tuple[ResearchCliSession, dict[str, Any]]:
    session_path = Path(product_paths.session)
    if persist_session and not new_session:
        loaded = load_research_product_session(product_paths)
        if loaded is not None:
            should_persist = False
            refresh_reason: list[str] = []
            if not loaded.workspace_root:
                loaded.workspace_root = str(workspace_root)
                should_persist = True
                refresh_reason.append("workspace_root")
            if loaded.refresh_for_current_runtime():
                should_persist = True
                refresh_reason.append("runtime_build")
            if should_persist:
                save_research_product_session(product_paths, loaded)
            return loaded, {
                "status": "loaded",
                "session_exists": session_path.exists(),
                "loaded_turns": len(loaded.turns),
                "refresh_reasons": refresh_reason,
                "persisted_refresh": should_persist,
            }
        if session_path.exists():
            return ResearchCliSession(workspace_root=str(workspace_root)), {
                "status": "recovered_blank_or_invalid",
                "session_exists": True,
                "loaded_turns": 0,
            }
        return ResearchCliSession(workspace_root=str(workspace_root)), {
            "status": "created_missing",
            "session_exists": False,
            "loaded_turns": 0,
        }
    if new_session:
        status = "created_new_session"
    elif not persist_session:
        status = "created_no_persist"
    else:
        status = "created"
    return ResearchCliSession(workspace_root=str(workspace_root)), {
        "status": status,
        "session_exists": session_path.exists(),
        "loaded_turns": 0,
    }


def _persist_session(
    *,
    product_paths: ResearchProductPaths,
    session: ResearchCliSession,
    report: ResearchOrganismReport,
) -> None:
    save_research_product_session(product_paths, session)
    append_research_product_transcript(product_paths, session=session, report=report)


def _initialize_product_config(
    *,
    product_paths: ResearchProductPaths,
    workspace_root: Path,
    product_config: ResearchProductConfig | None,
    args,
) -> ResearchProductConfig:
    depth_profile = _effective_depth_profile(args.depth, product_config=product_config)
    max_tool_rounds, max_tool_calls = _depth_budget(
        depth_profile,
        requested_depth=args.depth,
        explicit_rounds=args.max_tool_rounds,
        explicit_calls=args.max_tool_calls,
        product_config=product_config,
    )
    config = ResearchProductConfig(
        workspace_root=str(workspace_root),
        default_model=_maybe_resolve_live_model(args.model, product_config=product_config),
        thinking_mode=_effective_thinking_mode(
            args.thinking_mode,
            product_config=product_config,
        ),
        default_tool_ids=_effective_research_tool_ids(
            args.tool_ids,
            product_config=product_config,
        ),
        acceptance_criteria=_effective_acceptance_criteria(
            args.acceptance_criteria,
            product_config=product_config,
        ),
        delivery_target=_effective_delivery_target(
            args.delivery_target,
            product_config=product_config,
        ),
        depth_profile=depth_profile,
        max_supervision_loops=_effective_max_supervision_loops(
            args.max_supervision_loops,
            product_config=product_config,
        ),
        research_reader_count=_effective_research_reader_count(
            args.research_readers,
            product_config=product_config,
        ),
        max_tool_rounds=max_tool_rounds,
        max_tool_calls=max_tool_calls,
    )
    write_research_product_config(product_paths, config)
    return config


def _resolved_config_payload(
    *,
    workspace_root: Path,
    product_paths: ResearchProductPaths,
    product_config: ResearchProductConfig | None,
    session: ResearchCliSession | None,
    requested_model: str | None,
    tool_ids: Sequence[str],
    acceptance_criteria: Sequence[str],
    delivery_target: str,
    depth_profile: str,
    research_reader_count: int | None,
    max_supervision_loops: int | None,
    max_tool_rounds: int | None,
    max_tool_calls: int,
    max_runtime_seconds: int | None,
    thinking_mode: str,
    persist_session: bool,
) -> dict[str, Any]:
    return {
        "product_name": RESEARCH_PRODUCT_NAME,
        "workspace_root": str(workspace_root),
        "product_dir": product_paths.root,
        "config_path": product_paths.config,
        "session_path": product_paths.session,
        "transcript_path": product_paths.transcript,
        "control_log_path": product_paths.control_log,
        "config_exists": Path(product_paths.config).exists(),
        "session_exists": Path(product_paths.session).exists(),
        "persist_session": persist_session,
        "configured_default_model": (
            str(product_config.default_model).strip()
            if product_config and product_config.default_model is not None
            else None
        ),
        "configured_thinking_mode": (
            str(product_config.thinking_mode).strip()
            if product_config is not None
            else None
        ),
        "resolved_model": _maybe_resolve_live_model(
            requested_model,
            product_config=product_config,
        ),
        "resolved_thinking_mode": thinking_mode,
        "tool_ids": list(tool_ids),
        "acceptance_criteria": list(acceptance_criteria),
        "delivery_target": delivery_target,
        "depth_profile": depth_profile,
        "research_reader_count": research_reader_count,
        "max_supervision_loops": max_supervision_loops,
        "max_tool_rounds": max_tool_rounds,
        "max_tool_calls": int(max_tool_calls),
        "max_runtime_seconds": max_runtime_seconds,
        "saved_turns": len(session.turns) if session is not None else 0,
    }


def _print_config_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return
    print(f"product: {payload['product_name']}")
    print(f"workspace: {payload['workspace_root']}")
    print(f"product dir: {payload['product_dir']}")
    print(f"config path: {payload['config_path']}")
    print(f"session path: {payload['session_path']}")
    print(f"control log: {payload['control_log_path']}")
    print(f"resolved model: {payload.get('resolved_model') or '(unset)'}")
    print(f"thinking mode: {payload.get('resolved_thinking_mode') or 'auto'}")
    print(f"tools: {', '.join(payload['tool_ids']) or '(none)'}")
    print(f"delivery target: {payload['delivery_target']}")
    print(f"depth: {payload['depth_profile']}")
    print(
        "research readers: "
        + (
            str(payload["research_reader_count"])
            if payload.get("research_reader_count") is not None
            else "auto"
        )
    )
    print(
        "max supervision loops: "
        + (
            str(payload["max_supervision_loops"])
            if payload.get("max_supervision_loops") is not None
            else "unbounded"
        )
    )
    max_tool_rounds = payload.get("max_tool_rounds")
    print(
        "max tool rounds: "
        + (str(max_tool_rounds) if max_tool_rounds is not None else "unbounded")
    )
    print(f"max tool calls: {payload['max_tool_calls']}")
    print(
        "max runtime seconds per cell: "
        + (
            str(payload["max_runtime_seconds"])
            if payload.get("max_runtime_seconds") is not None
            else "unbounded"
        )
    )
    print(f"saved turns: {payload['saved_turns']}")
    print(f"session persistence: {'enabled' if payload['persist_session'] else 'disabled'}")


def _interactive_loop(
    *,
    args,
    controller: ResearchConversationController,
    llm_provider: LLMProvider,
    workspace_root: Path,
    model: str,
    thinking_mode: str,
    session: ResearchCliSession,
    product_paths: ResearchProductPaths,
    tool_ids: Sequence[str],
    acceptance_criteria: Sequence[str],
    delivery_target: str,
    hard_constraints: Sequence[str],
    soft_constraints: Sequence[str],
    evidence_summaries: Sequence[str],
    reader_briefs: Sequence[str],
    research_reader_count: int | None,
    max_supervision_loops: int | None,
    depth_profile: str,
    max_tool_rounds: int | None,
    max_tool_calls: int,
    max_runtime_seconds: int | None,
    persist_session: bool,
    run_root: Path,
    progress_renderer: ResearchProgressRenderer,
    control_logger: ResearchEventLogger | None,
) -> int:
    _log_event(
        control_logger,
        "interactive.started",
        session_id=session.session_id,
        resumed_turns=len(session.turns),
        workspace_root=str(workspace_root),
    )
    print(RESEARCH_PRODUCT_NAME)
    print(f"workspace: {workspace_root}")
    print(f"session: {session.session_id}")
    if session.turns:
        print(f"resumed: {len(session.turns)} prior turns")
    print("enter a research task, or /help")
    while True:
        try:
            raw = input("danresearch> ")
        except EOFError:
            _log_event(control_logger, "interactive.exited", reason="eof")
            print()
            return 0
        except KeyboardInterrupt:
            _log_event(control_logger, "interactive.exited", reason="keyboard_interrupt")
            print()
            return 0

        objective = str(raw or "").strip()
        if not objective:
            continue
        _log_event(
            control_logger,
            "interactive.input.received",
            text=objective,
            is_command=objective.startswith("/"),
        )
        if objective in {"/exit", "exit", "quit", ":q"}:
            _log_event(control_logger, "interactive.exited", reason="user_exit")
            return 0
        if objective in {"/help", "help"}:
            _log_event(control_logger, "interactive.command.executed", command="help")
            _print_repl_help()
            continue
        if objective == "/tools":
            _log_event(control_logger, "interactive.command.executed", command="tools")
            _print_tool_catalog(as_json=False)
            continue
        if objective == "/status":
            _log_event(control_logger, "interactive.command.executed", command="status")
            _print_session_status(
                session=session,
                workspace_root=workspace_root,
                product_paths=product_paths,
                model=model,
                thinking_mode=thinking_mode,
                tool_ids=tool_ids,
                delivery_target=delivery_target,
                depth_profile=depth_profile,
                research_reader_count=research_reader_count,
                max_supervision_loops=max_supervision_loops,
                max_tool_rounds=max_tool_rounds,
                max_tool_calls=max_tool_calls,
                max_runtime_seconds=max_runtime_seconds,
                persist_session=persist_session,
            )
            continue
        if objective == "/history":
            _log_event(control_logger, "interactive.command.executed", command="history")
            _print_session_history(session)
            continue
        if objective == "/summary":
            _log_event(control_logger, "interactive.command.executed", command="summary")
            _print_session_rollup(session)
            continue
        if objective in {"/reset", "/clear"}:
            session = ResearchCliSession(workspace_root=str(workspace_root))
            if persist_session:
                save_research_product_session(product_paths, session)
            _log_event(
                control_logger,
                "interactive.command.executed",
                command="reset",
                persisted=bool(persist_session),
            )
            print("session context cleared")
            continue
        outcome = asyncio.run(
            _run_orchestrated_turn(
                args=args,
                controller=controller,
                llm_provider=llm_provider,
                workspace_root=workspace_root,
                model=model,
                objective=objective,
                session=session,
                tool_ids=tool_ids,
                acceptance_criteria=acceptance_criteria,
                delivery_target=delivery_target,
                hard_constraints=hard_constraints,
                soft_constraints=soft_constraints,
                default_evidence_summaries=evidence_summaries,
                default_reader_briefs=reader_briefs,
                research_reader_count=research_reader_count,
                max_supervision_loops=max_supervision_loops,
                depth_profile=depth_profile,
                max_tool_rounds=max_tool_rounds,
                max_tool_calls=max_tool_calls,
                max_runtime_seconds=max_runtime_seconds,
                thinking_mode=thinking_mode,
                run_root=run_root,
                progress_renderer=progress_renderer,
                control_logger=control_logger,
            )
        )
        if outcome.reports:
            for index, report in enumerate(outcome.reports):
                report = _attach_default_markdown_report(report)
                report = report.model_copy(
                    update={
                        "control_log_path": str(product_paths.control_log),
                        "control_log_schema": ORGANISM_LOG_SCHEMA_VERSION,
                    }
                )
                outcome.reports[index] = report
                session.record_turn(report)
                if persist_session:
                    _persist_session(product_paths=product_paths, session=session, report=report)
                    _log_event(
                        control_logger,
                        "session.persisted",
                        session_id=session.session_id,
                        trace_id=report.trace_id,
                        event_log_path=report.event_log_path,
                        control_log_path=report.control_log_path,
                    )
            _render_report(
                outcome.reports[-1],
                as_json=bool(args.json),
                output_path=args.output,
                workspace_root=workspace_root,
            )
            _log_event(
                control_logger,
                "interactive.objective.completed",
                status=outcome.status,
                report_count=len(outcome.reports),
                final_report_status=outcome.reports[-1].status,
            )
        else:
            if outcome.response_kind == "report_reply":
                outcome = _attach_orchestrator_report_artifact(
                    outcome,
                    product_paths=product_paths,
                    session=session,
                )
                _log_event(
                    control_logger,
                    "orchestrator.report_artifact.persisted",
                    artifact_kind=outcome.response_kind,
                    artifact_path=outcome.artifact_path,
                    report_count=0,
                )
                if args.output:
                    _render_outcome_payload(
                        outcome,
                        as_json=bool(args.json),
                        output_path=args.output,
                        workspace_root=workspace_root,
                    )
            if persist_session:
                save_research_product_session(product_paths, session)
                _log_event(
                    control_logger,
                    "session.persisted",
                    session_id=session.session_id,
                    report_count=0,
                    control_log_path=str(product_paths.control_log),
                )
        if not args.json and outcome.reports:
            _print_session_rollup(session)
            print()


def main(argv: Sequence[str] | None = None) -> int:
    load_env()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.list_tools:
        _print_tool_catalog(as_json=bool(args.json))
        return 0
    if args.research_readers is not None and not (
        1 <= args.research_readers <= MAX_DEEP_RESEARCH_READERS
    ):
        parser.error(
            f"--research-readers must be between 1 and {MAX_DEEP_RESEARCH_READERS}"
        )

    resolved_config = resolve_config(workspace=args.workspace)
    workspace_root = normalize_workspace_root(str(resolved_config["workspace"]))
    os.environ["DAN_WORKSPACE_ROOT"] = str(workspace_root)

    product_paths = resolve_research_product_paths(
        workspace_root,
        session_file=args.session_file,
    )
    control_logger = ResearchEventLogger(
        path=Path(product_paths.control_log),
        stream_kind="control_plane",
    )
    _log_event(
        control_logger,
        "cli.started",
        argv=list(argv) if argv is not None else sys.argv[1:],
        workspace_root=str(workspace_root),
        session_file=args.session_file,
    )
    _log_event(
        control_logger,
        "product.paths.resolved",
        product_dir=product_paths.root,
        config_path=product_paths.config,
        session_path=product_paths.session,
        transcript_path=product_paths.transcript,
        control_log_path=product_paths.control_log,
        runs_dir=product_paths.runs_dir,
    )
    product_config = load_research_product_config(product_paths)
    _log_event(
        control_logger,
        "product.config.loaded",
        status=(
            "loaded"
            if product_config is not None
            else ("recovered_blank_or_invalid" if Path(product_paths.config).exists() else "missing")
        ),
        path=product_paths.config,
    )
    persist_session = not bool(args.no_session_persist)
    session, session_state = _load_or_create_session(
        product_paths=product_paths,
        workspace_root=workspace_root,
        new_session=bool(args.new_session),
        persist_session=persist_session,
    )
    control_logger.update_context(session_id=session.session_id)
    _log_event(
        control_logger,
        "session.load.completed",
        path=product_paths.session,
        persist_session=persist_session,
        **session_state,
    )

    tool_ids = _effective_research_tool_ids(
        args.tool_ids,
        product_config=product_config,
    )
    if args.tool_ids and not tool_ids:
        parser.error("DAN Research only allows read-only tools on this surface")
    acceptance_criteria = _effective_acceptance_criteria(
        args.acceptance_criteria,
        product_config=product_config,
    )
    delivery_target = _effective_delivery_target(
        args.delivery_target,
        product_config=product_config,
    )
    hard_constraints = _non_empty(
        args.hard_constraints,
        DEFAULT_RESEARCH_HARD_CONSTRAINTS,
    )
    soft_constraints = _non_empty(
        args.soft_constraints,
        DEFAULT_RESEARCH_SOFT_CONSTRAINTS,
    )
    evidence_summaries = _dedupe(args.evidence_summaries)
    reader_briefs = _dedupe(args.reader_briefs)
    try:
        depth_profile = _effective_depth_profile(
            args.depth,
            product_config=product_config,
        )
        thinking_mode = _effective_thinking_mode(
            args.thinking_mode,
            product_config=product_config,
        )
        max_supervision_loops = _effective_max_supervision_loops(
            args.max_supervision_loops,
            product_config=product_config,
        )
    except ValueError as exc:
        parser.error(str(exc))
    max_tool_rounds, max_tool_calls = _depth_budget(
        depth_profile,
        requested_depth=args.depth,
        explicit_rounds=args.max_tool_rounds,
        explicit_calls=args.max_tool_calls,
        product_config=product_config,
    )
    max_runtime_seconds = _depth_runtime_budget(depth_profile)
    research_reader_count = _effective_research_reader_count(
        args.research_readers,
        product_config=product_config,
    )

    if args.init:
        config = _initialize_product_config(
            product_paths=product_paths,
            workspace_root=workspace_root,
            product_config=product_config,
            args=args,
        )
        _log_event(
            control_logger,
            "product.config.initialized",
            path=product_paths.config,
            depth_profile=config.depth_profile,
            research_reader_count=config.research_reader_count,
        )
        payload = {
            **_resolved_config_payload(
                workspace_root=workspace_root,
                product_paths=product_paths,
                product_config=config,
                session=session,
                requested_model=args.model,
                tool_ids=config.default_tool_ids,
                acceptance_criteria=config.acceptance_criteria,
                delivery_target=config.delivery_target,
                depth_profile=config.depth_profile,
                research_reader_count=config.research_reader_count,
                max_supervision_loops=config.max_supervision_loops,
                max_tool_rounds=config.max_tool_rounds,
                max_tool_calls=config.max_tool_calls,
                max_runtime_seconds=max_runtime_seconds,
                thinking_mode=config.thinking_mode,
                persist_session=persist_session,
            ),
            "initialized": True,
        }
        _print_config_payload(payload, as_json=bool(args.json))
        _log_event(control_logger, "cli.completed", exit_code=0, mode="init")
        control_logger.close()
        return 0

    if args.show_config:
        payload = _resolved_config_payload(
            workspace_root=workspace_root,
            product_paths=product_paths,
            product_config=product_config,
            session=session,
            requested_model=args.model,
            tool_ids=tool_ids,
            acceptance_criteria=acceptance_criteria,
            delivery_target=delivery_target,
            depth_profile=depth_profile,
            research_reader_count=research_reader_count,
            max_supervision_loops=max_supervision_loops,
            max_tool_rounds=max_tool_rounds,
            max_tool_calls=max_tool_calls,
            max_runtime_seconds=max_runtime_seconds,
            thinking_mode=thinking_mode,
            persist_session=persist_session,
        )
        _print_config_payload(payload, as_json=bool(args.json))
        _log_event(control_logger, "cli.completed", exit_code=0, mode="show_config")
        control_logger.close()
        return 0

    try:
        _log_event(
            control_logger,
            "provider.build.started",
            requested_model=args.model,
            thinking_mode=thinking_mode,
        )
        live_model = _resolve_live_model(args.model, product_config=product_config)
        provider = _build_live_provider(
            live_model,
            api_key=args.api_key,
            base_url=args.base_url,
        )
        _log_event(
            control_logger,
            "provider.build.completed",
            model=live_model,
            thinking_mode=thinking_mode,
        )
    except Exception as exc:
        _log_event(
            control_logger,
            "provider.build.failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        control_logger.close()
        parser.error(str(exc))

    run_root = (
        _resolve_user_path(args.workdir, base_dir=workspace_root)
        if args.workdir
        else Path(product_paths.runs_dir)
    )
    progress_renderer = ResearchProgressRenderer(
        enabled=not bool(args.json) and not bool(args.quiet_progress),
        show_model_trace=bool(args.show_model_trace),
    )
    conversation_controller = ResearchConversationController(
        provider=provider,
        model=live_model,
        stream_text_responses=bool(args.show_model_trace),
        provider_request_overrides=_provider_request_overrides_for_thinking_mode(
            thinking_mode
        ),
        event_callback=progress_renderer,
    )

    if args.objective is None:
        exit_code = _interactive_loop(
            args=args,
            controller=conversation_controller,
            llm_provider=provider,
            workspace_root=workspace_root,
            model=live_model,
            thinking_mode=thinking_mode,
            session=session,
            product_paths=product_paths,
            tool_ids=tool_ids,
            acceptance_criteria=acceptance_criteria,
            delivery_target=delivery_target,
            hard_constraints=hard_constraints,
            soft_constraints=soft_constraints,
            evidence_summaries=evidence_summaries,
            reader_briefs=reader_briefs,
            research_reader_count=research_reader_count,
            max_supervision_loops=max_supervision_loops,
            depth_profile=depth_profile,
            max_tool_rounds=max_tool_rounds,
            max_tool_calls=max_tool_calls,
            max_runtime_seconds=max_runtime_seconds,
            persist_session=persist_session,
            run_root=run_root,
            progress_renderer=progress_renderer,
            control_logger=control_logger,
        )
        _log_event(control_logger, "cli.completed", exit_code=exit_code, mode="interactive")
        control_logger.close()
        return exit_code

    try:
        outcome = asyncio.run(
            _run_orchestrated_turn(
                args=args,
                controller=conversation_controller,
                llm_provider=provider,
                workspace_root=workspace_root,
                model=live_model,
                objective=str(args.objective),
                session=session,
                tool_ids=tool_ids,
                acceptance_criteria=acceptance_criteria,
                delivery_target=delivery_target,
                hard_constraints=hard_constraints,
                soft_constraints=soft_constraints,
                default_evidence_summaries=evidence_summaries,
                default_reader_briefs=reader_briefs,
                research_reader_count=research_reader_count,
                max_supervision_loops=max_supervision_loops,
                depth_profile=depth_profile,
                max_tool_rounds=max_tool_rounds,
                max_tool_calls=max_tool_calls,
                max_runtime_seconds=max_runtime_seconds,
                thinking_mode=thinking_mode,
                run_root=run_root,
                progress_renderer=progress_renderer,
                control_logger=control_logger,
            )
        )
    except Exception as exc:
        _log_event(
            control_logger,
            "cli.failed",
            mode="single_turn",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        control_logger.close()
        raise
    for index, report in enumerate(outcome.reports):
        report = _attach_default_markdown_report(report)
        report = report.model_copy(
            update={
                "control_log_path": str(product_paths.control_log),
                "control_log_schema": ORGANISM_LOG_SCHEMA_VERSION,
            }
        )
        outcome.reports[index] = report
        session.record_turn(report)
        if persist_session:
            _persist_session(product_paths=product_paths, session=session, report=report)
            _log_event(
                control_logger,
                "session.persisted",
                session_id=session.session_id,
                trace_id=report.trace_id,
                event_log_path=report.event_log_path,
                control_log_path=report.control_log_path,
            )
    if not outcome.reports and outcome.response_kind == "report_reply":
        outcome = _attach_orchestrator_report_artifact(
            outcome,
            product_paths=product_paths,
            session=session,
        )
        _log_event(
            control_logger,
            "orchestrator.report_artifact.persisted",
            artifact_kind=outcome.response_kind,
            artifact_path=outcome.artifact_path,
            report_count=0,
        )
    if persist_session and not outcome.reports:
        save_research_product_session(product_paths, session)
        _log_event(
            control_logger,
            "session.persisted",
            session_id=session.session_id,
            report_count=0,
            control_log_path=str(product_paths.control_log),
        )

    if outcome.reports:
        _render_report(
            outcome.reports[-1],
            as_json=bool(args.json),
            output_path=args.output,
            workspace_root=workspace_root,
        )
        exit_code = 0 if outcome.reports[-1].status == "completed" else 1
        _log_event(
            control_logger,
            "cli.completed",
            exit_code=exit_code,
            mode="single_turn",
            final_status=outcome.reports[-1].status,
            final_trace_id=outcome.reports[-1].trace_id,
        )
        control_logger.close()
        return exit_code

    _render_outcome_payload(
        outcome,
        as_json=bool(args.json),
        output_path=args.output,
        workspace_root=workspace_root,
    )
    if not args.json and not outcome.assistant_messages and outcome.question:
        print(f"[assistant] {outcome.question}")
    _log_event(
        control_logger,
        "cli.completed",
        exit_code=0,
        mode="single_turn",
        outcome_status=outcome.status,
    )
    control_logger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
