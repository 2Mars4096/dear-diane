from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field

from .task_registry import ConciergeTask, TaskLifecycleEvent, TaskRegistry, TaskState

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AttentionReason(str, Enum):
    NEEDS_INPUT = "needs_input"
    COMPLETED_UNREAD = "completed_unread"
    FAILED_UNREAD = "failed_unread"
    STUCK = "stuck"
    NONE = "none"


class TaskAttention(BaseModel):
    needs_attention: bool = False
    attention_reason: AttentionReason = AttentionReason.NONE


class TaskNotificationSummary(BaseModel):
    task_id: str
    title: str
    state: str
    attention_reason: str
    summary_line: str
    creator_surface: str


def derive_attention(
    task: ConciergeTask,
    *,
    now: datetime | None = None,
    stuck_after: timedelta | None = None,
) -> TaskAttention:
    current = now or _utc_now()
    running_stuck_after = stuck_after or timedelta(
        minutes=max(1, int(os.environ.get("DAN_TASK_STUCK_MINUTES", "10") or "10"))
    )
    if task.state == TaskState.WAITING_INPUT:
        return TaskAttention(needs_attention=True, attention_reason=AttentionReason.NEEDS_INPUT)
    if task.state == TaskState.COMPLETED and task.dispatch_mode.value == "background" and (
        task.last_notification_at is None or task.last_notification_at < task.updated_at
    ):
        return TaskAttention(needs_attention=True, attention_reason=AttentionReason.COMPLETED_UNREAD)
    if task.state == TaskState.FAILED and (
        task.last_notification_at is None or task.last_notification_at < task.updated_at
    ):
        return TaskAttention(needs_attention=True, attention_reason=AttentionReason.FAILED_UNREAD)
    if task.state == TaskState.RUNNING:
        marker = task.last_progress_at or task.updated_at
        if current - marker > running_stuck_after:
            return TaskAttention(needs_attention=True, attention_reason=AttentionReason.STUCK)
    return TaskAttention(needs_attention=False, attention_reason=AttentionReason.NONE)


def build_notification_summary(task: ConciergeTask) -> TaskNotificationSummary | None:
    attention = derive_attention(task)
    if not attention.needs_attention:
        return None
    line = f"{task.title} — {task.state.value.replace('_', ' ')}"
    if task.state == TaskState.WAITING_INPUT:
        line = f"{task.title} needs input"
    elif task.state == TaskState.COMPLETED:
        line = f"{task.title} completed"
    elif task.state == TaskState.FAILED:
        line = f"{task.title} failed"
    elif attention.attention_reason == AttentionReason.STUCK:
        line = f"{task.title} looks stuck"
    return TaskNotificationSummary(
        task_id=task.task_id,
        title=task.title,
        state=task.state.value,
        attention_reason=attention.attention_reason.value,
        summary_line=line[:200],
        creator_surface=task.creator_surface,
    )


def coalesce_notification_summaries(
    notifications: list[TaskNotificationSummary],
) -> list[TaskNotificationSummary | list[TaskNotificationSummary]]:
    if len(notifications) <= 1:
        return notifications
    return [notifications]


class TaskAttentionMonitor:
    def __init__(
        self,
        registry: TaskRegistry,
        *,
        clear_pending_action: Callable[[ConciergeTask], None] | None = None,
        emit_telemetry: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self._registry = registry
        self._clear_pending_action = clear_pending_action
        self._emit_telemetry = emit_telemetry
        self._poll_interval_seconds = 60.0
        self._runner: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._runner is not None and not self._runner.done():
            return
        self._runner = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._runner is None or self._runner.done():
            return
        self._runner.cancel()
        try:
            await self._runner
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while True:
            try:
                await self.sweep_once()
            except Exception:
                logger.debug("Task attention sweep failed", exc_info=True)
            await asyncio.sleep(self._poll_interval_seconds)

    async def sweep_once(self) -> list[TaskLifecycleEvent]:
        events: list[TaskLifecycleEvent] = []
        now = _utc_now()
        waiting_timeout = timedelta(
            minutes=max(
                1,
                int(os.environ.get("DAN_TASK_WAITING_TIMEOUT_MINUTES", "30") or "30"),
            )
        )
        for task in self._registry.list_all(limit=None):
            previous = str(task.last_attention_reason or "none")
            attention = derive_attention(task, now=now)
            if task.state == TaskState.WAITING_INPUT and now - task.updated_at > waiting_timeout:
                if self._clear_pending_action is not None:
                    try:
                        self._clear_pending_action(task)
                    except Exception:
                        logger.debug("Clearing timed-out pending action failed", exc_info=True)
                task = self._registry.transition(
                    task.task_id,
                    TaskState.FAILED,
                    metadata={"reason": "waiting_input_timeout"},
                )
                attention = derive_attention(task, now=now)
            task.last_attention_reason = attention.attention_reason.value
            if attention.attention_reason.value == previous:
                continue
            if self._emit_telemetry is not None:
                maybe = self._emit_telemetry(
                    "task_attention_change",
                    {
                        "task_id": task.task_id,
                        "project_id": task.project_id,
                        "old_attention": previous,
                        "new_attention": attention.attention_reason.value,
                    },
                )
                if asyncio.iscoroutine(maybe):
                    await maybe
            events.append(
                TaskLifecycleEvent(
                    event_type="task_attention_change",
                    task_id=task.task_id,
                    project_id=task.project_id,
                    metadata={
                        "old_attention": previous,
                        "new_attention": attention.attention_reason.value,
                    },
                )
            )
        return events
