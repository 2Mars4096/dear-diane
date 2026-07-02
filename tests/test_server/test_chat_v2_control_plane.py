from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dan.server.chat_v2 import (
    AgentRunCommand,
    AgentRunEvent,
    SurfaceTurn,
    SurfaceUpdateHandle,
    build_surface_turn_from_chat_request,
    build_v2_bridge_context,
    summarize_v2_bridge_context,
    triage_surface_turn,
)
from dan.server.chat_v2_async_core import (
    admit_foreground_turn,
    build_task_board_snapshot,
    mark_background_run_started,
    parse_admission_command,
)
from dan.server.chat_v2_progress import AgentProgressStateMachine, TelegramProgressSink
from dan.server.chat_v2_organism import map_organism_log_row_to_agent_event
from dan.server.chat_v2_backend import (
    AgentBackendRunRequest,
    AgentBackendRunResult,
    CodexAgentBackendAdapter,
    _CodexObservableTaskGraph,
    _build_codex_exec_command,
    _build_super_dan_args,
    _is_safe_backend_checkpoint,
    _load_super_dan_cli,
    _objective_with_surface_context,
    build_agent_backend_request,
    run_agent_backend,
    select_agent_backend_adapter,
)
from dan.server.chat_v2_store import (
    AgentRunRecord,
    ChatV2Store,
    QueueItemRecord,
    V2TaskRecord,
    _start_payload_from_queue_item,
    structured_operator_context,
)
from dan.server.routers.chat import ChatMessageRequest
from dan.server.routers import chat_v2 as chat_v2_router


def test_v2_operator_context_extracts_absolute_target_folder() -> None:
    context = structured_operator_context(
        'Can you build a static app in /private/tmp/dan-blueprint-smoke/trip-planner? '
        "include README.md, see https://example.com/docs/guide, and run a quick smoke check."
    )

    assert context["target_paths"] == [
        "README.md",
        "/private/tmp/dan-blueprint-smoke/trip-planner",
    ]


def test_v2_parse_admission_command_continuation_alias_is_append() -> None:
    hints = parse_admission_command("/continue run compiler fixes now")
    assert hints.explicit_append is True
    assert hints.payload_text == "run compiler fixes now"
    assert hints.command == "/continue"


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
    assert "message_edit" in turn.capabilities
    assert "hugo_notes" in turn.capabilities
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


def test_v2_surface_turn_uses_conversation_lane_for_surface_topic(tmp_path) -> None:
    req = ChatMessageRequest(
        workflow_id="_scratch",
        message="build one independent Telegram task",
        mode="agent",
        surface_type="telegram",
        surface_id="bot",
        session_id="chat-1:topic-7:m42",
        thread_id="chat-1:topic-7",
        surface_context={
            "workspace_root": str(tmp_path),
            "telegram": {
                "chat_id": 1,
                "message_thread_id": 7,
                "message_id": 42,
                "from_user_id": 9,
                "chat_type": "private",
            },
            "conversation": {
                "conversation_key": "chat-1:topic-7",
                "lane_key": "chat-1:topic-7:m42",
            },
        },
    )

    turn = build_surface_turn_from_chat_request(req)

    assert turn.metadata["surface_topic_key"].endswith("chat-1:topic-7:m42")


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


def test_v2_super_dan_args_forward_structured_surface_context(tmp_path) -> None:
    history = [
        {"role": "user", "content": "does it compile?"},
        {"role": "assistant", "content": "Fresh validation failed earlier."},
    ]
    surface_context = {
        "workspace_root": str(tmp_path),
        "workspace_source": "super_tui",
        "conversation": {"recent_turns": history},
        "communication_policy": {"answer_budget": "brief", "latency_preference": "fast"},
        "execution_policy": {
            "autonomy_mode": "guided",
            "stop_condition": "validation_passes",
            "max_auto_fix_rounds": 0,
            "allow_repair_cycles": False,
        },
        "surface_policy": {
            "controller_lane": "execute",
            "permission_scope": "transient_execute",
            "phase_shape": "validation_gate",
        },
    }
    request = AgentBackendRunRequest(
        task_id="task-1",
        run_id="run-1",
        objective="can you make sure it successfully compiles?",
        workspace_root=str(tmp_path),
        history=[],
        surface_context=surface_context,
        metadata={},
    )

    args = _build_super_dan_args(
        _load_super_dan_cli(),
        request,
        workspace_root=tmp_path,
        objective=request.objective,
    )

    assert args._surface_history == history
    assert args._tui_surface_history == history
    assert args._surface_context["workspace_root"] == surface_context["workspace_root"]
    assert args._surface_context["conversation"] == surface_context["conversation"]
    assert args._surface_context["communication_policy"] == surface_context["communication_policy"]
    assert args._surface_context["execution_policy"] == surface_context["execution_policy"]
    assert args._surface_context["surface_policy"] == surface_context["surface_policy"]
    assert args._surface_context["notes_feature"]["kind"] == "hugo_notes"
    assert args._tui_communication_policy["answer_budget"] == "brief"
    assert args._tui_execution_policy["stop_condition"] == "validation_passes"
    assert args._tui_surface_policy["phase_shape"] == "validation_gate"


