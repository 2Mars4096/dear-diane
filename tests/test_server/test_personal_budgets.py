import concurrent.futures
import json
from decimal import Decimal

import pytest
from dan.personal.budgets import policy, status, completion_options
from dan.personal.extraction_queue import ExtractionQueue
from dan.personal.models import CaptureInput
from dan.personal.store import PersonalStore, Conflict


def capture(store, number):
    return store.capture('operator', CaptureInput(operation_id=f'capture-budget-{number}', text=f'Meeting {number}'))['commitment']


def enqueue(store, record, number):
    return ExtractionQueue(store).enqueue('operator', record['id'], f'extract-budget-{number}', record['revision'], 'fixture')


def test_atomic_daily_admission_and_replay_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('DAN_PERSONAL_DAILY_USD', '0.40')
    store = PersonalStore(tmp_path)
    records = [capture(store, n) for n in range(2)]
    def submit(n):
        try:
            return enqueue(store, records[n], n)
        except Conflict:
            return None
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sum(result is not None for result in results) == 2
    winner = next(n for n, result in enumerate(results) if result is not None)
    restarted = PersonalStore(tmp_path)
    before = status(restarted, 'operator')
    assert enqueue(restarted, records[winner], winner) == results[winner]
    assert status(restarted, 'operator') == before
    assert before['available'] is False
    assert Decimal(before['used_reservations_usd']) == Decimal('0.40')
    loser = 1 - winner
    assert store.detail('operator', records[loser]['id'])['commitment']['revision'] == 2
    with pytest.raises(Conflict): enqueue(store, capture(store, 3), 3)
    assert status(restarted, 'another-owner')['used_reservations_usd'] == '0'


def test_stop_and_failure_do_not_refund_uncertain_cost(tmp_path):
    store = PersonalStore(tmp_path)
    record = capture(store, 1)
    queued = enqueue(store, record, 1)
    ExtractionQueue(store).stop('operator', record['id'], 'stop-budget-001', queued['revision'])
    assert status(store, 'operator')['used_reservations_usd'] == '0'  # Never dispatched.
    second = capture(store, 2)
    enqueue(store, second, 2)
    queue = ExtractionQueue(store)
    job = queue.claim()
    from dan.personal.billing import Receipts
    Receipts(store, 'extraction_jobs', 'operator', job['id']).begin('0.02')
    queue.finish(job, error='unknown outcome', state='failed')
    assert Decimal(status(store, 'operator')['used_reservations_usd']) == Decimal('0.02')


def test_rollover_reserves_execution_day_and_current_limits(tmp_path, monkeypatch):
    store = PersonalStore(tmp_path)
    enqueue(store, capture(store, 1), 1)
    queue = ExtractionQueue(store)
    job = queue.claim()
    monkeypatch.setattr('dan.personal.extraction_queue.now', lambda: '2099-01-02T00:00:00+00:00')
    monkeypatch.setattr('dan.personal.budgets.now', lambda: '2099-01-02T00:00:00+00:00')
    assert status(store, 'operator')['used_reservations_usd'] == '0'
    assert queue.authorize_spend(job)
    assert queue.authorize_spend(job)  # Repeated authorization cannot double-reserve.
    assert status(store, 'operator')['used_reservations_usd'] == policy()['reserved_usd']
    monkeypatch.setenv('DAN_PERSONAL_DAILY_USD', '0')
    with pytest.raises(Conflict, match='daily budget'):
        queue.authorize_spend(job)


def test_invalid_limits_and_unknown_legacy_cost_fail_closed(tmp_path, monkeypatch):
    store = PersonalStore(tmp_path)
    record = capture(store, 1)
    monkeypatch.setenv('DAN_PERSONAL_TASK_USD', '0')
    with pytest.raises(Conflict, match='per-task'):
        enqueue(store, record, 1)
    monkeypatch.setenv('DAN_PERSONAL_TASK_USD', 'NaN')
    with pytest.raises(ValueError):
        enqueue(store, record, 1)
    monkeypatch.delenv('DAN_PERSONAL_TASK_USD')
    enqueue(store, record, 1)
    with store.connection() as db:
        row = db.execute('SELECT id,body FROM extraction_jobs').fetchone()
        body = json.loads(row['body']); del body['budget']
        db.execute('UPDATE extraction_jobs SET body=? WHERE id=?', (json.dumps(body), row['id']))
    assert status(store, 'operator')['available'] is False
    with pytest.raises(Conflict, match='unknown'):
        enqueue(store, capture(store, 2), 2)


