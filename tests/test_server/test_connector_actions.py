from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from dan.connectors.actions import CalendarAction, MockActionService
from dan.connectors.mock import MockCalendarAdapter
from dan.personal.store import Conflict


def setup(tmp_path):
    adapter = MockCalendarAdapter()
    revision = {'value': 1}
    clock = {'value': 1000}
    service = MockActionService(tmp_path, adapter, lambda owner, identity: revision['value'], lambda: clock['value'])
    connection = service.connect('owner', 'account-one', ['calendar.create', 'calendar.update'])
    action = CalendarAction(operation='calendar.create', calendar_id='personal', title='Meeting', start='2026-10-03T14:00:00Z', end='2026-10-03T15:00:00Z')
    proposal = service.prepare('owner', 'operation-one', connection['id'], 'commitment', 1, action)
    return service, adapter, revision, clock, connection, action, proposal


def approve(service, proposal):
    return service.approve('owner', proposal['id'], proposal['hash'], proposal['revision'])


def test_exact_approval_and_concurrent_replay_make_one_event(tmp_path):
    service, adapter, _, _, connection, action, proposal = setup(tmp_path)
    assert service.prepare('owner', 'operation-one', connection['id'], 'commitment', 1, action) == proposal
    with pytest.raises(Conflict):
        service.execute('owner', proposal['id'])
    approved = approve(service, proposal)
    assert approve(service, proposal) == approved
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: service.execute('owner', proposal['id']), range(2)))
    receipt = service.execute('owner', proposal['id'])
    assert receipt['state'] == 'completed'
    assert receipt['receipt']['verified'] and adapter.calls == 1
    assert len(adapter.events) == 1
    with pytest.raises(Conflict):
        service.prepare('owner', 'operation-one', connection['id'], 'commitment', 1, action.model_copy(update={'title': 'Changed'}))


def test_stale_expired_revoked_and_cross_owner_cannot_write(tmp_path):
    service, adapter, revision, clock, connection, _, proposal = setup(tmp_path)
    with pytest.raises(KeyError):
        service.approve('other', proposal['id'], proposal['hash'], 1)
    with pytest.raises(Conflict):
        service.approve('owner', proposal['id'], 'wrong-hash', 1)
    approve(service, proposal)
    revision['value'] = 2
    with pytest.raises(Conflict, match='Commitment changed'):
        service.execute('owner', proposal['id'])
    revision['value'] = 1; clock['value'] = 2000
    with pytest.raises(Conflict, match='expired'):
        service.execute('owner', proposal['id'])
    clock['value'] = 1000
    service.disconnect('owner', connection['id'])
    with pytest.raises(Conflict, match='revoked'):
        service.execute('owner', proposal['id'])
    assert adapter.calls == 0


def test_provider_success_lost_response_reconciles_even_after_expiry(tmp_path):
    service, adapter, revision, clock, _, _, proposal = setup(tmp_path)
    approve(service, proposal)
    adapter.timeout_after_commit = True
    assert service.execute('owner', proposal['id'])['state'] == 'unknown'
    revision['value'] = 2; clock['value'] = 2000
    assert service.execute('owner', proposal['id'])['state'] == 'completed'
    assert adapter.calls == 1


def test_receipt_save_failure_survives_service_restart(tmp_path, monkeypatch):
    service, adapter, revision, clock, _, _, proposal = setup(tmp_path)
    approve(service, proposal)
    monkeypatch.setattr(service, '_receipt', lambda *args: (_ for _ in ()).throw(OSError('simulated disk failure')))
    assert service.execute('owner', proposal['id'])['state'] == 'unknown'
    recovered = MockActionService(tmp_path, adapter, lambda *args: revision['value'], lambda: clock['value'])
    assert recovered.execute('owner', proposal['id'])['receipt']['resource_id'] == proposal['id']
    assert adapter.calls == 1


def test_changed_etag_and_wrong_account_never_update(tmp_path):
    service, adapter, _, _, connection, action, proposal = setup(tmp_path)
    approve(service, proposal); service.execute('owner', proposal['id'])
    update = action.model_copy(update={'operation': 'calendar.update', 'event_id': proposal['id'], 'expected_etag': 'stale', 'title': 'Changed'})
    second = service.prepare('owner', 'update-one', connection['id'], 'commitment', 1, update)
    approve(service, second)
    assert service.execute('owner', second['id'])['state'] == 'conflict'
    other = service.connect('owner', 'account-two', ['calendar.update'])
    third = service.prepare('owner', 'update-two', other['id'], 'commitment', 1, update.model_copy(update={'expected_etag': '1'}))
    approve(service, third)
    assert service.execute('owner', third['id'])['state'] == 'conflict'
    assert next(iter(adapter.events.values()))['title'] == 'Meeting'
    assert len(adapter.events) == 1


