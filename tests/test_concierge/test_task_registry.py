from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dan.server.concierge.project_store import ProjectStore
from dan.server.concierge.task_registry import DispatchMode, TaskRegistry, TaskState


def _utc_datetime(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_task_registry_tracks_attempts_and_retry_identity(tmp_path) -> None:
    store = ProjectStore(base_dir=tmp_path / "projects")
    registry = TaskRegistry(project_store=store)

    task = registry.create(
        "proj-alpha",
        "Draft migration plan",
        "Plan the migration steps",
        "cli",
        dispatch_mode=DispatchMode.BACKGROUND,
        project_task_id="project-task-1",
    )

    registry.link_attempt(task.task_id, "session-1")
    registry.transition(task.task_id, TaskState.RUNNING)
    registry.record_progress(task.task_id, "Planning the rollout")
    registry.complete_attempt(task.task_id, "session-1", outcome="completed")
    registry.transition(task.task_id, TaskState.COMPLETED)

    registry.transition(task.task_id, TaskState.QUEUED, metadata={"reason": "retry"})
    registry.link_attempt(task.task_id, "session-2")
    registry.transition(task.task_id, TaskState.RUNNING)
    registry.complete_attempt(task.task_id, "session-2", outcome="failed", error="boom")
    registry.transition(task.task_id, TaskState.FAILED, metadata={"error": "boom"})

    current = registry.get(task.task_id)
    assert current is not None
    assert current.task_id == task.task_id
    assert current.project_task_id == "project-task-1"
    assert current.state == TaskState.FAILED
    assert current.attempt_count == 2
    assert current.current_attempt_ref == "session-2"
    assert current.latest_progress_line == "Planning the rollout"


def test_task_registry_prunes_old_terminal_tasks_but_keeps_active(tmp_path) -> None:
    store = ProjectStore(base_dir=tmp_path / "projects")
    registry = TaskRegistry(project_store=store)

    for index in range(60):
        task = registry.create(
            "proj-prune",
            f"Done task {index}",
            "already finished",
            "cli",
            dispatch_mode=DispatchMode.BACKGROUND,
        )
        task.created_at = _utc_datetime(2026, 4, 4, 12, 0) + timedelta(minutes=index)
        task.updated_at = task.created_at
        registry.transition(task.task_id, TaskState.RUNNING)
        registry.transition(task.task_id, TaskState.COMPLETED)

    active = registry.create(
        "proj-prune",
        "Still running",
        "active work",
        "cli",
        dispatch_mode=DispatchMode.BACKGROUND,
    )
    registry.transition(active.task_id, TaskState.RUNNING)

    tasks = registry.list("proj-prune", limit=None)
    terminal = [task for task in tasks if task.is_terminal]
    non_terminal = [task for task in tasks if not task.is_terminal]

    assert len(terminal) == 50
    assert [task.task_id for task in non_terminal] == [active.task_id]


def test_task_registry_recovers_stale_running_tasks_on_reload(tmp_path) -> None:
    store = ProjectStore(base_dir=tmp_path / "projects")
    registry = TaskRegistry(project_store=store)

    task = registry.create(
        "proj-recover",
        "Background research",
        "Gather sources",
        "cli",
        dispatch_mode=DispatchMode.BACKGROUND,
    )
    registry.transition(task.task_id, TaskState.RUNNING)

    recovered = TaskRegistry(project_store=store)
    fresh = recovered.get(task.task_id)

    assert fresh is not None
    assert fresh.state == TaskState.FAILED
    assert fresh.metadata["reason"] == "process_restart"
