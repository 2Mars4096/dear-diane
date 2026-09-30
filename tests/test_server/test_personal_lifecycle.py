import pytest
from dan.personal.models import CaptureInput, ReviewInput
from dan.personal.store import PersonalStore, Conflict
from dan.personal.lifecycle import TaskDecision, decide
from dan.personal.reminders import Reminders, ReminderRequest


def active(tmp_path):
    store = PersonalStore(tmp_path)
    record = store.capture('operator', CaptureInput(operation_id='capture-001', text='Test commitment'))['commitment']
    record = store.review('operator', record['id'], ReviewInput(operation_id='confirm-001', expected_revision=1, title='Commitment', date='2099-10-02', time='14:00', timezone='UTC', decision='confirm'))
    return store, record


def test_pause_restart_resume_and_completion_receipt(tmp_path):
    store, record = active(tmp_path)
    service = Reminders(store)
    record = service.change('operator', record['id'], ReminderRequest(operation_id='schedule-001', expected_revision=2, action='schedule', date='2099-10-02', time='13:00', timezone='UTC'))
    record = decide(store, 'operator', record['id'], TaskDecision(operation_id='pause-task-001', expected_revision=3, action='pause'))
    restarted = PersonalStore(tmp_path)
    assert Reminders(restarted).deliver_due(4100000000) == 0
    with pytest.raises(Conflict):
        store.review('operator', record['id'], ReviewInput(operation_id='paused-edit', expected_revision=4, title='Changed'))
    record = decide(restarted, 'operator', record['id'], TaskDecision(operation_id='resume-task-001', expected_revision=4, action='resume'))
    assert record['reminder']['state'] == 'scheduled'
    request = TaskDecision(operation_id='complete-task-001', expected_revision=5, action='complete', note='I attended the meeting.')
    completed = decide(restarted, 'operator', record['id'], request)
    assert decide(restarted, 'operator', record['id'], request) == completed
    assert completed['lifecycle'] == 'fulfilled' and completed['outcome']['kind'] == 'user_reported'
    assert Reminders(restarted).deliver_due(4100000000) == 0
    with pytest.raises(Conflict):
        restarted.review('operator', record['id'], ReviewInput(operation_id='closed-edit', expected_revision=6, title='Changed'))


def test_wait_requires_reason_and_events_replay_is_owner_scoped(tmp_path):
    store, record = active(tmp_path)
    snapshot = store.snapshot('operator')
    with pytest.raises(ValueError):
        decide(store, 'operator', record['id'], TaskDecision(operation_id='wait-empty-001', expected_revision=2, action='wait'))
    result = decide(store, 'operator', record['id'], TaskDecision(operation_id='wait-001', expected_revision=2, action='wait', note='Waiting for Ada to confirm.'))
    assert result['attention'] == 'waiting-external'
    events = store.events('operator', snapshot['cursor'])
    assert [event['kind'] for event in events['events']] == ['task_wait']
    assert store.events('operator', events['cursor'])['events'] == []
    assert store.events('other', 0)['events'] == []
    assert store.events('operator', 999)['reset'] is True
    with pytest.raises(KeyError):
        decide(store, 'other', record['id'], TaskDecision(operation_id='cancel-other', expected_revision=3, action='cancel'))