def test_v2_continue_payload_rechecks_mutation_policy_instead_of_inheriting(
    tmp_path,
) -> None:
    previous_run = AgentRunRecord(
        run_id="run-1",
        task_id="task-1",
        thread_id="thread-1",
        workspace_root=str(tmp_path),
        workspace_id=str(tmp_path),
        status="completed",
        command=AgentRunCommand(
            command="start",
            task_id="task-1",
            run_id="run-1",
            payload={
                "text": "Inspect the website and suggest exact changes.",
                "profile_policy": {"backend": "super_dan"},
                "mutation_policy": {
                    "mode": "workspace_read",
                    "permission": "forbidden",
                },
            },
        ),
        latest_summary="Read-only inspection completed with paste-ready changes.",
    )
    task = V2TaskRecord(
        task_id="task-1",
        thread_id="thread-1",
        workspace_root=str(tmp_path),
        workspace_id=str(tmp_path),
        metadata={"original_request": "Inspect the website and suggest exact changes."},
    )
    queue_item = QueueItemRecord(
        id="queue-1",
        task_id="task-1",
        lane="continue_after_current",
        surface_turn_id="turn-2",
        text="ok sounds good, please implement these?",
        metadata={
            "original_request": "Inspect the website and suggest exact changes.",
            "satisfaction_gap": "ok sounds good, please implement these?",
        },
    )

    payload = _start_payload_from_queue_item(
        task,
        queue_item,
        previous_run=previous_run,
        previous_run_id=previous_run.run_id,
    )

    assert payload["profile_policy"] == {"backend": "super_dan"}
    assert "mutation_policy" not in payload
    assert payload["original_request"] == "Inspect the website and suggest exact changes."
    assert payload["satisfaction_gap"] == "ok sounds good, please implement these?"


def test_v2_backend_request_recomputes_mutation_policy_for_each_run(tmp_path) -> None:
    followup = "ok sounds good, please implement these?"
    run = AgentRunRecord(
        run_id="run-2",
        task_id="task-1",
        thread_id="thread-1",
        workspace_root=str(tmp_path),
        workspace_id=str(tmp_path),
        command=AgentRunCommand(
            command="start",
            task_id="task-1",
            run_id="run-2",
            payload={
                "text": followup,
                "original_request": "Inspect the website and suggest exact changes.",
                "satisfaction_gap": followup,
                "continued_from_run_id": "run-1",
                "mutation_policy": {
                    "mode": "workspace_read",
                    "permission": "forbidden",
                },
            },
        ),
    )
    task = V2TaskRecord(
        task_id="task-1",
        thread_id="thread-1",
        workspace_root=str(tmp_path),
        workspace_id=str(tmp_path),
    )

    request = build_agent_backend_request(run, task)

    assert request.mutation_policy["source"] == "operator_intent_policy"
    assert request.mutation_policy["mode"] == "workspace_mutation"
    assert request.mutation_policy["permission"] == "workspace_mutation"
    assert request.mutation_policy["operator_mutation_policy"] == "required"


def test_v2_backend_request_recompute_keeps_hard_no_edits_binding(tmp_path) -> None:
    followup = "ok sounds good, please implement these?"
    run = AgentRunRecord(
        run_id="run-2",
        task_id="task-1",
        thread_id="thread-1",
        workspace_root=str(tmp_path),
        workspace_id=str(tmp_path),
        command=AgentRunCommand(
            command="start",
            task_id="task-1",
            run_id="run-2",
            payload={
                "text": followup,
                "original_request": "Inspect the website and suggest exact changes. No edits.",
                "satisfaction_gap": followup,
                "continued_from_run_id": "run-1",
                "mutation_policy": {
                    "mode": "workspace_mutation",
                    "permission": "workspace_mutation",
                },
            },
        ),
    )
    task = V2TaskRecord(
        task_id="task-1",
        thread_id="thread-1",
        workspace_root=str(tmp_path),
        workspace_id=str(tmp_path),
    )

    request = build_agent_backend_request(run, task)

    assert request.mutation_policy["source"] == "operator_intent_policy"
    assert request.mutation_policy["mode"] == "workspace_read"
    assert request.mutation_policy["permission"] == "forbidden"
    assert request.mutation_policy["operator_mutation_policy"] == "forbidden"


def test_v2_selects_codex_agent_backend_from_profile_policy(tmp_path) -> None:
    request = AgentBackendRunRequest(
        task_id="task-1",
        run_id="run-1",
        objective="please work on this repo",
        workspace_root=str(tmp_path),
        profile_policy={"backend": "codex"},
    )

    adapter = select_agent_backend_adapter(request)

    assert isinstance(adapter, CodexAgentBackendAdapter)


def test_v2_codex_exec_command_is_additive_and_workspace_scoped(tmp_path) -> None:
    request = AgentBackendRunRequest(
        task_id="task-1",
        run_id="run-1",
        objective="please work on this repo",
        workspace_root=str(tmp_path),
        profile_policy={
            "backend": "codex",
            "codex_model": "gpt-5.5",
            "codex_reasoning_effort": "high",
            "codex_sandbox": "read-only",
        },
    )

    command = _build_codex_exec_command(
        "/usr/local/bin/codex",
        request,
        workspace_root=tmp_path,
        objective="please work on this repo",
    )

    assert command == [
        "/usr/local/bin/codex",
        "exec",
        "--json",
        "--color",
        "never",
        "--sandbox",
        "read-only",
        "--cd",
        str(tmp_path),
        "--skip-git-repo-check",
        "--model",
        "gpt-5.5",
        "-c",
        'model_reasoning_effort="high"',
        "--ephemeral",
        "please work on this repo",
    ]


