"""Restart recovery must consume durable follow-ups, not just preserve them."""
import asyncio
import json

from diane.server.chat_request import ChatMessageRequest
from diane.server.chat_v2 import AgentRunCommand, build_v2_bridge_context
from diane.server.chat_v2_backend import AgentBackendRunResult, run_agent_backend
from diane.server.chat_v2_store import ChatV2Store
from diane.server.routers import chat_v2 as router


def queued_store(tmp_path):
    store = ChatV2Store(tmp_path / "chat_v2")
    accepted = store.accept_bridge_context(build_v2_bridge_context(ChatMessageRequest(
        workflow_id="work", thread_id="thread", message="Original request",
        mode="agent", surface_type="frontend", surface_id="chunk-workspace",
    )), stream_channel_id="test")
    for index, text in enumerate(("First saved message", "Second saved message")):
        store.queue_agent_command(AgentRunCommand(command="continue_after_current",
            run_id=accepted.run_id, task_id=accepted.task_id,
            surface_turn_id=f"followup-{index}", payload={"text": text}))
    config = router.AgentRunExecuteRequest(backend="native_codex", background=True,
        profile_policy={"lead_profile": {"account": "app", "model": "test-model"}, "permission_mode": "auto"})
    store.update_run_metadata(accepted.run_id, {"restart_execution": config.model_dump()})
    return store, accepted, config


def executor(monkeypatch, seen):
    class Adapter:
        backend_name = "codex"

        async def run(self, request, emit_event, runtime=None):
            seen.append((request.objective, request.profile_policy))
            return AgentBackendRunResult(status="completed", backend="codex", summary="Handled")

    async def execute(store, run_id, **kwargs):
        return await run_agent_backend(store, run_id, adapter=Adapter(), **kwargs)
    monkeypatch.setattr(router, "run_agent_backend", execute)


def test_restart_drains_in_order_with_account_and_no_repeat(tmp_path, monkeypatch):
    store, accepted, _ = queued_store(tmp_path)
    # Match the report: first message had started, second was still waiting.
    store.update_run_metadata(accepted.run_id, {}, status="completed")
    first = store.promote_next_continue_after_current(accepted.run_id)
    store.update_run_metadata(first.run_id, {
        "restart_execution": store.get_run(accepted.run_id).metadata["restart_execution"],
    }, status="running")
    recovered = ChatV2Store(store.base_dir)
    recovered.recover_interrupted_runs_after_restart()
    seen = []
    executor(monkeypatch, seen)
    asyncio.run(router.resume_recovered_queues(recovered))
    assert [text for text, _ in seen] == ["First saved message", "Second saved message"]
    assert all(policy["lead_profile"]["account"] == "app" for _, policy in seen)
    assert all(policy["permission_mode"] == "auto" for _, policy in seen)
    assert [item.status for item in recovered.get_task(accepted.task_id).queue_items] == ["completed", "completed"]
    recovered.recover_interrupted_runs_after_restart()
    asyncio.run(router.resume_recovered_queues(recovered))
    assert len(seen) == 2


def test_restart_between_promotion_and_worker_start(tmp_path, monkeypatch):
    store, accepted, config = queued_store(tmp_path)
    store.update_run_metadata(accepted.run_id, {}, status="completed")
    promoted = store.promote_next_continue_after_current(accepted.run_id)
    # Simulate a crash before execution configuration is copied to the child.
    assert promoted.status == "queued"
    store.recover_interrupted_runs_after_restart()
    seen = []
    executor(monkeypatch, seen)
    asyncio.run(router.resume_recovered_queues(store))
    assert len(seen) == 2


def test_explicit_stop_and_disabled_continuation_do_not_restart(tmp_path, monkeypatch):
    store, accepted, config = queued_store(tmp_path)
    seen = []
    executor(monkeypatch, seen)
    store.update_run_metadata(accepted.run_id, {}, status="stopped")
    store.recover_interrupted_runs_after_restart()
    asyncio.run(router.resume_recovered_queues(store))
    assert not seen
    config.auto_execute_continuations = False
    store.update_run_metadata(accepted.run_id, {"restart_execution": config.model_dump()}, status="running")
    store.recover_interrupted_runs_after_restart()
    asyncio.run(router.resume_recovered_queues(store))
    assert not seen


