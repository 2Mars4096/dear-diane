import pytest
from dan.personal.store import PersonalStore, Conflict
from dan.personal.conversation import Conversation, ChatInput, Choice, brief_for
from dan.personal.budgets import status


@pytest.fixture
def service(tmp_path):
    return Conversation(PersonalStore(tmp_path/'personal'))


def submit(service, operation='conversation-001', text='Remind me tomorrow'):
    return service.submit('operator', ChatInput(operation_id=operation,text=text,timezone='Asia/Hong_Kong'), 'fixture/model')


def creation(**changes):
    return Choice(action='create',title='Meet Ada',date='2099-10-03',time='14:00',timezone='Asia/Hong_Kong',remind=True,**changes)


def test_conversation_replay_and_concurrent_admission(service):
    first = submit(service)
    assert submit(service) == first
    with pytest.raises(Conflict): submit(service, 'conversation-002')
    with pytest.raises(Conflict): submit(service, text='Different')
    assert len(service.history('operator')) == 1
    assert service.history('other') == []
    assert status(service.store,'operator')['used_reservations_usd'] == '0.305152'


def test_create_real_reminder_with_receipt_and_retry(service):
    job = submit(service)
    receipt = service.apply(job, creation())
    assert service.apply(job, creation()) == receipt
    assert len(service.store.snapshot('operator')['commitments']) == 1
    record = service.store.detail('operator',receipt['record_id'])['commitment']
    assert record['lifecycle'] == 'active'
    assert record['reminder']['state'] == 'scheduled'
    assert 'remind you in Diane' in receipt['reply']


def test_invalid_creation_has_no_partial_record(service):
    job = submit(service)
    with pytest.raises(ValueError): service.apply(job,Choice(action='create',title='Incomplete',remind=True))
    assert service.store.snapshot('operator')['commitments'] == []


def test_chat_references_require_exact_revision_and_owner(service):
    job = submit(service)
    result = service.apply(job, creation())
    with pytest.raises(Conflict): service.apply(job,Choice(action='cancel',record_id=result['record_id'],expected_revision=1))
    with pytest.raises(KeyError): service.apply({**job,'owner':'other'},Choice(action='cancel',record_id=result['record_id'],expected_revision=3))
    changed = service.apply({**job,'id':'next-operation'},Choice(action='cancel',record_id=result['record_id'],expected_revision=3))
    assert 'Cancelled' in changed['reply']
    assert service.store.detail('operator',result['record_id'])['commitment']['reminder']['state'] == 'stopped'


def test_stop_and_restart_never_replay_model_call(service):
    job = submit(service); claimed = service.claim()
    service.stop('operator',job['id'])
    assert not service.authorize(claimed)
    service.finish(claimed, {'reply':'must not replace stop'})
    assert service.history('operator')[0]['reply'] == 'Stopped.'
    next_job = submit(service,'conversation-002'); service.claim()
    with service.store.connection() as db:
        db.execute("UPDATE conversation_jobs SET lease_until=0,state='applying' WHERE id=?",(next_job['id'],))
    assert service.claim() is None
    assert service.history('operator')[-1]['state'] == 'interrupted'


def test_pause_and_midnight_reservations(service,monkeypatch):
    job=submit(service); service.claim()
    assert service.authorize(job)
    monkeypatch.setenv('DAN_PERSONAL_DAILY_USD','0')
    with pytest.raises(ValueError): service.authorize(job)


def test_brief_separates_file_evidence_and_contains_real_context(service):
    job=submit(service)
    brief=brief_for(job,service.history('operator'),[])
    assert brief.tool_policy.allowed_tool_ids == []
    assert any('untrusted' in line for line in brief.hard_constraints)
    assert 'Asia/Hong_Kong' in brief.evidence[0].content
    assert 'additionalProperties' in brief.output_contract.output_schema


def test_no_model_cannot_silently_accept_messages(service):
    with pytest.raises(ValueError,match='Connect an AI'):
        service.submit('operator',ChatInput(operation_id='conversation-001',text='Hello'),'')
    assert service.history('operator') == []


