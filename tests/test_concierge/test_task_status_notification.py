from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest

from dan.chat_events import ChatCompleteEvent
from dan.server.concierge.models import SurfaceMessage
from dan.server.concierge.project_store import ProjectStore
from dan.server.concierge.runtime import Concierge
from dan.server.concierge.task_attention import AttentionReason, TaskAttentionMonitor, build_notification_summary, derive_attention
from dan.server.concierge.task_registry import DispatchMode, TaskRegistry, TaskState


def _build_concierge(tmp_path) -> Concierge:
    chat_manager = MagicMock()

    async def _fake_send(**kwargs: Any) -> AsyncIterator[ChatCompleteEvent]:
        yield ChatCompleteEvent(
            message_id="m1",
            content="ok",
            token_usage={},
            context_window=0,
            graph_revision="",
        )

    chat_manager.send_message = MagicMock(side_effect=lambda **kwargs: _fake_send(**kwargs))
    chat_manager.send_message_with_tools = MagicMock(side_effect=lambda **kwargs: _fake_send(**kwargs))

    cap_ctx = MagicMock()
    cap_ctx.graph_store = None
    cap_ctx.run_manager = None
    cap_ctx.activity_tracker = None
    cap_ctx.experience_store = None
    cap_ctx.experience_index = None
    cap_ctx.llm_provider = None
    cap_ctx.event_bus = MagicMock()

    return Concierge(
        project_store=ProjectStore(base_dir=tmp_path / "projects"),
        chat_manager=chat_manager,
        capability_context=cap_ctx,
    )


def test_attention_summary_is_background_only_for_completed_tasks(tmp_path) -> None:
    registry = TaskRegistry(project_store=ProjectStore(base_dir=tmp_path / "projects"))

    background = registry.create("proj-1", "Background task", "done", "cli", dispatch_mode=DispatchMode.BACKGROUND)
    registry.transition(background.task_id, TaskState.RUNNING)
    registry.transition(background.task_id, TaskState.COMPLETED)
    background_task = registry.get(background.task_id)
    assert background_task is not None

    foreground = registry.create("proj-1", "Foreground task", "done", "cli", dispatch_mode=DispatchMode.FOREGROUND)
    registry.transition(foreground.task_id, TaskState.RUNNING)
    registry.transition(foreground.task_id, TaskState.COMPLETED)
    foreground_task = registry.get(foreground.task_id)
    assert foreground_task is not None

    assert derive_attention(background_task).attention_reason == AttentionReason.COMPLETED_UNREAD
    assert build_notification_summary(background_task) is not None
    assert derive_attention(foreground_task).attention_reason == AttentionReason.NONE
    assert build_notification_summary(foreground_task) is None


@pytest.mark.asyncio
async def test_attention_monitor_times_out_waiting_input_tasks(tmp_path) -> None:
    registry = TaskRegistry(project_store=ProjectStore(base_dir=tmp_path / "projects"))
    task = registry.create("proj-timeout", "Need clarification", "waiting", "cli", dispatch_mode=DispatchMode.BACKGROUND)
    registry.transition(task.task_id, TaskState.RUNNING)
    registry.transition(task.task_id, TaskState.WAITING_INPUT)
    current = registry.get(task.task_id)
    assert current is not None
    current.updated_at = current.updated_at - timedelta(minutes=31)

    cleared: list[str] = []
    monitor = TaskAttentionMonitor(
        registry,
        clear_pending_action=lambda concierge_task: cleared.append(concierge_task.task_id),
    )

    await monitor.sweep_once()

    fresh = registry.get(task.task_id)
    assert fresh is not None
    assert fresh.state == TaskState.FAILED
    assert fresh.metadata["reason"] == "waiting_input_timeout"
    assert cleared == [task.task_id]


def test_status_command_summarizes_active_and_recent_tasks(tmp_path) -> None:
    concierge = _build_concierge(tmp_path)
    project = concierge.project_store.create_project("Status Project", "cli-user")
    project_task = concierge.project_store.add_task(project.project_id, "Draft plan", "cli-user")

    active = concierge._task_registry.create(
        project.project_id,
        "Background research",
        "Gather sources",
        "cli",
        dispatch_mode=DispatchMode.BACKGROUND,
        project_task_id=project_task.task_id,
    )
    concierge._task_registry.transition(active.task_id, TaskState.RUNNING)

    completed = concierge._task_registry.create(
        project.project_id,
        "Completed task",
        "Finished earlier",
        "cli",
        dispatch_mode=DispatchMode.BACKGROUND,
    )
    concierge._task_registry.transition(completed.task_id, TaskState.RUNNING)
    concierge._task_registry.transition(completed.task_id, TaskState.COMPLETED)

    message = SurfaceMessage(
        surface="cli",
        external_id="cli-user",
        text="/status",
        metadata={"resolved_project_id": project.project_id},
    )

    output = concierge.handle_status_command(message)

    assert "Background research" in output
    assert "State: `running`" in output
    assert "Background research" in output
    assert "Age:" in output
