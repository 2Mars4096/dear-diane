from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dan.server.chat_v2 import (
    AgentRunCommand,
    AgentRunEvent,
    SurfaceUpdateHandle,
    build_surface_turn_from_chat_request,
    build_v2_bridge_context,
    summarize_v2_bridge_context,
    triage_surface_turn,
)
from dan.server.chat_v2_progress import AgentProgressStateMachine, TelegramProgressSink
from dan.server.chat_v2_organism import map_organism_log_row_to_agent_event
from dan.server.chat_v2_backend import (
    _is_safe_backend_checkpoint,
    build_agent_backend_request,
)
from dan.server.chat_v2_store import ChatV2Store
from dan.server.routers.chat import ChatMessageRequest
from dan.server.routers import chat_v2 as chat_v2_router


def test_v2_surface_turn_structures_telegram_attachment_inputs() -> None:
    req = ChatMessageRequest(
        workflow_id="_scratch",
        message="[Attachment: /tmp/legacy.png]\nplease analyze this figure",
        mode="auto",
        surface_type="telegram",
        surface_id="dan-bot",
        session_id="chat-111:topic-7",
        thread_id="thread-1",
        attachment_path="/tmp/primary.pdf",
        history=[
            {"role": "user", "content": "please inspect this"},
            {"role": "assistant", "content": "I started the previous pass"},
        ],
        surface_context={
            "telegram": {
                "chat_id": 111,
                "message_thread_id": 7,
                "message_id": 42,
                "reply_to_message_id": 41,
                "reply_to_text": "previous progress",
                "from_user_id": 222,
                "from_user_username": "operator",
                "chat_type": "private",
            },
            "appended_attachments": [
                {
                    "name": "chart.png",
                    "kind": "figure",
                    "path": "/tmp/chart.png",
                    "caption": "model accuracy plot",
                    "file_id": "tg-file-1",
                    "message_id": 42,
                }
            ],
        },
    )

    turn = build_surface_turn_from_chat_request(req)

    assert turn.surface_type == "telegram"
    assert turn.surface_id == "dan-bot"
    assert turn.native_chat_id == "111"
    assert turn.native_thread_id == "7"
    assert turn.native_message_id == "42"
    assert turn.reply_to_message_id == "41"
    assert turn.privacy_scope == "private"
    assert turn.workspace_root == str(Path.home())
    assert turn.workspace_id == str(Path.home())
    assert turn.metadata["workspace_source"] == "default_home"
    assert turn.update_handle is not None
    assert turn.update_handle.native_chat_id == "111"
    assert turn.metadata["reply_context"] == {
        "reply_to_message_id": "41",
        "reply_to_text": "previous progress",
    }
    assert turn.metadata["history"] == [
        {"role": "user", "content": "please inspect this"},
        {"role": "assistant", "content": "I started the previous pass"},
    ]
    assert turn.metadata["surface_context"]["telegram"]["reply_to_text"] == "previous progress"
    assert [ref.kind for ref in turn.attachments] == ["pdf", "figure", "image"]
    assert turn.attachments[1].native_file_id == "tg-file-1"
    assert turn.attachments[2].metadata["source"] == "legacy_text_marker"


def test_v2_surface_turn_links_session_to_explicit_workspace(tmp_path) -> None:
    req = ChatMessageRequest(
        workflow_id="_scratch",
        message="build in this workspace",
        mode="agent",
        surface_type="web",
        surface_id="v2",
        session_id="session-1",
        surface_context={
            "workspace": {
                "id": "workspace-alpha",
                "root": str(tmp_path / "workspace-alpha"),
            }
        },
    )

    turn = build_surface_turn_from_chat_request(req)
    decision = triage_surface_turn(turn, requested_mode=req.mode)

    assert turn.workspace_root == str((tmp_path / "workspace-alpha").resolve())
    assert turn.workspace_id == "workspace-alpha"
    assert turn.metadata["workspace_source"] == "explicit"
    assert "workspace-alpha" in decision.topic_key


def test_v2_surface_turn_infers_workspace_from_message_path(tmp_path) -> None:
    workspace = tmp_path / "phone-project"
    workspace.mkdir()
    req = ChatMessageRequest(
        workflow_id="_scratch",
        message=f"I have this path {workspace}, can you help me patch the app?",
        mode="agent",
        surface_type="telegram",
        surface_id="bot",
        session_id="chat-1:topic-7",
        thread_id="thread-1",
        surface_context={"telegram": {"chat_id": 1, "message_thread_id": 7}},
    )

    turn = build_surface_turn_from_chat_request(req)
    decision = triage_surface_turn(turn, requested_mode=req.mode)

    assert turn.workspace_root == str(workspace.resolve())
    assert turn.workspace_id == str(workspace.resolve())
    assert turn.metadata["workspace_source"] == "message_path"
    assert turn.metadata["surface_topic_key"]
    assert str(workspace.resolve()).replace("/", "_") in decision.topic_key


def test_v2_triage_routes_control_and_agent_turns() -> None:
    status_req = ChatMessageRequest(
        workflow_id="_scratch",
        message="/status",
        surface_type="telegram",
        surface_id="bot",
    )
    status_turn = build_surface_turn_from_chat_request(status_req)

    status_decision = triage_surface_turn(status_turn, requested_mode="auto")

    assert status_decision.action == "control_command"
    assert status_decision.task_binding == "bypass"
    assert status_decision.control_command == "status"
    assert status_decision.queue_key == "control:bypass"

    agent_req = ChatMessageRequest(
        workflow_id="_scratch",
        message="implement the frontend patch",
        mode="agent",
        surface_type="web",
        surface_id="v2",
    )
    agent_turn = build_surface_turn_from_chat_request(agent_req)

    agent_decision = triage_surface_turn(agent_turn, requested_mode=agent_req.mode)

    assert agent_decision.action == "agent_requested"
    assert agent_decision.task_binding == "new_task"
    assert "coding" in agent_decision.task_family_hints
    assert agent_decision.queue_key.startswith("task:")


