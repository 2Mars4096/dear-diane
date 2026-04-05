from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field

from .task_attention import derive_attention
from .task_registry import ConciergeTask, TaskLifecycleEvent, TaskRegistry, TaskState


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskSummary(BaseModel):
    task_id: str
    project_id: str
    title: str
    summary: str = ""
    state: str
    attention_reason: str = "none"
    dispatch_mode: str = "foreground"
    created_at: datetime
    updated_at: datetime
    age_seconds: float = 0.0
    attempt_count: int = 0
    current_attempt_ref: str | None = None
    superseded_by: str | None = None
    last_pause_reason: str | None = None
    has_unresolved_input: bool = False


class DispatcherProjectSummary(BaseModel):
    project_id: str
    active_count: int = 0
    queue_depth: int = 0
    oldest_queued_age: float | None = None


class DispatcherSummary(BaseModel):
    active_background_slots: int = 0
    max_background_slots: int = 0
    global_active: int = 0
    global_max: int = 0
    per_project: list[DispatcherProjectSummary] = Field(default_factory=list)


class ConciergeSnapshot(BaseModel):
    tasks: list[TaskSummary] = Field(default_factory=list)
    dispatcher: DispatcherSummary = Field(default_factory=DispatcherSummary)
    snapshot_at: datetime = Field(default_factory=_utc_now)


def build_task_summary(task: ConciergeTask, *, now: datetime | None = None) -> TaskSummary:
    current = now or _utc_now()
    attention = derive_attention(task, now=current)
    last_pause_reason = None
    if task.state == TaskState.WAITING_INPUT:
        last_pause_reason = str(task.metadata.get("reason") or "needs_input") or None
    return TaskSummary(
        task_id=task.task_id,
        project_id=task.project_id,
        title=task.title,
        summary=task.summary,
        state=task.state.value,
        attention_reason=attention.attention_reason.value,
        dispatch_mode=task.dispatch_mode.value,
        created_at=task.created_at,
        updated_at=task.updated_at,
        age_seconds=max(0.0, (current - task.created_at).total_seconds()),
        attempt_count=task.attempt_count,
        current_attempt_ref=task.current_attempt_ref,
        superseded_by=task.superseded_by,
        last_pause_reason=last_pause_reason,
        has_unresolved_input=bool(task.pending_action_id),
    )


def build_snapshot(
    registry: TaskRegistry,
    *,
    dispatcher: Any | None = None,
    creator_surface: str | None = None,
    include_recent_terminal: bool = True,
) -> ConciergeSnapshot:
    current = _utc_now()
    states = None
    tasks = registry.list_all(limit=None, creator_surface=creator_surface, states=states)
    visible: list[ConciergeTask] = []
    for task in tasks:
        if not include_recent_terminal and task.is_terminal:
            continue
        visible.append(task)
    summaries = [build_task_summary(task, now=current) for task in visible]
    summaries.sort(
        key=lambda item: (
            item.state in {"completed", "failed", "cancelled", "superseded"},
            -item.updated_at.timestamp(),
        )
    )
    dispatcher_summary = DispatcherSummary()
    if dispatcher is not None and hasattr(dispatcher, "snapshot_state"):
        raw = dispatcher.snapshot_state()
        if isinstance(raw, dict):
            dispatcher_summary = DispatcherSummary.model_validate(raw)
    return ConciergeSnapshot(
        tasks=summaries,
        dispatcher=dispatcher_summary,
        snapshot_at=current,
    )


async def task_event_stream(
    registry: TaskRegistry,
    *,
    project_id: str | None = None,
) -> AsyncIterator[TaskLifecycleEvent]:
    sub_id, queue = registry.subscribe(project_id=project_id)
    try:
        while True:
            yield await queue.get()
    finally:
        registry.unsubscribe(sub_id)


def replay_events(
    snapshot: ConciergeSnapshot,
    events: list[TaskLifecycleEvent],
) -> ConciergeSnapshot:
    tasks = {summary.task_id: summary.model_copy(deep=True) for summary in snapshot.tasks}
    for event in events:
        if event.event_type in {"snapshot_reset", "events_dropped"}:
            continue
        task_id = event.task_id
        if not task_id:
            continue
        if event.event_type == "task_created":
            tasks[task_id] = TaskSummary(
                task_id=task_id,
                project_id=event.project_id,
                title=str(event.metadata.get("title") or "Task"),
                summary=str(event.metadata.get("summary") or ""),
                state="queued",
                dispatch_mode=str(event.metadata.get("dispatch_mode") or "foreground"),
                created_at=event.timestamp,
                updated_at=event.timestamp,
            )
            continue
        summary = tasks.get(task_id)
        if summary is None:
            continue
        summary.updated_at = event.timestamp
        if event.event_type in {"task_queued", "task_started", "task_resumed"}:
            summary.state = "running" if event.event_type != "task_queued" else "queued"
        elif event.event_type == "task_paused":
            summary.state = "waiting_input"
        elif event.event_type == "task_completed":
            summary.state = "completed"
        elif event.event_type == "task_failed":
            summary.state = "failed"
        elif event.event_type == "task_cancelled":
            summary.state = "cancelled"
        elif event.event_type == "task_superseded":
            summary.state = "superseded"
            summary.superseded_by = str(event.metadata.get("superseded_by") or summary.superseded_by or "") or None
        elif event.event_type == "task_attention_change":
            summary.attention_reason = str(event.metadata.get("new_attention") or summary.attention_reason)
    replayed = snapshot.model_copy(deep=True)
    replayed.tasks = list(tasks.values())
    replayed.snapshot_at = _utc_now()
    return replayed