def test_provider_price_controls_and_unsupported_route():
    options = completion_options(policy(), 'https://openrouter.ai/api/v1/')
    prefs = options['extra_body']['provider']
    assert prefs['allow_fallbacks'] is False and prefs['require_parameters'] is True
    assert prefs['max_price'] == {'prompt': '1.00', 'completion': '5.00', 'request': 0}
    for model in ('@preset', 'vendor/model:online', 'openrouter/auto:online'):
        with pytest.raises(ValueError, match='explicit provider/model'):
            completion_options(policy(), 'https://openrouter.ai/api/v1', model)
    with pytest.raises(ValueError, match='requires OpenRouter'):
        completion_options(policy(), 'http://localhost:9999')


@pytest.mark.asyncio
async def test_input_limit_stops_before_provider(tmp_path):
    from dan.personal.extraction import extraction_brief
    from dan.server.cell_backend import BriefCellAdapter
    from dan.server.chat_v2_backend import run_agent_backend
    from dan.server.chat_v2_dispatch import reserve_dispatch
    from dan.server.chat_v2_store import ChatV2Store
    class NeverCalled:
        calls = 0
        async def complete(self, **kwargs):
            self.calls += 1
            raise AssertionError('Oversized input reached provider')
    provider = NeverCalled()
    adapter = BriefCellAdapter(extraction_brief('Meet tomorrow'), 'fixture', provider, max_input_bytes=1)
    store = ChatV2Store(tmp_path / 'runs')
    run = reserve_dispatch(store, key='input-budget', objective='fixture', thread_id='fixture')
    result = await run_agent_backend(store, run.run_id, adapter=adapter)
    assert result.status == 'failed' and provider.calls == 0


def test_saved_limits_replay_restart_conflict_and_owner_scope(tmp_path):
    from dan.personal.budgets import BudgetSettings, update_settings
    store = PersonalStore(tmp_path)
    body = BudgetSettings(operation_id='settings-one', expected_revision=0, task_limit='0.40', daily_limit='1.00', paused=True)
    saved = update_settings(store, 'operator', body)
    assert saved['revision'] == 1
    assert update_settings(store, 'operator', body) == saved
    restarted = PersonalStore(tmp_path)
    assert status(restarted, 'operator')['paused'] is True
    assert status(restarted, 'other')['paused'] is False
    with pytest.raises(Conflict, match='settings changed'):
        update_settings(store, 'operator', body.model_copy(update={'operation_id': 'settings-two'}))
    with pytest.raises(Conflict, match='maximum'):
        update_settings(store, 'operator', body.model_copy(update={'operation_id': 'settings-three', 'expected_revision': 1, 'daily_limit': Decimal('3.00')}))
    with pytest.raises(Conflict, match='paused'):
        enqueue(store, capture(store, 1), 1)
    resumed = body.model_copy(update={'operation_id': 'settings-resume', 'expected_revision': 1, 'paused': False})
    update_settings(store, 'operator', resumed)
    assert status(store, 'operator')['available'] is True


def test_host_caps_override_saved_settings_and_settings_changes_keep_reservations(tmp_path, monkeypatch):
    from dan.personal.budgets import BudgetSettings, update_settings
    store = PersonalStore(tmp_path)
    enqueue(store, capture(store, 1), 1)
    prior = status(store, 'operator')['used_reservations_usd']
    update_settings(store, 'operator', BudgetSettings(operation_id='settings-one', expected_revision=0, task_limit='0.50', daily_limit='2.00'))
    monkeypatch.setenv('DAN_PERSONAL_DAILY_USD', '0.10')
    current = status(store, 'operator')
    assert current['daily_limit'] == '0.10' and current['available'] is False
    assert current['used_reservations_usd'] == prior


def test_schema_four_backup_preserves_records_before_settings_migration(tmp_path):
    import sqlite3
    store = PersonalStore(tmp_path)
    record = capture(store, 1)
    with store.connection() as db:
        db.execute('DROP TABLE voice_operations'); db.execute('DROP TABLE conversation_jobs'); db.execute('DROP TABLE personal_settings')
        db.execute('PRAGMA user_version=4')
    reopened = PersonalStore(tmp_path)
    assert reopened.detail('operator', record['id'])['commitment']['revision'] == 1
    assert status(reopened, 'operator')['settings']['revision'] == 0
    backup = next(tmp_path.glob('state.v4.*.backup.sqlite3'))
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 4
        assert db.execute('SELECT COUNT(*) FROM commitments').fetchone()[0] == 1
