import asyncio
import json
import sys

import pytest
from diane.native_workers.service import NativeTeam, active_teams, continue_worker, public_worker


@pytest.mark.asyncio
async def test_recovery_reuses_identity_policy_and_rejects_duplicate(monkeypatch, tmp_path):
    calls = []
    def launch(backend, profile, prompt, workspace, session=''):
        calls.append((dict(profile), session))
        return [sys.executable, '-c', 'import time;time.sleep(20)'], {}
    monkeypatch.setattr('diane.native_workers.service.launch', launch)
    base = tmp_path / 'workers'
    base.mkdir()
    row = dict(worker_id='w', parent_run_id='p', backend='claude', profile={'enabled': True, 'permission': 'plan', 'account': 'saved'}, workspace_root=str(tmp_path), created_at=1, native_session_id='native', status='running', prompt='Original task', response='Partial', error='')
    (base / 'w.json').write_text(json.dumps(row))
    try:
        updated = await continue_worker(base, 'p', 'w', 'Continue safely')
        assert updated['worker_id'] == 'w' and updated['native_session_id'] == 'native'
        assert 'profile' not in updated
        with pytest.raises(ValueError, match='still running'):
            await continue_worker(base, 'p', 'w', 'Duplicate')
        assert calls == [({'enabled': True, 'permission': 'plan', 'account': 'saved'}, 'native')]
        with pytest.raises(ValueError):
            await continue_worker(base, 'wrong-parent', 'w', 'Wrong owner')
    finally:
        await active_teams.pop('p').close()


def test_observed_children_never_expose_independent_controls():
    row = dict(worker_id='observed', parent_run_id='p', backend='codex', can_stop=False, native_session_id='native', status='completed', profile={'secret': 'hidden'})
    public = public_worker(row)
    assert not public['can_reply'] and not public['can_stop'] and 'profile' not in public


def test_history_paging_and_partial_last_line(monkeypatch, tmp_path):
    from diane.server.routers.native_workers import worker_events
    monkeypatch.setattr('diane.server.routers.native_workers.resolve_graphs_dir', lambda: tmp_path)
    base = tmp_path / 'native_workers'; base.mkdir()
    (base / 'w.json').write_text(json.dumps(dict(worker_id='w', parent_run_id='p', status='completed', created_at=1)))
    (base / 'w.jsonl').write_text(''.join(json.dumps({'text': str(i)}) + '\n' for i in range(260)) + '{partial')
    latest = worker_events('p', 'w')
    assert latest['before'] == 160
    older = worker_events('p', 'w', before=latest['before'])
    assert older['entries'][0]['cursor'] == 60
    assert older['entries'][-1]['cursor'] == 159
    forward = worker_events('p', 'w', after=0)
    assert forward['has_more'] and forward['entries'][-1]['cursor'] == 100
    tail = worker_events('p', 'w', after=200)
    assert not tail['has_more'] and tail['entries'][-1]['cursor'] == 259


@pytest.mark.asyncio
async def test_interactive_answer_validates_and_does_not_escalate_session():
    from diane.native_workers.codex_live import CodexLive
    client = CodexLive(None, lambda *args: None, True)
    sent = []
    async def send(row): sent.append(row)
    client.send = send
    client.requests['approval'] = {'id': 'approval', 'method': 'item/commandExecution/requestApproval', 'params': {}}
    with pytest.raises(ValueError): await client.reply('approval', 'acceptForSession')
    await client.reply('approval', 'accept')
    assert sent == [{'id': 'approval', 'result': {'decision': 'accept'}}]
    with pytest.raises(ValueError): await client.reply('approval', 'accept')
    client.requests['q'] = {'id': 'q', 'method': 'item/tool/requestUserInput', 'params': {'questions': [{'id': 'color'}]}}
    with pytest.raises(ValueError): await client.reply('q', answers={})
    await client.reply('q', answers={'color': 'blue'})
    assert sent[-1]['result'] == {'answers': {'color': {'answers': ['blue']}}}


def test_attention_marks_disconnected_workers_interrupted(monkeypatch, tmp_path):
    from diane.server.routers.native_workers import worker_attention
    monkeypatch.setattr('diane.server.routers.native_workers.resolve_graphs_dir', lambda: tmp_path)
    base = tmp_path / 'native_workers'; base.mkdir()
    (base / 'w.json').write_text(json.dumps(dict(worker_id='w', parent_run_id='offline', thread_id='chat', status='needs_input', created_at=1)))
    assert worker_attention()['workers'][0]['status'] == 'interrupted'
