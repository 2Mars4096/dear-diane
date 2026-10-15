import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest
from diane.native_workers.service import NativeTeam


@pytest.fixture
def cli(tmp_path, monkeypatch):
    script = tmp_path / 'claude'
    script.write_text(f'#!{sys.executable}\n' + '''import sys,json,os
n=0
for line in sys.stdin:
 r=json.loads(line)
 if r['type']=='control_request':
  print(json.dumps({'type':'control_response','response':{'subtype':'success','request_id':r['request_id']}}),flush=True)
  print(json.dumps({'type':'result','subtype':'error_during_execution','is_error':True,'errors':['interrupted']}),flush=True)
 else:
  n+=1
  print(json.dumps({'type':'system','subtype':'init','session_id':'same-session'}),flush=True)
  if os.environ.get('BACKGROUND_RESULT'):
   print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'','num_turns':0,'origin':{'kind':'task-notification'}}),flush=True)
  if n>1 and os.environ.get('DROP_ACK'):sys.exit(1)
  print(json.dumps({'type':'user','uuid':r['uuid'],'session_id':'same-session','message':r['message']}),flush=True)
  if os.environ.get('BACKGROUND_RESULT'):
   print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'','num_turns':0,'origin':{'kind':'task-notification'}}),flush=True)
  print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'Working' if n==1 else 'Correction received'}]}}),flush=True)
  if n>1:print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'Correction received'}),flush=True)
''')
    script.chmod(0o700)
    monkeypatch.setattr('diane.native_workers.service.launch', lambda *args: ([str(script), '-p', args[2]], dict(os.environ)))


async def running_team(tmp_path):
    team = NativeTeam('lead', str(tmp_path), {'claude': {'enabled': True, '_live_steering': True}}, tmp_path / 'workers', is_lead=True)
    record = await team.start('claude', 'Original task')
    async with asyncio.timeout(5):
        while not team.steering or not team.steering[record['worker_id']].turn_id:
            await asyncio.sleep(.01)
    return team, record['worker_id'], team.steering[record['worker_id']]


@pytest.mark.asyncio
async def test_claude_steers_same_process_and_ignores_interrupted_result(cli, tmp_path):
    team, worker, client = await running_team(tmp_path)
    try:
        process = team.processes[worker]
        result = await client.steer([SimpleNamespace(text='Wrap up now', metadata={})])
        assert result['turnId']
        await asyncio.wait_for(team.tasks[worker], 5)
        row = team.records[worker]
        assert row['native_session_id'] == 'same-session'
        assert row['status'] == 'completed' and row['error'] == ''
        assert row['response'] == 'Correction received'
        assert process.returncode is not None
        assert not team.steering and not team.processes
    finally:
        await team.close()


@pytest.mark.asyncio
async def test_claude_does_not_acknowledge_input_without_replay(cli, tmp_path, monkeypatch):
    monkeypatch.setenv('DROP_ACK', '1')
    team, worker, client = await running_team(tmp_path)
    try:
        with pytest.raises(ConnectionError, match='acknowledging'):
            await client.steer([SimpleNamespace(text='Correction', metadata={})])
        await asyncio.wait_for(team.tasks[worker], 5)
        assert team.records[worker]['status'] == 'failed'
    finally:
        await team.close()


@pytest.mark.asyncio
async def test_claude_stop_cleans_live_process(cli, tmp_path):
    team, worker, client = await running_team(tmp_path)
    await team.stop(worker)
    assert team.records[worker]['status'] == 'stopped'
    assert not team.steering and not team.processes
    with pytest.raises(ValueError, match='finished'):
        await client.steer([SimpleNamespace(text='Late', metadata={})])


@pytest.mark.asyncio
async def test_claude_lead_publishes_steering_and_acknowledges_durable_queue(cli, tmp_path, monkeypatch):
    from diane.native_workers.lead import NativeLeadAdapter
    from diane.server.chat_v2_backend import AgentBackendRunRequest, AgentBackendRuntime
    from diane.server.chat_v2_store import ChatV2Store
    from diane.server.chat_v2 import AgentRunCommand, build_v2_bridge_context
    from diane.server.chat_request import ChatMessageRequest
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path / 'graphs'))
    store = ChatV2Store(tmp_path / 'store')
    accepted = store.accept_bridge_context(build_v2_bridge_context(ChatMessageRequest(workflow_id='w', thread_id='t', message='Work', mode='agent', surface_context={'workspace_root': str(tmp_path)})), stream_channel_id='test')
    store.update_run_metadata(accepted.run_id, {'selected_backend': 'claude'}, status='running')
    store.queue_agent_command(AgentRunCommand(command='append_followup', run_id=accepted.run_id, task_id=accepted.task_id, payload={'text': 'Wrap up now', 'client_message_id': 'user-visible-message'}))
    runtime = AgentBackendRuntime(store, run_id=accepted.run_id, task_id=accepted.task_id)
    request = AgentBackendRunRequest(run_id=accepted.run_id, task_id=accepted.task_id, thread_id='t', workspace_root=str(tmp_path), objective='Original task')
    events = []
    result = await asyncio.wait_for(NativeLeadAdapter('claude').run(request, events.append, runtime), 10)
    assert result.status == 'completed'
    assert result.summary == 'Correction received'
    assert any(event.payload.get('live_steering') is True for event in events)
    item = store.get_task(accepted.task_id).queue_items[0]
    assert item.status == 'completed'
    assert item.metadata['delivered_run_id'] == accepted.run_id
    assert store.get_run(accepted.run_id).metadata['live_steering'] is False


