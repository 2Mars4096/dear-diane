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
  if n>1 and os.environ.get('DROP_ACK'):sys.exit(1)
  print(json.dumps({'type':'user','uuid':r['uuid'],'session_id':'same-session','message':r['message']}),flush=True)
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
