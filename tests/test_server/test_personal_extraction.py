import json
import sqlite3
from types import SimpleNamespace

import pytest
from dan.personal.extraction import validate_extraction
from dan.personal.extraction_queue import ExtractionQueue
from dan.personal.models import CaptureInput, ReviewInput
from dan.personal.runner import PersonalRunner, redact_source
from dan.personal.store import PersonalStore
from dan.providers import CompletionResult
from dan.server.chat_v2_store import ChatV2Store

SOURCE = 'Coffee with Ada on 2026-10-02 at 14:00 Asia/Hong_Kong.'
DRAFT = {'title': 'Coffee with Ada', 'intent': 'invitation', 'date': '2026-10-02', 'time': '14:00', 'timezone': 'Asia/Hong_Kong',
         'anchors': [{'field': field, 'quote': SOURCE} for field in ('title', 'intent', 'date', 'time', 'timezone')]}


@pytest.fixture(autouse=True)
def configured_budget_provider(monkeypatch):
    monkeypatch.setattr('dan.personal.runner.resolve_config', lambda: {'api_key': 'fixture', 'base_url': 'https://openrouter.ai/api/v1'})


def setup(tmp_path):
    store = PersonalStore(tmp_path / 'personal')
    record = store.capture('operator', CaptureInput(operation_id='capture-001', text=SOURCE))['commitment']
    queue = ExtractionQueue(store)
    queue.enqueue('operator', record['id'], 'extract-001', record['revision'], 'fixture/model')
    job = queue.claim()
    assert queue.claim() is None
    app = SimpleNamespace(state=SimpleNamespace(chat_v2_store=ChatV2Store(tmp_path / 'runs')))
    return store, queue, job, PersonalRunner(app)


class Provider:
    def __init__(self): self.calls = 0
    async def complete(self, **kwargs):
        self.calls += 1
        assert kwargs.get('tools') is None
        assert kwargs['max_tokens'] == 4096
        assert kwargs['extra_body']['provider']['max_price']['request'] == 0
        return CompletionResult(text=json.dumps(DRAFT), usage={'prompt_tokens': 120, 'completion_tokens': 80})


@pytest.mark.asyncio
async def test_worker_uses_existing_cell_and_v2_and_never_confirms_automatically(tmp_path, monkeypatch):
    store, queue, job, runner = setup(tmp_path)
    provider = Provider()
    monkeypatch.setattr('dan.personal.runner.build_gateway_backed_live_provider', lambda *a, **kw: provider)
    await runner.process(queue, job)
    record = store.detail('operator', job['commitment_id'])['commitment']
    assert record['date'] == '2026-10-02' and record['lifecycle'] == 'draft'
    assert record['extraction']['status'] == 'completed'
    assert record['task_id']
    run = runner.app.state.chat_v2_store.get_run(record['extraction']['run_id'])
    assert run.status == 'completed' and run.metadata['backend_result']['raw_result']['model_calls'] == 1
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_crash_after_v2_result_recovers_without_another_model_call(tmp_path, monkeypatch):
    store, queue, job, runner = setup(tmp_path)
    provider = Provider()
    monkeypatch.setattr('dan.personal.runner.build_gateway_backed_live_provider', lambda *a, **kw: provider)
    finish = queue.finish
    monkeypatch.setattr(queue, 'finish', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('crash before personal receipt')))
    with pytest.raises(RuntimeError):
        await runner.process(queue, job)
    with store.connection() as db:
        db.execute('UPDATE extraction_jobs SET lease_until=0')
    monkeypatch.setattr(queue, 'finish', finish)
    recovery = queue.claim()
    assert recovery['reconcile_only'] is True
    await runner.process(queue, recovery)
    assert provider.calls == 1
    assert store.detail('operator', job['commitment_id'])['commitment']['extraction']['status'] == 'completed'


@pytest.mark.asyncio
async def test_stop_and_edits_win_over_extraction(tmp_path, monkeypatch):
    store, queue, job, runner = setup(tmp_path)
    provider = Provider()
    monkeypatch.setattr('dan.personal.runner.build_gateway_backed_live_provider', lambda *a, **kw: provider)
    record = store.review('operator', job['commitment_id'], ReviewInput(operation_id='review-001', expected_revision=2, title='My correction', decision='save'))
    await runner.process(queue, job)
    result = store.detail('operator', record['id'])['commitment']
    assert result['title'] == 'My correction'
    assert result['date'] is None and result['extraction']['draft']['date'] == '2026-10-02'
    assert 'edits were kept' in result['extraction']['error']
    queue.enqueue('operator', record['id'], 'extract-002', result['revision'], 'fixture/model')
    stopped_job = queue.claim()
    queue.stop('operator', record['id'], 'stop-0001', result['revision'] + 1)
    await runner.process(queue, stopped_job)
    assert provider.calls == 1
    assert store.detail('operator', record['id'])['commitment']['extraction']['status'] == 'stopped'


