from __future__ import annotations

from dan.server.concierge.dispatch_state import (
    background_queue_position,
    can_start_background_task,
    count_background_tasks,
)
from dan.server.concierge.task_registry import DispatchMode, TaskRegistry, TaskState


def test_background_queue_position_and_fifo_eligibility(tmp_path) -> None:
    registry = TaskRegistry(base_dir=tmp_path)
    first = registry.create(
        "proj-1",
        "First background task",
        "",
        "cli",
        dispatch_mode=DispatchMode.BACKGROUND,
    )
    second = registry.create(
        "proj-1",
        "Second background task",
        "",
        "cli",
        dispatch_mode=DispatchMode.BACKGROUND,
    )

    assert background_queue_position(registry, first.task_id, project_id="proj-1") == 1
    assert background_queue_position(registry, second.task_id, project_id="proj-1") == 2
    assert can_start_background_task(
        registry,
        first.task_id,
        project_id="proj-1",
        project_cap=1,
        global_cap=2,
    )
    assert not can_start_background_task(
        registry,
        second.task_id,
        project_id="proj-1",
        project_cap=1,
        global_cap=2,
    )


def test_running_background_tasks_consume_project_and_global_slots(tmp_path) -> None:
    registry = TaskRegistry(base_dir=tmp_path)
    running = registry.create(
        "proj-1",
        "Running background task",
        "",
        "cli",
        dispatch_mode=DispatchMode.BACKGROUND,
    )
    registry.transition(running.task_id, TaskState.RUNNING)
    waiting_same_project = registry.create(
        "proj-1",
        "Waiting same project",
        "",
        "cli",
        dispatch_mode=DispatchMode.BACKGROUND,
    )
    waiting_other_project = registry.create(
        "proj-2",
        "Waiting other project",
        "",
        "cli",
        dispatch_mode=DispatchMode.BACKGROUND,
    )

    assert count_background_tasks(registry, states={TaskState.RUNNING}) == 1
    assert not can_start_background_task(
        registry,
        waiting_same_project.task_id,
        project_id="proj-1",
        project_cap=1,
        global_cap=2,
    )
    assert not can_start_background_task(
        registry,
        waiting_other_project.task_id,
        project_id="proj-2",
        project_cap=1,
        global_cap=1,
    )
