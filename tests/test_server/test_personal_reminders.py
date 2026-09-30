from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
import pytest
from dan.personal.models import CaptureInput, ReviewInput, resolved_instant
from dan.personal.reminders import Reminders, ReminderRequest
from dan.personal.store import PersonalStore, Conflict


def scheduled(tmp_path):
    store = PersonalStore(tmp_path / 'personal')
    record = store.capture('operator', CaptureInput(operation_id='capture-001', text='Test meeting'))['commitment']
    record = store.review('operator', record['id'], ReviewInput(operation_id='confirm-001', expected_revision=1, title='Test meeting', date='2099-10-02', time='14:00', timezone='Asia/Hong_Kong', decision='confirm'))
    service = Reminders(store)
    request = ReminderRequest(operation_id='reminder-001', expected_revision=2, action='schedule', date='2099-10-02', time='13:00', timezone='Asia/Hong_Kong')
    result = service.change('operator', record['id'], request)
    assert service.change('operator', record['id'], request) == result
    return store, service, result


def test_restart_and_duplicate_ticks_deliver_once_with_late_receipt(tmp_path):
    store, service, record = scheduled(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: Reminders(PersonalStore(store.directory)).deliver_due(4100000000), range(8)))
    assert sum(results) == 1
    inbox = service.inbox('operator')['notifications']
    assert len(inbox) == 1 and inbox[0]['late'] is True and inbox[0]['delivery'] == 'inbox'
    assert service.inbox('other')['notifications'] == []
    ack = service.acknowledge('operator', inbox[0]['id'], 'acknowledge-001', 1)
    assert ack['read'] and service.acknowledge('operator', inbox[0]['id'], 'acknowledge-001', 1) == ack
    assert store.detail('operator', record['id'])['commitment']['reminder']['state'] == 'delivered'


def test_pause_survives_restart_and_resume_catches_up(tmp_path):
    store, service, record = scheduled(tmp_path)
    record = service.change('operator', record['id'], ReminderRequest(operation_id='pause-001', expected_revision=3, action='pause'))
    assert Reminders(PersonalStore(store.directory)).deliver_due(4100000000) == 0
    service.change('operator', record['id'], ReminderRequest(operation_id='resume-001', expected_revision=4, action='resume'))
    assert service.deliver_due(4100000000) == 1
    with pytest.raises(Conflict):
        service.change('operator', record['id'], ReminderRequest(operation_id='stale-stop-001', expected_revision=5, action='stop'))


def test_edits_invalidate_old_timing_and_other_owner_cannot_change(tmp_path):
    store, service, record = scheduled(tmp_path)
    with pytest.raises(KeyError):
        service.change('other', record['id'], ReminderRequest(operation_id='stop-0001', expected_revision=3, action='stop'))
    updated = store.review('operator', record['id'], ReviewInput(operation_id='edit-001', expected_revision=3, title='Changed', decision='save'))
    assert updated['reminder']['state'] == 'stopped'
    assert service.deliver_due(4100000000) == 0


def test_delivery_rolls_back_before_receipt_and_replays_safely(tmp_path, monkeypatch):
    store, service, _ = scheduled(tmp_path)
    with store.connection() as db:
        db.execute("CREATE TRIGGER simulate_crash BEFORE UPDATE ON commitments BEGIN SELECT RAISE(ABORT, 'simulated crash'); END")
    with pytest.raises(Exception, match='simulated crash'):
        service.deliver_due(4100000000)
    assert service.inbox('operator')['notifications'] == []
    with store.connection() as db:
        db.execute('DROP TRIGGER simulate_crash')
    assert Reminders(PersonalStore(store.directory)).deliver_due(4100000000) == 1


def test_explicit_offset_disambiguates_fold_and_rejects_wrong_offset():
    assert resolved_instant(date(2026,11,1), time(1,30), 'America/New_York', '-05:00').hour == 6
    assert resolved_instant(date(2026,11,1), time(1,30), 'America/New_York', '-04:00').hour == 5
    with pytest.raises(ValueError):
        resolved_instant(date(2026,11,1), time(1,30), 'America/New_York', '+08:00')


def test_reschedule_atomically_replaces_old_reminder_and_replays(tmp_path):
    store, service, record = scheduled(tmp_path)
    request = ReminderRequest(operation_id='reschedule-001', expected_revision=record['revision'], action='schedule', date='2099-10-02', time='13:30', timezone='Asia/Hong_Kong')
    updated = service.change('operator', record['id'], request)
    assert service.change('operator', record['id'], request) == updated
    assert updated['reminder']['id'] != record['reminder']['id']
    old_due = resolved_instant(date(2099, 10, 2), time(13, 0), 'Asia/Hong_Kong').timestamp()
    assert service.deliver_due(old_due) == 0
    assert Reminders(PersonalStore(store.directory)).deliver_due(old_due + 1800) == 1
    assert service.inbox('operator')['notifications'][0]['id'] == updated['reminder']['id']