def test_time_correction_replaces_reminder_without_another_question(service):
    job=submit(service)
    created=service.apply(job,creation())
    changed=service.apply({**job,'id':'follow-up'},Choice(action='update',record_id=created['record_id'],expected_revision=3,time='15:00',remind=True))
    record=service.store.detail('operator',created['record_id'])['commitment']
    assert record['time']=='15:00:00'
    assert record['reminder']['state']=='scheduled'
    assert '07:00:00' in record['reminder']['due_at']
    assert 'When should' not in changed['reply']


def test_stopping_reminder_preserves_commitment(service):
    job=submit(service); created=service.apply(job,creation())
    service.apply({**job,'id':'stop-follow-up'},Choice(action='stop_reminder',record_id=created['record_id'],expected_revision=3))
    record=service.store.detail('operator',created['record_id'])['commitment']
    assert record['lifecycle']=='active'
    assert record['reminder']['state']=='stopped'


def test_all_day_event_can_have_separate_timed_reminder(service):
    job=submit(service)
    result=service.apply(job,Choice(action='create',title='Deadline',date='2099-10-03',all_day=True,timezone='Asia/Hong_Kong',remind=True,reminder_time='10:00'))
    record=service.store.detail('operator',result['record_id'])['commitment']
    assert record['all_day'] and record['time'] is None
    assert '02:00:00' in record['reminder']['due_at']


def test_earlier_reminder_keeps_event_time(service):
    job=submit(service)
    result=service.apply(job,creation(reminder_time='13:30'))
    record=service.store.detail('operator',result['record_id'])['commitment']
    assert record['time']=='14:00:00'
    assert '05:30:00' in record['reminder']['due_at']


@pytest.mark.parametrize('action,note,expected', [('pause','','paused'),('wait','Waiting for Ada','waiting-external'),('complete','I called Ada','fulfilled')])
def test_conversation_lifecycle_uses_existing_services(service,action,note,expected):
    job=submit(service); result=service.apply(job,creation())
    service.apply({**job,'id':'status-follow-up'},Choice(action=action,record_id=result['record_id'],expected_revision=3,note=note))
    record=service.store.detail('operator',result['record_id'])['commitment']
    assert expected in (record['attention'],record['lifecycle'])


def test_schema_five_gets_private_backup_before_conversation_migration(service):
    with service.store.connection() as db:
        db.execute('DROP TABLE voice_operations'); db.execute('DROP TABLE conversation_jobs'); db.execute('PRAGMA user_version=5')
    migrated=PersonalStore(service.store.directory)
    assert list(migrated.directory.glob('state.v5.*.backup.sqlite3'))
    with migrated.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==7


def test_clarifying_turn_preserves_original_user_message(service):
    job=submit(service,text='Remind me to call Ada'); service.claim(); service.finish(job,{'reply':'When?'})
    follow=submit(service,'conversation-002',text='Tomorrow at 2 pm')
    result=service.apply(follow,creation())
    source=service.store.detail('operator',result['record_id'])['capture']['text']
    assert 'call Ada' in source and 'Tomorrow at 2 pm' in source


def test_stop_cancels_inflight_model_and_prevents_effects(service,monkeypatch,tmp_path):
    import asyncio
    from types import SimpleNamespace
    from dan.personal.conversation import process
    from dan.server.chat_v2_store import ChatV2Store
    async def scenario():
        started=asyncio.Event(); cancelled=asyncio.Event(); closed=asyncio.Event()
        async def close(): closed.set()
        async def backend(*args,**kwargs):
            started.set()
            try: await asyncio.Event().wait()
            finally: cancelled.set()
        monkeypatch.setattr('dan.cli.resolve_config',lambda: {'base_url':'https://openrouter.ai/api/v1','api_key':'fixture'})
        monkeypatch.setattr('dan.cli.live_gateway.build_gateway_backed_live_provider',lambda *args,**kwargs: SimpleNamespace(aclose=close))
        monkeypatch.setattr('dan.server.chat_v2_backend.run_agent_backend',backend)
        submit(service); job=service.claim()
        app=SimpleNamespace(state=SimpleNamespace(chat_v2_store=ChatV2Store(tmp_path/'chat')))
        task=asyncio.create_task(process(app,service,job))
        await asyncio.wait_for(started.wait(),2)
        service.stop('operator',job['id'])
        await asyncio.wait_for(task,2)
        assert cancelled.is_set() and closed.is_set()
        assert service.store.snapshot('operator')['commitments']==[]
        assert service.history('operator')[0]['state']=='stopped'
    asyncio.run(scenario())