@pytest.mark.asyncio
async def test_resumed_background_results_do_not_finish_current_message(cli, tmp_path, monkeypatch):
    monkeypatch.setenv('BACKGROUND_RESULT', '1')
    team, worker, client = await running_team(tmp_path)
    try:
        await asyncio.sleep(.03)
        assert not team.tasks[worker].done()
        assert not client.finished.done()
        await client.steer([SimpleNamespace(text='Continue the actual request', metadata={})])
        await asyncio.wait_for(team.tasks[worker], 5)
        assert team.records[worker]['status'] == 'completed'
        assert team.records[worker]['response'] == 'Correction received'
    finally:
        await team.close()


@pytest.mark.asyncio
async def test_permission_reply_uses_stored_input_and_does_not_persist_allow_rules():
    from diane.native_workers.claude_live import ClaudeLive
    writes = []
    class Stdin:
        def write(self, data): writes.append(json.loads(data))
        async def drain(self): pass
    client = ClaudeLive(SimpleNamespace(stdin=Stdin(), returncode=None), 'default')
    row = {'type': 'control_request', 'request_id': 'permission-1', 'request': {'subtype': 'can_use_tool', 'tool_name': 'Bash', 'input': {'command': 'printf test'}, 'description': 'Print test'}}
    client.receive(row)
    assert client.requests['permission-1']['params']['input'] == {'command': 'printf test'}
    await client.reply('permission-1', 'accept')
    response = writes[-1]['response']['response']
    assert response == {'behavior': 'allow', 'updatedInput': {'command': 'printf test'}}
    assert not client.requests
    with pytest.raises(ValueError, match='no longer pending'):
        await client.reply('permission-1', 'accept')
    client.receive({**row, 'request_id': 'permission-2'})
    client.receive({'type': 'control_cancel_request', 'request_id': 'permission-2'})
    assert not client.requests
    client.close()


@pytest.mark.asyncio
async def test_read_only_permission_cannot_be_approved():
    from diane.native_workers.claude_live import ClaudeLive
    writes = []
    class Stdin:
        def write(self, data): writes.append(json.loads(data))
        async def drain(self): pass
    client = ClaudeLive(SimpleNamespace(stdin=Stdin(), returncode=None), 'plan')
    client.receive({'type': 'control_request', 'request_id': 'p', 'request': {'subtype': 'can_use_tool', 'tool_name': 'Write', 'input': {'file_path': 'x', 'content': 'x'}}})
    with pytest.raises(ValueError, match='Read-only'):
        await client.reply('p', 'accept')
    await client.reply('p', 'decline')
    assert writes[-1]['response']['response']['behavior'] == 'deny'
    client.close()


@pytest.mark.asyncio
async def test_waiting_reply_does_not_close_session_with_live_background_command():
    from diane.native_workers.claude_live import ClaudeLive
    client = ClaudeLive(SimpleNamespace(returncode=None))
    client.latest_input = 'current'
    client.receive({'type': 'user', 'uuid': 'current'})
    client.receive({'type': 'system', 'subtype': 'task_started', 'task_id': 'job', 'task_type': 'local_bash'})
    assert not client.receive({'type': 'result', 'subtype': 'success', 'result': 'Waiting for regression results'})
    assert not client.finished.done()
    # An older session's notification must not finish this run either.
    client.receive({'type': 'system', 'subtype': 'task_notification', 'task_id': 'old', 'status': 'stopped'})
    assert not client.receive({'type': 'result', 'subtype': 'success', 'origin': {'kind': 'task-notification'}, 'result': ''})
    client.receive({'type': 'system', 'subtype': 'task_notification', 'task_id': 'job', 'status': 'completed'})
    assert client.receive({'type': 'result', 'subtype': 'success', 'origin': {'kind': 'task-notification'}, 'result': 'Regression suite passed'})
    assert client.finished.result()['result'] == 'Regression suite passed'

@pytest.mark.asyncio
async def test_live_text_burst_bounds_snapshots_and_keeps_terminal_durable(tmp_path, monkeypatch):
    script = tmp_path / 'burst.py'
    script.write_text('''import sys,json
r=json.loads(sys.stdin.readline())
print(json.dumps({'type':'user','uuid':r['uuid'],'message':r['message']}),flush=True)
for i in range(100):
 print(json.dumps({'type':'stream_event','event':{'type':'content_block_delta','delta':{'text':'x'}}}),flush=True)
print(json.dumps({'type':'result','subtype':'success','result':'x'*100}),flush=True)
''')
    monkeypatch.setattr('diane.native_workers.service.launch', lambda *args: ([sys.executable, str(script), '-p', args[2]], dict(os.environ)))
    team = NativeTeam('lead', str(tmp_path), {'claude': {'enabled': True, '_live_steering': True}}, tmp_path / 'workers', is_lead=True)
    saves = []
    save = team.save
    def tracked(record):
        saves.append(record['status'])
        save(record)
    team.save = tracked
    record = await team.start('claude', 'Burst')
    await asyncio.wait_for(team.tasks[record['worker_id']], 5)
    saved = json.loads((team.base / (record['worker_id'] + '.json')).read_text())
    assert saved['status'] == 'completed'
    assert saved['response'] == 'x' * 100
    assert len(saves) < 20
    events = (team.base / (record['worker_id'] + '.jsonl')).read_text().splitlines()
    assert sum(json.loads(row).get('type') == 'stream_event' for row in events) == 100