class _FailingCodexContinuationAdapter:
    backend_name = "codex"

    async def run(self, request, emit_event, runtime=None):
        emit_event(
            AgentRunEvent(
                type="failed",
                run_id=request.run_id,
                task_id=request.task_id,
                summary="Codex hit an output-size parsing limit while reading command output.",
                source_event_type="turn.failed",
                payload={"backend": "codex"},
            )
        )
        return AgentBackendRunResult(
            status="failed",
            backend="codex",
            summary="Codex hit an output-size parsing limit while reading command output.",
            raw_result={
                "return_code": 1,
                "stderr": "Separator is not found, and chunk exceeded the limit",
                "events": [{"type": "turn.failed", "message": "chunk exceeded the limit"}],
            },
        )


class _CompletingContextAdapter:
    backend_name = "deterministic"

    async def run(self, request, emit_event, runtime=None):
        emit_event(
            AgentRunEvent(
                type="completed",
                run_id=request.run_id,
                task_id=request.task_id,
                summary="Completed context composer smoke run.",
                source_event_type="deterministic.completed",
            )
        )
        return AgentBackendRunResult(
            status="completed",
            backend=self.backend_name,
            summary="Completed context composer smoke run.",
            raw_result={"final_text": "Completed context composer smoke run."},
        )


@pytest.mark.asyncio
async def test_v2_codex_failed_limit_queues_bounded_auto_continuation(tmp_path) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    accepted = store.accept_bridge_context(
        build_v2_bridge_context(
            ChatMessageRequest(
                workflow_id="_scratch",
                message="build a static project page and report back",
                mode="agent",
                surface_type="web",
                surface_id="v2",
                thread_id="thread-web",
                history=[{"role": "assistant", "content": "Earlier context"}],
            )
        )
    )
    assert accepted.run_id is not None

    result = await run_agent_backend(
        store,
        accepted.run_id,
        adapter=_FailingCodexContinuationAdapter(),
        backend_name="codex",
    )

    assert result.status == "failed"
    first_run = store.get_run(accepted.run_id)
    assert first_run is not None
    next_run_id = first_run.metadata["continued_run_id"]
    assert next_run_id != accepted.run_id
    next_run = store.get_run(next_run_id)
    assert next_run is not None
    assert next_run.status == "queued"
    assert next_run.metadata["continued_from_run_id"] == accepted.run_id
    assert next_run.metadata["original_request"] == "build a static project page and report back"
    assert "Previous status: failed" in next_run.metadata["satisfaction_gap"]
    assert next_run.command.payload["profile_policy"]["backend"] == "codex"
    assert next_run.command.payload["continued_from_run_id"] == accepted.run_id
    assert next_run.command.payload["auto_continuation_depth"] == 1
    assert next_run.command.payload["history"] == [
        {"role": "assistant", "content": "Earlier context"}
    ]
    backend_request = build_agent_backend_request(next_run, store.get_task(next_run.task_id))
    packet = backend_request.metadata["normalized_request"]
    assert packet["original_request"] == "build a static project page and report back"
    assert packet["current_operator_update"].startswith(
        "The previous Codex run ended before"
    )
    assert packet["continued_from_run_id"] == accepted.run_id
    assert packet["previous_run_status"] == "failed"
    assert "output-size parsing limit" in packet["previous_failure_or_blocker"]
    effective = _objective_with_surface_context(backend_request)
    assert "Original operator request: build a static project page and report back" in effective
    assert "Current follow-up / satisfaction gap:" in effective
    assert "Codex hit an output-size parsing limit" in effective


@pytest.mark.asyncio
async def test_v2_codex_auto_continuation_can_be_disabled_by_profile_policy(tmp_path) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    accepted = store.accept_bridge_context(
        build_v2_bridge_context(
            ChatMessageRequest(
                workflow_id="_scratch",
                message="build a static project page and report back",
                mode="agent",
                surface_type="web",
                surface_id="v2",
                thread_id="thread-web",
            )
        )
    )
    assert accepted.run_id is not None

    result = await run_agent_backend(
        store,
        accepted.run_id,
        adapter=_FailingCodexContinuationAdapter(),
        backend_name="codex",
        overrides={"profile_policy": {"auto_backend_continuation": False}},
    )

    assert result.status == "failed"
    first_run = store.get_run(accepted.run_id)
    assert first_run is not None
    assert "continued_run_id" not in first_run.metadata