def test_v2_triage_requires_explicit_append_or_continue_lane() -> None:
    reply_req = ChatMessageRequest(
        workflow_id="_scratch",
        message="also use the attached figure",
        surface_type="telegram",
        surface_id="bot",
        surface_context={
            "telegram": {
                "message_id": 101,
                "reply_to_message_id": 100,
                "chat_type": "private",
            }
        },
    )
    reply_turn = build_surface_turn_from_chat_request(reply_req)

    reply_decision = triage_surface_turn(reply_turn, requested_mode="auto")

    assert reply_decision.action == "agent_suggested"
    assert reply_decision.task_binding == "existing_task"
    assert reply_decision.requires_confirmation is True
    assert reply_decision.queue_key.endswith(":needs-lane")

    append_req = ChatMessageRequest(
        workflow_id="_scratch",
        message="/append also use the attached figure",
        surface_type="telegram",
        surface_id="bot",
    )
    continue_req = ChatMessageRequest(
        workflow_id="_scratch",
        message="use the figure when current work finishes",
        surface_type="telegram",
        surface_id="bot",
        surface_context={"queue_action": "continue_after_current"},
    )

    append_decision = triage_surface_turn(
        build_surface_turn_from_chat_request(append_req),
        requested_mode="auto",
    )
    continue_decision = triage_surface_turn(
        build_surface_turn_from_chat_request(continue_req),
        requested_mode="auto",
    )

    assert append_decision.action == "append_to_active_run"
    assert append_decision.task_binding == "append"
    assert append_decision.queue_key.endswith(":append")
    assert continue_decision.action == "continue_after_current"
    assert continue_decision.task_binding == "continue_after_current"
    assert continue_decision.queue_key.endswith(":continue")


def test_v2_bridge_context_is_compact_and_legacy_compatible() -> None:
    req = ChatMessageRequest(
        workflow_id="_scratch",
        message="build the dashboard",
        mode="agent",
        surface_type="telegram",
        surface_id="bot",
        attachment_path="/tmp/mockup.png",
    )

    context = build_v2_bridge_context(req)
    summary = summarize_v2_bridge_context(context)

    assert context["selected_control_plane"] == "v2"
    assert context["legacy_bridge"] is True
    assert context["surface_turn"]["attachments"][0]["kind"] == "image"
    assert context["triage_decision"]["action"] == "agent_requested"
    assert summary == {
        "surface_turn_id": context["surface_turn"]["id"],
        "triage_action": "agent_requested",
        "task_binding": "new_task",
        "topic_key": context["triage_decision"]["topic_key"],
        "queue_key": context["triage_decision"]["queue_key"],
        "workspace_root": str(Path.home()),
        "workspace_id": str(Path.home()),
        "attachment_count": 1,
        "legacy_bridge": True,
        "delegated_to": "/api/chat/message",
    }


def test_v2_agent_hook_models_accept_command_and_event_payloads() -> None:
    command = AgentRunCommand(
        command="append_followup",
        task_id="task-1",
        run_id="run-1",
        payload={"text": "also handle the figure"},
    )
    event = AgentRunEvent(
        type="artifact_changed",
        run_id="run-1",
        task_id="task-1",
        summary="Updated figure output",
        source_event_type="live.artifact.changed",
        artifact_refs=[{"path": "figure.png"}],
    )

    assert command.command == "append_followup"
    assert event.type == "artifact_changed"
    assert event.artifact_refs == [{"path": "figure.png"}]


def test_v2_maps_organism_log_rows_to_normalized_agent_events() -> None:
    validation = map_organism_log_row_to_agent_event(
        {
            "record_id": "row-1",
            "event": "contract.validation.started",
            "summary": "Validating output contract",
        },
        run_id="run-1",
        task_id="task-1",
        source_event_path=".dan-super/runs/turn-01/events.jsonl",
    )
    repair = map_organism_log_row_to_agent_event(
        {
            "sequence": 2,
            "event": "contract.repair.started",
            "summary": "Repairing missing artifact",
            "payload": {"changed_files": ["report.md"]},
        },
        run_id="run-1",
        task_id="task-1",
    )
    completed = map_organism_log_row_to_agent_event(
        {
            "event": "organism.completed",
            "status": "completed",
            "summary": "Final report ready",
        },
        run_id="run-1",
        task_id="task-1",
    )

    assert validation.type == "validation_started"
    assert validation.source_event_id == "row-1"
    assert validation.source_event_path == ".dan-super/runs/turn-01/events.jsonl"
    assert repair.type == "repair_started"
    assert repair.artifact_refs == [{"path": "report.md"}]
    assert completed.type == "completed"
    assert completed.summary == "Final report ready"

    usage = map_organism_log_row_to_agent_event(
        {
            "record_id": "row-usage-1",
            "event": "model.responded",
            "round": 2,
            "model": "fake-model",
            "model_call_id": "call-2",
            "usage": {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50},
            "usage_totals": {"prompt_tokens": 70, "completion_tokens": 30, "total_tokens": 100},
        },
        run_id="run-1",
        task_id="task-1",
    )

    assert usage.type == "token_usage_recorded"
    assert usage.token_usage_delta["total_tokens"] == 50
    assert usage.token_usage_total["total_tokens"] == 100
    assert usage.token_usage_round["round"] == 2