def test_legacy_native_account_recovered_from_lead_record(tmp_path):
    store, accepted, _ = queued_store(tmp_path)
    store.update_run_metadata(accepted.run_id, {"restart_execution": None, "requested_backend": "native_codex"})
    run = store.get_run(accepted.run_id)
    assert router._restart_execution(store, run) is None
    directory = tmp_path / "native_leads"
    directory.mkdir()
    (directory / "lead.json").write_text(json.dumps({"parent_run_id": run.run_id,
        "backend": "codex", "profile": {"account": "app", "model": "test-model", "permission": "plan", "resume_session": "old"}}))
    execute = router._restart_execution(store, run)
    assert execute.backend == "native_codex"
    assert execute.profile_policy["lead_profile"]["account"] == "app"
    assert execute.profile_policy["permission_mode"] == "plan"
    assert "resume_session" not in execute.profile_policy["lead_profile"]


def test_restart_claim_is_idempotent(tmp_path):
    store, accepted, _ = queued_store(tmp_path)
    store.recover_interrupted_runs_after_restart()
    assert store.promote_restarted_queue(accepted.run_id) is not None
    assert store.promote_restarted_queue(accepted.run_id) is None
    assert [item.status for item in store.get_task(accepted.task_id).queue_items] == ["injected", "queued"]


def test_app_lifespan_runs_recovery(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from diane.server.app import create_app

    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path))
    store, accepted, _ = queued_store(tmp_path)
    seen = []
    executor(monkeypatch, seen)
    with TestClient(create_app()) as client:
        import time
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            client.get("/api/v2/tasks/" + accepted.task_id)
            if len(seen) == 2:
                break
            time.sleep(.01)
        assert len(seen) == 2
    assert all(item.status == "completed" for item in store.get_task(accepted.task_id).queue_items)


def test_followup_after_stop_has_fresh_task_control_state(tmp_path):
    store, accepted, _ = queued_store(tmp_path)
    store.request_agent_run_stop(AgentRunCommand(
        command="stop", task_id=accepted.task_id, run_id=accepted.run_id,
        payload={"reason": "user_requested_from_workspace_gui"},
    ))
    store.confirm_agent_run_stopped(accepted.run_id, checkpoint="test")
    continued = store.promote_next_continue_after_current(accepted.run_id)
    assert continued is not None
    store.update_run_metadata(continued.run_id, {}, status="running")
    snapshot = store.get_task_snapshot(accepted.task_id)
    assert snapshot.status == "running"
    assert snapshot.metadata["active_run_id"] == continued.run_id
    assert not any(key.startswith("stop_") or key.startswith("pause_") for key in snapshot.metadata)
    assert store.get_run(accepted.run_id).metadata["stop_requested"] is True


def test_fifo_when_new_messages_arrive_after_partial_drain_and_restart(tmp_path):
    store, accepted, _ = queued_store(tmp_path)
    def enqueue(run_id, text, lane="continue_after_current"):
        store.queue_agent_command(AgentRunCommand(command=lane, run_id=run_id,
            task_id=accepted.task_id, idempotency_key=text, payload={"text":text}))
    enqueue(accepted.run_id, "Third")
    store.update_run_metadata(accepted.run_id, {}, status="completed")
    first = store.promote_next_continue_after_current(accepted.run_id)
    assert first.command.payload["text"] == "First saved message"
    store.update_run_metadata(first.run_id, {}, status="completed")
    second = store.promote_next_continue_after_current(first.run_id)
    assert second.command.payload["text"] == "Second saved message"
    # Third has old rank 3; Fourth gets rank 2. Sorting rank would overtake Third.
    enqueue(second.run_id, "Fourth")
    enqueue(second.run_id, "Fifth", "append_followup")
    store = ChatV2Store(store.base_dir)
    previous = second
    for expected in ["Third", "Fourth", "Fifth"]:
        store.update_run_metadata(previous.run_id, {}, status="completed")
        previous = store.promote_next_continue_after_current(previous.run_id)
        assert previous.command.payload["text"] == expected


def test_cancel_removes_only_waiting_items_and_renumbers(tmp_path):
    store, accepted, _ = queued_store(tmp_path)
    task = store.get_task(accepted.task_id)
    first, second = [item.id for item in task.queue_items]
    store.cancel_queued_item(accepted.run_id, first)
    task = store.get_task(accepted.task_id)
    assert [(item.status, item.position) for item in task.queue_items] == [("cancelled", 1), ("queued", 1)]
    import pytest
    with pytest.raises(ValueError):
        store.cancel_queued_item(accepted.run_id, first)  # already withdrawn
    store.update_run_metadata(accepted.run_id, {}, status="completed")
    promoted = store.promote_next_continue_after_current(accepted.run_id)
    assert promoted is not None and store.get_task(accepted.task_id).queue_items[1].status == "injected"
    with pytest.raises(ValueError):
        store.cancel_queued_item(accepted.run_id, second)  # delivered entries stay
