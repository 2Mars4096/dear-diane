"""Business task lifecycle. User reports are explicit evidence, not provider receipts."""
from typing import Literal
import uuid
from pydantic import BaseModel, ConfigDict, Field
from .store import Conflict, canonical, now


class TaskDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    operation_id: str = Field(min_length=8, max_length=100)
    expected_revision: int = Field(ge=1)
    action: Literal['complete', 'wait', 'pause', 'resume', 'cancel']
    note: str = Field(default='', max_length=2000)


def decide(store, owner, record_id, body: TaskDecision):
    with store.connection() as db:
        def apply():
            record = store._detail(db, owner, record_id)['commitment']
            if record['revision'] != body.expected_revision:
                raise Conflict('This commitment changed; reload before updating it')
            if record['lifecycle'] != 'active':
                raise Conflict('Only active, confirmed commitments can change task state')
            if body.action in {'complete', 'wait'} and not body.note:
                raise ValueError('Describe the outcome or what you are waiting for')
            attention = record.get('attention')
            if body.action == 'resume' and attention not in {'paused', 'waiting-external'}:
                raise Conflict('This commitment is not paused or waiting')
            if body.action == 'pause' and attention == 'paused':
                raise Conflict('This commitment is already paused')
            reminder = record.get('reminder')
            if body.action in {'complete', 'cancel'}:
                record['lifecycle'] = 'fulfilled' if body.action == 'complete' else 'cancelled'
                record['attention'] = None
                record['outcome'] = {'id': str(uuid.uuid4()), 'kind': 'user_reported', 'action': body.action, 'note': body.note, 'at': now()}
                db.execute("UPDATE reminders SET state='stopped' WHERE owner=? AND commitment_id=? AND state IN ('scheduled','paused')", (owner, record_id))
                if reminder and reminder['state'] in {'scheduled', 'paused'}:
                    reminder['state'] = 'stopped'
            elif body.action == 'pause':
                record.update(attention='paused', wait_reason=body.note, task_paused_reminder=bool(reminder and reminder['state'] == 'scheduled'))
                if record['task_paused_reminder']:
                    reminder['state'] = 'paused'
                    db.execute("UPDATE reminders SET state='paused' WHERE id=? AND owner=?", (reminder['id'], owner))
            elif body.action == 'wait':
                if attention == 'paused':
                    raise Conflict('Resume this commitment before changing its wait reason')
                record.update(attention='waiting-external', wait_reason=body.note)
            else:
                record.update(attention=None, wait_reason='')
                if record.pop('task_paused_reminder', False) and reminder and reminder['state'] == 'paused':
                    reminder['state'] = 'scheduled'
                    db.execute("UPDATE reminders SET state='scheduled' WHERE id=? AND owner=?", (reminder['id'], owner))
            record.update(revision=record['revision'] + 1, updated_at=now())
            db.execute('UPDATE commitments SET revision=?,body=? WHERE id=? AND owner=?', (record['revision'], canonical(record), record_id, owner))
            db.execute('INSERT INTO business_events(owner,entity_id,kind,at) VALUES (?,?,?,?)', (owner, record_id, 'task_' + body.action, now()))
            return record
        return store._operation(db, owner, body.operation_id, {'action': 'task', 'id': record_id, **body.model_dump(mode='json')}, apply)
