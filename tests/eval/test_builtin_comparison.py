import asyncio
from pathlib import Path

import pytest

from tests.eval.run_builtin_comparison import Meter, check, seed, pipeline
from diane.providers import CompletionResult


def test_oracle_rejects_original_bug_and_accepts_repair(tmp_path):
    root = tmp_path / 'fixture'
    seed(root, 'medium-source-repair')
    assert not check(root, 'medium-source-repair')['passed']
    (root/'stats.py').write_text('def average(values):\n values=[x for x in values if x is not None]\n return sum(values)/len(values) if values else None\n')
    assert check(root, 'medium-source-repair')['passed']


def test_note_requires_all_facts_and_three_bullets(tmp_path):
    (tmp_path/'note.md').write_text('- migrate the archive\n- Mei Chen\n- 2026-11-03\n')
    assert check(tmp_path, 'short-note-create')['passed']
    (tmp_path/'note.md').write_text('- migrate the archive\n- Mei Chen\n')
    assert not check(tmp_path, 'short-note-create')['passed']


@pytest.mark.asyncio
async def test_meter_counts_and_enforces_calls(tmp_path):
    class Provider:
        async def complete(self, **kwargs):
            assert kwargs['model']=='fixed'
            assert kwargs['temperature']==0
            assert kwargs['max_tokens']==4096
            return CompletionResult(text='ok',usage={'total_tokens':12})
    meter=Meter(Provider(),'fixed',tmp_path/'calls.jsonl',max_calls=1)
    await meter.complete(model='wrong',max_tokens=99999)
    assert meter.tokens==12
    with pytest.raises(RuntimeError,match='budget'):
        await meter.complete()


@pytest.mark.asyncio
async def test_pipeline_wires_shared_tools_and_selective_gate(tmp_path,monkeypatch):
    from tests.eval import run_builtin_comparison as runner
    root=tmp_path/'workspace'
    seed(root,'short-note-create')
    (root/'note.md').write_text('- migrate the archive\n- Mei Chen\n- 2026-11-03\n')
    original=runner.cli._run_live_validation
    async def fake(report,args,**kwargs):
        choice=runner.cli._super_live_choice(report,args)
        assert choice.tool_policy['allowed_tool_ids']==runner.TOOLS
        assert not runner.cli._super_should_run_planner('build a project')
        gate=await runner.cli._run_live_validation()
        assert gate['passed'] and gate['validation_source']=='public_deterministic_check'
        return {'status':'completed'}
    monkeypatch.setattr(runner.cli,'_run_live_generic_execution',fake)
    meter=Meter(None,'test',tmp_path/'calls.jsonl')
    assert (await pipeline(root,'short-note-create','selective',meter,tmp_path/'events.jsonl'))['status']=='completed'
    assert runner.cli._run_live_validation is original


@pytest.mark.asyncio
async def test_meter_preserves_failed_call_without_fake_zero_usage(tmp_path):
    import json
    class Provider:
        async def complete(self, **kwargs):
            raise ConnectionError('fixture')
    meter=Meter(Provider(),'fixed',tmp_path/'calls.jsonl')
    with pytest.raises(ConnectionError):
        await meter.complete()
    row=json.loads((tmp_path/'calls.jsonl').read_text())
    assert row['usage_unknown'] is True and row['error_type']=='ConnectionError'
    assert 'usage' not in row


def test_multifile_oracle_checks_decimal_cli_and_invalid_amount(tmp_path):
    assert not check(tmp_path,'long-greenfield-project')['passed']
    (tmp_path/'summary.py').write_text('''import csv
from decimal import Decimal, InvalidOperation
def summarize(path):
 result={}
 with open(path) as f:
  for row in csv.DictReader(f):
   if not row['amount'].strip(): continue
   try: value=Decimal(row['amount'])
   except InvalidOperation as exc: raise ValueError('invalid amount') from exc
   key=row['category'].strip()
   result[key]=result.get(key,Decimal(0))+value
 return result
''')
    (tmp_path/'cli.py').write_text('import json, sys\nfrom summary import summarize\nprint(json.dumps({k:format(v,".2f") for k,v in sorted(summarize(sys.argv[1]).items())}))\n')
    (tmp_path/'README.md').write_text('python cli.py INPUT')
    (tmp_path/'test_summary.py').write_text('')
    assert check(tmp_path,'long-greenfield-project')['passed']
    (tmp_path/'cli.py').write_text('print("{}")\n')
    assert not check(tmp_path,'long-greenfield-project')['passed']
