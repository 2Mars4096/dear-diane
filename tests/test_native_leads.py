import asyncio
import json
import sys

import pytest

from diane.native_workers.lead import NativeLeadAdapter, _active_sessions
from diane.native_workers.service import NativeTeam, active_teams
from diane.server.chat_v2_backend import AgentBackendRunRequest, AgentBackendRunResult, AgentBackendStopped, select_agent_backend_adapter


def request(tmp_path, **kwargs):
    return AgentBackendRunRequest(task_id="task", run_id=kwargs.pop("run_id", "run"), thread_id="chat", workspace_root=str(tmp_path), objective="Hello", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["codex", "claude", "antigravity"])
async def test_lead_streams_and_resumes_same_account_only(monkeypatch, tmp_path, backend):
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path / "graphs"))
    calls = []
    def launch(runtime, profile, prompt, workspace, session=""):
        calls.append((runtime, profile, prompt, session))
        row = {"type": "result", "session_id": "fork-123", "result": "Answer"}
        return [sys.executable, "-c", f"print({json.dumps(row)!r})"], {}
    monkeypatch.setattr("diane.native_workers.service.launch", launch)
    events = []
    adapter = NativeLeadAdapter(backend)
    req = request(tmp_path, history=[{"role":"user", "content":"Prior context"}], profile_policy={"lead_profile": {"account":"work", "model":"model", "effort":"high", "fast":True}})
    result = await adapter.run(req, events.append)
    assert result.status == "completed" and result.summary == "Answer"
    assert "Session ID: chat" in calls[0][2]
    assert "GET /api/chats?q=" in calls[0][2]
    assert str(tmp_path / "graphs" / "chats") in calls[0][2]
    assert "Prior context" in calls[0][2]
    assert calls[0][1]["fast"] is True
    assert any(event.type == "model_text_delta" and event.payload["accumulated"] == "Answer" for event in events)
    await adapter.run(req.model_copy(update={"run_id":"next"}), events.append)
    assert calls[-1][-1] == "fork-123"
    req.profile_policy["lead_profile"]["account"] = "another"
    await adapter.run(req, events.append)
    assert calls[-1][-1] == ""
    assert not _active_sessions and not active_teams


@pytest.mark.asyncio
async def test_changing_source_uses_separate_continuation_without_losing_native_session(monkeypatch, tmp_path):
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path / "graphs"))
    calls = []
    def launch(runtime, profile, prompt, workspace, session=""):
        calls.append(session)
        native_id = "router-session" if profile.get("provider") == "openrouter" else "native-session"
        row = {"type": "result", "session_id": native_id, "result": "Answer"}
        return [sys.executable, "-c", f"print({json.dumps(row)!r})"], {}
    monkeypatch.setattr("diane.native_workers.service.launch", launch)
    adapter = NativeLeadAdapter("claude")
    for source in ["native", "openrouter", "openrouter", "native"]:
        result = await adapter.run(request(tmp_path, profile_policy={"lead_profile": {"provider": source}}), lambda event: None)
        assert result.status == "completed"
    assert calls == ["", "", "router-session", "native-session"]


@pytest.mark.asyncio
async def test_stop_cleans_up_native_lead_and_bridge(monkeypatch, tmp_path):
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path / "graphs"))
    monkeypatch.setattr("diane.native_workers.service.launch", lambda *args: ([sys.executable, "-c", "import time; time.sleep(30)"], {}))
    class Runtime:
        count = 0
        def raise_if_interrupted(self, checkpoint):
            self.count += 1
            if self.count > 2:
                raise AgentBackendStopped("stop", checkpoint=checkpoint)
    with pytest.raises(AgentBackendStopped):
        await NativeLeadAdapter("codex").run(request(tmp_path, profile_policy={"native_workers":{"claude":{"enabled":True}}}), lambda e: None, Runtime())
    assert not list(tmp_path.glob(".dan-team-*"))
    assert not active_teams and not _active_sessions
    records = list((tmp_path / "graphs/native_leads").glob("*.json"))
    assert json.loads(records[0].read_text())["status"] == "stopped"


@pytest.mark.asyncio
async def test_bridge_can_delegate_to_dan_and_reject_disabled_workers(monkeypatch, tmp_path):
    from diane.native_workers import bridge
    from diane.native_workers.service import current_team
    seen = []
    async def run(self, req, emit, runtime=None):
        seen.append(req)
        assert current_team.get() is None  # no recursive delegation
        return AgentBackendRunResult(status="completed", backend="super_dan", summary="Reviewed")
    monkeypatch.setattr("diane.server.chat_v2_backend.SuperDanBackendAdapter._run", run)
    team = NativeTeam("parent", str(tmp_path), {"dan":{"enabled":True,"model":"test"}}, tmp_path / "records")
    directory = tmp_path / "bridge"; directory.mkdir()
    server = asyncio.create_task(bridge.serve(directory, team))
    async def call(*args):
        process = await asyncio.create_subprocess_exec(sys.executable, bridge.__file__, "--queue", str(directory), *args, stdout=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(process.communicate(), 5)
        return json.loads(out)
    try:
        result = await call("start", "--backend", "dan", "--prompt", "review", "--shared-workspace")
        assert result["ok"] and "profile" not in result["result"]
        worker = result["result"]["worker_id"]
        status = await call("status", "--worker-id", worker)
        assert status["result"]["response"] == "Reviewed"
        assert seen[0].profile_policy["model"] == "test"
        assert not (await call("start", "--backend", "claude", "--prompt", "review"))["ok"]
        assert (await call("resume", "--worker-id", worker, "--prompt", "follow-up"))["ok"]
        await call("status", "--worker-id", worker)
        assert seen[-1].history[1]["content"] == "Reviewed"
    finally:
        server.cancel(); await asyncio.gather(server, return_exceptions=True); await team.close()


@pytest.mark.parametrize("name,runtime", [("native_codex","codex"),("claude","claude"),("antigravity","antigravity"),("cursor","cursor")])
def test_backend_routing(tmp_path, name, runtime):
    assert select_agent_backend_adapter(request(tmp_path), backend_name=name).backend_name == runtime


@pytest.mark.asyncio
async def test_legacy_codex_lead_starts_private_session_with_saved_history(monkeypatch, tmp_path):
    import hashlib
    from diane.native_workers.codex_home import SCOPE
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path / 'graphs'))
    identity = json.dumps(['chat', str(tmp_path.resolve()), 'codex', 'default'])
    state = tmp_path / 'graphs/native_lead_sessions' / (hashlib.sha256(identity.encode()).hexdigest() + '.json')
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({'native_session_id': 'shared-desktop-thread'}))
    calls = []
    def launch(runtime, profile, prompt, workspace, session=''):
        calls.append((session, prompt))
        return [sys.executable, '-c', 'print(\'{"type":"result","session_id":"private-thread","result":"done"}\')'], {}
    monkeypatch.setattr('diane.native_workers.service.launch', launch)
    req = request(tmp_path, history=[{'role': 'user', 'content': 'Existing conversation context'}])
    adapter = NativeLeadAdapter('codex')
    assert (await adapter.run(req, lambda event: None)).status == 'completed'
    assert calls[0][0] == '' and 'Existing conversation context' in calls[0][1]
    assert json.loads(state.read_text())['session_scope'] == SCOPE
    await adapter.run(req, lambda event: None)
    assert calls[-1][0] == 'private-thread'