def test_changed_payload_detected_and_unverified_readback_not_completed(tmp_path, monkeypatch):
    service, adapter, _, _, _, _, proposal = setup(tmp_path)
    approve(service, proposal)
    monkeypatch.setattr(adapter, 'reconcile', lambda proposal: {'verified': True, 'proposal_hash': proposal['hash'], 'connection_id': proposal['connection_id'], 'observed': {'title': 'unrelated'}})
    assert service.execute('owner', proposal['id'])['state'] == 'unknown'
    with service.db() as db:
        changed = service._load(db, 'proposals', 'owner', proposal['id'])
        changed['action']['title'] = 'Tampered'
        service._save(db, changed)
    with pytest.raises(Conflict, match='approved hash'):
        service.execute('owner', proposal['id'])
    assert adapter.calls == 1


def test_explicit_end_no_attendees_and_real_adapter_gate(tmp_path):
    with pytest.raises(ValidationError):
        CalendarAction(operation='calendar.create', calendar_id='c', title='t', start='2026-10-03T14:00:00Z', end='2026-10-03T13:00:00Z')
    with pytest.raises(ValidationError):
        CalendarAction(operation='calendar.create', calendar_id='c', title='t', start='2026-10-03T14:00:00Z', end='2026-10-03T15:00:00Z', attendees=['x@example.org'])
    adapter = MockCalendarAdapter(); adapter.provider = 'google'
    with pytest.raises(ValueError, match='disabled'):
        MockActionService(tmp_path, adapter, lambda *args: 1)


def test_approved_update_reads_back_one_existing_event(tmp_path):
    service, adapter, _, _, connection, action, proposal = setup(tmp_path)
    approve(service, proposal)
    initial = service.execute('owner', proposal['id'])['receipt']
    update = action.model_copy(update={'operation': 'calendar.update', 'event_id': initial['resource_id'], 'expected_etag': initial['etag'], 'title': 'Revised meeting'})
    second = service.prepare('owner', 'valid-update', connection['id'], 'commitment', 1, update)
    approve(service, second)
    result = service.execute('owner', second['id'])
    assert result['receipt']['observed']['title'] == 'Revised meeting'
    assert result['receipt']['etag'] == '2'
    assert service.execute('owner', second['id']) == result
    assert adapter.calls == 2 and len(adapter.events) == 1


def test_rejection_revokes_unconsumed_approval_and_preserves_decisions(tmp_path):
    service, adapter, _, _, _, _, proposal = setup(tmp_path)
    approved = approve(service, proposal)
    rejected = service.reject('owner', proposal['id'], proposal['hash'], approved['revision'])
    assert service.reject('owner', proposal['id'], proposal['hash'], approved['revision']) == rejected
    assert [item['decision'] for item in rejected['decisions']] == ['approve', 'reject']
    with pytest.raises(Conflict):
        service.execute('owner', proposal['id'])
    assert adapter.calls == 0


@pytest.mark.parametrize('field,value', [('calendar_id', 'unrelated-calendar'), ('resource_id', 'unrelated-resource'), ('etag', 'unobserved-revision')])
def test_receipt_identity_must_match_read_back(tmp_path, monkeypatch, field, value):
    service, adapter, _, _, _, _, proposal = setup(tmp_path)
    approve(service, proposal)
    original = adapter.reconcile
    monkeypatch.setattr(adapter, 'reconcile', lambda proposed: {**original(proposed), field: value})
    assert service.execute('owner', proposal['id'])['state'] == 'unknown'
    assert adapter.calls == 1


def test_synthetic_full_workflow_demonstration():
    from dan.connectors.__main__ import demonstrate
    report = demonstrate()
    assert report['passed'] and report['real_accounts_used'] is False
    assert report['provider_mutations'] == 2 and report['provider_events'] == 1
    assert report['create_receipt']['resource_id'] == report['update_receipt']['resource_id']


def test_all_day_actions_preserve_exclusive_end_without_invented_times(tmp_path):
    service, adapter, _, _, connection, _, _ = setup(tmp_path)
    action = CalendarAction(operation='calendar.create', calendar_id='personal', title='Conference', all_day=True, start='2026-10-03', end='2026-10-05')
    proposal = service.prepare('owner', 'all-day-create', connection['id'], 'commitment', 1, action)
    assert proposal['action']['start'] == '2026-10-03'
    assert proposal['action']['end'] == '2026-10-05'
    approve(service, proposal)
    receipt = service.execute('owner', proposal['id'])['receipt']
    assert receipt['observed']['all_day'] is True
    assert receipt['observed']['end'] == '2026-10-05'
    update = CalendarAction(operation='calendar.update', calendar_id='personal', title='Conference', all_day=True, start='2026-10-03', end='2026-10-06', event_id=receipt['resource_id'], expected_etag=receipt['etag'])
    next_proposal = service.prepare('owner', 'all-day-update', connection['id'], 'commitment', 1, update)
    approve(service, next_proposal)
    assert service.execute('owner', next_proposal['id'])['receipt']['observed']['end'] == '2026-10-06'
    assert len(adapter.events) == 1


@pytest.mark.parametrize('all_day,start,end', [(True, '2026-10-03T14:00:00Z', '2026-10-04'), (True, '2026-10-03', '2026-10-03'), (False, '2026-10-03', '2026-10-04')])
def test_mixed_or_ambiguous_calendar_time_shapes_rejected(all_day, start, end):
    with pytest.raises(ValidationError):
        CalendarAction(operation='calendar.create', calendar_id='c', title='t', all_day=all_day, start=start, end=end)
