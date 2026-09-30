"""Mock-only approval ledger; NOT an authentication or credential boundary.

The future isolated broker must authenticate user decisions before calling this
core. No approval HTTP endpoint or real connector is exposed by this module.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from datetime import datetime, date
from typing import Literal, Protocol, Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from dan.personal.store import Conflict, canonical


class CalendarAdapter(Protocol):
    provider: str

    def execute(self, proposal: dict) -> None: ...
    def reconcile(self, proposal: dict) -> dict | None: ...


class CalendarAction(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    operation: Literal['calendar.create', 'calendar.update']
    calendar_id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=240)
    all_day: bool = False
    start: datetime | date
    end: datetime | date
    event_id: str | None = Field(default=None, max_length=500)
    expected_etag: str | None = Field(default=None, max_length=500)

    @field_validator('start', 'end', mode='before')
    @classmethod
    def parse_time(cls, value, info):
        if info.data.get('all_day', False):
            if type(value) is date:
                return value
            if isinstance(value, str):
                return date.fromisoformat(value)
            raise ValueError('All-day actions require date-only start and exclusive end dates')
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        raise ValueError('Timed actions require explicit zoned timestamps')

    @model_validator(mode='after')
    def valid(self):
        if self.all_day:
            if type(self.start) is not date or type(self.end) is not date or self.end <= self.start:
                raise ValueError('Supply all-day start and exclusive end dates, with end after start')
        elif not isinstance(self.start, datetime) or not isinstance(self.end, datetime) or self.start.utcoffset() is None or self.end.utcoffset() is None or self.end <= self.start:
            raise ValueError('Supply explicit zoned start/end times, with end after start')
        if self.operation == 'calendar.update' and (not self.event_id or not self.expected_etag):
            raise ValueError('Updates need a provider event ID and current ETag')
        if self.operation == 'calendar.create' and (self.event_id or self.expected_etag):
            raise ValueError('Create does not accept an existing event identity')
        return self


class MockActionService:
    def __init__(self, directory: Path, adapter: CalendarAdapter, current_revision: Callable[[str, str], int], clock=time.time):
        if adapter.provider != 'mock':
            raise ValueError('Real connectors require the isolated broker and are disabled')
        self.adapter, self.current_revision, self.clock = adapter, current_revision, clock
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / 'mock-actions.sqlite3'
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600); os.close(descriptor)
        self.path.chmod(0o600)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, owner TEXT NOT NULL, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, owner TEXT NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(owner,operation_id))')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE'); yield db; db.commit()
        except BaseException:
            db.rollback(); raise
        finally:
            db.close()

    def connect(self, owner, account_id, scopes):
        identity = str(uuid.uuid4())
        body = {'id': identity, 'owner': owner, 'account_id': account_id, 'provider': 'mock', 'schema_version': 1, 'revision': 1, 'active': True, 'scopes': sorted(set(scopes))}
        with self.db() as db:
            db.execute('INSERT INTO connections VALUES (?,?,?)', (identity, owner, canonical(body)))
        return body

    def _load(self, db, table, owner, identity):
        assert table in {'connections', 'proposals'}
        row = db.execute(f'SELECT body FROM {table} WHERE owner=? AND id=?', (owner, identity)).fetchone()
        if not row:
            raise KeyError(identity)
        return json.loads(row['body'])

    def _save(self, db, proposal):
        db.execute('UPDATE proposals SET body=? WHERE owner=? AND id=?', (canonical(proposal), proposal['owner'], proposal['id']))

    def disconnect(self, owner, connection_id):
        with self.db() as db:
            connection = self._load(db, 'connections', owner, connection_id)
            connection.update(active=False, revision=connection['revision'] + 1)
            db.execute('UPDATE connections SET body=? WHERE owner=? AND id=?', (canonical(connection), owner, connection_id))

    def prepare(self, owner, operation_id, connection_id, commitment_id, revision, action: CalendarAction, ttl=300):
        action = CalendarAction.model_validate(action.model_dump())
        if not 1 <= ttl <= 3600:
            raise ValueError('Approval expiry must be between 1 and 3600 seconds')
        request = {'connection_id': connection_id, 'commitment_id': commitment_id, 'input_revision': revision, 'action': action.model_dump(mode='json'), 'ttl': ttl}
        request_hash = hashlib.sha256(canonical(request).encode()).hexdigest()
        with self.db() as db:
            prior = db.execute('SELECT request_hash,body FROM proposals WHERE owner=? AND operation_id=?', (owner, operation_id)).fetchone()
            if prior:
                if prior['request_hash'] != request_hash:
                    raise Conflict('Operation ID already binds a different proposal')
                return json.loads(prior['body'])
            connection = self._load(db, 'connections', owner, connection_id)
            if not connection['active'] or action.operation not in connection['scopes']:
                raise Conflict('Connection is inactive or lacks the requested scope')
            if self.current_revision(owner, commitment_id) != revision:
                raise Conflict('Commitment changed before preparation')
            payload = {**request, 'owner': owner, 'account_id': connection['account_id'], 'connection_revision': connection['revision'], 'expires_at': self.clock() + ttl}
            digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
            proposal = {**payload, 'id': str(uuid.uuid4()), 'hash': digest, 'state': 'prepared', 'schema_version': 1, 'revision': 1, 'created_at': self.clock(), 'receipt': None, 'decisions': []}
            db.execute('INSERT INTO proposals VALUES (?,?,?,?,?)', (proposal['id'], owner, operation_id, request_hash, canonical(proposal)))
            return proposal

    def _validate(self, db, proposal, *, read_only=False):
        payload = {key: proposal[key] for key in ('connection_id', 'commitment_id', 'input_revision', 'action', 'ttl', 'owner', 'account_id', 'connection_revision', 'expires_at')}
        if hashlib.sha256(canonical(payload).encode()).hexdigest() != proposal['hash']:
            raise Conflict('Proposal parameters no longer match the approved hash')
        connection = self._load(db, 'connections', proposal['owner'], proposal['connection_id'])
        if not connection['active'] or connection['revision'] != proposal['connection_revision'] or proposal['action']['operation'] not in connection['scopes']:
            raise Conflict('Connection was revoked or its permissions changed')
        if not read_only and self.clock() >= proposal['expires_at']:
            raise Conflict('Approval expired; prepare a new proposal')
        if not read_only and self.current_revision(proposal['owner'], proposal['commitment_id']) != proposal['input_revision']:
            raise Conflict('Commitment changed; prepare a new proposal')

    def approve(self, owner, proposal_id, digest, revision):
        """Caller must eventually be the authenticated control plane, never an agent tool."""
        with self.db() as db:
            proposal = self._load(db, 'proposals', owner, proposal_id)
            if digest != proposal['hash']:
                raise Conflict('Approval does not match the exact proposal')
            self._validate(db, proposal)
            if proposal['state'] == 'approved' and revision == proposal['revision'] - 1:
                return proposal
            if proposal['state'] != 'prepared' or proposal['revision'] != revision:
                raise Conflict('Proposal is no longer awaiting this decision')
            proposal.update(state='approved', revision=revision + 1, approved_at=self.clock(), approval={'actor': owner, 'decision': 'approve', 'hash': digest, 'expires_at': proposal['expires_at'], 'source': 'mock_control_plane'})
            proposal['decisions'].append({**proposal['approval'], 'at': proposal['approved_at']})
            self._save(db, proposal)
            return proposal

    def reject(self, owner, proposal_id, digest, revision):
        with self.db() as db:
            proposal = self._load(db, 'proposals', owner, proposal_id)
            if digest != proposal['hash']:
                raise Conflict('Decision does not match the exact proposal')
            if proposal['state'] == 'rejected' and revision == proposal['revision'] - 1:
                return proposal
            if proposal['state'] not in {'prepared', 'approved'} or proposal['revision'] != revision:
                raise Conflict('This action can no longer be rejected before execution')
            proposal.update(state='rejected', revision=revision + 1)
            proposal['decisions'].append({'actor': owner, 'decision': 'reject', 'hash': digest, 'at': self.clock(), 'source': 'mock_control_plane'})
            self._save(db, proposal)
            return proposal

    def inspect(self, owner, proposal_id):
        with self.db() as db:
            return self._load(db, 'proposals', owner, proposal_id)

    def _receipt(self, owner, proposal_id, receipt):
        with self.db() as db:
            proposal = self._load(db, 'proposals', owner, proposal_id)
            if proposal['state'] == 'completed':
                return proposal
            proposal.update(state='completed', receipt=receipt, revision=proposal['revision'] + 1)
            self._save(db, proposal)
            return proposal

    @staticmethod
    def _verified_receipt(proposal, receipt):
        if not receipt or not isinstance(receipt, dict):
            return False
        observed, action = receipt.get('observed'), proposal['action']
        if not isinstance(observed, dict):
            return False
        resource = receipt.get('resource_id')
        etag = receipt.get('etag')
        return (receipt.get('verified') is True and receipt.get('provider') == 'mock'
                and receipt.get('proposal_hash') == proposal['hash']
                and receipt.get('connection_id') == proposal['connection_id']
                and receipt.get('calendar_id') == action['calendar_id']
                and isinstance(resource, str) and bool(resource)
                and observed.get('id') == resource
                and isinstance(etag, str) and bool(etag) and observed.get('etag') == etag
                and (action['operation'] == 'calendar.create' or resource == action['event_id'])
                and all(observed.get(key) == action[key] for key in ('title', 'start', 'end', 'all_day')))

    def execute(self, owner, proposal_id):
        with self.db() as db:
            proposal = self._load(db, 'proposals', owner, proposal_id)
            if proposal['state'] == 'completed':
                return proposal  # Reading an old receipt performs no external action.
            self._validate(db, proposal, read_only=proposal['state'] in {'executing', 'unknown'})
            if proposal['state'] not in {'approved', 'executing', 'unknown'}:
                raise Conflict('The exact action has not been approved')
            reconcile = proposal['state'] in {'executing', 'unknown'}
            proposal.update(state='executing', revision=proposal['revision'] + 1)
            self._save(db, proposal)  # Intent is durable before contacting the adapter.
        outcome = 'unknown'
        try:
            if not reconcile:
                self.adapter.execute(proposal)
            receipt = self.adapter.reconcile(proposal)
            if self._verified_receipt(proposal, receipt):
                return self._receipt(owner, proposal_id, receipt)
        except Conflict:
            outcome = 'conflict'
        except Exception:
            # A provider may have committed before a timeout. Never blindly replay.
            pass
        with self.db() as db:
            latest = self._load(db, 'proposals', owner, proposal_id)
            if latest['state'] not in {'completed', 'conflict', 'rejected'}:
                latest.update(state=outcome, revision=latest['revision'] + 1)
                self._save(db, latest)
            return latest
