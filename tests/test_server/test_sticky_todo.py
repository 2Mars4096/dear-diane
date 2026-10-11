import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from diane.server.chat_store import ChatStore
from diane.server.chat_v2 import AgentRunEvent
from diane.server.chat_v2_store import ChatV2Store
from diane.server.routers import sticky_todo as module


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    app = FastAPI()
    app.state.chat_store = ChatStore(tmp_path / 'graphs')
    app.state.chat_v2_store = ChatV2Store(tmp_path / 'v2')
    app.include_router(module.router)
    executions = []
    async def execute(run_id, execute, request):
        executions.append(execute.model_dump())
        app.state.chat_v2_store.record_agent_event(AgentRunEvent(type='worker_started', run_id=run_id, summary='Working'))
    monkeypatch.setattr(module, 'execute_agent_run', execute)
    client = TestClient(app)
    defaults = {'projects': [{'id': 'project-a', 'name': 'A', 'root': str(tmp_path)}], 'active_project_id': 'project-a',
                'execute': {'backend': 'native_codex', 'profile_policy': {'lead_profile': {'account': 'configured'}}}}
    assert client.put('/api/sticky-todo/defaults', json=defaults).status_code == 200
    return client, app, executions, defaults


def body():
    return {'request_id': str(uuid4()), 'task_key': 'day-task', 'text': 'Inspect the project', 'steps': ['Report findings'], 'project_id': 'project-a'}


def test_round_trip_retry_restart_result_and_context(handoff):
    client, app, executions, defaults = handoff
    payload = body()
    first = client.post('/api/sticky-todo/requests', json=payload)
    assert first.status_code == 200, first.text
    record = first.json()
    assert record['status'] == 'running'
    assert client.post('/api/sticky-todo/requests', json=payload).json()['run_id'] == record['run_id']
    assert len(executions) == 1
    assert executions[0]['profile_policy']['lead_profile']['account'] == 'configured'
    run = app.state.chat_v2_store.get_run(record['run_id'])
    assert run.workspace_root == defaults['projects'][0]['root']
    assert run.workspace_id == 'project-a'
    app.state.chat_v2_store.record_agent_event(AgentRunEvent(type='completed', run_id=run.run_id, summary='Verified result'))
    app.state.chat_v2_store.update_run_metadata(run.run_id, {'backend_result': {'raw_result': {'final_text': 'Evidence: checks passed'}}})
    # Recreate stores as on restart; the receipt remains authoritative.
    app.state.chat_store = ChatStore(app.state.chat_store.base_dir)
    app.state.chat_v2_store = ChatV2Store(app.state.chat_v2_store.base_dir)
    result = client.get('/api/sticky-todo/requests/' + payload['request_id']).json()
    assert result['status'] == 'ready'
    assert result['summary'] == 'Evidence: checks passed'
    thread = app.state.chat_store.get_thread('project-a', result['session_id'])
    assert 'Report findings' in thread.messages[0].content
    assert thread.messages[-1].content == result['summary']
    assert client.post('/api/sticky-todo/requests', json=payload).json()['session_id'] == record['session_id']
    assert len(executions) == 1
    assert client.post('/api/sticky-todo/requests', json={**payload, 'text': 'Different'}).status_code == 409


def test_stop_is_acknowledged_not_assumed(handoff):
    client, app, _, _ = handoff
    payload = body()
    record = client.post('/api/sticky-todo/requests', json=payload).json()
    stopping = client.post(f"/api/sticky-todo/requests/{payload['request_id']}/stop").json()
    assert stopping['stopping'] and stopping['status'] == 'running'
    app.state.chat_v2_store.record_agent_event(AgentRunEvent(type='stopped', run_id=record['run_id'], summary='Stopped at checkpoint'))
    stopped = client.get(f"/api/sticky-todo/requests/{payload['request_id']}").json()
    assert stopped['status'] == 'stopped' and not stopped['stopping']