def test_v2_backend_runtime_treats_mutating_tool_start_as_safe_checkpoint() -> None:
    assert _is_safe_backend_checkpoint(
        {"event": "tool.started", "tool_id": "file_write"}
    )
    assert _is_safe_backend_checkpoint(
        {"event": "tool.started", "tool_id": "file_edit"}
    )
    assert _is_safe_backend_checkpoint(
        {"event": "tool.started", "tool_id": "shell_command"}
    )
    assert not _is_safe_backend_checkpoint(
        {"event": "tool.started", "tool_id": "file_read"}
    )


def test_v2_store_persists_tasks_runs_and_explicit_queue_lanes(tmp_path) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    req = ChatMessageRequest(
        workflow_id="_scratch",
        message="implement the dashboard",
        mode="agent",
        surface_type="telegram",
        surface_id="bot",
        session_id="chat-1:topic-2",
        thread_id="thread-1",
        history=[
            {"role": "user", "content": "build a first dashboard"},
            {"role": "assistant", "content": "first pass is done"},
        ],
        surface_context={
            "telegram": {
                "chat_type": "private",
                "chat_id": 1,
                "reply_to_message_id": 8,
                "reply_to_text": "first pass is done",
            },
            "conversation": {
                "conversation_key": "chat-1:topic-2:bot",
                "lane_key": "chat-1:topic-2:bot:m8",
                "reply_lane_key": "chat-1:topic-2:bot",
                "history_turn_count": 2,
            },
        },
    )

    accepted = store.accept_bridge_context(
        build_v2_bridge_context(req),
        stream_channel_id="chat-stream-1",
    )

    assert accepted.task_id is not None
    assert accepted.run_id is not None
    assert accepted.snapshot is not None
    assert accepted.snapshot.status == "running"
    assert accepted.snapshot.metadata["workspace_root"] == str(Path.home())
    assert accepted.snapshot.metadata["workspace_id"] == str(Path.home())
    assert accepted.snapshot.metadata["active_run_id"] == accepted.run_id
    assert store.get_run(accepted.run_id).stream_channel_id == "chat-stream-1"
    assert store.get_run(accepted.run_id).workspace_root == str(Path.home())
    assert store.get_run(accepted.run_id).command.payload["workspace_root"] == str(Path.home())
    assert store.get_run(accepted.run_id).command.payload["history"] == [
        {"role": "user", "content": "build a first dashboard"},
        {"role": "assistant", "content": "first pass is done"},
    ]
    assert store.get_run(accepted.run_id).command.payload["reply_context"] == {
        "reply_to_message_id": "8",
        "reply_to_text": "first pass is done",
    }
    assert (
        store.get_run(accepted.run_id)
        .command.payload["surface_context"]["conversation"]["reply_lane_key"]
        == "chat-1:topic-2:bot"
    )
    backend_request = build_agent_backend_request(
        store.get_run(accepted.run_id),
        store.get_task(accepted.task_id),
    )
    assert backend_request.history[-1]["content"] == "first pass is done"
    assert backend_request.reply_context["reply_to_text"] == "first pass is done"
    assert (
        backend_request.surface_context["conversation"]["history_turn_count"] == 2
    )
    assert store.load_run_events(accepted.run_id)[0]["type"] == "accepted"

    append_req = ChatMessageRequest(
        workflow_id="_scratch",
        message="/append also use this chart",
        surface_type="telegram",
        surface_id="bot",
        session_id="chat-1:topic-2",
        thread_id="thread-1",
        surface_context={"telegram": {"chat_type": "private", "chat_id": 1}},
    )
    continue_req = ChatMessageRequest(
        workflow_id="_scratch",
        message="run the polish pass after current work",
        surface_type="telegram",
        surface_id="bot",
        session_id="chat-1:topic-2",
        thread_id="thread-1",
        surface_context={
            "queue_action": "continue_after_current",
            "telegram": {"chat_type": "private", "chat_id": 1},
        },
    )

    appended = store.accept_bridge_context(build_v2_bridge_context(append_req))
    continued = store.accept_bridge_context(build_v2_bridge_context(continue_req))

    assert appended.task_id == accepted.task_id
    assert appended.queue_position == 1
    assert appended.snapshot.metadata["append_queue_length"] == 1
    assert continued.task_id == accepted.task_id
    assert continued.queue_position == 1
    assert continued.snapshot.metadata["continue_queue_length"] == 1

    reloaded = ChatV2Store(tmp_path / "chat_v2")
    snapshot = reloaded.get_task_snapshot(accepted.task_id)
    assert snapshot is not None
    assert snapshot.metadata["append_queue_length"] == 1
    assert snapshot.metadata["continue_queue_length"] == 1
    assert reloaded.list_thread_tasks("thread-1")[0].task_id == accepted.task_id


