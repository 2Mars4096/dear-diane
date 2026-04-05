from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .project_store import ProjectStore

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return cleaned or "default"


class TaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class DispatchMode(str, Enum):
    INLINE = "inline"
    FOREGROUND = "foreground"
    BACKGROUND = "background"


_TERMINAL_STATES = frozenset({
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELLED,
    TaskState.SUPERSEDED,
})

_VALID_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.QUEUED: frozenset({
        TaskState.RUNNING,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.SUPERSEDED,
    }),
    TaskState.RUNNING: frozenset({
        TaskState.WAITING_INPUT,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.SUPERSEDED,
    }),
    TaskState.WAITING_INPUT: frozenset({
        TaskState.RUNNING,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.SUPERSEDED,
    }),
    TaskState.COMPLETED: frozenset({TaskState.QUEUED}),
    TaskState.FAILED: frozenset({TaskState.QUEUED}),
    TaskState.CANCELLED: frozenset({TaskState.QUEUED}),
    TaskState.SUPERSEDED: frozenset(),
}


class TaskAttemptRecord(BaseModel):
    session_id: str
    started_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    outcome: str = "running"
    error: str | None = None


class TaskLifecycleEvent(BaseModel):
    schema_version: int = 1
    event_type: str
    task_id: str
    project_id: str
    timestamp: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConciergeTask(BaseModel):
    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    project_id: str
    title: str
    summary: str = ""
    state: TaskState = TaskState.QUEUED
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    creator_surface: str = "unknown"
    dispatch_mode: DispatchMode = DispatchMode.FOREGROUND
    project_task_id: str | None = None
    root_session_attempts: list[TaskAttemptRecord] = Field(default_factory=list)
    pending_action_id: str | None = None
    superseded_by: str | None = None
    latest_progress_line: str | None = None
    last_progress_at: datetime | None = None
    last_notification_at: datetime | None = None
    last_attention_reason: str = "none"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def attempt_count(self) -> int:
        return len(self.root_session_attempts)

    @property
    def current_attempt(self) -> TaskAttemptRecord | None:
        if not self.root_session_attempts:
            return None
        return self.root_session_attempts[-1]

    @property
    def current_attempt_ref(self) -> str | None:
        current = self.current_attempt
        return current.session_id if current is not None else None

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES


class TaskRegistry:
    def __init__(
        self,
        *,
        project_store: ProjectStore | None = None,
        base_dir: str | Path | None = None,
    ) -> None:
        self._project_store = project_store
        store_base = getattr(project_store, "base_dir", None)
        configured = str(os.environ.get("DAN_PROJECT_STORE_DIR", "") or "").strip()
        self._base_dir = Path(
            base_dir
            or configured
            or store_base
            or (Path.home() / ".dan" / "projects")
        )
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._retention_count = max(
            1,
            int(os.environ.get("DAN_TASK_RETENTION_COUNT", "50") or "50"),
        )
        self._event_history: deque[TaskLifecycleEvent] = deque(maxlen=1000)
        self._subscribers: dict[str, tuple[str | None, asyncio.Queue[TaskLifecycleEvent]]] = {}
        self._cache: dict[str, list[ConciergeTask]] = {}
        self._by_id: dict[str, str] = {}
        self._loaded: set[str] = set()
        self._live_task_ids: set[str] = set()
        self._history_append(
            TaskLifecycleEvent(
                event_type="snapshot_reset",
                task_id="",
                project_id="",
                metadata={"reason": "registry_init"},
            )
        )

    def _history_append(self, event: TaskLifecycleEvent) -> None:
        self._event_history.append(event)

    def _project_dir(self, project_id: str) -> Path:
        path = self._base_dir / _safe_segment(project_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _project_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "tasks.json"

    def _write_project(self, project_id: str) -> None:
        tasks = self._cache.get(project_id, [])
        path = self._project_path(project_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([task.model_dump(mode="json") for task in tasks], indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def _load_project(self, project_id: str) -> list[ConciergeTask]:
        if project_id in self._loaded:
            return self._cache.setdefault(project_id, [])
        self._loaded.add(project_id)
        path = self._project_path(project_id)
        tasks: list[ConciergeTask] = []
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Failed to load concierge tasks from %s", path, exc_info=True)
                payload = []
            if isinstance(payload, list):
                for entry in payload:
                    try:
                        task = ConciergeTask.model_validate(entry)
                    except Exception:
                        logger.warning("Skipping invalid concierge task entry for %s", project_id, exc_info=True)
                        continue
                    tasks.append(task)
                    self._by_id[task.task_id] = project_id
        recovered = False
        now = _utc_now()
        for task in tasks:
            if task.state not in {TaskState.QUEUED, TaskState.RUNNING}:
                continue
            if task.task_id in self._live_task_ids:
                continue
            logger.warning(
                "Recovering stale concierge task %s for project %s after restart",
                task.task_id,
                project_id,
            )
            task.state = TaskState.FAILED
            task.updated_at = now
            task.metadata = {
                **task.metadata,
                "reason": "process_restart",
                "recovered_at": now.isoformat(),
            }
            recovered = True
            self._history_append(
                TaskLifecycleEvent(
                    event_type="task_failed",
                    task_id=task.task_id,
                    project_id=task.project_id,
                    metadata={"reason": "process_restart"},
                )
            )
        self._cache[project_id] = tasks
        if recovered:
            self._write_project(project_id)
        return tasks

    def _project_for(self, task_id: str) -> str | None:
        project_id = self._by_id.get(task_id)
        if project_id:
            return project_id
        for candidate in self.project_ids():
            for task in self._load_project(candidate):
                if task.task_id == task_id:
                    self._by_id[task_id] = candidate
                    return candidate
        return None

    def project_ids(self) -> list[str]:
        ids: set[str] = set(self._cache.keys())
        for path in self._base_dir.glob("*/tasks.json"):
            ids.add(path.parent.name)
        return sorted(ids)

    def mark_task_live(self, task_id: str) -> None:
        if task_id:
            self._live_task_ids.add(task_id)

    def mark_task_idle(self, task_id: str) -> None:
        self._live_task_ids.discard(task_id)

    def list_all(
        self,
        *,
        states: set[TaskState] | None = None,
        limit: int | None = None,
        creator_surface: str | None = None,
    ) -> list[ConciergeTask]:
        tasks: list[ConciergeTask] = []
        for project_id in self.project_ids():
            tasks.extend(self._load_project(project_id))
        return self._filter_tasks(tasks, states=states, limit=limit, creator_surface=creator_surface)

    def _filter_tasks(
        self,
        tasks: list[ConciergeTask],
        *,
        states: set[TaskState] | None = None,
        limit: int | None = None,
        creator_surface: str | None = None,
    ) -> list[ConciergeTask]:
        filtered = [
            task for task in tasks
            if (not states or task.state in states)
            and (creator_surface is None or task.creator_surface == creator_surface)
        ]
        filtered.sort(key=lambda item: item.updated_at, reverse=True)
        if limit is not None:
            return filtered[:limit]
        return filtered

    def create(
        self,
        project_id: str,
        title: str,
        summary: str,
        surface: str,
        *,
        dispatch_mode: DispatchMode = DispatchMode.FOREGROUND,
        project_task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConciergeTask:
        tasks = self._load_project(project_id)
        self._prune(project_id, tasks)
        task = ConciergeTask(
            project_id=project_id,
            title=str(title or "Task").strip()[:120] or "Task",
            summary=str(summary or "").strip()[:240],
            creator_surface=surface or "unknown",
            dispatch_mode=dispatch_mode,
            project_task_id=project_task_id,
            metadata=dict(metadata or {}),
        )
        tasks.append(task)
        self._by_id[task.task_id] = project_id
        self._write_project(project_id)
        self._publish_event(
            TaskLifecycleEvent(
                event_type="task_created",
                task_id=task.task_id,
                project_id=project_id,
                metadata={
                    "title": task.title,
                    "summary": task.summary,
                    "dispatch_mode": task.dispatch_mode.value,
                },
            )
        )
        return task

    def get(self, task_id: str) -> ConciergeTask | None:
        project_id = self._project_for(task_id)
        if not project_id:
            return None
        for task in self._load_project(project_id):
            if task.task_id == task_id:
                return task
        return None

    def list(
        self,
        project_id: str,
        *,
        states: set[TaskState] | None = None,
        limit: int | None = 20,
    ) -> list[ConciergeTask]:
        return self._filter_tasks(self._load_project(project_id), states=states, limit=limit)

    def find_by_project_task(self, project_id: str, project_task_id: str | None) -> ConciergeTask | None:
        if not project_task_id:
            return None
        for task in self._load_project(project_id):
            if task.project_task_id == project_task_id:
                return task
        return None

    def transition(
        self,
        task_id: str,
        new_state: TaskState | str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> ConciergeTask:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unknown concierge task '{task_id}'")
        if isinstance(new_state, str):
            new_state = TaskState(new_state)
        if new_state == task.state:
            if metadata:
                task.metadata = {**task.metadata, **metadata}
                task.updated_at = _utc_now()
                self._write_project(task.project_id)
            return task
        allowed = _VALID_TRANSITIONS.get(task.state, frozenset())
        if new_state not in allowed:
            raise ValueError(f"Illegal concierge task transition: {task.state.value} -> {new_state.value}")
        old_state = task.state
        task.state = new_state
        task.updated_at = _utc_now()
        if metadata:
            task.metadata = {**task.metadata, **metadata}
        if new_state in _TERMINAL_STATES:
            self.mark_task_idle(task.task_id)
            current = task.current_attempt
            if current is not None and current.completed_at is None:
                current.completed_at = task.updated_at
                current.outcome = new_state.value
                if metadata:
                    current.error = str(metadata.get("error") or metadata.get("reason") or "") or None
        event_type = {
            TaskState.QUEUED: "task_queued",
            TaskState.RUNNING: "task_resumed" if old_state == TaskState.WAITING_INPUT else "task_started",
            TaskState.WAITING_INPUT: "task_paused",
            TaskState.COMPLETED: "task_completed",
            TaskState.FAILED: "task_failed",
            TaskState.CANCELLED: "task_cancelled",
            TaskState.SUPERSEDED: "task_superseded",
        }[new_state]
        self._write_project(task.project_id)
        self._publish_event(
            TaskLifecycleEvent(
                event_type=event_type,
                task_id=task.task_id,
                project_id=task.project_id,
                metadata={
                    "old_state": old_state.value,
                    "new_state": new_state.value,
                    **dict(metadata or {}),
                },
            )
        )
        return task

    def link_attempt(self, task_id: str, session_id: str) -> ConciergeTask:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unknown concierge task '{task_id}'")
        if not any(attempt.session_id == session_id for attempt in task.root_session_attempts):
            task.root_session_attempts.append(TaskAttemptRecord(session_id=session_id))
            task.updated_at = _utc_now()
            self._write_project(task.project_id)
        return task

    def complete_attempt(
        self,
        task_id: str,
        session_id: str,
        *,
        outcome: str,
        error: str | None = None,
    ) -> ConciergeTask:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unknown concierge task '{task_id}'")
        for attempt in task.root_session_attempts:
            if attempt.session_id != session_id:
                continue
            attempt.completed_at = _utc_now()
            attempt.outcome = outcome
            attempt.error = error
            break
        task.updated_at = _utc_now()
        self._write_project(task.project_id)
        return task

    def bind_project_task(self, task_id: str, project_task_id: str | None) -> ConciergeTask:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unknown concierge task '{task_id}'")
        task.project_task_id = project_task_id
        task.updated_at = _utc_now()
        self._write_project(task.project_id)
        return task

    def set_pending_action(self, task_id: str, pending_action_id: str | None) -> ConciergeTask:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unknown concierge task '{task_id}'")
        task.pending_action_id = pending_action_id
        task.updated_at = _utc_now()
        self._write_project(task.project_id)
        return task

    def record_progress(self, task_id: str, line: str | None) -> ConciergeTask:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unknown concierge task '{task_id}'")
        task.latest_progress_line = str(line or "").strip() or None
        task.last_progress_at = _utc_now()
        task.updated_at = task.last_progress_at
        self._write_project(task.project_id)
        return task

    def note_notification(self, task_id: str, *, attention_reason: str) -> ConciergeTask:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unknown concierge task '{task_id}'")
        task.last_notification_at = _utc_now()
        task.last_attention_reason = attention_reason
        self._write_project(task.project_id)
        return task

    def supersede(
        self,
        old_task_id: str,
        new_task_id: str,
        *,
        reason: str = "",
    ) -> tuple[ConciergeTask, ConciergeTask]:
        old_task = self.transition(
            old_task_id,
            TaskState.SUPERSEDED,
            metadata={"superseded_by": new_task_id, "reason": reason},
        )
        old_task.superseded_by = new_task_id
        self._write_project(old_task.project_id)
        new_task = self.get(new_task_id)
        if new_task is None:
            raise KeyError(f"Unknown concierge task '{new_task_id}'")
        return old_task, new_task

    def recent_events(self, limit: int | None = None) -> list[TaskLifecycleEvent]:
        events = list(self._event_history)
        if limit is None:
            return events
        return events[-limit:]

    def subscribe(
        self,
        *,
        project_id: str | None = None,
        maxsize: int = 256,
    ) -> tuple[str, asyncio.Queue[TaskLifecycleEvent]]:
        sub_id = uuid.uuid4().hex[:12]
        queue: asyncio.Queue[TaskLifecycleEvent] = asyncio.Queue(maxsize=maxsize)
        self._subscribers[sub_id] = (project_id, queue)
        return sub_id, queue

    def unsubscribe(self, sub_id: str) -> None:
        self._subscribers.pop(sub_id, None)

    def _publish_event(self, event: TaskLifecycleEvent) -> None:
        self._history_append(event)
        for sub_id, (project_filter, queue) in list(self._subscribers.items()):
            if project_filter and project_filter != event.project_id:
                continue
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dropped = 0
                while True:
                    try:
                        queue.get_nowait()
                        dropped += 1
                    except asyncio.QueueEmpty:
                        break
                try:
                    queue.put_nowait(
                        TaskLifecycleEvent(
                            event_type="events_dropped",
                            task_id=event.task_id,
                            project_id=event.project_id,
                            metadata={"count": dropped + 1},
                        )
                    )
                except asyncio.QueueFull:
                    logger.debug("Dropping events_dropped sentinel for subscriber %s", sub_id)

    def _prune(self, project_id: str, tasks: list[ConciergeTask]) -> None:
        if not tasks:
            return
        terminal = [task for task in tasks if task.state in _TERMINAL_STATES]
        if len(terminal) <= self._retention_count:
            return
        protected: set[str] = set()
        for task in tasks:
            if task.state not in _TERMINAL_STATES and task.superseded_by:
                protected.add(task.superseded_by)
            if task.state not in _TERMINAL_STATES and task.task_id:
                protected.add(task.task_id)
        terminal.sort(key=lambda item: item.updated_at, reverse=True)
        keep_terminal = {task.task_id for task in terminal[: self._retention_count]}
        kept: list[ConciergeTask] = []
        changed = False
        for task in tasks:
            if task.state not in _TERMINAL_STATES:
                kept.append(task)
                continue
            if task.task_id in keep_terminal or task.task_id in protected:
                kept.append(task)
                continue
            changed = True
            self._by_id.pop(task.task_id, None)
        if changed:
            tasks[:] = kept
            self._write_project(project_id)
