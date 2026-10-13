import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from diane.server.chat_store import ChatStore
from diane.server.chat_request import ChatMessageRequest
from diane.server.chat_v2 import AgentRunEvent, build_v2_bridge_context
from diane.server.chat_v2_store import ChatV2Store
from diane.server.chat_v2_backend import AgentBackendRunResult, _objective_with_surface_context, run_agent_backend
from diane.server.plan_mode import PlanConflict, PlanDocument, PlanProposal, PlanQuestion, PlanStore, PlanUpdate
from diane.server.routers.sessions import router


@pytest.fixture
def setup(tmp_path):
    chats = ChatStore(str(tmp_path))
    thread = chats.create_thread('project')
    plans = PlanStore(chats)
    state = plans.update('project', thread.id, PlanUpdate(version=0, action='mode', mode='plan'))
    return chats, thread, plans, state


def document(**kwargs):
    return PlanDocument(title='Search', outcome='All results reachable', scope='Includes: result list\nLater: semantic search',
        specifications='Long titles wrap.', steps='1. Inspect\n2. Implement\n3. Verify', **kwargs)


def test_durable_edits_cas_conflict_and_session_isolation(setup, tmp_path):
    chats, thread, plans, state = setup
    state = plans.update('project', thread.id, PlanUpdate(version=state['version'], action='edit', document=document()))
    assert PlanStore(ChatStore(str(tmp_path))).read('project', thread.id)['document']['title'] == 'Search'
    with pytest.raises(PlanConflict):
        plans.update('project', thread.id, PlanUpdate(version=0, action='edit', document=document()))
    with pytest.raises(KeyError):
        plans.read('other-project', thread.id)
    assert len(chats.list_all_threads()) == 1
    chats.delete_thread('project', thread.id)
    assert not list((tmp_path / 'chats/project').glob('*.plan.json'))


def test_agent_revision_does_not_overwrite_human_edit(setup):
    _, thread, plans, state = setup
    state = plans.update('project', thread.id, PlanUpdate(version=state['version'], action='edit', document=document()))
    plans.receive('project', thread.id, 0, 'agent', PlanProposal(document=document(blockers='Choose data source')))
    state = plans.read('project', thread.id)
    assert not state['document']['blockers']
    assert state['proposal']['document']['blockers']
    with pytest.raises(PlanConflict):
        plans.claim('project', thread.id, 1, 'start')
    state = plans.update('project', thread.id, PlanUpdate(version=state['version'], action='accept_proposal'))
    assert state['revision'] == 2 and state['history'][0]['revision'] == 1
    with pytest.raises(PlanConflict):
        plans.claim('project', thread.id, 2, 'start')


def test_question_budget_is_durable_and_receipts_are_idempotent(setup):
    _, thread, plans, _ = setup
    proposal = PlanProposal(document=document(), question=PlanQuestion(question='Which scope?', recommended='Small'))
    for index in range(5):
        state = plans.receive('project', thread.id, index, str(index), proposal)
        assert state['questions_asked'] == min(index+1, 3)
        assert bool(state['question']) == (index < 3)
    repeated = plans.receive('project', thread.id, 4, '4', proposal)
    assert repeated['revision'] == 5


def test_start_freezes_reviewed_revision_and_prevents_double_start(setup):
    _, thread, plans, _ = setup
    state = plans.receive('project', thread.id, 0, 'draft', PlanProposal(document=document()))
    with pytest.raises(PlanConflict):
        plans.claim('project', thread.id, 0, 'old')
    state = plans.claim('project', thread.id, 1, 'start')
    assert state['mode'] == 'work' and state['execution']['run_id'] == 'start'
    with pytest.raises(PlanConflict):
        plans.claim('project', thread.id, 1, 'double')
    state = plans.update('project', thread.id, PlanUpdate(version=state['version'], action='edit', document=document(blockers='Changed scope')))
    assert not state['execution']['document']['blockers']
    assert state['revision'] == 2