def test_rejects_missing_project_and_foreign_origin(handoff):
    client, _, executions, _ = handoff
    assert client.post('/api/sticky-todo/requests', json={**body(), 'project_id': 'missing'}).status_code == 409
    assert client.post('/api/sticky-todo/requests', json=body(), headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.put('/api/sticky-todo/defaults', json={}, headers={'Origin':'https://evil.example'}).status_code == 403
    assert not executions


def test_dispatch_exception_preserves_session_without_reexecution(handoff, monkeypatch):
    client, app, _, _ = handoff
    async def broken(*args, **kwargs):
        raise RuntimeError('response lost')
    monkeypatch.setattr(module, 'execute_agent_run', broken)
    payload = body()
    with pytest.raises(RuntimeError):
        client.post('/api/sticky-todo/requests', json=payload)
    resumed = client.post('/api/sticky-todo/requests', json=payload)
    assert resumed.status_code == 200
    assert app.state.chat_store.get_thread('project-a', resumed.json()['session_id'])


def test_actual_background_execution_survives_client_disconnect(tmp_path, monkeypatch):
    from diane.server.routers import chat_v2
    from diane.server.chat_v2_backend import AgentBackendRunResult
    app = FastAPI()
    app.state.chat_store = ChatStore(tmp_path / 'graphs')
    app.state.chat_v2_store = ChatV2Store(tmp_path / 'v2')
    app.include_router(module.router)
    executions = []
    async def backend(store, run_id, **kwargs):
        executions.append(run_id)
        await asyncio.sleep(0.05)
        store.record_agent_event(AgentRunEvent(type='completed', run_id=run_id, summary='Checked'))
        return AgentBackendRunResult(status='completed', backend='test', summary='Checked')
    monkeypatch.setattr(chat_v2, 'run_agent_backend', backend)
    payload = body()
    with TestClient(app) as client:
        client.put('/api/sticky-todo/defaults', json={'projects':[{'id':'project-a','name':'A','root':str(tmp_path)}], 'active_project_id':'project-a', 'execute':{'backend':'native_codex'}})
        first = client.post('/api/sticky-todo/requests', json=payload)
        assert first.status_code == 200, first.text
        # No websocket or foreground request is held while the backend works.
        import time
        time.sleep(0.1)
        result = client.get('/api/sticky-todo/requests/' + payload['request_id']).json()
        assert result['status'] == 'ready'
        assert executions == [result['run_id']]


def test_followup_in_diane_updates_same_link_and_stops_current_run(handoff):
    from diane.server.chat_request import ChatMessageRequest
    client, app, _, defaults = handoff
    payload = body()
    first = client.post('/api/sticky-todo/requests', json=payload).json()
    app.state.chat_v2_store.record_agent_event(AgentRunEvent(type='completed', run_id=first['run_id'], summary='Initial exploration done'))
    request = SimpleNamespace(app=app)
    continuation = asyncio.run(module.create_agent_run(request=request, req=ChatMessageRequest(
        workflow_id='project-a', thread_id=first['session_id'], message='Now implement the recommendation',
        surface_context={'workspace_id':'project-a','workspace_root':defaults['projects'][0]['root']})))
    run_id = continuation['task_run_ref']['run_id']
    assert run_id != first['run_id']
    app.state.chat_v2_store.record_agent_event(AgentRunEvent(type='worker_started', run_id=run_id, summary='Follow-up running'))
    latest = client.get('/api/sticky-todo/requests/' + payload['request_id']).json()
    assert latest['run_id'] == run_id and latest['status'] == 'running'
    assert latest['session_id'] == first['session_id']
    client.post('/api/sticky-todo/requests/' + payload['request_id'] + '/stop')
    assert app.state.chat_v2_store.get_run(run_id).metadata['stop_requested']
    assert not app.state.chat_v2_store.get_run(first['run_id']).metadata.get('stop_requested')


def test_receipt_stays_prepared_until_execution_is_accepted(handoff, monkeypatch):
    """A crash before the runtime accepts a request must not leave it queued."""
    client, app, _, _ = handoff
    payload = body()

    async def accept(run_id, execute, request):
        receipt = module.read(app.state.chat_store.base_dir / "sticky_todo" / f"{payload['request_id']}.json")
        assert receipt["phase"] == "prepared"
        assert receipt["run_id"] == run_id
        assert module.status(request, receipt)["status"] == "attention"
        app.state.chat_v2_store.record_agent_event(AgentRunEvent(type="worker_started", run_id=run_id, summary="Working"))

    monkeypatch.setattr(module, "execute_agent_run", accept)
    response = client.post("/api/sticky-todo/requests", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    receipt = module.read(app.state.chat_store.base_dir / "sticky_todo" / f"{payload['request_id']}.json")
    assert receipt["phase"] == "submitted"