def test_v2_store_inherits_active_task_workspace_for_followups(tmp_path) -> None:
    workspace = tmp_path / "topic-workspace"
    workspace.mkdir()
    store = ChatV2Store(tmp_path / "chat_v2")
    accepted = store.accept_bridge_context(
        build_v2_bridge_context(
            ChatMessageRequest(
                workflow_id="_scratch",
                message=f"I have this path {workspace}, build the patch",
                mode="agent",
                surface_type="telegram",
                surface_id="bot",
                session_id="chat-1:topic-9",
                thread_id="thread-1",
                surface_context={
                    "telegram": {
                        "chat_id": 1,
                        "message_thread_id": 9,
                        "chat_type": "private",
                    }
                },
            )
        )
    )

    appended = store.accept_bridge_context(
        build_v2_bridge_context(
            ChatMessageRequest(
                workflow_id="_scratch",
                message="/append also update tests",
                surface_type="telegram",
                surface_id="bot",
                session_id="chat-1:topic-9",
                thread_id="thread-1",
                surface_context={
                    "telegram": {
                        "chat_id": 1,
                        "message_thread_id": 9,
                        "chat_type": "private",
                    }
                },
            )
        )
    )

    assert appended.task_id == accepted.task_id
    assert appended.snapshot is not None
    assert appended.snapshot.metadata["workspace_root"] == str(workspace.resolve())
    queue_metadata = appended.snapshot.metadata["queue_items"][0]["metadata"]
    assert queue_metadata["workspace_root"] == str(workspace.resolve())
    assert queue_metadata["operator_context"]["raw_text"] == "/append also update tests"
    assert queue_metadata["operator_context"]["validation_requirements"]


@pytest.mark.asyncio
async def test_v2_agent_run_endpoints_expose_task_and_event_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    req = ChatMessageRequest(
        workflow_id="_scratch",
        message="build the landing page",
        mode="agent",
        surface_type="web",
        surface_id="v2",
        thread_id="thread-web",
    )

    created = await chat_v2_router.create_agent_run(req=req)

    task_id = created["v2_control_plane"]["task_id"]
    run_id = created["v2_control_plane"]["run_id"]
    assert task_id
    assert run_id
    assert created["task"]["status"] == "queued"

    task_response = await chat_v2_router.get_task(task_id)
    run_response = await chat_v2_router.get_agent_run(run_id)
    assert task_response["task"]["task_id"] == task_id
    assert run_response["run"]["run_id"] == run_id

    completed = await chat_v2_router.append_agent_run_event(
        run_id,
        AgentRunEvent(type="completed", summary="Done."),
    )

    assert completed["task"]["status"] == "completed"
    events = await chat_v2_router.get_agent_run_events(run_id)
    assert [event["type"] for event in events["events"]] == ["accepted", "completed"]


@pytest.mark.asyncio
async def test_v2_agent_run_execute_uses_backend_adapter_and_persists_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="build the landing page",
            mode="agent",
            surface_type="web",
            surface_id="v2",
            thread_id="thread-web",
            surface_context={"workspace_root": str(tmp_path / "workspace")},
        )
    )
    run_id = created["v2_control_plane"]["run_id"]

    executed = await chat_v2_router.execute_agent_run(
        run_id,
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )

    assert executed["status"] == "completed"
    assert executed["backend"] == "deterministic"
    assert executed["run"]["metadata"]["selected_backend"] == "deterministic"
    assert executed["task"]["status"] == "completed"
    assert executed["task"]["metadata"]["selected_backend"] == "deterministic"
    assert executed["task"]["metadata"]["workspace_root"] == str((tmp_path / "workspace").resolve())
    assert executed["run"]["token_usage"]["total_tokens"] > 0
    assert executed["task"]["token_usage"]["total_tokens"] == executed["run"]["token_usage"]["total_tokens"]
    assert executed["task"]["latest_token_usage_round"]["model"] == "deterministic"
    events = await chat_v2_router.get_agent_run_events(run_id)
    assert [event["type"] for event in events["events"]] == [
        "accepted",
        "planned",
        "worker_started",
        "token_usage_recorded",
        "completed",
    ]


@pytest.mark.asyncio
async def test_v2_agent_run_append_command_is_admitted_at_backend_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="build the landing page",
            mode="agent",
            surface_type="web",
            surface_id="v2",
            thread_id="thread-web",
            surface_context={"workspace_root": str(tmp_path / "workspace")},
        )
    )
    run_id = created["v2_control_plane"]["run_id"]

    queued = await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(
            command="append_followup",
            surface_turn_id="turn-followup-1",
            idempotency_key="followup-1",
            payload={
                "text": (
                    "also document keyboard shortcuts in README.md; "
                    "do not touch app.py; validate with pytest"
                )
            },
        ),
    )

    assert queued["event"]["type"] == "queue_item_added"
    assert queued["task"]["metadata"]["append_queue_length"] == 1
    operator_context = queued["task"]["metadata"]["queue_items"][0]["metadata"][
        "operator_context"
    ]
    assert operator_context["target_paths"] == ["README.md", "app.py"]
    assert operator_context["hard_constraints"]
    assert operator_context["validation_requirements"]

    executed = await chat_v2_router.execute_agent_run(
        run_id,
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )

    assert executed["status"] == "completed"
    assert executed["task"]["metadata"]["append_queue_length"] == 0
    events = await chat_v2_router.get_agent_run_events(run_id)
    event_types = [event["type"] for event in events["events"]]
    assert event_types == [
        "accepted",
        "queue_item_added",
        "queue_item_injected",
        "planned",
        "worker_started",
        "token_usage_recorded",
        "completed",
    ]
    injected = events["events"][2]
    assert injected["payload"]["checkpoint"] == "backend.start"
    assert injected["payload"]["text"].startswith("also document keyboard shortcuts")
    injected_context = injected["payload"]["metadata"]["operator_context"]
    assert injected_context["target_paths"] == ["README.md", "app.py"]