def test_api_conflicts_and_loading(setup):
    chats, thread, _, _ = setup
    app = FastAPI(); app.state.chat_store = chats; app.include_router(router)
    client = TestClient(app)
    url = f'/api/chats/project/{thread.id}/plan'
    assert client.get(url).json()['mode'] == 'plan'
    assert client.put(url, json={'version': 0, 'action': 'mode', 'mode': 'work'}).status_code == 409
    assert client.get('/api/chats/project/missing/plan').status_code == 404
    assert client.put(url, json={'version': 1, 'action': 'edit'}).status_code == 400


@pytest.mark.asyncio
async def test_real_run_boundary_enforces_readonly_saves_plan_and_starts_once(setup, tmp_path, monkeypatch):
    chats, thread, plans, _ = setup
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path))
    runs = ChatV2Store(tmp_path / 'chat_v2')
    captured = []
    class Adapter:
        backend_name = 'native_codex'
        async def run(self, request, emit, runtime=None):
            captured.append(request)
            text = '```diane-plan\n' + PlanProposal(document=document(), summary='Scope drafted.').model_dump_json() + '\n```'
            emit(AgentRunEvent(type='model_text_delta', run_id=request.run_id, task_id=request.task_id, summary='raw', payload={'text':text}))
            emit(AgentRunEvent(type='completed', run_id=request.run_id, task_id=request.task_id, summary=text))
            return AgentBackendRunResult(status='completed', backend=self.backend_name, summary=text)
    async def run(action, revision):
        accepted = runs.accept_bridge_context(build_v2_bridge_context(ChatMessageRequest(workflow_id='project',
            thread_id=thread.id, message='Draft search changes' if action == 'refine' else 'Start the reviewed plan', mode='agent',
            surface_context={'plan_review': {'action':action, 'workflow_id':'project', 'revision':revision}, 'selected_skills':['paper-writing']})))
        result = await run_agent_backend(runs, accepted.run_id, adapter=Adapter(),
            overrides={'profile_policy': {'permission_mode':'full', 'codex_sandbox':'danger-full-access'}})
        return result, accepted.run_id
    result, run_id = await run('refine', 0)
    assert result.status == 'completed', result.summary
    assert '```' not in result.summary
    assert captured[-1].profile_policy['permission_mode'] == 'plan'
    assert captured[-1].profile_policy['codex_sandbox'] == 'read-only'
    assert 'not a skill' in _objective_with_surface_context(captured[-1])
    assert plans.read('project', thread.id)['revision'] == 1
    result, start_id = await run('start', 1)
    assert result.status == 'completed', result.summary
    assert captured[-1].profile_policy['permission_mode'] == 'full'
    assert 'All results reachable' in _objective_with_surface_context(captured[-1])
    assert plans.read('project', thread.id)['execution']['run_id'] == start_id
    count = len(captured)
    result, _ = await run('start', 1)
    assert result.status == 'failed' and len(captured) == count


@pytest.mark.asyncio
async def test_invalid_model_output_preserves_draft(setup, tmp_path):
    _, thread, plans, _ = setup
    runs = ChatV2Store(tmp_path / 'chat_v2')
    accepted = runs.accept_bridge_context(build_v2_bridge_context(ChatMessageRequest(workflow_id='project',
        thread_id=thread.id, message='Draft', surface_context={'plan_review': {'action':'refine', 'workflow_id':'project', 'revision':0}})))
    class Adapter:
        backend_name = 'native_codex'
        async def run(self, request, emit, runtime=None):
            return AgentBackendRunResult(status='completed', backend=self.backend_name, summary='Malformed response')
    result = await run_agent_backend(runs, accepted.run_id, adapter=Adapter())
    assert result.status == 'failed'
    assert plans.read('project', thread.id)['document'] is None


