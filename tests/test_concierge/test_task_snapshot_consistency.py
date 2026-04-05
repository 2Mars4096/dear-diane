from __future__ import annotations

from dan.server.concierge.project_store import ProjectStore
from dan.server.concierge.task_registry import DispatchMode, TaskRegistry, TaskState
from dan.server.concierge.task_snapshot import build_snapshot, replay_events


def test_snapshot_replay_matches_current_registry_state(tmp_path) -> None:
    store = ProjectStore(base_dir=tmp_path / "projects")
    registry = TaskRegistry(project_store=store)

    first = registry.create(
        "proj-snapshot",
        "First task",
        "baseline",
        "cli",
        dispatch_mode=DispatchMode.BACKGROUND,
    )
    registry.transition(first.task_id, TaskState.RUNNING)

    event_cutoff = len(registry.recent_events())
    baseline = build_snapshot(registry)

    second = registry.create(
        "proj-snapshot",
        "Second task",
        "follow-up",
        "cli",
        dispatch_mode=DispatchMode.FOREGROUND,
    )
    registry.transition(second.task_id, TaskState.RUNNING)
    registry.transition(second.task_id, TaskState.COMPLETED)
    registry.transition(first.task_id, TaskState.WAITING_INPUT, metadata={"reason": "needs_input"})

    replayed = replay_events(baseline, registry.recent_events()[event_cutoff:])
    current = build_snapshot(registry)

    current_map = {task.task_id: task.state for task in current.tasks}
    replayed_map = {task.task_id: task.state for task in replayed.tasks}

    assert replayed_map == current_map
    assert {task.task_id for task in replayed.tasks} == {task.task_id for task in current.tasks}


def test_snapshot_uses_dispatcher_summary_hook_when_available(tmp_path) -> None:
    store = ProjectStore(base_dir=tmp_path / "projects")
    registry = TaskRegistry(project_store=store)
    registry.create(
        "proj-summary",
        "Queued task",
        "summary check",
        "cli",
        dispatch_mode=DispatchMode.BACKGROUND,
    )

    class _FakeDispatcher:
        def snapshot_state(self):
            return {
                "active_background_slots": 1,
                "max_background_slots": 3,
                "global_active": 2,
                "global_max": 5,
                "per_project": [
                    {
                        "project_id": "proj-summary",
                        "active_count": 1,
                        "queue_depth": 0,
                        "oldest_queued_age": None,
                    }
                ],
            }

    snapshot = build_snapshot(registry, dispatcher=_FakeDispatcher())

    assert snapshot.dispatcher.active_background_slots == 1
    assert snapshot.dispatcher.max_background_slots == 3
    assert snapshot.dispatcher.per_project[0].project_id == "proj-summary"
