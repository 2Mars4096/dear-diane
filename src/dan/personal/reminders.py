"""Deterministic reminders: scheduling and inbox delivery share one SQLite authority.

No provider call occurs here. Atomic delivery replaces a claim/lease because both
the wakeup and its visible inbox receipt commit in the same database transaction.
"""
from datetime import date as Date, time as Clock
import json
import time
import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .models import resolved_instant, ReviewInput
from .store import Conflict, canonical, now


class ReminderRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(min_length=8, max_length=100)
    expected_revision: int = Field(ge=1)
    action: Literal['schedule', 'pause', 'resume', 'stop']
    date: Date | None = None
    time: Clock | None = None
    timezone: str | None = Field(default=None, max_length=80)
    offset: str | None = Field(default=None, pattern=r'^[+-](?:0\d|1[0-4]):[0-5]\d$')

    @field_validator('timezone')
    @classmethod
    def known_zone(cls, value):
        return ReviewInput.known_zone(value)


class Reminders:
    def __init__(self, store):
        self.store = store

    def change(self, owner, record_id, body: ReminderRequest):
        with self.store.connection() as db:
            def apply():
                record = self.store._detail(db, owner, record_id)['commitment']
                if record['revision'] != body.expected_revision:
                    raise Conflict('Reload this commitment before changing its reminder')
                if record['lifecycle'] != 'active':
                    raise Conflict('Confirm the commitment before scheduling a reminder')
                if record.get('attention') == 'paused':
                    raise Conflict('Resume the commitment before changing its reminder')
                if body.action == 'schedule':
                    if not body.date or body.time is None or not body.timezone:
                        raise ValueError('Choose a reminder date, time and timezone')
                    if body.time.tzinfo or body.time.second or body.time.microsecond:
                        raise ValueError('Choose a local time with minute precision')
                    due = resolved_instant(body.date, body.time, body.timezone, body.offset)
                    if due.timestamp() <= time.time():
                        raise ValueError('Choose a reminder time in the future')
                    db.execute("UPDATE reminders SET state='stopped' WHERE owner=? AND commitment_id=? AND state IN ('scheduled','paused')", (owner, record_id))
                    reminder = {'id': str(uuid.uuid4()), 'state': 'scheduled', 'due_at': due.isoformat(),
                                'timezone': body.timezone, 'created_at': now()}
                    db.execute('INSERT INTO reminders VALUES (?,?,?,?,?,?)', (reminder['id'], owner, record_id, 'scheduled', due.timestamp(), canonical(reminder)))
                else:
                    reminder = record.get('reminder')
                    if not reminder:
                        raise Conflict('There is no reminder to change')
                    row = db.execute('SELECT state FROM reminders WHERE id=? AND owner=?', (reminder['id'], owner)).fetchone()
                    allowed = {'pause': {'scheduled'}, 'resume': {'paused'}, 'stop': {'scheduled', 'paused'}}
                    if not row or row['state'] not in allowed[body.action]:
                        raise Conflict('The reminder has already changed; reload it')
                    reminder['state'] = {'pause': 'paused', 'resume': 'scheduled', 'stop': 'stopped'}[body.action]
                    db.execute('UPDATE reminders SET state=? WHERE id=? AND owner=?', (reminder['state'], reminder['id'], owner))
                record.update(reminder=reminder, revision=record['revision'] + 1, updated_at=now())
                db.execute('UPDATE commitments SET revision=?,body=? WHERE owner=? AND id=?', (record['revision'], canonical(record), owner, record_id))
                db.execute('INSERT INTO business_events(owner,entity_id,kind,at) VALUES (?,?,?,?)', (owner, record_id, 'reminder_' + body.action, now()))
                return record
            return self.store._operation(db, owner, body.operation_id, {'action': 'reminder', 'id': record_id, **body.model_dump(mode='json')}, apply)

    def deliver_due(self, timestamp=None):
        timestamp = time.time() if timestamp is None else timestamp
        delivered = 0
        with self.store.connection() as db:
            # Bounded batches; another host tick picks up remaining overdue records.
            rows = db.execute("SELECT * FROM reminders WHERE state='scheduled' AND due<=? ORDER BY due LIMIT 100", (timestamp,)).fetchall()
            for row in rows:
                record = self.store._detail(db, row['owner'], row['commitment_id'])['commitment']
                if record.get('attention') == 'paused':
                    continue
                if record['lifecycle'] != 'active' or record.get('reminder', {}).get('id') != row['id']:
                    db.execute("UPDATE reminders SET state='stopped' WHERE id=?", (row['id'],))
                    continue
                notification = {'id': row['id'], 'revision': 1, 'commitment_id': record['id'], 'title': record['title'],
                                'due_at': record['reminder']['due_at'], 'created_at': now(), 'read': False,
                                'late': timestamp - row['due'] > 60, 'delivery': 'inbox'}
                db.execute('INSERT INTO notifications VALUES (?,?,?,?)', (row['id'], row['owner'], row['id'], canonical(notification)))
                db.execute("UPDATE reminders SET state='delivered' WHERE id=?", (row['id'],))
                record['reminder']['state'] = 'delivered'
                record.update(revision=record['revision'] + 1, updated_at=now())
                db.execute('UPDATE commitments SET revision=?,body=? WHERE id=? AND owner=?', (record['revision'], canonical(record), record['id'], row['owner']))
                db.execute('INSERT INTO business_events(owner,entity_id,kind,at) VALUES (?,?,?,?)', (row['owner'], record['id'], 'reminder_inbox', now()))
                delivered += 1
        return delivered

    def inbox(self, owner):
        with self.store.connection() as db:
            rows = db.execute('SELECT body FROM notifications WHERE owner=? ORDER BY rowid DESC LIMIT 501', (owner,)).fetchall()
            return {'notifications': [json.loads(row[0]) for row in rows[:500]], 'has_more': len(rows) > 500}

    def acknowledge(self, owner, notification_id, operation_id, revision):
        with self.store.connection() as db:
            def apply():
                row = db.execute('SELECT body FROM notifications WHERE owner=? AND id=?', (owner, notification_id)).fetchone()
                if not row:
                    raise KeyError(notification_id)
                value = json.loads(row[0])
                if value['revision'] != revision:
                    raise Conflict('This reminder changed; reload the inbox')
                value.update(read=True, revision=revision + 1)
                db.execute('UPDATE notifications SET body=? WHERE owner=? AND id=?', (canonical(value), owner, notification_id))
                db.execute('INSERT INTO business_events(owner,entity_id,kind,at) VALUES (?,?,?,?)', (owner, value['commitment_id'], 'reminder_read', now()))
                return value
            return self.store._operation(db, owner, operation_id, {'action': 'ack', 'id': notification_id, 'revision': revision}, apply)