@pytest.mark.asyncio
async def test_api_planner_uses_selected_model_without_exposing_tools(monkeypatch):
    from types import SimpleNamespace
    from diane.server.plan_mode import run_api_planner
    from diane.server.chat_v2_backend import AgentBackendRunRequest
    calls = []
    class Provider:
        async def complete(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text='plan', usage={'prompt_tokens':12,'completion_tokens':8})
    closed = []
    async def close(provider): closed.append(provider)
    cli = SimpleNamespace(_resolve_live_model=lambda model: model,
        _build_live_provider=lambda model, **kwargs: Provider(), _close_live_provider=close)
    monkeypatch.setattr('diane.server.chat_v2_backend._load_super_dan_cli', lambda: cli)
    request = AgentBackendRunRequest(task_id='t',run_id='r',objective='Plan the search',workspace_root='',
        profile_policy={'model':'configured-model'},history=[{'role':'user','content':'Keep ranking'}])
    result = await run_api_planner(request, SimpleNamespace(raise_if_interrupted=lambda _:None))
    assert calls[0]['model'] == 'configured-model'
    assert 'tools' not in calls[0] and 'functions' not in calls[0]
    assert any(row['content']=='Keep ranking' for row in calls[0]['messages'])
    assert result.token_usage['prompt_tokens'] == 12
    assert closed


@pytest.mark.asyncio
async def test_plan_process_bridge_blocks_mutations_before_reaching_manager(tmp_path, monkeypatch):
    import asyncio
    import json
    from diane.native_workers import proc_bridge
    def forbidden_manager():
        raise AssertionError('Process manager must not be called')
    monkeypatch.setattr('diane.processes.manager', forbidden_manager)
    for action in ('start', 'stop'):
        (tmp_path / f'{action}.proc-request').write_text(json.dumps({'action':action,'command':'touch unexpected','id':'running'}))
    server = asyncio.create_task(proc_bridge.serve(tmp_path, str(tmp_path), permission='plan'))
    try:
        for _ in range(20):
            if (tmp_path / 'stop.proc-response').exists(): break
            await asyncio.sleep(.02)
        for action in ('start','stop'):
            response = json.loads((tmp_path / f'{action}.proc-response').read_text())
            assert not response['ok'] and 'Plan mode' in response['error']
    finally:
        server.cancel(); await asyncio.gather(server, return_exceptions=True)


@pytest.mark.asyncio
async def test_second_window_cannot_append_plan_start_to_active_run(setup, tmp_path):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from diane.server.routers.chat_v2 import create_agent_run
    chats, thread, _, _ = setup
    runs = ChatV2Store(tmp_path / 'chat_v2')
    runs.accept_bridge_context(build_v2_bridge_context(ChatMessageRequest(workflow_id='project',thread_id=thread.id,message='Existing work',mode='agent')))
    req = ChatMessageRequest(workflow_id='project',thread_id=thread.id,message='Start plan',mode='agent',
        surface_context={'plan_review':{'action':'start','workflow_id':'project','revision':1}})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(chat_v2_store=runs,chat_store=chats)))
    with pytest.raises(HTTPException) as error:
        await create_agent_run(request=request,req=req)
    assert error.value.status_code == 409


def test_question_batches_share_budget_and_clear_after_answer(setup):
    _, thread, plans, _ = setup
    questions = [PlanQuestion(question=f'Choice {i}?', recommended='A', alternatives=['B'], selection_mode='multiple') for i in range(2)]
    state = plans.receive('project', thread.id, 0, 'batch', PlanProposal(document=document(), questions=questions))
    assert len(state['questions']) == 2 and state['questions_asked'] == 2
    assert state['questions'][0]['selection_mode'] == 'multiple'
    state = plans.receive('project', thread.id, 1, 'next', PlanProposal(document=document(), questions=questions))
    assert len(state['questions']) == 1 and state['questions_asked'] == 3
    state = plans.receive('project', thread.id, 2, 'done', PlanProposal(document=document()))
    assert state['questions'] == [] and state['question'] is None
