import asyncio
import json
from pathlib import Path
import sys

import pytest

from dan.native_workers import catalog as runtime_catalog
from dan.native_workers.service import NativeTeam, read_workers
from dan.native_workers.sessions import discover, messages


def test_codex_account_isolation_and_flags(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_catalog, "binary", lambda _: "/bin/codex")
    monkeypatch.setattr(runtime_catalog, "accounts", lambda: {"codex": {"work": {"env": {"CODEX_HOME": str(tmp_path)}}}})
    command, env = runtime_catalog.launch("codex", {"account": "work", "model": "model", "effort": "high", "fast": True}, "test task", str(tmp_path))
    assert env["CODEX_HOME"] == str(tmp_path)
    assert 'service_tier="fast"' in command
    assert 'model_reasoning_effort="high"' in command
    assert "--ephemeral" not in command
    assert command[-2:] == ["--", "test task"]
    with pytest.raises(ValueError, match="account"):
        runtime_catalog.launch("codex", {"account": "../bad"}, "test", str(tmp_path))


def test_missing_runtime_never_falls_back(monkeypatch):
    monkeypatch.setattr(runtime_catalog, "binary", lambda _: None)
    with pytest.raises(ValueError, match="not installed"):
        runtime_catalog.launch("antigravity", {}, "hello", "/tmp")


@pytest.mark.asyncio
async def test_parallel_workers_are_scoped_and_capture_sessions(monkeypatch, tmp_path):
    def launch(backend, profile, objective, workspace, session=""):
        code = 'import json,time; print(json.dumps({"type":"system","session_id":"native-123"}),flush=True); time.sleep(0.05); print(json.dumps({"type":"result","result":"done"}),flush=True)'
        return [sys.executable, "-c", code], {}
    monkeypatch.setattr("dan.native_workers.service.launch", launch)
    team = NativeTeam("parent", str(tmp_path), {"claude": {"enabled": True}}, tmp_path / "workers")
    first = await team.start("claude", "first")
    second = await team.start("claude", "second")
    assert first["worker_id"] != second["worker_id"]
    assert not team.tasks[first["worker_id"]].done()
    await asyncio.gather(*team.tasks.values())
    records = read_workers(team.base, "parent")
    assert len(records) == 2
    assert all(row["status"] == "completed" and row["native_session_id"] == "native-123" for row in records)
    assert read_workers(team.base, "another-parent") == []
    with pytest.raises(ValueError, match="parent"):
        await team.stop("unknown")


@pytest.mark.asyncio
async def test_stop_kills_worker_and_does_not_stop_sibling(monkeypatch, tmp_path):
    monkeypatch.setattr("dan.native_workers.service.launch", lambda *a: ([sys.executable, "-c", "import time;time.sleep(20)"], {}))
    team = NativeTeam("parent", str(tmp_path), {"codex": {"enabled": True}}, tmp_path / "workers")
    first = await team.start("codex", "first")
    second = await team.start("codex", "second")
    await asyncio.sleep(.05)
    await team.stop(first["worker_id"])
    assert team.records[first["worker_id"]]["status"] == "stopped"
    assert not team.tasks[second["worker_id"]].done()
    await team.close()
    assert not team.processes


def test_session_discovery_matches_recorded_folder_and_keeps_sources(monkeypatch, tmp_path):
    profile = tmp_path / "claude"
    directory = profile / "projects" / "project"
    directory.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = directory / "session.jsonl"
    source.write_text(json.dumps({"type": "user", "cwd": str(workspace), "message": {"role": "user", "content": "Earlier work"}}) + "\n")
    original = source.read_bytes()
    monkeypatch.setattr("dan.native_workers.sessions.accounts", lambda: {"claude": {"work": {"env": {"CLAUDE_CONFIG_DIR": str(profile)}}}})
    sources = discover(str(workspace))
    assert len(sources) == 1
    assert sources[0]["fork"] is True
    assert discover(str(tmp_path / "another")) == []
    assert messages(sources[0]) == [{"role": "user", "content": "Earlier work"}]
    assert source.read_bytes() == original