@pytest.mark.asyncio
async def test_v2_status_command_reports_without_mutating_run_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="build the landing page",
            mode="agent",
            surface_type="web",
            surface_id="v2",
            thread_id="thread-web",
        )
    )
    run_id = created["v2_control_plane"]["run_id"]
    task_id = created["v2_control_plane"]["task_id"]
    await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(
            command="append_followup",
            surface_turn_id="turn-status-append",
            payload={"text": "also update README.md"},
        ),
    )
    before = await chat_v2_router.get_agent_run_events(run_id)

    reported = await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(command="status", surface_turn_id="turn-status-1"),
    )
    after = await chat_v2_router.get_agent_run_events(run_id)

    assert reported["event"]["type"] == "status_reported"
    assert reported["event"]["run_id"] == run_id
    assert reported["event"]["task_id"] == task_id
    assert reported["event"]["payload"]["run_status"] == "queued"
    assert reported["event"]["payload"]["task_status"] == "queued"
    assert reported["event"]["payload"]["append_queue_length"] == 1
    assert reported["task"]["metadata"]["append_queue_length"] == 1
    assert [event["type"] for event in after["events"]] == [
        event["type"] for event in before["events"]
    ]


@pytest.mark.asyncio
async def test_v2_branch_command_creates_sibling_queued_agent_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="build the landing page",
            mode="agent",
            surface_type="web",
            surface_id="v2",
            thread_id="thread-web",
            surface_context={"workspace_root": str(workspace)},
        )
    )
    source_run_id = created["v2_control_plane"]["run_id"]
    source_task_id = created["v2_control_plane"]["task_id"]

    branched = await chat_v2_router.append_agent_run_command(
        source_run_id,
        AgentRunCommand(
            command="branch_from",
            surface_turn_id="turn-branch-1",
            idempotency_key="branch-1",
            payload={
                "text": "try the same page as a docs-focused version in README.md",
                "branch_label": "docs version",
            },
        ),
    )

    event = branched["event"]
    branch_task = branched["branch_task"]
    branch_run = branched["branch_run"]
    assert event["type"] == "branch_created"
    assert event["run_id"] == source_run_id
    assert event["task_id"] == source_task_id
    assert branch_task["task_id"] != source_task_id
    assert branch_task["metadata"]["branched_from_task_id"] == source_task_id
    assert branch_run["run_id"] == event["payload"]["branch_run_id"]
    assert branch_run["task_id"] == branch_task["task_id"]
    assert branch_run["status"] == "queued"
    assert branch_run["metadata"]["branched_from_run_id"] == source_run_id
    assert branch_run["command"]["payload"]["text"].startswith("try the same page")
    assert branch_run["command"]["payload"]["operator_context"]["target_paths"] == [
        "README.md"
    ]
    assert store.get_run(source_run_id).status == "queued"
    source_events = await chat_v2_router.get_agent_run_events(source_run_id)
    branch_events = await chat_v2_router.get_agent_run_events(branch_run["run_id"])
    assert [item["type"] for item in source_events["events"]] == [
        "accepted",
        "branch_created",
    ]
    assert [item["type"] for item in branch_events["events"]] == ["accepted"]

    completed = await chat_v2_router.execute_agent_run(
        branch_run["run_id"],
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )

    assert completed["status"] == "completed"
    assert completed["run"]["metadata"]["branched_from_run_id"] == source_run_id


@pytest.mark.asyncio
async def test_v2_human_queue_stays_out_of_super_dan_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="build the landing page",
            mode="agent",
            surface_type="web",
            surface_id="v2",
            thread_id="thread-web",
            surface_context={"workspace_root": str(workspace)},
        )
    )
    run_id = created["v2_control_plane"]["run_id"]

    queued = await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(
            command="append_followup",
            surface_turn_id="turn-followup-no-super-state",
            payload={"text": "also update README.md"},
        ),
    )

    assert queued["task"]["metadata"]["append_queue_length"] == 1
    assert not (workspace / ".dan-super" / "state").exists()
    executed = await chat_v2_router.execute_agent_run(
        run_id,
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )

    assert executed["status"] == "completed"
    assert not (workspace / ".dan-super" / "state").exists()


@pytest.mark.asyncio
async def test_v2_surface_command_payload_aliases_share_queue_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="build the landing page",
            mode="agent",
            surface_type="web",
            surface_id="v2",
            thread_id="thread-web",
            surface_context={"workspace_root": str(workspace)},
        )
    )
    run_id = created["v2_control_plane"]["run_id"]
    payloads = [
        ("gui", {"text": "update README.md"}),
        ("tui", {"message": "update docs/notes.md"}),
        ("telegram", {"content": "validate with pytest"}),
        ("cli", {"objective": "only edit app.py"}),
    ]

    for source, payload in payloads:
        queued = await chat_v2_router.append_agent_run_command(
            run_id,
            AgentRunCommand(
                command="append_followup",
                surface_turn_id=f"turn-{source}",
                idempotency_key=f"append-{source}",
                payload={"surface": source, **payload},
            ),
        )
        assert queued["event"]["type"] == "queue_item_added"

    snapshot = store.get_task_snapshot(created["v2_control_plane"]["task_id"])
    assert snapshot is not None
    queue_items = snapshot.metadata["queue_items"]
    assert [item["text"] for item in queue_items] == [
        "update README.md",
        "update docs/notes.md",
        "validate with pytest",
        "only edit app.py",
    ]
    assert [item["metadata"]["command"] for item in queue_items] == [
        "append_followup",
        "append_followup",
        "append_followup",
        "append_followup",
    ]
    assert [item["metadata"]["operator_context"]["raw_text"] for item in queue_items] == [
        "update README.md",
        "update docs/notes.md",
        "validate with pytest",
        "only edit app.py",
    ]
    assert all(
        item["metadata"]["workspace_root"] == str(workspace.resolve())
        for item in queue_items
    )