def test_source_grounding_year_numeric_ambiguity_and_secret_redaction():
    with pytest.raises(ValueError, match='absent'):
        validate_extraction({**DRAFT, 'anchors': [{'field': 'title', 'quote': 'Invented'}]}, SOURCE)
    missing_year = 'Coffee October 2 at 14:00 Asia/Hong_Kong'
    candidate = {**DRAFT, 'anchors': [{'field': field, 'quote': missing_year} for field in ('title','intent','date','time','timezone')]}
    assert validate_extraction(candidate, missing_year).date is None
    ambiguous = 'Meet on 03/04/2027 at 14:00 UTC'
    candidate = {**DRAFT, 'date': '2027-03-04', 'timezone': 'UTC', 'anchors': [{'field': field, 'quote': ambiguous} for field in ('title','intent','date','time','timezone')]}
    assert validate_extraction(candidate, ambiguous).date is None
    redacted = redact_source('verification code: 123456 password: secretvalue Bearer abc1234567890')
    assert '123456' not in redacted and 'secretvalue' not in redacted and 'abc1234567890' not in redacted


def test_v1_migration_backs_up_records_and_rejects_future_schema(tmp_path):
    store = PersonalStore(tmp_path / 'personal')
    record = store.capture('operator', CaptureInput(operation_id='capture-001', text='Hello'))['commitment']
    with store.connection() as db:
        db.execute('DROP TABLE voice_operations'); db.execute('DROP TABLE conversation_jobs'); db.execute('DROP TABLE personal_settings'); db.execute('DROP TABLE sources'); db.execute('DROP TABLE notifications'); db.execute('DROP TABLE reminders')
        db.execute('DROP TABLE extraction_jobs'); db.execute('PRAGMA user_version=1')
    migrated = PersonalStore(tmp_path / 'personal')
    assert migrated.detail('operator', record['id'])['capture']['text'] == 'Hello'
    backup = next((tmp_path / 'personal').glob('*.backup.sqlite3'))
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM commitments').fetchone()[0] == 1
    with migrated.connection() as db: db.execute('PRAGMA user_version=999')
    with pytest.raises(RuntimeError, match='newer schema'): PersonalStore(tmp_path / 'personal')

@pytest.mark.asyncio
async def test_stopping_an_active_model_call_cancels_it_without_applying_result(tmp_path, monkeypatch):
    import asyncio
    store, queue, job, runner = setup(tmp_path)
    started, cancelled = asyncio.Event(), asyncio.Event()
    class SlowProvider:
        async def complete(self, **kwargs):
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()
    monkeypatch.setattr('dan.personal.runner.build_gateway_backed_live_provider', lambda *a, **kw: SlowProvider())
    work = asyncio.create_task(runner.process(queue, job))
    await asyncio.wait_for(started.wait(), 2)
    queue.stop('operator', job['commitment_id'], 'stop-active-001', 2)
    await asyncio.wait_for(work, 2)
    assert cancelled.is_set()
    record = store.detail('operator', job['commitment_id'])['commitment']
    assert record['date'] is None and record['extraction']['status'] == 'stopped'


@pytest.mark.asyncio
async def test_cell_retry_policy_is_scoped_and_has_no_hidden_provider_retries(tmp_path):
    from dan.providers.retrying_provider import RetryingLLMProvider, ProviderRetryPolicy
    from dan.server.cell_backend import BriefCellAdapter
    from dan.personal.extraction import extraction_brief
    from dan.server.chat_v2_backend import run_agent_backend
    from dan.server.chat_v2_dispatch import reserve_dispatch
    class Unavailable:
        calls = 0
        async def complete(self, **kwargs):
            self.calls += 1
            raise TimeoutError('provider timed out')
    underlying = Unavailable()
    wrapper = RetryingLLMProvider(underlying, policy=ProviderRetryPolicy(max_attempts=3))
    adapter = BriefCellAdapter(extraction_brief(SOURCE), 'fixture', wrapper)
    assert wrapper._policy.max_attempts == 3
    store = ChatV2Store(tmp_path / 'runs')
    run = reserve_dispatch(store, key='bounded-retry', objective='fixture', thread_id='fixture')
    result = await run_agent_backend(store, run.run_id, adapter=adapter)
    assert result.status == 'failed' and underlying.calls == 1