def test_conversation_api_is_private_and_rejects_bad_timezone(monkeypatch,tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dan.server.routers.personal import router
    monkeypatch.setenv('DAN_GRAPHS_DIR',str(tmp_path))
    monkeypatch.setenv('DAN_PERSONAL_ENABLED','1')
    monkeypatch.setenv('DAN_PERSONAL_MODEL','fixture/model')
    app=FastAPI(); app.include_router(router); client=TestClient(app)
    payload={'operation_id':'chat-api-test','text':'Hello','timezone':'No/Such'}
    assert client.post('/api/personal/conversation',json=payload).status_code==422
    payload['timezone']='Asia/Hong_Kong'
    assert client.post('/api/personal/conversation',json=payload).status_code==200
    response=client.get('/api/personal/conversation')
    assert response.headers['cache-control']=='no-store'
    assert 'budget' not in response.json()['turns'][0]
    monkeypatch.setenv('DAN_PERSONAL_ENABLED','0')
    assert client.get('/api/personal/conversation').status_code==404


def test_failed_turn_preserves_user_context_without_repeating_system_error(service):
    job = submit(service, text='I just told you about the budget')
    history = [{'id': 'earlier', 'text': 'Central, budget HKD 1000', 'state': 'failed',
                'reply': 'I couldn’t understand that request. Could you rephrase it?'}]
    evidence = brief_for(job, history, []).evidence[0].content
    assert 'budget HKD 1000' in evidence
    assert 'couldn’t understand' not in evidence


@pytest.mark.parametrize('error_type,expected', [('TimeoutError', 'timed out'), ('ValueError', 'failed')])
def test_failed_backend_reports_infrastructure_failure_without_effects(service, monkeypatch, tmp_path, error_type, expected):
    import asyncio
    from types import SimpleNamespace
    from dan.personal.conversation import process
    from dan.server.chat_v2_store import ChatV2Store
    async def backend(*args, **kwargs):
        assert kwargs['adapter'].timeout == 120
        return SimpleNamespace(status='failed', raw_result={'error_type': error_type, 'error': 'private diagnostic'})
    monkeypatch.setattr('dan.cli.resolve_config', lambda: {'base_url': 'https://openrouter.ai/api/v1', 'api_key': 'fixture'})
    monkeypatch.setattr('dan.cli.live_gateway.build_gateway_backed_live_provider', lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr('dan.server.chat_v2_backend.run_agent_backend', backend)
    submit(service); job = service.claim()
    app = SimpleNamespace(state=SimpleNamespace(chat_v2_store=ChatV2Store(tmp_path/'chat')))
    asyncio.run(process(app, service, job))
    turn = service.history('operator')[0]
    assert turn['state'] == 'failed'
    assert expected in turn['reply'] and 'no action was taken' in turn['reply']
    assert 'private diagnostic' not in turn['reply']
    assert not service.store.snapshot('operator')['commitments']


def test_fast_reply_options_preserve_price_and_call_constraints(service):
    from dan.personal.conversation import reply_options
    job = submit(service)
    options = reply_options({**job, 'model': 'deepseek/deepseek-v4.1-flash'}, 'https://openrouter.ai/api/v1')['extra_body']
    assert options['reasoning'] == {'effort': 'low'}
    assert options['provider']['sort'] == 'latency'
    assert options['provider']['max_price']['request'] == 0
    assert options['provider']['allow_fallbacks'] is False
    assert 'reasoning' not in reply_options(job, 'https://openrouter.ai/api/v1')['extra_body']


def test_spending_failure_reports_budget_instead_of_generic_model_error():
    from types import SimpleNamespace
    from dan.personal.conversation import failed_reply
    reply = failed_reply(SimpleNamespace(raw_result={'error_type': 'ValueError', 'error': 'This turn has reached its reserved AI spending limit'}))
    assert 'budget' in reply
    assert 'Settings' in reply
    assert 'no action was taken' in reply