@pytest.mark.asyncio
async def test_v2_continue_after_current_command_promotes_next_run_after_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="build the landing page",
            mode="agent",
            surface_type="web",
            surface_id="v2",
            thread_id="thread-web",
        )
    )
    run_id = created["v2_control_plane"]["run_id"]

    queued = await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(
            command="continue_after_current",
            surface_turn_id="turn-followup-2",
            idempotency_key="followup-2",
            payload={"text": "then run a polish pass"},
        ),
    )

    assert queued["event"]["type"] == "queue_item_added"
    assert queued["task"]["metadata"]["continue_queue_length"] == 1

    executed = await chat_v2_router.execute_agent_run(
        run_id,
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )

    assert executed["status"] == "completed"
    assert executed["task"]["metadata"]["continue_queue_length"] == 0
    next_run_id = executed["task"]["metadata"]["active_run_id"]
    assert next_run_id != run_id
    next_run = store.get_run(next_run_id)
    assert next_run is not None
    assert next_run.status == "queued"
    assert next_run.command.payload["text"] == "then run a polish pass"
    assert next_run.command.payload["continued_from_run_id"] == run_id
    assert store.get_run(run_id).metadata["continued_run_id"] == next_run_id
    events = await chat_v2_router.get_agent_run_events(run_id)
    event_types = [event["type"] for event in events["events"]]
    assert event_types[-2:] == ["completed", "queue_item_injected"]
    next_events = await chat_v2_router.get_agent_run_events(next_run_id)
    assert [event["type"] for event in next_events["events"]] == ["accepted"]

    next_executed = await chat_v2_router.execute_agent_run(
        next_run_id,
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )

    assert next_executed["status"] == "completed"


@pytest.mark.asyncio
async def test_v2_background_execution_auto_runs_promoted_continue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="build the landing page",
            mode="agent",
            surface_type="web",
            surface_id="v2",
            thread_id="thread-web",
        )
    )
    run_id = created["v2_control_plane"]["run_id"]
    await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(
            command="continue_after_current",
            surface_turn_id="turn-followup-background",
            idempotency_key="followup-background",
            payload={"text": "then add tests"},
        ),
    )

    await chat_v2_router._execute_agent_run_background(
        store,
        run_id,
        backend_name="deterministic",
        overrides={},
        auto_execute_continuations=True,
        remaining_continuations=2,
    )

    first_run = store.get_run(run_id)
    assert first_run.status == "completed"
    next_run_id = first_run.metadata["continued_run_id"]
    next_run = store.get_run(next_run_id)
    assert next_run is not None
    assert next_run.status == "completed"
    assert next_run.metadata["promoted_from_run_id"] == run_id
    first_events = await chat_v2_router.get_agent_run_events(run_id)
    next_events = await chat_v2_router.get_agent_run_events(next_run_id)
    assert first_events["events"][-1]["type"] == "queue_item_injected"
    assert next_events["events"][-1]["type"] == "completed"


@pytest.mark.asyncio
async def test_v2_cancel_command_stops_at_backend_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="build the landing page",
            mode="agent",
            surface_type="web",
            surface_id="v2",
            thread_id="thread-web",
        )
    )
    run_id = created["v2_control_plane"]["run_id"]

    requested = await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(
            command="cancel",
            surface_turn_id="turn-cancel-1",
            payload={"reason": "operator changed priorities"},
        ),
    )

    assert requested["event"]["type"] == "stop_requested"
    assert requested["task"]["status"] == "queued"
    assert store.get_run(run_id).metadata["stop_requested"] is True

    executed = await chat_v2_router.execute_agent_run(
        run_id,
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )

    assert executed["status"] == "stopped"
    assert executed["run"]["status"] == "stopped"
    assert executed["task"]["status"] == "stopped"
    assert executed["run"]["metadata"]["backend_result"]["status"] == "stopped"
    assert executed["run"]["metadata"]["stop_checkpoint"] == "backend.start"
    events = await chat_v2_router.get_agent_run_events(run_id)
    assert [event["type"] for event in events["events"]] == [
        "accepted",
        "stop_requested",
        "stopped",
    ]


@pytest.mark.asyncio
async def test_v2_pause_command_pauses_at_backend_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="build the landing page",
            mode="agent",
            surface_type="web",
            surface_id="v2",
            thread_id="thread-web",
        )
    )
    run_id = created["v2_control_plane"]["run_id"]

    requested = await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(
            command="pause",
            surface_turn_id="turn-pause-1",
            payload={"reason": "operator wants to inspect"},
        ),
    )

    assert requested["event"]["type"] == "pause_requested"
    assert requested["task"]["status"] == "queued"
    assert store.get_run(run_id).metadata["pause_requested"] is True

    executed = await chat_v2_router.execute_agent_run(
        run_id,
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )

    assert executed["status"] == "paused"
    assert executed["run"]["status"] == "paused"
    assert executed["task"]["status"] == "paused"
    assert executed["run"]["metadata"]["backend_result"]["status"] == "paused"
    assert executed["run"]["metadata"]["pause_checkpoint"] == "backend.start"
    assert "paused" in chat_v2_router._TERMINAL_RUN_STATUSES
    events = await chat_v2_router.get_agent_run_events(run_id)
    assert [event["type"] for event in events["events"]] == [
        "accepted",
        "pause_requested",
        "paused",
    ]


