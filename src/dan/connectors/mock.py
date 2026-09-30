"""Deterministic in-memory provider simulator, with account-scoped read-back."""
from copy import deepcopy
from threading import Lock
from dan.personal.store import Conflict


class MockCalendarAdapter:
    provider = 'mock'

    def __init__(self):
        self.events = {}
        self.operations = {}
        self.calls = 0
        self.lock = Lock()
        self.timeout_after_commit = False

    def execute(self, proposal):
        with self.lock:
            operation = proposal['id']
            if operation in self.operations:
                return
            self.calls += 1
            action = proposal['action']
            identity = action['event_id'] or operation
            key = (proposal['connection_id'], action['calendar_id'], identity)
            current = self.events.get(key)
            if action['operation'] == 'calendar.update' and (not current or current['etag'] != action['expected_etag']):
                raise Conflict('Provider event changed')
            version = current['version'] + 1 if current else 1
            self.events[key] = {'id': identity, 'etag': str(version), 'version': version, 'title': action['title'], 'all_day': action['all_day'], 'start': action['start'], 'end': action['end']}
            self.operations[operation] = (key, deepcopy(self.events[key]))
            if self.timeout_after_commit:
                raise TimeoutError('Simulated lost response after provider success')

    def reconcile(self, proposal):
        with self.lock:
            known = self.operations.get(proposal['id'])
            if not known:
                return None
            key, expected = known
            if key[0] != proposal['connection_id'] or self.events.get(key) != expected:
                return None
            return {'provider': 'mock', 'connection_id': key[0], 'calendar_id': key[1], 'resource_id': expected['id'], 'etag': expected['etag'], 'proposal_hash': proposal['hash'], 'verified': True, 'observed': deepcopy(expected)}
