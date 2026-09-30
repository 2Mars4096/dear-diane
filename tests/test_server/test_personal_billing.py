import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from dan.personal.billing import Receipts, dollars, reconcile
from dan.personal.budgets import status
from dan.personal.conversation import Conversation, ChatInput, process
from dan.personal.store import PersonalStore


def start(tmp_path):
    service = Conversation(PersonalStore(tmp_path))
    service.submit('owner', ChatInput(operation_id='billing-fixture', text='Hello'), 'fixture/model')
    job = service.claim()
    return service, job, Receipts(service.store, 'conversation_jobs', 'owner', job['id'])


@pytest.mark.parametrize('value', [None, True, '-1', 'NaN', 'Infinity', 'bad'])
def test_invalid_cost_is_unknown_not_free(value):
    assert dollars(value) is None


def test_actual_cost_releases_unused_hold_and_replay_is_idempotent(tmp_path):
    service, job, receipts = start(tmp_path)
    identity = receipts.begin('0.03')
    receipts.record(identity, {'generation_id':'gen-1','cost_usd':'0.000123456'})
    receipts.record(identity, {'generation_id':'gen-1','cost_usd':'0.000123456'})
    assert Decimal(status(service.store,'owner')['held_usd']) > 0
    service.finish(job, {'reply':'Hello'})
    usage = status(PersonalStore(tmp_path),'owner')
    assert usage['actual_usd'] == '0.000123456'
    assert usage['held_usd'] == usage['unverified_usd'] == '0'
    assert usage['available']
    assert status(service.store,'someone-else')['actual_usd'] == '0'


def test_partial_failure_reconciles_without_repeating_paid_work(tmp_path,monkeypatch):
    service, job, receipts = start(tmp_path)
    first = receipts.begin('0.03'); receipts.record(first, {'cost_usd':'0.001'})
    second = receipts.begin('0.04'); receipts.record(second, {'generation_id':'gen-pending'})
    service.finish(job, {'reply':'Interrupted'}, 'failed')
    assert status(service.store,'owner')['unverified_usd'] == '0.04'
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={'data':{'id':'gen-pending','total_cost':0.002}})
    client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr('dan.personal.billing.httpx.AsyncClient',lambda **kwargs:client)
    asyncio.run(reconcile(PersonalStore(tmp_path), {'base_url':'https://openrouter.ai/api/v1','api_key':'fixture'}))
    usage=status(service.store,'owner')
    assert Decimal(usage['actual_usd']) == Decimal('0.003')
    assert usage['unverified_usd']=='0'
    assert len(requests)==1 and requests[0].method=='GET'


def test_zero_cost_and_unknown_legacy_are_distinct(tmp_path):
    service,job,receipts=start(tmp_path)
    attempt=receipts.begin('0.03');receipts.record(attempt,{'cost_usd':0})
    service.finish(job,{'reply':'Free reply'})
    assert status(service.store,'owner')['used_reservations_usd']=='0'
    with service.store.connection() as db:
        body=json.loads(db.execute('SELECT body FROM conversation_jobs').fetchone()[0]); del body['billing']
        db.execute('UPDATE conversation_jobs SET body=?',(json.dumps(body),))
    assert status(service.store,'owner')['unverified_usd']==job['budget']['reserved_usd']
    assert status(service.store,'owner')['actual_usd']=='0'


def test_real_adapter_accounts_for_structured_output_repair(tmp_path,monkeypatch):
    from dan.providers import CompletionResult
    from dan.server.chat_v2_store import ChatV2Store
    service,job,_=start(tmp_path/'personal')
    class Provider:
        calls=0
        async def complete(self,**kwargs):
            self.calls+=1
            return CompletionResult(text='{}' if self.calls==1 else '{"action":"reply","reply":"Hello"}',provider_metadata={'generation_id':f'gen-{self.calls}','cost_usd':'0.00001'})
    provider=Provider()
    monkeypatch.setattr('dan.cli.resolve_config',lambda:{'base_url':'https://openrouter.ai/api/v1','api_key':'fixture'})
    monkeypatch.setattr('dan.cli.live_gateway.build_gateway_backed_live_provider',lambda *args,**kwargs:provider)
    app=SimpleNamespace(state=SimpleNamespace(chat_v2_store=ChatV2Store(tmp_path/'chat')))
    asyncio.run(process(app,service,job))
    assert provider.calls==2
    usage=status(service.store,'owner')
    assert Decimal(usage['actual_usd'])==Decimal('0.00002')
    assert usage['unverified_usd']=='0'


def test_stopped_request_cannot_start_a_new_paid_attempt(tmp_path):
    service,job,receipts=start(tmp_path)
    service.stop('owner',job['id'])
    with pytest.raises(ValueError,match='stopped'): receipts.begin('0.03')
    assert status(service.store,'owner')['used_reservations_usd']=='0'


def test_costs_stay_on_the_call_day_after_restart(tmp_path,monkeypatch):
    service,job,receipts=start(tmp_path)
    monkeypatch.setattr('dan.personal.billing.now',lambda:'2099-01-01T23:59:59+00:00')
    first=receipts.begin('0.03');receipts.record(first,{'cost_usd':'0.001'})
    monkeypatch.setattr('dan.personal.billing.now',lambda:'2099-01-02T00:00:01+00:00')
    second=receipts.begin('0.03');receipts.record(second,{'cost_usd':'0.002'})
    service.finish(job,{'reply':'Done'})
    monkeypatch.setattr('dan.personal.budgets.now',lambda:'2099-01-02T00:00:10+00:00')
    assert status(PersonalStore(tmp_path),'owner')['actual_usd']=='0.002'


def test_lookup_wrong_generation_does_not_release_hold(tmp_path,monkeypatch):
    service,job,receipts=start(tmp_path)
    attempt=receipts.begin('0.03');receipts.record(attempt,{'generation_id':'expected'})
    service.finish(job,{'reply':'Done'})
    client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'data':{'id':'someone-else','total_cost':0}})))
    monkeypatch.setattr('dan.personal.billing.httpx.AsyncClient',lambda **kwargs:client)
    asyncio.run(reconcile(service.store,{'base_url':'https://openrouter.ai/api/v1','api_key':'fixture'}))
    assert status(service.store,'owner')['unverified_usd']=='0.03'


def test_settlement_funds_next_call_using_actual_cost_only():
    from dan.personal.budgets import CallAllowance,policy
    budget=policy();budget['reserved_usd']='0.03';budget['max_model_calls']=4
    allowance=CallAllowance(budget)
    allowance.charge(1000)
    allowance.settle(Decimal('0.00001'))
    assert allowance.remaining==Decimal('0.02999')
    allowance.charge(1000)  # Would fail if the first call's full hold were retained.
    allowance.settle(Decimal('0'))
    assert allowance.remaining==Decimal('0.02999')


def test_admission_uses_remaining_headroom_instead_of_fixed_minimum(tmp_path,monkeypatch):
    monkeypatch.setenv('DAN_PERSONAL_DAILY_USD','0.05')
    service,job,_=start(tmp_path)
    assert job['budget']['reserved_usd']=='0.05'
    assert not status(service.store,'owner')['available']
    service.finish(job,{'reply':'No call made'})
    assert status(service.store,'owner')['available']