@pytest.mark.asyncio
async def test_v2_resume_command_requeues_paused_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="build the landing page",
            mode="agent",
            surface_type="web",
            surface_id="v2",
            thread_id="thread-web",
        )
    )
    run_id = created["v2_control_plane"]["run_id"]
    await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(command="pause", surface_turn_id="turn-pause-resume"),
    )
    paused = await chat_v2_router.execute_agent_run(
        run_id,
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )
    assert paused["status"] == "paused"

    resumed = await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(
            command="resume",
            surface_turn_id="turn-resume-1",
            payload={"reason": "operator approved continuation"},
        ),
    )

    assert resumed["event"]["type"] == "queued"
    assert store.get_run(run_id).status == "queued"
    assert store.get_run(run_id).metadata["resume_policy"] == "restart_backend_run_from_paused_boundary"
    assert "pause_requested" not in store.get_run(run_id).metadata

    completed = await chat_v2_router.execute_agent_run(
        run_id,
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )

    assert completed["status"] == "completed"
    events = await chat_v2_router.get_agent_run_events(run_id)
    assert [event["type"] for event in events["events"]] == [
        "accepted",
        "pause_requested",
        "paused",
        "queued",
        "planned",
        "worker_started",
        "token_usage_recorded",
        "completed",
    ]


@pytest.mark.asyncio
async def test_v2_append_command_preserves_paused_run_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="build the landing page",
            mode="agent",
            surface_type="web",
            surface_id="v2",
            thread_id="thread-web",
        )
    )
    run_id = created["v2_control_plane"]["run_id"]
    await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(command="pause", surface_turn_id="turn-pause-append"),
    )
    paused = await chat_v2_router.execute_agent_run(
        run_id,
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )
    assert paused["status"] == "paused"

    queued = await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(
            command="append_followup",
            surface_turn_id="turn-append-while-paused",
            payload={"text": "when resumed, only update README.md"},
        ),
    )

    assert queued["event"]["type"] == "queue_item_added"
    assert queued["task"]["status"] == "paused"
    run = store.get_run(run_id)
    assert run is not None
    assert run.status == "paused"
    queue_metadata = queued["task"]["metadata"]["queue_items"][0]["metadata"]
    assert queue_metadata["operator_context"]["target_paths"] == ["README.md"]
    assert queue_metadata["operator_context"]["hard_constraints"]


@pytest.mark.asyncio
async def test_v2_retry_command_requeues_stopped_run_and_clears_stop_flags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="build the landing page",
            mode="agent",
            surface_type="web",
            surface_id="v2",
            thread_id="thread-web",
        )
    )
    run_id = created["v2_control_plane"]["run_id"]
    await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(command="cancel", surface_turn_id="turn-cancel-retry"),
    )
    stopped = await chat_v2_router.execute_agent_run(
        run_id,
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )
    assert stopped["status"] == "stopped"
    assert store.get_run(run_id).metadata["stop_requested"] is True

    retried = await chat_v2_router.append_agent_run_command(
        run_id,
        AgentRunCommand(
            command="retry",
            surface_turn_id="turn-retry-1",
            payload={"reason": "operator wants another attempt"},
        ),
    )

    run = store.get_run(run_id)
    assert retried["event"]["type"] == "queued"
    assert retried["event"]["payload"]["previous_status"] == "stopped"
    assert retried["task"]["status"] == "queued"
    assert run is not None
    assert run.status == "queued"
    assert run.metadata["retry_count"] == 1
    assert run.metadata["retry_policy"] == "restart_backend_run_from_original_request"
    assert run.metadata["retry_history"][0]["status"] == "stopped"
    assert "stop_requested" not in run.metadata
    assert "stop_checkpoint" not in run.metadata
    assert "backend_result" not in run.metadata

    completed = await chat_v2_router.execute_agent_run(
        run_id,
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )

    assert completed["status"] == "completed"
    events = await chat_v2_router.get_agent_run_events(run_id)
    assert [event["type"] for event in events["events"]] == [
        "accepted",
        "stop_requested",
        "stopped",
        "queued",
        "planned",
        "worker_started",
        "token_usage_recorded",
        "completed",
    ]


def test_v2_agent_run_events_websocket_replays_persisted_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    accepted = store.accept_bridge_context(
        build_v2_bridge_context(
            ChatMessageRequest(
                workflow_id="_scratch",
                message="build the dashboard",
                mode="agent",
                surface_type="web",
                surface_id="v2",
                thread_id="thread-web",
            )
        )
    )
    assert accepted.run_id is not None
    store.record_agent_event(
        AgentRunEvent(
            type="completed",
            run_id=accepted.run_id,
            summary="Done.",
        )
    )
    app = FastAPI()
    app.include_router(chat_v2_router.router)

    received: list[dict[str, Any]] = []
    with TestClient(app) as client:
        with client.websocket_connect(
            f"/api/v2/agent-runs/{accepted.run_id}/events"
        ) as websocket:
            with pytest.raises(WebSocketDisconnect):
                while True:
                    received.append(websocket.receive_json())

    assert [event["type"] for event in received] == ["accepted", "completed"]


