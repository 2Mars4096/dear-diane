from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .task_registry import ConciergeTask, TaskState

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STATUS_RE = re.compile(
    r"^(?:/status(?:\s+\S+)?|status|what(?:'s| is) happening|what(?:'s| is) going on|are you done)\b",
    re.IGNORECASE,
)
_RETRY_RE = re.compile(r"^(?:/retry\b|retry\b|redo\b|try again\b|run it again\b)", re.IGNORECASE)
_SUPERSEDE_RE = re.compile(
    r"^(?:actually|instead|ignore\b|correction\b|wait\b|hold on\b|stop\b|cancel\b|change of plan\b|new instruction\b)",
    re.IGNORECASE,
)
_CONTINUATION_RE = re.compile(
    r"\b(?:continue|also|then|next|update|refine|focus|expand|check|review|show|summarize|that|this|it)\b",
    re.IGNORECASE,
)
_TASK_REF_RE = re.compile(r"\b(task_[a-f0-9]{12})\b")


class FollowUpType(str, Enum):
    NEW_TASK = "new_task"
    REFINE_TASK = "refine_task"
    SUPERSEDE_TASK = "supersede_task"
    RETRY_TASK = "retry_task"
    ANSWER_CLARIFICATION = "answer_clarification"
    QUERY_STATUS = "query_status"


class FollowUpResolution(BaseModel):
    follow_up_type: FollowUpType
    task_id: str | None = None
    requires_disambiguation: bool = False
    prompt: str | None = None
    candidate_task_ids: list[str] = Field(default_factory=list)


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            return token[: -len(suffix)]
    return token


def _tokenize(text: str) -> set[str]:
    return {_stem(token) for token in _TOKEN_RE.findall(str(text or "").lower())}


def _similarity(a: str, b: str) -> float:
    left = _tokenize(a)
    right = _tokenize(b)
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    return intersection / union if union else 0.0


def build_disambiguation_prompt(tasks: list[ConciergeTask]) -> str:
    lines = ["Which task did you mean?"]
    for index, task in enumerate(tasks, 1):
        lines.append(f"({index}) {task.title} — {task.state.value}")
    return " ".join(lines)


def classify_follow_up(
    text: str,
    *,
    triage: Any | None = None,
    tasks: list[ConciergeTask] | None = None,
    waiting_task_id: str | None = None,
) -> FollowUpResolution:
    body = str(text or "").strip()
    lower = body.lower()
    candidates = list(tasks or [])

    if _STATUS_RE.search(lower):
        return FollowUpResolution(follow_up_type=FollowUpType.QUERY_STATUS)

    explicit_task = None
    if triage is not None:
        explicit_task = str(getattr(triage, "resume_task_id", "") or "").strip() or None
    if explicit_task is None:
        match = _TASK_REF_RE.search(lower)
        if match:
            explicit_task = match.group(1)

    if waiting_task_id:
        waiting = next((task for task in candidates if task.task_id == waiting_task_id), None)
        if waiting is not None:
            return FollowUpResolution(
                follow_up_type=FollowUpType.ANSWER_CLARIFICATION,
                task_id=waiting.task_id,
            )

    if explicit_task:
        follow_up_type = FollowUpType.RETRY_TASK if _RETRY_RE.search(lower) else FollowUpType.REFINE_TASK
        if _SUPERSEDE_RE.search(lower):
            follow_up_type = FollowUpType.SUPERSEDE_TASK
        return FollowUpResolution(follow_up_type=follow_up_type, task_id=explicit_task)

    non_terminal = [task for task in candidates if task.state not in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED, TaskState.SUPERSEDED}]
    if _RETRY_RE.search(lower):
        if len(non_terminal) == 1:
            return FollowUpResolution(
                follow_up_type=FollowUpType.RETRY_TASK,
                task_id=non_terminal[0].task_id,
            )
        if non_terminal:
            return FollowUpResolution(
                follow_up_type=FollowUpType.RETRY_TASK,
                requires_disambiguation=True,
                prompt=build_disambiguation_prompt(non_terminal[:3]),
                candidate_task_ids=[task.task_id for task in non_terminal[:3]],
            )

    if _SUPERSEDE_RE.search(lower):
        if len(non_terminal) == 1:
            return FollowUpResolution(
                follow_up_type=FollowUpType.SUPERSEDE_TASK,
                task_id=non_terminal[0].task_id,
            )
        if non_terminal:
            return FollowUpResolution(
                follow_up_type=FollowUpType.SUPERSEDE_TASK,
                requires_disambiguation=True,
                prompt=build_disambiguation_prompt(non_terminal[:3]),
                candidate_task_ids=[task.task_id for task in non_terminal[:3]],
            )

    scored: list[tuple[float, ConciergeTask]] = []
    for task in non_terminal:
        score = max(
            _similarity(body, task.title),
            _similarity(body, task.summary),
            _similarity(body, str(task.metadata.get("original_text") or "")),
        )
        if score >= 0.4:
            scored.append((score, task))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        top_score = scored[0][0]
        top = [task for score, task in scored if score == top_score]
        if len(top) == 1:
            return FollowUpResolution(
                follow_up_type=FollowUpType.REFINE_TASK if _CONTINUATION_RE.search(lower) else FollowUpType.NEW_TASK,
                task_id=top[0].task_id if _CONTINUATION_RE.search(lower) else None,
            )
        return FollowUpResolution(
            follow_up_type=FollowUpType.REFINE_TASK,
            requires_disambiguation=True,
            prompt=build_disambiguation_prompt(top[:3]),
            candidate_task_ids=[task.task_id for task in top[:3]],
        )

    if len(non_terminal) == 1 and _CONTINUATION_RE.search(lower):
        return FollowUpResolution(
            follow_up_type=FollowUpType.REFINE_TASK,
            task_id=non_terminal[0].task_id,
        )

    return FollowUpResolution(follow_up_type=FollowUpType.NEW_TASK)
