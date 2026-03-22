from __future__ import annotations

from pathlib import Path

from dan.server.concierge.models import PendingAction, Project, SurfaceMessage, Task, TaskTurn
from dan.server.concierge.project_store import ProjectStore


def test_surface_message_defaults():
    msg = SurfaceMessage(surface="cli", external_id="user-1", text="hello")
    assert msg.surface == "cli"
    assert msg.external_id == "user-1"
    assert msg.text == "hello"
    assert msg.attachments == []
    assert msg.metadata == {}


def test_project_store_create_and_load(tmp_path: Path):
    store = ProjectStore(base_dir=tmp_path)
    project = store.create_project("lit-review", "cli-user")

    loaded = store.get_project(project.project_id, "cli-user")
    assert loaded is not None
    assert loaded.label == "lit-review"
    assert loaded.surface_id == "cli-user"
    assert loaded.tasks == []


def test_project_store_add_task_and_get_current(tmp_path: Path):
    store = ProjectStore(base_dir=tmp_path)
    project = store.create_project("lit-review", "cli-user")
    task1 = store.add_task(project.project_id, "outline", "cli-user")
    task2 = store.add_task(project.project_id, "intro", "cli-user")

    current = store.get_current_task(project.project_id, "cli-user")
    assert current is not None
    assert current.task_id == task2.task_id
    assert current.label == "intro"

    loaded = store.get_project(project.project_id, "cli-user")
    assert loaded is not None
    assert [task.label for task in loaded.tasks] == ["outline", "intro"]


def test_project_store_append_turn_updates_task(tmp_path: Path):
    store = ProjectStore(base_dir=tmp_path)
    project = store.create_project("lit-review", "cli-user")
    task = store.add_task(project.project_id, "outline", "cli-user")
    turn = TaskTurn(role="user", content="draft outline", intent="workflow_build")

    store.append_turn(project.project_id, task.task_id, turn, "cli-user")

    loaded = store.get_project(project.project_id, "cli-user")
    assert loaded is not None
    assert loaded.tasks[0].turns[0].content == "draft outline"
    assert loaded.tasks[0].turns[0].intent == "workflow_build"


def test_project_store_append_turn_replays_from_journal(tmp_path: Path):
    store = ProjectStore(base_dir=tmp_path)
    project = store.create_project("lit-review", "cli-user")
    task = store.add_task(project.project_id, "outline", "cli-user")

    store.append_turn(
        project.project_id,
        task.task_id,
        TaskTurn(role="assistant", content="done"),
        "cli-user",
    )

    snapshot = Project.model_validate_json(
        (tmp_path / "cli-user" / f"{project.project_id}.json").read_text(encoding="utf-8")
    )
    assert snapshot.tasks[0].turns == []

    loaded = store.get_project(project.project_id, "cli-user")
    assert loaded is not None
    assert loaded.tasks[0].turns[0].content == "done"


def test_project_store_update_task_progress_replays_from_journal(tmp_path: Path):
    store = ProjectStore(base_dir=tmp_path)
    project = store.create_project("lit-review", "cli-user")
    task = store.add_task(project.project_id, "outline", "cli-user")

    store.update_task_progress(
        project.project_id,
        task.task_id,
        "cli-user",
        completed_steps=["write tests"],
        pending_steps=["update docs"],
        current_blocker="Waiting on CI",
        artifacts={"notes": "/tmp/notes.md"},
        goal_id="goal-1",
        progress_updated_at=123.0,
    )

    loaded = store.get_project(project.project_id, "cli-user")
    assert loaded is not None
    stored_task = loaded.tasks[0]
    assert stored_task.completed_steps == ["write tests"]
    assert stored_task.pending_steps == ["update docs"]
    assert stored_task.current_blocker == "Waiting on CI"
    assert stored_task.artifacts == {"notes": "/tmp/notes.md"}
    assert stored_task.goal_id == "goal-1"
    assert stored_task.progress_updated_at == 123.0


def test_project_store_search_projects_uses_label_summary_and_task_labels(tmp_path: Path):
    store = ProjectStore(base_dir=tmp_path)
    project = store.create_project("late-payment-analysis", "cli-user")
    store.update_project_summary(project.project_id, "Analyze late payment seasonality", "cli-user")
    store.add_task(project.project_id, "find document", "cli-user")

    matches = store.search_projects("late payment seasonality", "cli-user")

    assert len(matches) == 1
    assert matches[0].label == "late-payment-analysis"


def test_project_store_link_workflow_and_run_ids(tmp_path: Path):
    store = ProjectStore(base_dir=tmp_path)
    project = store.create_project("lit-review", "cli-user")

    store.link_workflow(project.project_id, "wf-a", "cli-user")
    store.link_workflow(project.project_id, "wf-a", "cli-user")
    store.link_run(project.project_id, "run-1", "cli-user")
    store.link_run(project.project_id, "run-1", "cli-user")

    loaded = store.get_project(project.project_id, "cli-user")
    assert loaded is not None
    assert loaded.linked_workflow_ids == ["wf-a"]
    assert loaded.linked_run_ids == ["run-1"]


def test_project_store_list_pending_projects(tmp_path: Path):
    store = ProjectStore(base_dir=tmp_path)
    first = store.create_project("alpha", "cli-user")
    second = store.create_project("beta", "cli-user")
    store.set_pending_action(
        first.project_id,
        PendingAction(kind="confirm", intent="publish_share", original_text="share it"),
        "cli-user",
    )
    store.set_pending_action(
        second.project_id,
        PendingAction(kind="clarify", intent="file_request", original_text="send file"),
        "cli-user",
    )

    pending = store.list_pending_projects("cli-user")

    assert [project.label for project in pending] == ["beta", "alpha"]
