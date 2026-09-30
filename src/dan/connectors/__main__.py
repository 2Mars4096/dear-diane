"""Run the account-free calendar approval/recovery demonstration with synthetic data."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from dan.personal.models import CaptureInput, ReviewInput
from dan.personal.store import PersonalStore
from .actions import CalendarAction, MockActionService
from .mock import MockCalendarAdapter


def demonstrate():
    with TemporaryDirectory(prefix='diane-mock-calendar-') as directory:
        root = Path(directory)
        personal = PersonalStore(root / 'personal')
        captured = personal.capture('operator', CaptureInput(operation_id='demo-capture-001', text='Synthetic: Meet Ada on 2026-10-03 from 14:00 to 15:00 UTC.'))['commitment']
        confirmed = personal.review('operator', captured['id'], ReviewInput(operation_id='demo-confirm-001', expected_revision=captured['revision'], title='Meet Ada', date='2026-10-03', time='14:00', timezone='UTC', decision='confirm'))
        adapter = MockCalendarAdapter()
        revision = lambda owner, identity: personal.detail(owner, identity)['commitment']['revision']
        service = MockActionService(root / 'actions', adapter, revision)
        connection = service.connect('operator', 'synthetic-account', ['calendar.create', 'calendar.update'])
        start = datetime.fromisoformat(confirmed['date'] + 'T' + confirmed['time']).replace(tzinfo=timezone.utc)
        action = CalendarAction(operation='calendar.create', calendar_id='synthetic-calendar', title=confirmed['title'], start=start, end=start + timedelta(hours=1))
        proposed = service.prepare('operator', 'demo-create-001', connection['id'], confirmed['id'], confirmed['revision'], action)
        service.approve('operator', proposed['id'], proposed['hash'], proposed['revision'])
        created = service.execute('operator', proposed['id'])
        assert created['state'] == 'completed' and len(adapter.events) == 1
        assert service.execute('operator', proposed['id']) == created
        assert adapter.calls == 1
        corrected = personal.review('operator', confirmed['id'], ReviewInput(operation_id='demo-edit-001', expected_revision=confirmed['revision'], title='Meet Ada, revised time', date='2026-10-03', time='14:30', timezone='UTC', decision='confirm'))
        update = CalendarAction(operation='calendar.update', calendar_id=action.calendar_id, title=corrected['title'], start=start + timedelta(minutes=30), end=start + timedelta(minutes=90), event_id=created['receipt']['resource_id'], expected_etag=created['receipt']['etag'])
        proposed_update = service.prepare('operator', 'demo-update-001', connection['id'], corrected['id'], corrected['revision'], update)
        service.approve('operator', proposed_update['id'], proposed_update['hash'], proposed_update['revision'])
        adapter.timeout_after_commit = True
        uncertain = service.execute('operator', proposed_update['id'])
        assert uncertain['state'] == 'unknown'
        restarted = MockActionService(root / 'actions', adapter, revision)
        recovered = restarted.execute('operator', proposed_update['id'])
        assert recovered['state'] == 'completed' and recovered['receipt']['observed']['title'] == corrected['title']
        assert len(adapter.events) == 1 and adapter.calls == 2
        return {'mode': 'synthetic_mock_only', 'real_accounts_used': False, 'passed': True,
                'steps': ['capture', 'confirm', 'prepare_exact_create', 'approve', 'create_and_read_back', 'replay_without_duplicate', 'edit_commitment', 'approve_exact_update', 'provider_success_response_lost', 'restart_and_reconcile'],
                'provider_mutations': adapter.calls, 'provider_events': len(adapter.events),
                'create_receipt': created['receipt'], 'update_receipt': recovered['receipt']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, help='Write the synthetic JSON report to this file')
    args = parser.parse_args()
    report = demonstrate()
    rendered = json.dumps(report, indent=2) + '\n'
    if args.report:
        args.report.write_text(rendered)
    print(rendered, end='')


if __name__ == '__main__':
    main()
