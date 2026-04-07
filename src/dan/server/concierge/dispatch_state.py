from __future__ import annotations

"""Helpers for the split concierge dispatch authorities.

`ConcurrentDispatcher` owns foreground ingress queues.
`TaskRegistry` owns persisted background queue/running state.
`SessionManager` traces execution attempts once a task starts running.
"""

import os

from .task_registry import ConciergeTask, DispatchMode, TaskRegistry, TaskState

_BACKGROUND_QUEUE_STATES = frozenset({TaskState.QUEUED, TaskState.RUNNING})


def background_project_cap() -> int:
    return max(1, int(os.environ.get("DAN_MAX_BACKGROUND_TASKS", "3") or "3"))


def background_global_cap() -> int:
    return max(1, int(os.environ.get("DAN_MAX_GLOBAL_TASKS", "10") or "10"))


def _background_tasks(
    registry: TaskRegistry,
    *,
    states: set[TaskState] | None = None,
    project_id: str | None = None,
) -> list[ConciergeTask]:
    tasks = registry.list_all(states=states, limit=None)
    filtered = [
        task
        for task in tasks
        if task.dispatch_mode == DispatchMode.BACKGROUND
        and (project_id is None or task.project_id == project_id)
    ]
    filtered.sort(key=lambda task: (task.updated_at, task.created_at, task.task_id))
    return filtered


def count_background_tasks(
    registry: TaskRegistry,
    *,
    states: set[TaskState] | None = None,
    project_id: str | None = None,
) -> int:
    return len(_background_tasks(registry, states=states, project_id=project_id))


def background_queue_position(
    registry: TaskRegistry,
    task_id: str,
    *,
    project_id: str | None = None,
) -> int:
    queued = _background_tasks(
        registry,
        states={TaskState.QUEUED},
        project_id=project_id,
    )
    for index, task in enumerate(queued, start=1):
        if task.task_id == task_id:
            return index
    return 0


def can_start_background_task(
    registry: TaskRegistry,
    task_id: str,
    *,
    project_id: str,
    project_cap: int | None = None,
    global_cap: int | None = None,
) -> bool:
    current = registry.get(task_id)
    if current is None or current.state != TaskState.QUEUED:
        return False
    if current.dispatch_mode != DispatchMode.BACKGROUND:
        return False

    project_limit = project_cap if project_cap is not None else background_project_cap()
    global_limit = global_cap if global_cap is not None else background_global_cap()

    running_project = count_background_tasks(
        registry,
        states={TaskState.RUNNING},
        project_id=project_id,
    )
    running_global = count_background_tasks(
        registry,
        states={TaskState.RUNNING},
    )
    available_project = max(0, project_limit - running_project)
    available_global = max(0, global_limit - running_global)
    if available_project <= 0 or available_global <= 0:
        return False

    project_rank = background_queue_position(
        registry,
        task_id,
        project_id=project_id,
    )
    global_rank = background_queue_position(registry, task_id)
    return (
        project_rank > 0
        and global_rank > 0
        and project_rank <= available_project
        and global_rank <= available_global
    )


def iter_background_tasks(
    registry: TaskRegistry,
    *,
    project_id: str | None = None,
) -> list[ConciergeTask]:
    return _background_tasks(
        registry,
        states=set(_BACKGROUND_QUEUE_STATES),
        project_id=project_id,
    )