def test_alias_normalization_and_anchor_schema_trigger_existing_repair():
    from dan.personal.extraction import extraction_brief
    from jsonschema import Draft202012Validator
    source = 'Meet on 2026-10-03 at 10:00 UTC.'
    proposal = {**DRAFT, 'timezone': 'Etc/UTC', 'anchors': [{'field': field, 'quote': source} for field in ('title','intent','date','time','timezone')]}
    assert validate_extraction(proposal, source).timezone == 'UTC'
    proposal['anchors'] = []
    validator = Draft202012Validator(extraction_brief(source).output_contract.output_schema)
    assert list(validator.iter_errors(proposal))


def test_cancellation_requires_explicit_new_commitment_choice(tmp_path):
    store, queue, job, _ = setup(tmp_path)
    proposal = {**DRAFT, 'intent': 'cancellation'}
    queue.finish(job, draft=proposal)
    record = store.detail('operator', job['commitment_id'])['commitment']
    values = dict(operation_id='confirm-cancel-001', expected_revision=record['revision'], title='Meeting', date='2026-10-02', time='14:00', timezone='UTC', decision='confirm')
    with pytest.raises(ValueError, match='clear new commitment'):
        store.review('operator', record['id'], ReviewInput(**values))
    confirmed = store.review('operator', record['id'], ReviewInput(**values, confirm_as_new=True))
    assert confirmed['lifecycle'] == 'active'
    assert 'explicit_new_commitment' in [event['kind'] for event in store.detail('operator', record['id'])['history']]


@pytest.mark.asyncio
async def test_dismissal_stops_queued_extraction_before_provider_call(tmp_path, monkeypatch):
    store, queue, job, runner = setup(tmp_path)
    provider = Provider()
    monkeypatch.setattr('dan.personal.runner.build_gateway_backed_live_provider', lambda *a, **kw: provider)
    result = store.review('operator', job['commitment_id'], ReviewInput(operation_id='dismiss-queued-001', expected_revision=2, title='Dismissed', decision='dismiss'))
    await runner.process(queue, job)
    assert result['extraction']['status'] == 'stopped' and provider.calls == 0


@pytest.mark.asyncio
async def test_claim_failure_does_not_kill_extraction_loop(tmp_path, monkeypatch):
    import asyncio
    _, queue, _, runner = setup(tmp_path)
    attempts = 0
    real_sleep = asyncio.sleep
    def claim():
        nonlocal attempts
        attempts += 1
        if attempts == 1: raise RuntimeError('database temporarily unavailable')
        raise asyncio.CancelledError()
    monkeypatch.setenv('DAN_PERSONAL_ENABLED', '1')
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path / 'isolated-graphs'))
    monkeypatch.setattr('dan.personal.runner.ExtractionQueue', lambda *args: queue)
    monkeypatch.setattr(queue, 'claim', claim)
    monkeypatch.setattr('dan.personal.runner.asyncio.sleep', lambda seconds: real_sleep(0))
    with pytest.raises(asyncio.CancelledError):
        await runner.run()
    assert attempts == 2

@pytest.mark.asyncio
async def test_unsupported_budget_route_stops_run_without_provider_call(tmp_path, monkeypatch):
    store, queue, job, runner = setup(tmp_path)
    provider = Provider()
    monkeypatch.setattr('dan.personal.runner.resolve_config', lambda: {'api_key': 'fixture', 'base_url': 'http://localhost:9999'})
    monkeypatch.setattr('dan.personal.runner.build_gateway_backed_live_provider', lambda *a, **kw: provider)
    await runner.process(queue, job)
    record = store.detail('operator', job['commitment_id'])['commitment']
    assert record['extraction']['status'] == 'blocked'
    assert 'requires OpenRouter' in record['extraction']['error']
    assert provider.calls == 0
    run = runner.app.state.chat_v2_store.get_run(record['extraction']['run_id'])
    assert run.status == 'stopped'

@pytest.mark.asyncio
async def test_pause_between_model_response_and_repair_prevents_second_paid_call(tmp_path, monkeypatch):
    from dan.personal.budgets import BudgetSettings, update_settings, status
    store, queue, job, runner = setup(tmp_path)
    class PauseAfterResponse:
        calls = 0
        async def complete(self, **kwargs):
            self.calls += 1
            update_settings(store, 'operator', BudgetSettings(operation_id='pause-between-calls', expected_revision=0, task_limit='0.50', daily_limit='2.00', paused=True))
            return CompletionResult(text='not a valid structured extraction', usage={'prompt_tokens': 120, 'completion_tokens': 10})
    provider = PauseAfterResponse()
    monkeypatch.setattr('dan.personal.runner.build_gateway_backed_live_provider', lambda *a, **kw: provider)
    await runner.process(queue, job)
    assert provider.calls == 1
    assert status(store, 'operator')['paused'] is True
    assert store.detail('operator', job['commitment_id'])['commitment']['extraction']['status'] == 'failed'