def test_v2_agent_progress_state_machine_uses_backend_events() -> None:
    now = 100.0

    def _clock() -> float:
        return now

    progress = AgentProgressStateMachine(run_id="run-1", clock=_clock)
    accepted = progress.observe(
        AgentRunEvent(type="accepted", run_id="run-1", summary="Accepted.")
    )
    assert accepted.phase == "Got it"

    now += 2.0
    web = progress.observe(
        AgentRunEvent(
            type="tool_used",
            run_id="run-1",
            source_event_type="tool.started",
            payload={
                "tool_id": "web_search",
                "arguments": {"query": "latest model docs"},
            },
        )
    )
    assert web.phase == "Fetching web evidence"
    assert "web_search" in web.detail

    now += 10.0
    heartbeat = progress.render_status(heartbeat=True)
    assert "Fetching web evidence" in heartbeat
    assert "Elapsed: 12s" in heartbeat
    assert "Last backend event: 10s ago" in heartbeat

    now += 1.0
    summarize = progress.observe(
        AgentRunEvent(
            type="model_text_delta",
            run_id="run-1",
            source_event_type="model.requested",
            payload={"tool_count": 0, "round": 2},
        )
    )
    assert summarize.phase == "Summarizing"

    progress.observe(
        AgentRunEvent(
            type="token_usage_recorded",
            run_id="run-1",
            token_usage_delta={"input_tokens": 10, "output_tokens": 5},
            token_usage_total={"input_tokens": 10, "output_tokens": 5},
        )
    )
    done = progress.observe(
        AgentRunEvent(
            type="completed",
            run_id="run-1",
            summary="Done.",
            artifact_refs=[{"path": "report.md"}],
        )
    )
    rendered = progress.render_status()
    assert done.terminal is True
    assert "Done: Done." in rendered
    assert "Tokens: prompt=10, completion=5, total=15" in rendered
    assert "- report.md" in rendered


@pytest.mark.asyncio
async def test_v2_telegram_progress_sink_edits_and_falls_back() -> None:
    class _Adapter:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def send_or_edit(
            self,
            chat_id,
            text,
            message_id=None,
            *,
            reply_to=None,
            thread_id=None,
        ):
            self.calls.append(
                {
                    "chat_id": chat_id,
                    "text": text,
                    "message_id": message_id,
                    "reply_to": reply_to,
                    "thread_id": thread_id,
                }
            )
            if message_id == 55:
                raise RuntimeError("edit failed")
            return 55 if message_id is None else message_id

    adapter = _Adapter()
    sink = TelegramProgressSink(adapter)
    handle = SurfaceUpdateHandle(
        surface_type="telegram",
        native_chat_id="111",
        native_thread_id="7",
        native_message_id="42",
        reply_to_message_id="41",
        supports_edit=True,
    )

    sent = await sink.deliver(
        AgentRunEvent(type="accepted", summary="Accepted."),
        handle,
    )
    fallback = await sink.deliver(
        AgentRunEvent(type="completed", summary="Done."),
        handle,
        progress_message_id=55,
    )

    assert sent.delivered is True
    assert sent.native_message_id == "55"
    assert fallback.delivered is True
    assert fallback.used_edit is False
    assert adapter.calls == [
        {
            "chat_id": 111,
            "text": "Accepted.",
            "message_id": None,
            "reply_to": 41,
            "thread_id": 7,
        },
        {
            "chat_id": 111,
            "text": "Done.",
            "message_id": 55,
            "reply_to": None,
            "thread_id": 7,
        },
        {
            "chat_id": 111,
            "text": "Done.",
            "message_id": None,
            "reply_to": 41,
            "thread_id": 7,
        },
    ]


@pytest.mark.asyncio
async def test_v2_chat_endpoint_forces_v2_and_delegates_to_legacy_chat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    captured: dict[str, Any] = {}
    store = ChatV2Store(tmp_path / "chat_v2")

    async def _fake_chat_message(*, request=None, req=None, concierge=True):
        captured["request"] = request
        captured["req"] = req
        captured["concierge"] = concierge
        return {
            "message_id": "msg-1",
            "stream_channel_id": "chat-1",
            "status": "processing",
            "control_plane_mode": req.control_plane_mode,
        }

    monkeypatch.setattr(chat_v2_router, "chat_message", _fake_chat_message)
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)

    req = ChatMessageRequest(
        workflow_id="_scratch",
        message="build the agent dashboard",
        mode="agent",
        surface_type="telegram",
        surface_id="bot",
        attachment_path="/tmp/mockup.png",
    )

    response = await chat_v2_router.chat_v2_message(req=req, concierge=True)

    delegated_req = captured["req"]
    assert delegated_req.control_plane_mode == "v2"
    assert delegated_req.surface_context["v2_control_plane"]["triage_decision"]["action"] == "agent_requested"
    assert delegated_req.surface_context["v2_control_plane"]["surface_turn"]["attachments"][0]["kind"] == "image"
    assert response["v2_endpoint"] is True
    assert response["v2_control_plane"]["triage_action"] == "agent_requested"
    assert response["v2_control_plane"]["workspace_root"] == str(Path.home())
    assert response["v2_control_plane"]["attachment_count"] == 1
    assert response["v2_control_plane"]["task_id"]
    assert response["v2_control_plane"]["run_id"]
    assert store.get_run(response["v2_control_plane"]["run_id"]).stream_channel_id == "chat-1"
