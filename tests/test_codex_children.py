import json
import sqlite3
from datetime import datetime, timezone

from dan.native_workers.codex_children import CodexChildren
from dan.native_workers.service import NativeTeam


def row(at, payload):
    return {"timestamp": datetime.fromtimestamp(at, timezone.utc).isoformat(), "type": "event_msg", "payload": payload}


def activity(at, child, kind="started", parent="lead"):
    return row(at, {"type": "item_completed", "thread_id": parent, "item": {
        "type": "SubAgentActivity", "agent_thread_id": child, "agent_path": f"/root/{child}", "kind": kind}})


def write(path, *rows):
    with path.open("a") as stream:
        for item in rows:
            stream.write(json.dumps(item) + "\n")


def setup(tmp_path):
    home = tmp_path / "account"
    (home / "sessions").mkdir(parents=True)
    paths = {name: home / "sessions" / f"{name}.jsonl" for name in ["lead", "first", "second", "old"]}
    with sqlite3.connect(home / "state_5.sqlite") as con:
        con.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT)")
        for name, path in paths.items():
            path.touch()
            con.execute("INSERT INTO threads VALUES (?, ?)", (name, str(path)))
    team = NativeTeam("run", str(tmp_path), {}, tmp_path / "workers")
    return CodexChildren(team, home, since=100), paths, team


def test_two_children_without_enabled_team_readonly_and_persistent(tmp_path):
    observer, paths, team = setup(tmp_path)
    write(paths['lead'], activity(101, 'first'), activity(102, 'second'))
    observer.poll('lead')
    assert len(team.records) == 2
    assert all(r['status'] == 'running' and r['can_stop'] is False for r in team.records.values())
    write(paths['first'], row(103, {'type': 'item_completed', 'thread_id': 'first', 'item': {
        'type': 'CommandExecution', 'command': ['pytest', '-q']}}))
    observer.poll('lead')
    assert observer.children['first']['activity'] == 'Running pytest -q'
    write(paths['lead'], activity(104, 'first', 'completed'))
    write(paths['first'], row(104, {'type': 'task_complete', 'last_agent_message': 'Reviewed successfully'}))
    before = {p: p.read_bytes() for p in paths.values()}
    observer.poll('lead'); observer.poll('lead')
    assert observer.children['first']['response'] == 'Reviewed successfully'
    assert observer.children['first']['status'] == 'completed'
    assert len(observer.children['first']['actions']) == 1
    observer.close()
    assert observer.children['second']['status'] == 'interrupted'
    assert all(p.read_bytes() == value for p, value in before.items())
    saved = [json.loads(p.read_text()) for p in team.base.glob('*.json')]
    assert {r['status'] for r in saved} == {'completed', 'interrupted'}


def test_turn_and_parent_isolation_partial_line_and_late_rollout(tmp_path):
    observer, paths, team = setup(tmp_path)
    write(paths['lead'], activity(99, 'old'), activity(101, 'old', 'completed'), activity(102, 'second', parent='unrelated'))
    event = json.dumps(activity(103, 'first'))
    with paths['lead'].open('a') as stream: stream.write(event[:30])
    observer.poll('lead')
    assert not team.records
    with paths['lead'].open('a') as stream: stream.write(event[30:] + '\n')
    paths['first'].unlink()
    observer.poll('lead')
    assert len(team.records) == 1
    write(paths['first'], row(104, {'type': 'task_complete', 'last_agent_message': 'Finished'}))
    observer.poll('lead')
    assert observer.children['first']['response'] == 'Finished'


def test_missing_account_index_does_not_break_lead(tmp_path):
    team = NativeTeam('run', str(tmp_path), {}, tmp_path / 'workers')
    observer = CodexChildren(team, tmp_path / 'missing')
    observer.poll('lead'); observer.close()
    assert not team.records


def test_shared_session_directory_and_oversized_unrelated_record(tmp_path):
    observer, paths, team = setup(tmp_path)
    sessions = observer.reader.home / 'sessions'
    shared = tmp_path / 'shared-sessions'
    sessions.rename(shared)
    sessions.symlink_to(shared, target_is_directory=True)
    with paths['lead'].open('a') as stream:
        stream.write('x' * (2 * 1024 * 1024 + 20) + '\n')
    write(paths['lead'], activity(102, 'first'))
    observer.poll('lead'); observer.poll('lead')
    assert len(team.records) == 1


async def _run_adapter(monkeypatch, tmp_path):
    import sys
    import time
    from dan.native_workers.lead import NativeLeadAdapter
    from dan.server.chat_v2_backend import AgentBackendRunRequest
    observer, paths, _ = setup(tmp_path)
    now = time.time() + 1
    write(paths['lead'], activity(now, 'first'), activity(now + 1, 'first', 'completed'))
    write(paths['first'], row(now + 1, {'type': 'task_complete', 'last_agent_message': 'Real child result'}))
    monkeypatch.setattr('dan.native_workers.catalog.accounts', lambda: {'codex': {'default': {'env': {'CODEX_HOME': str(observer.reader.home)}}}})
    events = [{'type': 'thread.started', 'thread_id': 'lead'}, {'type': 'turn.completed'}]
    monkeypatch.setattr('dan.native_workers.service.launch', lambda *args: ([sys.executable, '-c', '\n'.join(f'print({json.dumps(e)!r}, flush=True)' for e in events)], {}))
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path / 'graphs'))
    request = AgentBackendRunRequest(task_id='task', run_id='run', thread_id='chat', workspace_root=str(tmp_path), objective='Review')
    result = await NativeLeadAdapter('codex').run(request, lambda _: None)
    assert result.status == 'completed'
    records = list((tmp_path / 'graphs/native_workers').glob('*.json'))
    assert len(records) == 1
    child = json.loads(records[0].read_text())
    assert child['response'] == 'Real child result'
    assert child['status'] == 'completed' and child['can_stop'] is False


def test_native_lead_observes_children_without_team_enabled(monkeypatch, tmp_path):
    import asyncio
    asyncio.run(_run_adapter(monkeypatch, tmp_path))


def test_forked_parent_completion_is_not_child_result(tmp_path):
    observer, paths, team = setup(tmp_path)
    write(paths['lead'], activity(103, 'first'))
    write(paths['first'],
          row(104, {'type': 'task_started', 'started_at': 50, 'turn_id': 'parent-turn'}),
          row(104, {'type': 'task_complete', 'turn_id': 'parent-turn', 'last_agent_message': 'Parent history'}),
          row(105, {'type': 'task_started', 'started_at': 105, 'turn_id': 'child-turn'}))
    observer.poll('lead')
    assert observer.children['first']['status'] == 'running'
    assert observer.children['first']['response'] == ''
    write(paths['first'], row(106, {'type': 'task_complete', 'turn_id': 'child-turn', 'last_agent_message': 'Child result'}))
    observer.poll('lead')
    assert observer.children['first']['status'] == 'completed'
    assert observer.children['first']['response'] == 'Child result'