@pytest.mark.asyncio
async def test_v2_codex_auto_continuation_cap_uses_scheduler_soft_budget(tmp_path) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    accepted = store.accept_bridge_context(
        build_v2_bridge_context(
            ChatMessageRequest(
                workflow_id="_scratch",
                message="build a static project page and report back",
                mode="agent",
                surface_type="web",
                surface_id="v2",
                thread_id="thread-web",
            )
        )
    )
    assert accepted.run_id is not None
    store.update_run_metadata(
        accepted.run_id,
        {"max_auto_backend_continuations": 0},
    )

    result = await run_agent_backend(
        store,
        accepted.run_id,
        adapter=_FailingCodexContinuationAdapter(),
        backend_name="codex",
    )

    assert result.status == "failed"
    first_run = store.get_run(accepted.run_id)
    assert first_run is not None
    next_run_id = first_run.metadata["continued_run_id"]
    assert next_run_id
    extensions = first_run.metadata["scheduler_budget_extensions"]
    assert extensions[0]["cap_name"] == "backend_auto_continuation"
    assert extensions[0]["extra_continuations"] == 1
    next_run = store.get_run(next_run_id)
    assert next_run is not None
    assert next_run.command.payload["max_auto_backend_continuations"] == 1
    assert next_run.command.payload["scheduler_budget_extensions"][0]["cap_name"] == (
        "backend_auto_continuation"
    )
    event_types = [
        event.get("source_event_type")
        for event in store.load_run_events(accepted.run_id)
    ]
    assert "chat_v2.scheduler.soft_budget.approved" in event_types


@pytest.mark.asyncio
async def test_v2_run_persists_context_composer_for_gui_task_metadata(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = ChatV2Store(tmp_path / "chat_v2")
    accepted = store.accept_bridge_context(
        build_v2_bridge_context(
            ChatMessageRequest(
                workflow_id="_scratch",
                message="continue the selected card",
                mode="agent",
                surface_type="web",
                surface_id="v2",
                thread_id="thread-web",
                surface_context={
                    "workspace_root": str(workspace),
                    "selected_blueprint_node": {
                        "id": "plan-3",
                        "title": "Plan 3",
                        "kind": "plan",
                        "status": "active",
                        "detail": "Dependent plan is generating.",
                    },
                },
            )
        )
    )
    assert accepted.run_id is not None

    await run_agent_backend(
        store,
        accepted.run_id,
        adapter=_CompletingContextAdapter(),
        backend_name="deterministic",
    )

    task_record = store.get_task(accepted.task_id)
    assert task_record is not None
    composer = task_record.metadata["context_composer"]
    assert composer["compiler"] == "chat_v2_context_composer_v1"
    evidence_items = task_record.metadata["shared_evidence_context"]["items"]
    assert any(
        item["kind"] == "selected_card" and "Plan 3" in item["title"]
        for item in evidence_items
    )


def test_v2_normalized_request_carries_selected_card_and_active_file(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = ChatV2Store(tmp_path / "chat_v2")
    accepted = store.accept_bridge_context(
        build_v2_bridge_context(
            ChatMessageRequest(
                workflow_id="_scratch",
                message="please continue from this card",
                mode="agent",
                surface_type="web",
                surface_id="v2",
                thread_id="thread-web",
                surface_context={
                    "workspace_root": str(workspace),
                    "selected_blueprint_node": {
                        "id": "plan-3",
                        "title": "Plan 3",
                        "kind": "plan",
                        "status": "active",
                        "detail": "Dependent plan is now generating.",
                        "preview": "Plan 3 checklist preview",
                    },
                    "active_file": {
                        "path": str(workspace / "src/app.ts"),
                        "relativePath": "src/app.ts",
                        "name": "app.ts",
                        "preview": "export const active = true;",
                    },
                    "conversation": {
                        "recent_turns": [
                            {
                                "role": "user",
                                "content": "build the plan graph UI",
                            },
                            {
                                "role": "assistant",
                                "content": "I started the card rendering pass.",
                            },
                        ]
                    },
                },
            )
        )
    )
    assert accepted.run_id is not None
    run = store.get_run(accepted.run_id)
    assert run is not None

    backend_request = build_agent_backend_request(run, store.get_task(run.task_id))

    assert backend_request.surface_context["selected_blueprint_node"]["title"] == "Plan 3"
    assert backend_request.surface_context["active_file"]["relativePath"] == "src/app.ts"
    packet = backend_request.metadata["normalized_request"]
    assert packet["selected_plan_context"]["selected_card"]["title"] == "Plan 3"
    assert (
        packet["active_workspace_context"]["active_file"]["relativePath"]
        == "src/app.ts"
    )
    composer = packet["context_composer"]
    evidence_items = composer["shared_evidence"]["items"]
    assert composer["compiler"] == "chat_v2_context_composer_v1"
    assert any(
        item["kind"] == "selected_card" and "Plan 3" in item["title"]
        for item in evidence_items
    )
    assert any(
        item["kind"] == "active_file" and item["ref"] == "src/app.ts"
        for item in evidence_items
    )
    effective = _objective_with_surface_context(backend_request)
    assert "Selected plan/card context:" in effective
    assert "Active workspace context:" in effective
    assert "Shared evidence ledger:" in effective
    assert "Plan 3" in effective
    assert "src/app.ts" in effective


def test_v2_codex_observable_task_graph_emits_live_revisions(tmp_path) -> None:
    request = AgentBackendRunRequest(
        task_id="task-1",
        run_id="run-1",
        objective="fix the failing test",
        workspace_root=str(tmp_path),
        profile_policy={"backend": "codex"},
    )
    graph = _CodexObservableTaskGraph(
        request,
        workspace_root=tmp_path,
        objective=request.objective,
    )

    start = graph.start()
    assert start.source_event_type == "live.task_graph.updated"
    assert start.payload["task_graph_state"]["version_id"] == "codex.r1"
    assert "codex-worker" in start.payload["task_graph_state"]["active_task_ids"]

    started = graph.events_for_row(
        {
            "type": "item.started",
            "item": {
                "id": "shell-1",
                "type": "command_execution",
                "command": "npm test",
            },
        }
    )
    assert started[0].payload["task_graph_state"]["version_id"] == "codex.r2"
    assert started[0].payload["task_graph_state"]["changed_branch_ids"] == ["workspace"]
    assert any(
        task["task_id"] == "codex-shell-1" and task["state"] == "active"
        for task in started[0].payload["task_graph_state"]["tasks"]
    )

    completed = graph.events_for_row(
        {
            "type": "item.completed",
            "item": {
                "id": "shell-1",
                "type": "command_execution",
                "command": "npm test",
            },
        }
    )
    state = completed[0].payload["task_graph_state"]
    assert state["version_id"] == "codex.r3"
    assert "codex-shell-1" in state["completed_task_ids"]
    assert any(branch["branch_id"] == "workspace" for branch in state["branches"])

    final = graph.finish("Codex finished.")
    final_state = final.payload["task_graph_state"]
    assert final_state["version_id"] == "codex.r4"
    assert "codex-final" in final_state["completed_task_ids"]
    assert any(branch["branch_id"] == "answer" for branch in final_state["branches"])


def test_v2_codex_observable_task_graph_reuses_unnamed_command_nodes(tmp_path) -> None:
    request = AgentBackendRunRequest(
        task_id="task-1",
        run_id="run-1",
        objective="check the app",
        workspace_root=str(tmp_path),
        profile_policy={"backend": "codex"},
    )
    graph = _CodexObservableTaskGraph(
        request,
        workspace_root=tmp_path,
        objective=request.objective,
    )

    graph.start()
    started = graph.events_for_row(
        {
            "type": "item.started",
            "item": {
                "type": "command_execution",
                "command": "npm test",
            },
        }
    )[0]
    completed = graph.events_for_row(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "npm test",
            },
        }
    )[0]

    started_task_ids = {
        task["task_id"]
        for task in started.payload["task_graph_state"]["tasks"]
        if task["goal"] == "Run `npm test`"
    }
    completed_task_ids = {
        task["task_id"]
        for task in completed.payload["task_graph_state"]["tasks"]
        if task["goal"] == "Run `npm test`"
    }
    assert started_task_ids == {"codex-command-npm-test"}
    assert completed_task_ids == {"codex-command-npm-test"}
    assert "codex-command-npm-test" in completed.payload["task_graph_state"]["completed_task_ids"]


