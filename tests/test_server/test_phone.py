"""Phone admission/reconnect contracts; no providers, sockets or live machines."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from diane.server.chat_request import ChatMessageRequest
from diane.server.chat_store import ChatMessage, ChatStore, ChatThread, TaskRunRef
from diane.server.chat_v2 import build_v2_bridge_context
from diane.server.chat_v2_store import ChatV2Store
from diane.server.routers import chat_v2, phone, sessions


@pytest.fixture
def app(tmp_path):
    application = FastAPI()
    application.state.chat_store = ChatStore(str(tmp_path))
    application.state.chat_v2_store = ChatV2Store(tmp_path / "chat_v2")
    application.include_router(sessions.router)
    application.include_router(chat_v2.router)
    application.include_router(phone.router)
    return application


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def saved_run(app, *, workflow="work", thread_id="thread", workspace_id=None, status="completed"):
    chats, store = app.state.chat_store, app.state.chat_v2_store
    workspace = chats.base_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    if chats.get_thread(workflow, thread_id) is None:
        chats.save_thread(ChatThread(id=thread_id, workflow_id=workflow))
    accepted = store.accept_bridge_context(build_v2_bridge_context(ChatMessageRequest(
        workflow_id=workflow, thread_id=thread_id, message="Implement the original request", mode="agent",
        surface_type="frontend", surface_id="chunk-workspace",
        surface_context={"workspace_root": str(workspace), "workspace_id": workspace_id or workflow},
    )), stream_channel_id="test")
    execution = chat_v2.AgentRunExecuteRequest(backend="native_codex", background=True,
        profile_policy={"permission_mode": "plan", "lead_profile": {
            "model": "saved-model", "account": "app", "home": "/private/account-home", "api_key": "private-key"},
            "native_workers": {"reviewer": {"backend": "claude", "model": "worker-model"}}},
        mutation_policy={"mode": "readonly"}, approval_policy={"require": True},
        tool_policy={"deny": ["write"]})
    store.update_run_metadata(accepted.run_id, {"restart_execution": execution.model_dump(mode="json")}, status=status)
    return accepted, execution


@pytest.mark.asyncio
async def test_additive_messages_keep_metadata_concurrent_rows_and_legacy_edits(app):
    store = app.state.chat_store
    thread = ChatThread(id="thread", workflow_id="work", messages=[ChatMessage(id="original", role="assistant",
        content="Saved", attachments=[{"path": "saved.pdf"}], token_usage={"total_tokens": 9},
        task_run_ref=TaskRunRef(run_id="run", workspace_root="/project"), run_events=[{"type": "completed"}])])
    store.save_thread(thread)
    async with client(app) as http:
        stale = (await http.get("/api/chats/work/thread")).json()
        store.append_message("work", "thread", ChatMessage(id="desktop", role="user", content="Concurrent desktop"))
        response = await http.post("/api/chats/work/thread/messages", json={"messages": [
            {"id": "original", "role": "assistant", "content": "Saved"},
            {"id": "phone", "role": "user", "content": "From phone", "attachments": [{"path": "phone.png"}]}]})
        assert response.status_code == 200
        messages = response.json()["messages"]
        assert [message["id"] for message in messages] == ["original", "desktop", "phone"]
        assert messages[0]["attachments"] == [{"path": "saved.pdf"}]
        assert messages[0]["task_run_ref"]["workspace_root"] == "/project"
        assert messages[0]["token_usage"] == {"total_tokens": 9}
        # A pre-phone desktop snapshot still retains the additive phone row.
        assert (await http.put("/api/chats/work/thread", json={"messages": stale["messages"]})).status_code == 200
        legacy = {"messages": [{"id": "original", "role": "assistant", "content": "Edited in desktop"},
                               {"id": "phone", "role": "user", "content": "From phone"}]}
        assert (await http.put("/api/chats/work/thread", json=legacy)).status_code == 200
        current = (await http.get("/api/chats/work/thread")).json()
        assert current["messages"][1]["source_surface"] == "phone"
        assert current["messages"][1]["attachments"] == [{"path": "phone.png"}]
        assert current["messages"][0]["run_events"] == [{"type": "completed"}]
        assert (await http.put("/api/chats/work/thread", json={"messages": [], "revision": stale["revision"]})).status_code == 409
        assert (await http.put("/api/chats/work/thread", json={"messages": [], "revision": current["revision"]})).status_code == 200
        assert not store.get_thread("work", "thread").messages
        assert (await http.put("/api/chats/work/thread", json={"messages": ["invalid"]})).status_code == 409


def test_phone_merge_and_legacy_journal_append_are_serialized(app):
    store = app.state.chat_store
    store.save_thread(ChatThread(id="thread", workflow_id="work"))
    def append(index):
        if index % 2:
            store.append_message("work", "thread", ChatMessage(id=str(index), role="user", content=str(index)))
        else:
            store.merge_messages("work", "thread", [{"id": str(index), "role": "user", "content": str(index)}])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(append, range(40)))
    restored = ChatStore(str(store.base_dir)).get_thread("work", "thread")
    assert {message.id for message in restored.messages} == {str(index) for index in range(40)}


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_field", ["idempotency_key", "surface_turn_id"])
async def test_command_replay_through_http_is_idempotent_for_entire_lifecycle(app, identity_field):
    accepted, _ = saved_run(app, status="running")
    store = app.state.chat_v2_store
    path = f"/api/v2/agent-runs/{accepted.run_id}/commands"
    body = {"command": "append_followup", identity_field: "stable", "payload": {"text": "One steering message"}}
    async with client(app) as http:
        original = (await http.post(path, json=body)).json()
        item_id = original["event"]["payload"]["queue_item_id"]
        assert (await http.post(path, json=body)).json()["event"]["payload"]["replayed"]
        store.claim_queued_run_items(accepted.run_id)
        assert (await http.post(path, json=body)).json()["event"]["payload"]["queue_status"] == "injected"
        assert not store.claim_queued_run_items(accepted.run_id)
        store.acknowledge_injected_run_items(accepted.run_id, [item_id])
        app.state.chat_v2_store = ChatV2Store(store.base_dir)
        assert (await http.post(path, json=body)).json()["event"]["payload"]["queue_status"] == "completed"
        assert len(store.get_task(accepted.task_id).queue_items) == 1
        conflict = await http.post(path, json={**body, "payload": {"text": "Different message"}})
        assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_phone_followup_receipt_survives_delivery_and_restarts(app):
    accepted, _ = saved_run(app, status="running")
    store = app.state.chat_v2_store
    body = {"request_id": "followup", "workflow_id": "work", "thread_id": "thread",
            "run_id": accepted.run_id, "action": "steer", "text": "Use the saved notes"}
    async with client(app) as http:
        first = (await http.post("/api/phone/requests", json=body)).json()
        store.claim_queued_run_items(accepted.run_id)
        injected = (await http.post("/api/phone/requests", json=body)).json()
        assert injected["queue_status"] == "injected"
        store.acknowledge_injected_run_items(accepted.run_id, [first["queue_item_id"]])
        store.update_run_metadata(accepted.run_id, {}, status="completed")
        app.state.chat_v2_store = ChatV2Store(store.base_dir)
        app.state.phone_instance = "restarted-host"
        replay = (await http.post("/api/phone/requests", json=body)).json()
        assert replay["queue_status"] == replay["status"] == "completed"
        assert replay["queue_item_id"] == first["queue_item_id"]
        assert len(store.get_task(accepted.task_id).queue_items) == 1
        assert len(app.state.chat_store.get_thread("work", "thread").messages) == 1


@pytest.mark.asyncio
async def test_final_reply_waits_for_durable_result_and_retried_run_retains_both_answers(app):
    accepted, _ = saved_run(app)
    store = app.state.chat_v2_store
    run = store.get_run(accepted.run_id)
    run.latest_event_type, run.latest_summary = "completed", "Short progress preview"
    store._save_run(run)
    async with client(app) as http:
        initial = (await http.get("/api/phone/sessions/work/thread/state")).json()
        assert not initial["thread"]["messages"]
        for answer in ("The full first answer.", "The revised answer after retry."):
            store.update_run_metadata(run.run_id, {"backend_result": {"status": "completed", "summary": answer}})
            assert (await http.get("/api/phone/sessions/work/thread/state")).status_code == 200
        final = (await http.get("/api/phone/sessions/work/thread/state")).json()
        assert [message["content"] for message in final["thread"]["messages"]] == ["The full first answer.", "The revised answer after retry."]


@pytest.mark.asyncio
async def test_phone_send_inherits_profile_and_lost_response_does_not_execute_twice(app, monkeypatch):
    _, execution = saved_run(app)
    seen = []
    async def execute(run_id, execute, request):
        seen.append((run_id, execute.model_dump(mode="json")))
        request.app.state.chat_v2_store.update_run_metadata(run_id, {"restart_execution": execute.model_dump(mode="json")}, status="running")
        return {"status": "started"}
    monkeypatch.setattr(phone, "execute_agent_run", execute)
    body = {"request_id": "phone-send", "workflow_id": "work", "thread_id": "thread", "message_id": "user-1",
            "text": "Implement the new UI and verify the changed behavior", "attachments": [{"path": "/project/mock.png", "kind": "image"}]}
    async with client(app) as http:
        first = await http.post("/api/phone/requests", json=body)
        assert first.status_code == 200, first.text
        receipt = first.json()
        assert receipt["status"] == "running" and receipt["delivery_status"] == "accepted"
        assert len(seen) == 1 and seen[0][1] == execution.model_dump(mode="json")
        assert app.state.chat_v2_store.get_run(receipt["run_id"]).command.payload["attachments"][0]["local_path"] == "/project/mock.png"
        assert "private-key" not in first.text and "account-home" not in first.text
        assert receipt["execution_context"]["account_label"] == "app"
        # Simulate losing the POST response, then changing desktop defaults.
        app.state.chat_v2_store.update_run_metadata(receipt["run_id"], {"restart_execution": {"backend": "claude"}})
        assert (await http.get("/api/phone/requests/phone-send")).json()["run_id"] == receipt["run_id"]
        replay = await http.post("/api/phone/requests", json=body)
        assert replay.json()["replayed"] and len(seen) == 1
        assert replay.json()["execution_context"]["backend"] == "native_codex"
        assert (await http.post("/api/phone/requests", json={**body, "text": "Different"})).status_code == 409
        app.state.chat_v2_store.update_run_metadata(receipt["run_id"], {"backend_result": {
            "status": "completed", "summary": "Done", "raw_result": {"final_text": "The persisted final answer."}}}, status="completed")
        state = (await http.get("/api/phone/sessions/work/thread/state")).json()
        assert state["thread"]["messages"][-1]["content"] == "The persisted final answer."
        await http.get("/api/phone/sessions/work/thread/state")
        restored = ChatStore(str(app.state.chat_store.base_dir)).get_thread("work", "thread")
        assert sum(message.content == "The persisted final answer." for message in restored.messages) == 1


@pytest.mark.asyncio
async def test_offline_send_queues_active_legacy_scratch_session_and_frozen_stop(app):
    root = str(app.state.chat_store.base_dir / "workspace")
    accepted, _ = saved_run(app, workflow="_scratch", workspace_id=root, status="running")
    store = app.state.chat_v2_store
    task = store.get_task(accepted.task_id)
    task.metadata.pop("workflow_id")  # An actual legacy phone record.
    store._save_task(task)
    body = {"request_id": "queued-phone", "workflow_id": "_scratch", "thread_id": "thread", "text": "Continue from my phone"}
    async with client(app) as http:
        response = await http.post("/api/phone/requests", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "queued"
        assert response.json()["run_id"] == accepted.run_id
        assert len(store.list_run_records(thread_id="thread")) == 1
        stop = {"request_id": "stop-1", "action": "stop", "workflow_id": "_scratch", "thread_id": "thread", "run_id": accepted.run_id}
        assert (await http.post("/api/phone/requests", json=stop)).json()["stopping"]
        store.update_run_metadata(accepted.run_id, {}, status="stopped")
        # A replay targets the saved run, even after a later run exists.
        later, _ = saved_run(app, workflow="_scratch", thread_id="thread", workspace_id=root, status="running")
        replay = await http.post("/api/phone/requests", json=stop)
        assert replay.json()["target_run_id"] == accepted.run_id
        assert not store.get_run(later.run_id).metadata.get("stop_requested")
        assert (await http.post("/api/phone/requests", json={**stop, "request_id": "stop-stale"})).json()["detail"]["code"] == "run_not_active"


@pytest.mark.asyncio
async def test_new_session_uses_exact_published_folder_and_replay_identity(app, monkeypatch):
    root = app.state.chat_store.base_dir / "workspace"
    root.mkdir()
    directory = app.state.chat_store.base_dir / "sticky_todo"
    directory.mkdir()
    (directory / "defaults.json").write_text(json.dumps({"projects": [{"id": "project", "name": "Project", "root": str(root)}],
        "active_project_id": "project", "execute": {"backend": "claude", "profile_policy": {"permission_mode": "plan", "lead_profile": {"account": "personal"}}}}))
    seen = []
    async def execute(run_id, execute, request):
        seen.append(run_id)
        request.app.state.chat_v2_store.update_run_metadata(run_id, {"restart_execution": execute.model_dump()}, status="running")
    monkeypatch.setattr(phone, "execute_agent_run", execute)
    body = {"request_id": "new-conversation", "workflow_id": "_scratch", "thread_id": "", "workspace_root": str(root), "text": "Implement the requested change"}
    async with client(app) as http:
        first = await http.post("/api/phone/requests", json=body)
        assert first.status_code == 200, first.text
        replay = await http.post("/api/phone/requests", json=body)
        assert first.json()["thread_id"] == replay.json()["thread_id"]
        assert len(seen) == 1
        assert first.json()["execution_context"]["backend"] == "claude"
        rejected = await http.post("/api/phone/requests", json={**body, "request_id": "wrong-folder", "workspace_root": str(root / "other")})
        assert rejected.json()["detail"]["code"] == "execution_context_unavailable"
        assert (await http.get("/api/phone/requests/wrong-folder")).status_code == 404


@pytest.mark.asyncio
async def test_restart_between_admission_and_execute_is_reviewable_not_reexecuted(app, monkeypatch):
    saved_run(app)
    async def interrupted(*args, **kwargs):
        raise RuntimeError("Host disappeared at execution handoff")
    monkeypatch.setattr(phone, "execute_agent_run", interrupted)
    body = {"request_id": "uncertain", "workflow_id": "work", "thread_id": "thread", "text": "Implement the new request"}
    async with client(app) as http:
        with pytest.raises(RuntimeError, match="Host disappeared"):
            await http.post("/api/phone/requests", json=body)
        record_path = next(path for path in (app.state.chat_store.base_dir / "phone_requests").glob("*.json"))
        record = json.loads(record_path.read_text())
        record["phase"] = "admitting"
        record["run_id"] = ""
        record["task_id"] = ""
        record_path.write_text(json.dumps(record))
        app.state.phone_instance = "new-process"
        receipt = (await http.get("/api/phone/requests/uncertain")).json()
        assert receipt["run_id"] and receipt["status"] == "needs_review"
        assert receipt["delivery_status"] == "needs_review"
        retry = await http.post("/api/phone/requests", json=body)
        assert retry.status_code == 409 and retry.json()["detail"]["code"] == "receipt_needs_review"
        assert len(app.state.chat_v2_store.list_run_records(thread_id="thread")) == 2


@pytest.mark.asyncio
async def test_native_decision_receipt_replay_and_expiry_keep_scope(app, monkeypatch):
    from diane.native_workers.service import active_teams
    accepted, _ = saved_run(app, status="running")
    class NativeClient:
        def __init__(self):
            self.requests = {"approval-1": {"id": "approval-1", "method": "item/tool/requestUserInput", "params": {"questions": [{"id": "q", "question": "Choose"}]}}}
            self.calls = []
        async def reply(self, request_id, decision, answers):
            if request_id not in self.requests:
                raise ValueError("This request is no longer waiting")
            self.calls.append((request_id, decision, answers))
            del self.requests[request_id]
    native = NativeClient()
    team = SimpleNamespace(lead_team=SimpleNamespace(steering={"lead": native}), steering={})
    monkeypatch.setitem(active_teams, accepted.run_id, team)
    body = {"request_id": "answer-1", "action": "answer", "workflow_id": "work", "thread_id": "thread",
            "run_id": accepted.run_id, "approval_request_id": "approval-1", "answers": {"q": "Chosen"}}
    async with client(app) as http:
        state = (await http.get("/api/phone/sessions/work/thread/state")).json()
        assert state["pending_approvals"][0]["run_id"] == accepted.run_id
        assert state["pending_approvals"][0]["worker_id"] is None
        assert (await http.post("/api/phone/requests", json=body)).json()["status"] == "completed"
        assert (await http.post("/api/phone/requests", json=body)).json()["replayed"]
        assert len(native.calls) == 1
        expired = await http.post("/api/phone/requests", json={**body, "request_id": "expired"})
        assert expired.status_code == 409 and expired.json()["detail"]["code"] == "approval_expired"
        wrong = await http.post("/api/phone/requests", json={**body, "request_id": "wrong-thread", "thread_id": "another"})
        assert wrong.status_code == 404


@pytest.mark.asyncio
async def test_plan_mode_does_not_inherit_earlier_work_permissions(app):
    from diane.server.plan_mode import PlanStore, PlanUpdate
    accepted, execution = saved_run(app, status="running")
    execution.profile_policy["permission_mode"] = "full"
    app.state.chat_v2_store.update_run_metadata(accepted.run_id, {"restart_execution": execution.model_dump()})
    PlanStore(app.state.chat_store).update("work", "thread", PlanUpdate(version=0, action="mode", mode="plan"))
    async with client(app) as http:
        context = (await http.get("/api/phone/sessions/work/thread/execution-context")).json()
        assert not context["ready"] and context["conversation_mode"] == "plan"
        for action in ("send", "steer", "queue"):
            result = await http.post("/api/phone/requests", json={"request_id": "plan-" + action,
                "workflow_id": "work", "thread_id": "thread", "action": action, "run_id": accepted.run_id,
                "text": "Implement this now"})
            assert result.status_code == 409
            assert result.json()["detail"]["code"] == "plan_mode_requires_desktop"
        assert not app.state.chat_v2_store.get_task(accepted.task_id).queue_items


@pytest.mark.asyncio
async def test_ambiguous_native_decision_is_not_replayed(app, monkeypatch):
    from diane.native_workers.service import active_teams
    accepted, _ = saved_run(app, status="running")
    calls = []
    class NativeClient:
        requests = {"approve": {"method": "item/commandExecution/requestApproval", "params": {}}}
        async def reply(self, request_id, decision, answers):
            calls.append(request_id)
            self.requests.pop(request_id)
            raise OSError("Lost native response after write")
    monkeypatch.setitem(active_teams, accepted.run_id,
        SimpleNamespace(lead_team=SimpleNamespace(steering={"lead": NativeClient()}), steering={}))
    body = {"request_id": "uncertain-answer", "workflow_id": "work", "thread_id": "thread", "action": "answer",
            "run_id": accepted.run_id, "approval_request_id": "approve", "decision": "accept"}
    async with client(app) as http:
        with pytest.raises(OSError, match="Lost native response"):
            await http.post("/api/phone/requests", json=body)
        receipt = (await http.get("/api/phone/requests/uncertain-answer")).json()
        assert receipt["delivery_status"] == "needs_review"
        assert (await http.post("/api/phone/requests", json=body)).status_code == 409
        assert calls == ["approve"]


@pytest.mark.asyncio
async def test_worker_plan_permission_is_enforced_and_rejection_receipt_is_explicit(app, monkeypatch):
    from diane.native_workers.service import active_teams
    accepted, _ = saved_run(app, status="running")
    native = SimpleNamespace(requests={"approve": {"method": "item/commandExecution/requestApproval", "params": {}}})
    monkeypatch.setitem(active_teams, accepted.run_id, SimpleNamespace(lead_team=None,
        steering={"worker": native}, records={"worker": {"profile": {"permission": "plan"}}}))
    body = {"request_id": "worker-answer", "workflow_id": "work", "thread_id": "thread", "action": "answer",
            "run_id": accepted.run_id, "worker_id": "worker", "approval_request_id": "approve", "decision": "accept"}
    async with client(app) as http:
        rejected = await http.post("/api/phone/requests", json=body)
        assert rejected.status_code == 409 and "Plan mode" in rejected.text
        receipt = (await http.get("/api/phone/requests/worker-answer")).json()
        assert receipt["phase"] == receipt["delivery_status"] == "rejected"
        assert receipt["error_code"] == "invalid_answer"
        assert "approve" in native.requests
        replay = await http.post("/api/phone/requests", json=body)
        assert replay.status_code == 409 and replay.json()["detail"]["receipt"]["replayed"]