@pytest.mark.asyncio
async def test_imported_claude_context_always_forks(monkeypatch, tmp_path):
    launched = []
    def launch(*args):
        launched.append(args)
        return [sys.executable, "-c", "print('{}')"], {}
    monkeypatch.setattr("dan.native_workers.service.launch", launch)
    team = NativeTeam("parent", str(tmp_path), {"claude": {"enabled": True, "source_session": "original"}}, tmp_path / "workers")
    commands = []
    async def run(record, command, env):
        commands.append(command)
    monkeypatch.setattr(team, "run", run)
    await team.start("claude", "continue")
    await asyncio.gather(*team.tasks.values())
    assert launched[-1][-1] == "original"
    assert "--fork-session" in commands[0]


def test_import_api_is_opt_in_and_creates_independent_chat(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from dan.server.app import create_app
    source_path = tmp_path / "session.jsonl"
    source_path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "Keep original"}}) + "\n")
    original = source_path.read_bytes()
    source = {"id": "found", "backend": "claude", "account": "default", "session_id": "original", "title": "Earlier work", "path": str(source_path), "workspace": str(tmp_path), "can_import": True, "fork": True}
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path / "graphs"))
    monkeypatch.setattr("dan.server.routers.native_workers.discover", lambda _: [source])
    with TestClient(create_app()) as client:
        assert len(client.get("/api/native-sessions", params={"workspace": str(tmp_path)}).json()["sessions"]) == 1
        assert client.get("/api/chats").json()["threads"] == []
        body = {"source_id": "found", "workspace": str(tmp_path), "workspace_id": "project"}
        assert client.post("/api/native-sessions/import", json={**body, "fork": False}).status_code == 400
        imported = client.post("/api/native-sessions/import", json=body)
        assert imported.status_code == 200
        result = imported.json()
        assert result["id"] != "original"
        history = client.get(f'/api/chats/project/{result["id"]}').json()["messages"]
        assert history[0]["content"] == "Keep original"
        assert source_path.read_bytes() == original


@pytest.mark.asyncio
async def test_imported_codex_resumes_new_fork_only(monkeypatch, tmp_path):
    launched = []
    def launch(*args):
        launched.append(args)
        return [sys.executable, "-c", "print('{}')"], {}
    monkeypatch.setattr("dan.native_workers.service.launch", launch)
    async def fork(source, workspace, env):
        assert source == "original"
        return "new-fork"
    monkeypatch.setattr("dan.native_workers.sessions.fork_codex", fork)
    team = NativeTeam("parent", str(tmp_path), {"codex": {"enabled": True, "source_session": "original"}}, tmp_path / "workers")
    await team.start("codex", "continue")
    await asyncio.gather(*team.tasks.values())
    assert launched[-1][-1] == "new-fork"
    assert all(args[-1] != "original" for args in launched)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [("WAITING", "needs_input"), ("ERROR", "failed"), ("SUCCESS", "completed")])
async def test_antigravity_terminal_status_is_preserved(monkeypatch, tmp_path, status, expected):
    row = {"event": "result", "result": {"conversation_id": "agy-session", "status": status, "response": "result"}}
    monkeypatch.setattr("dan.native_workers.service.launch", lambda *a: ([sys.executable, "-c", f"print({json.dumps(row)!r})"], {}))
    team = NativeTeam("parent", str(tmp_path), {"antigravity": {"enabled": True}}, tmp_path / "workers")
    worker = await team.start("antigravity", "test")
    await asyncio.gather(*team.tasks.values())
    assert team.records[worker["worker_id"]]["status"] == expected