def test_v2_surface_turn_carries_hugo_notes_feature(monkeypatch, tmp_path) -> None:
    project = tmp_path / "my-knowledge-base"
    content = project / "content"
    content.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DAN_DEFAULT_CONTENT_ROOTS", str(project))
    monkeypatch.delenv("DAN_NOTES_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("DAN_NOTES_ROOT", raising=False)

    req = ChatMessageRequest(
        workflow_id="_scratch",
        message="summarize this project",
        mode="auto",
        surface_type="cli",
        surface_id="super-tui",
        surface_context={
            "workspace_root": str(workspace),
            "capabilities": ["streaming"],
        },
    )

    turn = build_surface_turn_from_chat_request(req)
    surface_context = turn.metadata["surface_context"]

    assert surface_context["notes_root"] == str(content.resolve())
    assert surface_context["notes_feature"]["kind"] == "hugo_notes"
    assert "Hugo content tree" in " ".join(surface_context["notes_feature"]["rules"])
    assert "hugo_notes" in turn.capabilities
    assert "streaming" in turn.capabilities


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
    assert snapshot.metadata["created_at"]
    assert snapshot.metadata["updated_at"]
    listed = reloaded.list_thread_tasks("thread-1")[0]
    assert listed.task_id == accepted.task_id
    assert listed.metadata["updated_at"] == snapshot.metadata["updated_at"]


def test_v2_thread_prompt_log_renders_model_prompt_and_response(tmp_path) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    accepted = store.accept_bridge_context(
        build_v2_bridge_context(
            ChatMessageRequest(
                workflow_id="_scratch",
                message="what is this project?",
                mode="agent",
                surface_type="cli",
                surface_id="super-tui",
                thread_id="thread-debug",
            )
        ),
        stream_channel_id="chat-stream-1",
    )
    assert accepted.run_id is not None
    assert accepted.task_id is not None

    store.record_agent_event(
        AgentRunEvent(
            type="model_text_delta",
            run_id=accepted.run_id,
            task_id=accepted.task_id,
            summary="Model request recorded.",
            source_event_type="model.requested",
            payload={
                "event": "model.requested",
                "model_call_id": "model-call:0001",
                "model": "kimi-k2.6",
                "round": 1,
                "tool_ids": ["file_read"],
                "prompt_messages": [
                    {"role": "system", "content": "You are DAN."},
                    {"role": "user", "content": "Explain the project."},
                ],
            },
        )
    )
    store.record_agent_event(
        AgentRunEvent(
            type="token_usage_recorded",
            run_id=accepted.run_id,
            task_id=accepted.task_id,
            summary="Model response recorded.",
            source_event_type="model.responded",
            payload={
                "event": "model.responded",
                "model_call_id": "model-call:0001",
                "model": "kimi-k2.6",
                "finish_reason": "stop",
                "response_text": "This project is a DAN workspace.",
            },
        )
    )

    log = store.thread_prompt_log("thread-debug")

    assert Path(log["path"]).exists()
    assert log["entry_count"] == 1
    assert accepted.run_id in log["run_ids"]
    assert "You are DAN." in log["content"]
    assert "Explain the project." in log["content"]
    assert "`file_read`" in log["content"]
    assert "This project is a DAN workspace." in log["content"]


def test_v2_store_recovers_running_agent_runs_after_restart(tmp_path) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    accepted = store.accept_bridge_context(
        build_v2_bridge_context(
            ChatMessageRequest(
                workflow_id="_scratch",
                message="keep working",
                mode="agent",
                surface_type="frontend",
                surface_id="chunk-workspace",
                thread_id="thread-restart",
            )
        ),
        stream_channel_id="chat-stream-1",
    )
    assert accepted.run_id is not None
    assert accepted.task_id is not None
    assert store.get_run(accepted.run_id).status == "running"

    recovered = ChatV2Store(tmp_path / "chat_v2")
    assert recovered.recover_interrupted_runs_after_restart() == 2

    run = recovered.get_run(accepted.run_id)
    task_snapshot = recovered.get_task_snapshot(accepted.task_id)
    assert run is not None
    assert task_snapshot is not None
    assert run.status == "stopped"
    assert run.metadata["restart_recovery_reason"] == "process_restart"
    assert task_snapshot.status == "stopped"
    assert task_snapshot.metadata["restart_recovery_reason"] == "process_restart"
    assert "backend restarted" in task_snapshot.latest_progress


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


def _async_core_turn(
    text: str,
    *,
    workspace: Path,
    turn_id: str,
    thread_id: str = "thread-async",
    surface_type: str = "web",
    surface_id: str = "v2",
    surface_topic_key: str | None = None,
) -> SurfaceTurn:
    topic_key = (
        surface_topic_key
        if surface_topic_key is not None
        else f"private:{surface_type}:user:{thread_id}"
    )
    return SurfaceTurn(
        id=turn_id,
        text=text,
        workspace_root=str(workspace),
        workspace_id=str(workspace),
        surface_type=surface_type,
        surface_id=surface_id,
        surface=f"{surface_type}:{surface_id}",
        session_id=thread_id,
        thread_id=thread_id,
        metadata={
            "workspace_source": "explicit",
            "surface_topic_key": topic_key,
        },
    )


def test_v2_async_board_snapshot_survives_restart_and_parallel_admission(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    store = ChatV2Store(tmp_path / "chat_v2")

    first = admit_foreground_turn(
        store,
        _async_core_turn("build script_a.py", workspace=workspace, turn_id="turn-a"),
    )
    assert first.decision.action == "start_parallel"
    assert first.decision.run_id is not None
    mark_background_run_started(store, first.decision.run_id, backend="deterministic")

    reloaded = ChatV2Store(tmp_path / "chat_v2")
    snapshot = build_task_board_snapshot(
        reloaded,
        workspace_root=str(workspace),
        thread_id="thread-async",
    )
    assert [run.run_id for run in snapshot.active_runs] == [first.decision.run_id]
    assert snapshot.active_runs[0].owned_paths == ["script_a.py"]
    assert snapshot.active_runs[0].admission_action == "start_parallel"

    second = admit_foreground_turn(
        reloaded,
        _async_core_turn("build script_b.py", workspace=workspace, turn_id="turn-b"),
    )
    assert second.decision.action == "start_parallel"
    assert (
        second.decision.reason
        == "explicit target paths do not overlap active or queued runs"
    )
    mark_background_run_started(reloaded, second.decision.run_id, backend="deterministic")

    updated = build_task_board_snapshot(
        reloaded,
        workspace_root=str(workspace),
        thread_id="thread-async",
    )
    assert {run.run_id for run in updated.active_runs} == {
        first.decision.run_id,
        second.decision.run_id,
    }


def test_v2_async_board_does_not_block_on_stop_requested_run(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    store = ChatV2Store(tmp_path / "chat_v2")

    first = admit_foreground_turn(
        store,
        _async_core_turn("show me the website", workspace=workspace, turn_id="turn-a"),
    )
    assert first.decision.action == "start_parallel"
    mark_background_run_started(store, first.decision.run_id, backend="codex")

    stop_event = store.request_agent_run_stop(
        AgentRunCommand(
            command="stop",
            run_id=first.decision.run_id,
            task_id=first.decision.task_id,
            surface_turn_id="turn-stop",
            payload={"reason": "user_requested_from_workspace_gui"},
        )
    )
    assert stop_event.type == "stop_requested"

    snapshot = build_task_board_snapshot(
        store,
        workspace_root=str(workspace),
        thread_id="thread-async",
    )
    assert snapshot.active_runs == []

    second = admit_foreground_turn(
        store,
        _async_core_turn("what's next", workspace=workspace, turn_id="turn-b"),
    )

    assert second.decision.action == "start_parallel"
    assert second.decision.reason == "no active or queued executor work in this board scope"


def test_v2_async_board_snapshot_is_isolated_by_surface_topic_key(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    store = ChatV2Store(tmp_path / "chat_v2")
    first = admit_foreground_turn(
        store,
        _async_core_turn(
            "build script_a.py",
            workspace=workspace,
            turn_id="turn-a",
            surface_topic_key="private:web:user:thread-async",
        ),
    )
    assert first.decision.action == "start_parallel"
    mark_background_run_started(store, first.decision.run_id, backend="deterministic")

    second = admit_foreground_turn(
        store,
        _async_core_turn(
            "build script_a.py",
            workspace=workspace,
            turn_id="turn-b",
            surface_topic_key="private:telegram:user:thread-async",
        ),
    )

    assert second.decision.action == "start_parallel"
    assert second.decision.task_id != first.decision.task_id
    mark_background_run_started(store, second.decision.run_id, backend="deterministic")

    first_lane = build_task_board_snapshot(
        store,
        workspace_root=str(workspace),
        thread_id="thread-async",
        surface_topic_key="private:web:user:thread-async",
    )
    second_lane = build_task_board_snapshot(
        store,
        workspace_root=str(workspace),
        thread_id="thread-async",
        surface_topic_key="private:telegram:user:thread-async",
    )
    assert {run.run_id for run in first_lane.active_runs} == {first.decision.run_id}
    assert {run.run_id for run in second_lane.active_runs} == {second.decision.run_id}


def test_v2_async_admission_queues_overlapping_paths_with_dependency_reason(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    store = ChatV2Store(tmp_path / "chat_v2")
    first = admit_foreground_turn(
        store,
        _async_core_turn("build script.py", workspace=workspace, turn_id="turn-a"),
    )
    mark_background_run_started(store, first.decision.run_id, backend="deterministic")

    second = admit_foreground_turn(
        store,
        _async_core_turn("also update script.py", workspace=workspace, turn_id="turn-b"),
    )

    assert second.decision.action == "queue_after"
    assert second.decision.relation == "conflicting"
    assert second.decision.depends_on_task_ids == [first.decision.task_id]
    assert "path conflict" in second.decision.reason
    queued_run = store.get_run(second.decision.run_id)
    assert queued_run is not None
    assert queued_run.status == "waiting_dependency"
    assert queued_run.metadata["depends_on_task_ids"] == [first.decision.task_id]
    assert store.load_run_events(second.decision.run_id)[-1]["type"] == "waiting_dependency"

    store.record_agent_event(
        AgentRunEvent(
            type="completed",
            run_id=first.decision.run_id,
            task_id=first.decision.task_id,
            summary="script.py complete",
        )
    )
    promoted = store.promote_waiting_dependency_runs(
        dependency_task_id=first.decision.task_id
    )
    assert [run.run_id for run in promoted] == [second.decision.run_id]
    assert store.get_run(second.decision.run_id).status == "queued"


def test_v2_async_admission_asks_clarification_for_ambiguous_second_task(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    store = ChatV2Store(tmp_path / "chat_v2")
    first = admit_foreground_turn(
        store,
        _async_core_turn("build script_a.py", workspace=workspace, turn_id="turn-a"),
    )
    mark_background_run_started(store, first.decision.run_id, backend="deterministic")

    second = admit_foreground_turn(
        store,
        _async_core_turn("also improve that", workspace=workspace, turn_id="turn-b"),
    )

    assert second.decision.action == "ask_clarification"
    assert second.decision.question
    assert second.decision.run_id is None
    assert len(store.list_run_records(workspace_root=str(workspace))) == 1


def test_v2_async_admission_append_and_status_are_foreground_commands(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    store = ChatV2Store(tmp_path / "chat_v2")
    first = admit_foreground_turn(
        store,
        _async_core_turn("build app.py", workspace=workspace, turn_id="turn-a"),
    )
    mark_background_run_started(store, first.decision.run_id, backend="deterministic")

    before_events = store.load_run_events(first.decision.run_id)
    status = admit_foreground_turn(
        store,
        _async_core_turn("/status", workspace=workspace, turn_id="turn-status"),
    )
    assert status.decision.action == "chat_or_status"
    assert "Active:" in status.decision.status_text
    assert store.load_run_events(first.decision.run_id) == before_events

    appended = admit_foreground_turn(
        store,
        _async_core_turn(
            "/append also update README.md",
            workspace=workspace,
            turn_id="turn-append",
        ),
    )
    assert appended.decision.action == "append_to_active"
    assert appended.decision.queue_item_id
    snapshot = store.get_task_snapshot(first.decision.task_id)
    assert snapshot.metadata["append_queue_length"] == 1
    assert len(store.list_run_records(workspace_root=str(workspace))) == 1


@pytest.mark.asyncio
async def test_v2_async_admit_endpoint_returns_before_background_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)

    response = await chat_v2_router.admit_agent_turn(
        chat_v2_router.AgentRunAdmissionRequest(
            chat_request=ChatMessageRequest(
                workflow_id="_scratch",
                message="build script_a.py",
                mode="agent",
                surface_type="web",
                surface_id="v2",
                thread_id="thread-async",
                surface_context={"workspace_root": str(workspace)},
            ),
            background=True,
            execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
        )
    )

    run_id = response["admission"]["run_id"]
    assert response["status"] == "started"
    assert response["run"]["status"] == "running"
    assert [event["type"] for event in store.load_run_events(run_id)] == [
        "accepted",
        "background_run_started",
    ]

    for _ in range(20):
        if store.get_run(run_id).status == "completed":
            break
        await asyncio.sleep(0.01)
    assert store.get_run(run_id).status == "completed"


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
async def test_v2_continue_after_current_preserves_original_goal_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ChatV2Store(tmp_path / "chat_v2")
    monkeypatch.setattr(chat_v2_router, "get_chat_v2_store", lambda request=None: store)
    created = await chat_v2_router.create_agent_run(
        req=ChatMessageRequest(
            workflow_id="_scratch",
            message="what is this project?",
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
            surface_turn_id="turn-followup-goal",
            idempotency_key="followup-goal",
            payload={"text": "yes please proceed"},
        ),
    )
    executed = await chat_v2_router.execute_agent_run(
        run_id,
        execute=chat_v2_router.AgentRunExecuteRequest(backend="deterministic"),
    )

    next_run_id = executed["task"]["metadata"]["active_run_id"]
    next_run = store.get_run(next_run_id)
    assert next_run is not None
    assert next_run.command.payload["text"] == "yes please proceed"
    assert next_run.command.payload["original_request"] == "what is this project?"
    assert next_run.command.payload["satisfaction_gap"] == "yes please proceed"
    assert next_run.command.payload["goal_context"]["previous_run_id"] == run_id
    assert next_run.metadata["original_request"] == "what is this project?"
    assert next_run.metadata["satisfaction_gap"] == "yes please proceed"

    backend_request = build_agent_backend_request(next_run, store.get_task(next_run.task_id))
    packet = backend_request.metadata["normalized_request"]
    assert packet["original_request"] == "what is this project?"
    assert packet["current_operator_update"] == "yes please proceed"
    assert packet["continued_from_run_id"] == run_id
    assert packet["previous_run_status"] == "completed"
    assert "Completed deterministic Agent run" in packet["previous_final_response"]
    evidence_items = packet["context_composer"]["shared_evidence"]["items"]
    assert any(
        item["kind"] == "final_response"
        and "Completed deterministic Agent run" in item["summary"]
        for item in evidence_items
    )
    effective = _objective_with_surface_context(backend_request)
    assert "Original operator request: what is this project?" in effective
    assert "Current follow-up / satisfaction gap: yes please proceed" in effective
    assert "Previous final response:" in effective
    assert "Shared evidence ledger:" in effective
    assert "Continue toward the same user-visible goal" in effective
    assert not effective.startswith("Operator request: yes please proceed")


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
async def test_v2_background_execution_soft_budget_extends_promoted_continue_cap(
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
            idempotency_key="followup-background-soft-budget",
            payload={"text": "then add tests"},
        ),
    )

    await chat_v2_router._execute_agent_run_background(
        store,
        run_id,
        backend_name="deterministic",
        overrides={},
        auto_execute_continuations=True,
        remaining_continuations=0,
    )

    first_run = store.get_run(run_id)
    assert first_run.status == "completed"
    next_run_id = first_run.metadata["continued_run_id"]
    next_run = store.get_run(next_run_id)
    assert next_run is not None
    assert next_run.status == "completed"
    assert next_run.metadata["promoted_from_run_id"] == run_id
    assert next_run.metadata["scheduler_budget_extensions"][0]["cap_name"] == (
        "promoted_continuations"
    )
    first_event_sources = [
        event.get("source_event_type")
        for event in store.load_run_events(run_id)
    ]
    assert "chat_v2.scheduler.soft_budget.approved" in first_event_sources


def test_v2_async_admission_parallel_cap_stays_hard(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    store = ChatV2Store(tmp_path / "chat_v2")
    first = admit_foreground_turn(
        store,
        _async_core_turn("build script_a.py", workspace=workspace, turn_id="turn-a"),
        max_parallel_runs=1,
    )
    assert first.decision.action == "start_parallel"
    mark_background_run_started(store, first.decision.run_id, backend="deterministic")

    second = admit_foreground_turn(
        store,
        _async_core_turn("build script_b.py", workspace=workspace, turn_id="turn-b"),
        max_parallel_runs=1,
    )

    assert second.decision.action == "queue_after"
    assert second.decision.capacity_blocked is True
    assert "capacity" in second.decision.reason.lower()


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
    assert run.metadata["original_request"] == "build the landing page"
    assert run.metadata["satisfaction_gap"] == "operator wants another attempt"
    assert run.metadata["goal_context"]["original_request"] == "build the landing page"
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
