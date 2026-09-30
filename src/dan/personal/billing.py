"""Durable per-request provider receipts. Unknown charges remain explicit holds."""
import json
import uuid
import time
from decimal import Decimal, InvalidOperation
import httpx
from .store import canonical, now

TABLES = ('conversation_jobs', 'extraction_jobs', 'voice_operations')
ACTIVE = {'queued', 'running', 'applying', 'attempted'}


def dollars(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


class Receipts:
    def __init__(self, store, table, owner, identity):
        if table not in TABLES:
            raise ValueError('Unknown billing journal')
        self.store, self.table, self.owner, self.identity = store, table, owner, identity

    def change(self, fn, require_active=False):
        with self.store.connection() as db:
            row = db.execute(f'SELECT body,state FROM {self.table} WHERE owner=? AND id=?', (self.owner, self.identity)).fetchone()
            if row is None:
                raise ValueError('Billing operation is missing')
            if require_active and row['state'] not in ACTIVE:
                raise ValueError('This request stopped before dispatch')
            body = json.loads(row[0]); fn(body)
            db.execute(f'UPDATE {self.table} SET body=? WHERE owner=? AND id=?', (canonical(body), self.owner, self.identity))

    def begin(self, hold):
        identity = uuid.uuid4().hex
        self.change(lambda body: body.setdefault('billing', []).append({'id': identity, 'day': now()[:10], 'hold_usd': str(hold)}), require_active=True)
        return identity

    def record(self, identity, metadata):
        def apply(body):
            receipt = next(row for row in body['billing'] if row['id'] == identity)
            generation = metadata.get('generation_id')
            if isinstance(generation, str) and generation and len(generation) <= 200:
                receipt['generation_id'] = generation
            if metadata.get('checked_at') is not None:
                receipt['checked_at'] = metadata['checked_at']
            cost = dollars(metadata.get('cost_usd'))
            if cost is not None:
                receipt['actual_usd'] = str(cost)
        self.change(apply)


def totals(db, owner, day):
    actual = Decimal(0); held = Decimal(0); unverified = Decimal(0)
    unknown_legacy = False
    for table in TABLES:
        for row in db.execute(f'SELECT body,state FROM {table} WHERE owner=?', (owner,)):
            body = json.loads(row['body'])
            receipts = body.get('billing')
            days = body.get('budget_days', [body['created_at'][:10]])
            relevant = [item for item in receipts or [] if item['day'] == day]
            paid = sum((Decimal(item['actual_usd']) for item in relevant if 'actual_usd' in item), Decimal(0))
            pending = sum((Decimal(item['hold_usd']) for item in relevant if 'actual_usd' not in item), Decimal(0))
            actual += paid
            if day not in days and not relevant:
                continue
            budget = body.get('budget')
            if not budget:
                unknown_legacy = True
            elif receipts is None:
                unverified += Decimal(budget['reserved_usd'])
            elif row['state'] in ACTIVE and not body.get('billing_closed') and day in days:
                held += max(pending, Decimal(budget['reserved_usd']) - paid, Decimal(0))
            else:
                unverified += pending
    return {'actual_usd': str(actual), 'held_usd': str(held), 'unverified_usd': str(unverified),
            'accounted_usd': None if unknown_legacy else str(actual + held + unverified)}


async def reconcile(store, settings, limit=20):
    """Read billing metadata only; never replay a model/audio request."""
    if str(settings.get('base_url', '')).rstrip('/') != 'https://openrouter.ai/api/v1':
        return
    pending = []
    with store.connection() as db:
        for table in TABLES:
            for row in db.execute(f'SELECT id,owner,body FROM {table}'):
                for receipt in json.loads(row['body']).get('billing', []):
                    if 'actual_usd' not in receipt and receipt.get('generation_id'):
                        pending.append((table, row['owner'], row['id'], receipt))
    async with httpx.AsyncClient(timeout=5) as client:
        pending.sort(key=lambda item: item[3].get('checked_at', 0))
        for table, owner, identity, receipt in pending[:limit]:
            try:
                response = await client.get(settings['base_url'].rstrip('/') + '/generation', params={'id': receipt['generation_id']}, headers={'Authorization': 'Bearer ' + settings['api_key']})
                response.raise_for_status()
                data = response.json()['data']
                if data.get('id') == receipt['generation_id']:
                    Receipts(store, table, owner, identity).record(receipt['id'], {'cost_usd': data.get('total_cost')})
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                pass
            finally:
                Receipts(store, table, owner, identity).record(receipt['id'], {'checked_at': time.time()})


class ModelReceipts(Receipts):
    def __init__(self, store, table, job, allowance=None):
        super().__init__(store, table, job['owner'], job['id'])
        self.budget = job['budget']
        self.allowance = allowance

    def record(self, identity, metadata):
        super().record(identity, metadata)
        cost = dollars(metadata.get('cost_usd'))
        if self.allowance is not None and cost is not None:
            self.allowance.settle(cost)

    def begin(self, input_bytes):
        from .budgets import INPUT_FRAMING, OUTPUT_TOKENS
        hold = ((input_bytes + INPUT_FRAMING) * Decimal(self.budget['input_price']) + OUTPUT_TOKENS * Decimal(self.budget['output_price'])) / Decimal(1000000)
        return super().begin(hold)


async def run_reconciliation():
    import asyncio
    import os
    from pathlib import Path
    from dan.cli import resolve_config
    from dan.server.paths import resolve_graphs_dir
    from .store import PersonalStore
    store = PersonalStore(Path(resolve_graphs_dir()) / 'personal')
    while True:
        try:
            if os.environ.get('DAN_PERSONAL_ENABLED', '0').lower() in {'1', 'true', 'yes'}:
                await reconcile(store, resolve_config())
        except Exception:
            pass  # Billing lookup failure never replays paid work or releases holds.
        await asyncio.sleep(30)