def test_describe_summarizes_native_actions():
    from dan.native_workers.service import describe
    assert describe({"type": "item.started", "item": {"type": "command_execution", "command": "npm test"}}) == "Running npm test"
    assert describe({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b/app.py"}}]}}) == "Reading app.py"
    assert describe({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebFetch", "input": {"url": "https://example.com/x"}}]}}) == "Reading example.com"
    assert describe({"type": "tool_used", "summary": "Searched the web"}) == "Searched the web"
    assert describe({"type": "stdout", "text": "noise"}) == ""


def test_dan_modes_map_to_native_permissions(monkeypatch):
    from dan.native_workers import catalog
    monkeypatch.setattr(catalog, "binary", lambda runtime: f"/bin/{runtime}")
    monkeypatch.setattr(catalog, "accounts", lambda: {"codex": {"default": {"env": {}}}, "claude": {"default": {"env": {}}}})
    def command(runtime, permission, session=""):
        return " ".join(catalog.launch(runtime, {"permission": permission}, "task", "/tmp", session)[0])
    assert 'sandbox_mode="read-only"' in command("codex", "plan")
    assert 'sandbox_mode="workspace-write"' in command("codex", "auto", session="abc")
    assert "--dangerously-bypass-approvals-and-sandbox" in command("codex", "full")
    assert "--permission-mode plan" in command("claude", "plan")
    auto = command("claude", "auto")
    assert "--permission-mode acceptEdits" in auto and "autoAllowBashIfSandboxed" in auto
    assert "--dangerously-skip-permissions" in command("claude", "full")


def test_cursor_modes_resume_and_binary_detection(monkeypatch, tmp_path):
    from dan.native_workers import catalog
    monkeypatch.setattr(catalog, "binary", lambda runtime: "/bin/agent")
    monkeypatch.setattr(catalog, "accounts", lambda: {"cursor": {"default": {"env": {}}}})
    def command(permission, session=""):
        return catalog.launch("cursor", {"permission": permission, "model": "gpt-5"}, "task", "/work", session)[0]
    plan = command("plan")
    assert plan[:4] == ["/bin/agent", "-p", "--output-format", "stream-json"] and plan[-1] == "task"
    assert "--mode" in plan and "--force" not in plan
    assert " ".join(command("auto", "chat-1")).endswith("--sandbox enabled --force --model gpt-5 --resume chat-1 task")
    assert "--sandbox disabled" in " ".join(command("full"))
    # A generic `agent` binary is only trusted when it resolves into Cursor's install.
    monkeypatch.undo()
    home = tmp_path / "home"
    (home / ".local/bin").mkdir(parents=True)
    other = home / ".local/bin/agent"
    other.write_text("#!/bin/sh\n"); other.chmod(0o755)
    monkeypatch.setattr(catalog, "user_home", lambda: home)
    monkeypatch.setattr(catalog.shutil, "which", lambda name: None)
    assert catalog.binary("cursor") is None
    real = home / ".local/share/cursor-agent/agent"
    real.parent.mkdir(parents=True); real.write_text("#!/bin/sh\n"); real.chmod(0o755)
    other.unlink(); other.symlink_to(real)
    assert catalog.binary("cursor") == str(other)


def test_describe_cursor_tool_calls():
    from dan.native_workers.service import describe
    assert describe({"type": "tool_call", "subtype": "started", "tool_call": {"readToolCall": {"args": {"path": "src/app.py"}}}}) == "Reading app.py"
    assert describe({"type": "tool_call", "subtype": "started", "tool_call": {"shellToolCall": {"args": {"command": "npm test"}}}}) == "Running npm test"
    assert describe({"type": "tool_call", "subtype": "completed", "tool_call": {"readToolCall": {}}}) == ""


def test_cursor_chat_import_reads_store_without_writing(monkeypatch, tmp_path):
    import json, sqlite3
    from dan.native_workers import sessions
    project = tmp_path / "project"; project.mkdir()
    user = tmp_path / "Cursor/User"
    for workspace_id, folder in [("w1", project.as_uri()), ("w2", "vscode-remote://ssh/elsewhere")]:
        (user / "workspaceStorage" / workspace_id).mkdir(parents=True)
        (user / "workspaceStorage" / workspace_id / "workspace.json").write_text(json.dumps({"folder": folder}))
    legacy = sqlite3.connect(user / "workspaceStorage/w1/state.vscdb")
    legacy.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
    legacy.execute("INSERT INTO ItemTable VALUES ('composer.composerData', ?)", (json.dumps({"allComposers": [{"composerId": "old", "name": "Older chat"}, {"composerId": "gone", "name": "Pruned"}]}),))
    legacy.commit(); legacy.close()
    (user / "globalStorage").mkdir(parents=True)
    db = user / "globalStorage/state.vscdb"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE composerHeaders (composerId TEXT PRIMARY KEY, workspaceId TEXT, createdAt INTEGER, lastUpdatedAt INTEGER, isArchived INTEGER, isSubagent INTEGER, recency INTEGER, checkpointAt INTEGER, value TEXT, subagentTypeName TEXT)")
    con.execute("CREATE TABLE cursorDiskKV (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
    for cid, wid, archived, sub, name in [("c1", "w1", 0, 0, "Plan the reader"), ("c2", "w1", 1, 0, "Archived"), ("c3", "w1", 0, 1, "Subagent"), ("c4", "w2", 0, 0, "Remote")]:
        con.execute("INSERT INTO composerHeaders VALUES (?,?,0,1,?,?,0,0,?,'')", (cid, wid, archived, sub, json.dumps({"name": name})))
    con.execute("INSERT INTO cursorDiskKV VALUES ('composerData:c1', ?)", (json.dumps({"fullConversationHeadersOnly": [{"bubbleId": b} for b in ["b1", "b2", "b3", "b4"]]}),))
    for bubble, kind, text in [("b1", 1, "How should the reader work?"), ("b2", 2, ""), ("b3", 2, "Select, then ask."), ("b4", 2, "Answers stay in the side chat.")]:
        con.execute("INSERT INTO cursorDiskKV VALUES (?, ?)", (f"bubbleId:c1:{bubble}", json.dumps({"type": kind, "text": text})))
    con.execute("INSERT INTO cursorDiskKV VALUES ('composerData:old', ?)", (json.dumps({"conversation": [{"type": 1, "text": "hi"}, {"type": 2, "text": "hello"}]}),))
    con.commit(); con.close()
    before = db.stat().st_mtime_ns
    monkeypatch.setenv("DAN_CURSOR_USER_DIR", str(user))
    chats = dict(sessions.cursor_chats(str(project)))
    assert chats == {"c1": "Plan the reader", "old": "Older chat"}  # archived, subagent, remote, and pruned chats are skipped
    rows = {row["session_id"]: row for row in sessions.discover(str(project)) if row["backend"] == "cursor"}
    assert rows["c1"]["can_import"] and rows["c1"]["continuation"] == "history"
    assert sessions.messages(rows["c1"]) == [{"role": "user", "content": "How should the reader work?"},
                                             {"role": "assistant", "content": "Select, then ask.\n\nAnswers stay in the side chat."}]
    assert sessions.messages(rows["old"])[1] == {"role": "assistant", "content": "hello"}
    assert db.stat().st_mtime_ns == before


def test_native_import_accepts_above_old_limit_and_rejects_above_new(monkeypatch, tmp_path):
    from types import SimpleNamespace
    source = tmp_path / "session.jsonl"
    source.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "History"}}) + "\n")
    real_stat = Path.stat
    size = 129 * 1024 * 1024
    monkeypatch.setattr(Path, "stat", lambda path, **kw: SimpleNamespace(st_size=size, st_mode=real_stat(path).st_mode) if path == source else real_stat(path, **kw))
    assert messages({"backend": "claude", "path": str(source)}) == [{"role": "user", "content": "History"}]
    size = 256 * 1024 * 1024 + 1
    with pytest.raises(ValueError, match="256 MB import limit"):
        messages({"backend": "claude", "path": str(source)})
