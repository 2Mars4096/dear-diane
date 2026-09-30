"""Conservative USD reservations, committed atomically with extraction admission.

Reservations are not provider invoices. Keep them after uncertain/stopped calls so
retries cannot reclaim potentially spent money. UTC creation day owns each reserve.
"""
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import os
import re
from .store import Conflict, now, canonical
from pydantic import BaseModel, ConfigDict, Field

INPUT_BYTES = 131072
INPUT_FRAMING = 1024
OUTPUT_TOKENS = 4096
MODEL_CALLS = 2


def amount(name, default):
    try:
        value = Decimal(os.environ.get(name, default))
    except InvalidOperation:
        raise ValueError(f'{name} must be a nonnegative finite USD amount') from None
    if not value.is_finite() or value < 0 or value > 10000:
        raise ValueError(f'{name} must be between 0 and 10000')
    return value


def policy(db=None, owner=None):
    task = amount('DAN_PERSONAL_TASK_USD', '0.50')
    daily = amount('DAN_PERSONAL_DAILY_USD', '2.00')
    prompt = amount('DAN_PERSONAL_INPUT_USD_PER_MILLION', '1.00')
    completion = amount('DAN_PERSONAL_OUTPUT_USD_PER_MILLION', '5.00')
    host_task, host_daily = task, daily
    settings = {'revision': 0, 'paused': False, 'task_limit': str(task), 'daily_limit': str(daily)}
    if db is not None:
        row = db.execute('SELECT revision,body FROM personal_settings WHERE owner=?', (owner,)).fetchone()
        if row:
            settings = {**json.loads(row['body']), 'revision': row['revision']}
            task = min(task, Decimal(settings['task_limit']))
            daily = min(daily, Decimal(settings['daily_limit']))
    reserve = (MODEL_CALLS * ((INPUT_BYTES + INPUT_FRAMING) * prompt + OUTPUT_TOKENS * completion) / Decimal(1000000)).quantize(Decimal('0.000001'), rounding=ROUND_CEILING)
    return {'currency': 'USD', 'settings': settings, 'host_task_limit': str(host_task), 'host_daily_limit': str(host_daily), 'paused': settings['paused'], 'task_limit': str(task), 'daily_limit': str(daily),
            'reserved_usd': str(reserve), 'input_price': str(prompt), 'output_price': str(completion),
            'max_input_bytes': INPUT_BYTES, 'max_output_tokens': OUTPUT_TOKENS, 'max_model_calls': MODEL_CALLS,
            'basis': 'conservative_reservation', 'price_control': 'openrouter_max_price'}


def daily_reserved(db, owner, day):
    total = Decimal(0)
    for row in db.execute('SELECT body FROM extraction_jobs WHERE owner=? UNION ALL SELECT body FROM conversation_jobs WHERE owner=? UNION ALL SELECT body FROM voice_operations WHERE owner=?', (owner, owner, owner)):
        job = json.loads(row[0])
        if day in job.get('budget_days', [job['created_at'][:10]]):
            # Legacy jobs have unknown cost; do not present them as free.
            if 'budget' not in job:
                return None
            total += Decimal(job['budget']['reserved_usd'])
    return total


def reserve(db, owner):
    budget = policy(db, owner)
    if budget['paused']:
        raise Conflict('AI replies and extraction are paused in spending settings. Manual review and reminders remain available.')
    used = daily_reserved(db, owner, now()[:10])
    cost = Decimal(budget['reserved_usd'])
    if Decimal(budget['task_limit']) == 0 or cost > Decimal(budget['task_limit']):
        raise Conflict('AI paused: its reserved cost exceeds the per-task budget. Manual review and reminders remain available.')
    if used is None:
        raise Conflict('AI paused: earlier extraction costs today are unknown. Retry after the next UTC day; manual review remains available.')
    if Decimal(budget['daily_limit']) == 0 or used + cost > Decimal(budget['daily_limit']):
        raise Conflict('AI paused: the daily reserved budget is exhausted. It resets at midnight UTC; manual review and reminders remain available.')
    return budget


def status(store, owner):
    day = now()[:10]
    with store.connection() as db:
        budget = policy(db, owner)
        used = daily_reserved(db, owner, day)
    return {**budget, 'day': day, 'used_reservations_usd': str(used) if used is not None else None,
            'remaining_usd': str(max(Decimal(0), Decimal(budget['daily_limit']) - used)) if used is not None else None,
            'available': not budget['paused'] and used is not None and Decimal(budget['task_limit']) > 0 and Decimal(budget['daily_limit']) > 0 and Decimal(budget['reserved_usd']) <= Decimal(budget['task_limit']) and used + Decimal(budget['reserved_usd']) <= Decimal(budget['daily_limit'])}


def completion_options(budget, base_url, model=None):
    if not budget:
        raise ValueError('This queued extraction predates spending controls. Start a new extraction.')
    if str(base_url or '').rstrip('/') != 'https://openrouter.ai/api/v1':
        raise ValueError('Budgeted extraction currently requires OpenRouter price controls. Manual review remains available.')
    if model is not None and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*(?::free)?', model):
        raise ValueError('Budgeted extraction needs an explicit provider/model ID without presets or paid plugins.')
    return {'extra_body': {'provider': {'allow_fallbacks': False, 'require_parameters': True,
            'max_price': {'prompt': budget['input_price'], 'completion': budget['output_price'], 'request': 0}}}}


class BudgetSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(min_length=8, max_length=100)
    expected_revision: int = Field(ge=0)
    task_limit: Decimal = Field(ge=0, le=10000, decimal_places=6)
    daily_limit: Decimal = Field(ge=0, le=10000, decimal_places=6)
    paused: bool = False


def update_settings(store, owner, body: BudgetSettings):
    with store.connection() as db:
        def apply():
            current = policy(db, owner)
            if current['settings']['revision'] != body.expected_revision:
                raise Conflict('Spending settings changed. Reload before saving.')
            if body.task_limit > Decimal(current['host_task_limit']) or body.daily_limit > Decimal(current['host_daily_limit']):
                raise Conflict('These limits exceed the maximum configured on this host.')
            settings = {'task_limit': str(body.task_limit), 'daily_limit': str(body.daily_limit), 'paused': body.paused}
            revision = body.expected_revision + 1
            db.execute('INSERT INTO personal_settings(owner,revision,body) VALUES (?,?,?) ON CONFLICT(owner) DO UPDATE SET revision=excluded.revision,body=excluded.body', (owner, revision, canonical(settings)))
            db.execute('INSERT INTO business_events(owner,entity_id,kind,at) VALUES (?,?,?,?)', (owner, 'spending-settings', 'budget_settings_changed', now()))
            return {**settings, 'revision': revision}
        return store._operation(db, owner, body.operation_id, {'action': 'budget_settings', **body.model_dump(mode='json')}, apply)
