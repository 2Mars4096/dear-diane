"""Deterministic task-lane policy helpers for latency-sensitive product shells."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TaskLane = Literal["fast", "deep"]

_CODE_FAST_HINTS = (
    "review",
    "inspect",
    "audit",
    "summarize",
    "summarise",
    "analyze",
    "analyse",
    "explain",
    "walk through",
    "walk me through",
    "understand",
    "assess",
    "scan",
)
_CODE_DEEP_HINTS = (
    "implement",
    "build",
    "create",
    "add ",
    "edit ",
    "modify",
    "patch",
    "refactor",
    "rename",
    "remove",
    "delete",
    "migrate",
    "wire ",
    "hook ",
    "fix ",
    "bugfix",
    "greenfield",
    "architecture",
    "redesign",
    "roadmap",
    "milestone",
    "phase ",
)
_CODE_BOUNDED_EDIT_HINTS = (
    "bounded",
    "focused",
    "small",
    "existing files",
    "within existing files",
    "keep changes within",
    "no new dependencies",
    "no new files",
    "one bounded",
    "single file",
    "single-file",
    "single full-file",
    "two files",
    "one file",
    "do not modify",
    "must not modify",
    "read back",
    "shell command",
    "exact counts",
)
_CODE_EDIT_INTENT_HINTS = (
    *_CODE_DEEP_HINTS,
    "apply",
    "repair",
    "overwrite",
    "update",
    "change",
    "switch",
    "improve",
    "refine",
    "refresh",
    "visual refresh",
)
_RESEARCH_FAST_HINTS = (
    "review",
    "inspect",
    "summarize",
    "summarise",
    "explain",
    "walk through",
    "walk me through",
    "analyze",
    "analyse",
    "read",
)
_RESEARCH_DOCUMENT_HINTS = (
    "page",
    "pages",
    "paper",
    "pdf",
    "document",
    "chapter",
    "section",
    "memo",
    "report",
    "manuscript",
    "article",
)
_RESEARCH_LIVE_HINTS = (
    "current",
    "currently",
    "latest",
    "recent",
    "today",
    "right now",
    "up to date",
    "as of now",
    "live",
)


@dataclass(frozen=True, slots=True)
class TaskLanePolicy:
    """Deterministic product-shell task lane for latency-sensitive asks."""

    lane: TaskLane
    reason: str
    use_fallback_pre_run_planner: bool = False
    use_fallback_post_run_review: bool = False


def _normalize_text(*values: str) -> str:
    bits = [" ".join(str(value or "").strip().lower().split()) for value in values]
    return " ".join(bit for bit in bits if bit).strip()


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def classify_code_task_lane(
    *,
    user_message: str,
    coding_objective: str,
    benchmark_mode: bool,
    pending_clarification: bool,
    existing_plan_milestones: int,
) -> TaskLanePolicy:
    """Choose whether DAN Code should stay deep or bypass extra control turns."""

    if benchmark_mode:
        return TaskLanePolicy(
            lane="deep",
            reason="Benchmark runs keep the full planner/review control shell.",
        )
    if pending_clarification:
        return TaskLanePolicy(
            lane="deep",
            reason="Pending clarification keeps the conservative deep lane active.",
        )
    if existing_plan_milestones > 1:
        return TaskLanePolicy(
            lane="deep",
            reason="Existing multi-milestone project plans keep the planner in the loop.",
        )

    text = _normalize_text(user_message, coding_objective)
    if not text:
        return TaskLanePolicy(lane="deep", reason="Empty tasks stay on the default deep lane.")
    if _contains_any(text, _CODE_EDIT_INTENT_HINTS) and _contains_any(
        text,
        _CODE_BOUNDED_EDIT_HINTS,
    ):
        return TaskLanePolicy(
            lane="fast",
            reason="Bounded existing-file edit requests can use deterministic planner/review fallbacks.",
            use_fallback_pre_run_planner=True,
            use_fallback_post_run_review=True,
        )
    if _contains_any(text, _CODE_DEEP_HINTS):
        return TaskLanePolicy(
            lane="deep",
            reason="Implementation/edit cues keep the full deep coding lane active.",
        )
    if _contains_any(text, _CODE_FAST_HINTS):
        return TaskLanePolicy(
            lane="fast",
            reason="Obvious bounded review/inspection asks can use deterministic planner/review fallbacks.",
            use_fallback_pre_run_planner=True,
            use_fallback_post_run_review=True,
        )
    return TaskLanePolicy(
        lane="deep",
        reason="Non-review coding work stays on the default deep lane.",
    )


def classify_research_task_lane(
    *,
    user_message: str,
    research_objective: str,
    pending_clarification: bool,
    depth_profile: str,
    requested_reader_count: int | None,
) -> TaskLanePolicy:
    """Choose whether DAN Research should bypass extra planning/review turns."""

    if pending_clarification:
        return TaskLanePolicy(
            lane="deep",
            reason="Pending clarification keeps the conservative deep research lane active.",
        )
    if str(depth_profile or "").strip().lower() == "deep":
        return TaskLanePolicy(
            lane="deep",
            reason="Explicit deep depth keeps the full research control shell active.",
        )
    if requested_reader_count is not None and requested_reader_count > 4:
        return TaskLanePolicy(
            lane="deep",
            reason="Wide explicit reader fan-out keeps the full research planning shell active.",
        )

    text = _normalize_text(user_message, research_objective)
    if not text:
        return TaskLanePolicy(lane="deep", reason="Empty tasks stay on the default deep lane.")
    if _contains_any(text, _RESEARCH_LIVE_HINTS):
        return TaskLanePolicy(
            lane="deep",
            reason="Live/current evidence requests keep the full research planning shell active.",
        )
    if _contains_any(text, _RESEARCH_FAST_HINTS) and _contains_any(
        text, _RESEARCH_DOCUMENT_HINTS
    ):
        return TaskLanePolicy(
            lane="fast",
            reason="Bounded document/page review asks can use deterministic plan/review fallbacks.",
            use_fallback_pre_run_planner=True,
            use_fallback_post_run_review=True,
        )
    return TaskLanePolicy(
        lane="deep",
        reason="General research tasks stay on the default deep lane.",
    )


__all__ = [
    "TaskLanePolicy",
    "classify_code_task_lane",
    "classify_research_task_lane",
    "override_task_lane_policy",
]


def override_task_lane_policy(
    policy: TaskLanePolicy,
    *,
    override: str | None,
) -> TaskLanePolicy:
    """Apply an explicit operator override without re-running classification."""

    normalized = str(override or "").strip().lower()
    if not normalized or normalized == "auto":
        return policy
    if normalized == "fast":
        return TaskLanePolicy(
            lane="fast",
            reason="Explicit task-lane override forced the fast lane.",
            use_fallback_pre_run_planner=True,
            use_fallback_post_run_review=True,
        )
    if normalized == "deep":
        return TaskLanePolicy(
            lane="deep",
            reason="Explicit task-lane override forced the deep lane.",
        )
    return policy
