"""Durable bounded extraction queue; no provider work inside SQLite transactions."""
from __future__ import annotations
import json
import time
import uuid

from .store import Conflict, canonical, now
from .budgets import reserve


class ExtractionQueue:
    def __init__(self, store):
        self.store = store

    def enqueue(self, owner, record_id, operation_id, revision, model):
        with self.store.connection() as db:
            def apply():
                detail = self.store._detail(db, owner, record_id)
                record, capture = detail['commitment'], detail['capture']
                if record['revision'] != revision or record['lifecycle'] != 'draft':
                    raise Conflict('Reload an unconfirmed commitment before extracting details')
                if db.execute("SELECT 1 FROM extraction_jobs WHERE owner=? AND commitment_id=? AND state IN ('queued','running')", (owner, record_id)).fetchone():
                    raise Conflict('This commitment already has an extraction in progress')
                today = now()[:10]
                jobs = [json.loads(row[0]) for row in db.execute('SELECT body FROM extraction_jobs WHERE owner=?', (owner,))]
                if sum(job['created_at'].startswith(today) for job in jobs) >= 30:
                    raise Conflict('The pilot daily limit of 30 extraction attempts is reached; manual review remains available')
                job = {'id': str(uuid.uuid4()), 'owner': owner, 'commitment_id': record_id,
                       'base_revision': revision + 1, 'source': capture['text'], 'source_hash': capture['sha256'],
                       'locale': capture['locale'], 'model': model, 'created_at': now(), 'max_model_calls': 2,
                       'budget': reserve(db, owner), 'budget_days': [today], 'timeout_seconds': 60, 'max_output_tokens': 4096, 'run_id': None, 'error': ''}
                record.update(revision=revision + 1, updated_at=now(), extraction={'status': 'queued', 'job_id': job['id'], 'model': model})
                db.execute('INSERT INTO extraction_jobs(id,owner,commitment_id,state,body) VALUES (?,?,?,?,?)', (job['id'], owner, record_id, 'queued', canonical(job)))
                db.execute('UPDATE commitments SET revision=?,body=? WHERE id=? AND owner=?', (record['revision'], canonical(record), record_id, owner))
                db.execute('INSERT INTO business_events(owner,entity_id,kind,at) VALUES (?,?,?,?)', (owner, record_id, 'extraction_queued', now()))
                return record
            return self.store._operation(db, owner, operation_id, {'action': 'extract', 'id': record_id, 'revision': revision, 'model': model}, apply)

    def authorize_spend(self, job):
        """Recheck current limits and reserve the execution day before a paid call."""
        from decimal import Decimal
        from .budgets import policy, daily_reserved
        with self.store.connection() as db:
            row = db.execute("SELECT body FROM extraction_jobs WHERE id=? AND state='running' AND lease_token=?", (job['id'], job['lease_token'])).fetchone()
            if row is None:
                return False
            values = json.loads(row[0])
            if 'budget' not in values:
                raise Conflict('This extraction predates spending controls; request a new extraction.')
            limits = policy(db, job['owner'])
            if limits['paused']:
                raise Conflict('Automatic extraction is paused in spending settings.')
            cost = Decimal(values['budget']['reserved_usd'])
            if Decimal(limits['task_limit']) == 0 or cost > Decimal(limits['task_limit']):
                raise Conflict('Extraction paused by the current per-task budget.')
            day = now()[:10]
            days = values.setdefault('budget_days', [values['created_at'][:10]])
            used = daily_reserved(db, job['owner'], day)
            additional = Decimal(0) if day in days else cost
            if used is None or Decimal(limits['daily_limit']) == 0 or used + additional > Decimal(limits['daily_limit']):
                raise Conflict('Extraction paused by the current daily budget. Manual review and reminders remain available.')
            if day not in days:
                days.append(day)
                db.execute('UPDATE extraction_jobs SET body=? WHERE id=?', (canonical(values), job['id']))
            return True

    def claim(self):
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM extraction_jobs WHERE state='queued' OR (state='running' AND lease_until<?) ORDER BY rowid LIMIT 1", (time.time(),)).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE extraction_jobs SET state='running',lease_until=?,lease_token=? WHERE id=?", (time.time() + 120, token, row['id']))
            return {**json.loads(row['body']), 'lease_token': token, 'reconcile_only': row['state'] == 'running'}

    def bind(self, job, run):
        with self.store.connection() as db:
            row = db.execute("SELECT body FROM extraction_jobs WHERE id=? AND state='running' AND lease_token=?", (job['id'], job['lease_token'])).fetchone()
            if row is None:
                return False
            values = json.loads(row[0]); values['run_id'] = run.run_id
            db.execute('UPDATE extraction_jobs SET body=? WHERE id=?', (canonical(values), job['id']))
            record = self.store._detail(db, job['owner'], job['commitment_id'])['commitment']
            record['task_id'] = run.task_id
            record['extraction'] = {**record.get('extraction', {}), 'status': 'running', 'run_id': run.run_id}
            db.execute('UPDATE commitments SET body=? WHERE id=? AND owner=?', (canonical(record), record['id'], job['owner']))
            return True

    def finish(self, job, *, draft=None, error='', state='completed'):
        with self.store.connection() as db:
            row = db.execute("SELECT body FROM extraction_jobs WHERE id=? AND state='running' AND lease_token=?", (job['id'], job['lease_token'])).fetchone()
            if row is None:
                return  # A stopped or newer lease owns the outcome.
            values = json.loads(row[0]); values.update(error=error, draft=draft)
            record = self.store._detail(db, job['owner'], job['commitment_id'])['commitment']
            if draft and record['revision'] == job['base_revision'] and record['lifecycle'] == 'draft':
                record.update(title=draft['title'], date=draft.get('date'), time=draft.get('time'), timezone=draft.get('timezone'),
                              all_day=draft.get('all_day', False), offset=draft.get('offset'), location=draft.get('place') or draft.get('link') or '')
            elif draft:
                error = 'Your edits were kept. Review the extracted suggestions in the source details.'
            record['extraction'] = {'status': state, 'job_id': job['id'], 'model': job['model'], 'run_id': values.get('run_id'), 'draft': draft, 'error': error}
            record.update(revision=record['revision'] + 1, updated_at=now())
            db.execute('UPDATE commitments SET revision=?,body=? WHERE id=? AND owner=?', (record['revision'], canonical(record), record['id'], job['owner']))
            db.execute('UPDATE extraction_jobs SET state=?,body=?,lease_until=0 WHERE id=?', (state, canonical(values), job['id']))
            db.execute('INSERT INTO business_events(owner,entity_id,kind,at) VALUES (?,?,?,?)', (job['owner'], record['id'], 'extraction_' + state, now()))

    def stop(self, owner, record_id, operation_id, revision):
        with self.store.connection() as db:
            def apply():
                record = self.store._detail(db, owner, record_id)['commitment']
                if record['revision'] != revision:
                    raise Conflict('Reload before stopping this extraction')
                db.execute("UPDATE extraction_jobs SET state='stopped',lease_until=0 WHERE owner=? AND commitment_id=? AND state IN ('queued','running')", (owner, record_id))
                record['extraction'] = {**record.get('extraction', {}), 'status': 'stopped'}
                record.update(revision=revision + 1, updated_at=now())
                db.execute('UPDATE commitments SET revision=?,body=? WHERE owner=? AND id=?', (record['revision'], canonical(record), owner, record_id))
                db.execute('INSERT INTO business_events(owner,entity_id,kind,at) VALUES (?,?,?,?)', (owner, record_id, 'extraction_stopped', now()))
                return record
            return self.store._operation(db, owner, operation_id, {'action': 'stop_extraction', 'id': record_id, 'revision': revision}, apply)

    def active(self, job):
        with self.store.connection() as db:
            return db.execute("SELECT 1 FROM extraction_jobs WHERE id=? AND state='running' AND lease_token=?", (job['id'], job['lease_token'])).fetchone() is not None
