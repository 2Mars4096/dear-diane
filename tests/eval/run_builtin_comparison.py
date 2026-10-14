"""Bounded same-model pilot; reuses Diane tools, live pipeline and eval scoring.

No production flags are changed. Sequential test-local patches gate stages only
inside the selective arm. Source documents are synthetic, frozen fixtures.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

from diane.cli import resolve_config
from diane.cli import super_organism as cli
from diane.worker.organisms.local_runtime import LocalOrganismToolRuntime
from tests.eval.super_diane_capability_benchmark import (
    SuperDanCapabilityObservation, _benchmark_cases, score_observed_run,
)

TOOLS = ['list_directory', 'file_read', 'file_edit', 'file_write', 'shell_command',
         'workspace_check', 'git_status', 'git_diff', 'git_log']
ARMS = ('direct', 'current', 'selective')
CASES = {
    'short-note-create': {
        'prompt': 'Read input.txt and create note.md with exactly three bullets: decision, owner, and deadline. Preserve all three facts exactly. Do not change input.txt.',
        'files': {'input.txt': 'Decision: migrate the archive. Owner: Mei Chen. Deadline: 2026-11-03.\n'},
    },
    'medium-source-repair': {
        'prompt': 'Fix average(values) in stats.py: ignore None, preserve zeros and negative values, return None when no numeric values remain, otherwise return arithmetic mean. Do not change test_stats.py. Run python -m unittest -q.',
        'files': {'stats.py': 'def average(values):\n    values = [x for x in values if x]\n    return sum(values) / len(values)\n',
                  'test_stats.py': 'import unittest\nfrom stats import average\nclass Tests(unittest.TestCase):\n    def test_zero(self): self.assertEqual(average([0, 2, None]), 1)\n    def test_empty(self): self.assertIsNone(average([None]))\n'},
    },
    'medium-research-report': {
        'prompt': 'Read sources/a.md and sources/b.md. Create report.md comparing the two studies: cite each source path beside its claims, report sample size and percentage-point change for each, explain that Study B is observational and cannot establish causality, and do not pool their incompatible outcomes. Use only these frozen sources; do not alter them.',
        'files': {'sources/a.md': 'Study A: randomized experiment, 100 participants. Completion increased from 40% to 55% (15 percentage points). Outcome: course completion.\n',
                  'sources/b.md': 'Study B: observational survey, 80 respondents. Satisfaction increased from 60% to 70% (10 percentage points). Outcome: self-reported satisfaction. Confounding prevents causal inference.\n'},
    },
    'long-greenfield-project': {
        'prompt': 'Build a small CSV summary project using only Python standard library. Create summary.py exposing summarize(path), cli.py, README.md, and test_summary.py. CSV has category,amount headers. Sum signed decimal amounts by category using Decimal; ignore blank amounts, trim category whitespace, and raise ValueError on invalid amounts. summarize returns category-to-Decimal mapping. python cli.py INPUT prints JSON with sorted category keys and fixed two-decimal string values. Document usage and run python -m unittest -q. Do not alter sample.csv.',
        'files': {'sample.csv': 'category,amount\n A ,1.20\nB,-2.00\nA,0.30\nB,\n'},
    },
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed(root, case):
    root.mkdir(parents=True, exist_ok=False)
    for name, value in CASES[case]['files'].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    return {name: digest(root / name) for name in CASES[case]['files']}


def check(root, case, *, public=False):
    """External deterministic checks; held-out tests never enter agent workspace."""
    if case == 'short-note-create':
        p = root / 'note.md'
        text = p.read_text() if p.is_file() else ''
        passed = all(s in text for s in ('migrate the archive', 'Mei Chen', '2026-11-03'))
        passed &= sum(line.lstrip().startswith(('- ', '* ')) for line in text.splitlines()) == 3
        return {'passed': passed, 'detail': 'three bullets and exact supplied facts'}
    if case == 'medium-research-report':
        p = root / 'report.md'
        text = p.read_text().lower() if p.is_file() else ''
        passed = all(s in text for s in ('sources/a.md', 'sources/b.md', '100', '80', '15', '10', 'observational'))
        passed &= ('causal' in text or 'causality' in text) and ('pool' in text or 'incompatible' in text)
        return {'passed': passed, 'detail': 'mechanical citation/fact coverage only; human semantic review required'}
    if public:
        command = [sys.executable, '-m', 'unittest', '-q']
    elif case == 'medium-source-repair':
        command = [sys.executable, '-c', 'from stats import average; assert average([]) is None; assert average([0,0]) == 0; assert average([-2,0,2,None]) == 0; assert average([1,2]) == 1.5; assert average([None,3]) == 3']
    else:
        command = [sys.executable, '-c', '''import json, subprocess, sys, tempfile
from pathlib import Path
from decimal import Decimal
from summary import summarize
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'input.csv'; p.write_text('category,amount\\n z ,0.10\\na,-1.00\\nz,0.20\\na,\\n')
 assert summarize(p)=={'z':Decimal('0.30'),'a':Decimal('-1.00')}
 out=subprocess.check_output([sys.executable,'cli.py',str(p)],text=True)
 assert json.loads(out)=={'a':'-1.00','z':'0.30'}
 assert list(json.loads(out))==['a','z']
 p.write_text('category,amount\\na,invalid\\n')
 try: summarize(p)
 except ValueError: pass
 else: raise AssertionError('invalid amount accepted')
assert Path('README.md').is_file() and Path('test_summary.py').is_file()
''']
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=20)
        return {'passed': result.returncode == 0, 'detail': (result.stdout + result.stderr)[-4000:]}
    except subprocess.TimeoutExpired:
        return {'passed': False, 'detail': 'acceptance check timed out'}


class Meter:
    """Count every provider call, including failed stages; impose equal outer caps."""
    def __init__(self, provider, model, path, max_calls=30):
        self._provider, self.model, self.path = provider, model, path
        self.calls, self.tokens, self.max_calls = 0, 0, max_calls
        self.usage_missing = 0

    async def complete(self, **kwargs):
        if self.calls >= self.max_calls or self.tokens >= 150_000:
            raise RuntimeError('pilot provider budget exhausted')
        self.calls += 1
        kwargs.update(model=self.model, temperature=0, max_tokens=min(kwargs.get('max_tokens') or 4096, 4096))
        started = time.monotonic()
        try:
            result = await self._provider.complete(**kwargs)
        except BaseException as exc:
            self.usage_missing += 1
            with self.path.open('a') as stream:
                stream.write(json.dumps({'call': self.calls, 'seconds': time.monotonic()-started, 'error_type': type(exc).__name__, 'usage_unknown': True})+'\n')
            raise
        usage = result.usage or {}
        self.usage_missing += not bool(usage)
        total = usage.get('total_tokens', usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0))
        self.tokens += total
        with self.path.open('a') as stream:
            stream.write(json.dumps({'call': self.calls, 'seconds': time.monotonic()-started, 'usage': usage, 'model': result.model, 'finish_reason': result.finish_reason})+'\n')
        return result


async def direct(root, prompt, meter):
    def record(event):
        with (meter.path.parent/'events.jsonl').open('a') as stream:
            stream.write(json.dumps(event, default=str)+'\n')
    runtime = LocalOrganismToolRuntime(tool_ids=TOOLS, workspace_root=root, event_callback=record)
    schemas = [{'type': 'function', 'function': {'name': t, 'description': runtime.metadata_for(t).get('description', ''), 'parameters': runtime.metadata_for(t).get('parameters', {})}} for t in runtime.tool_ids]
    messages = [{'role': 'system', 'content': f'Complete the task using tools in {root}. Stay within this workspace. Verify relevant behavior and report actual results concisely.'}, {'role': 'user', 'content': prompt}]
    for _ in range(30):
        result = await meter.complete(messages=messages, tools=schemas)
        calls = result.tool_calls or []
        message = result.raw_assistant_message or {'role': 'assistant', 'content': result.text, **({'tool_calls': calls} if calls else {})}
        messages.append(message)
        if not calls:
            return {'status': 'completed', 'summary': result.text}
        for call in calls:
            fn = call['function']
            try:
                args = fn['arguments']
                value = await runtime.call(fn['name'], json.loads(args) if isinstance(args, str) else args)
            except Exception as exc:
                value = {'error': str(exc)}
            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(value, default=str)})
    raise RuntimeError('pilot round limit exhausted')


async def pipeline(root, case, arm, meter, events):
    prompt = CASES[case]['prompt']
    args = cli.build_parser().parse_args([prompt, '--live', '--workspace', str(root), '--model', meter.model, '--max-tool-rounds', '10', '--max-tool-calls', '64'])
    args._code_like_live = True
    original_choice = cli._super_live_choice
    def choice(*a, **kw):
        value = original_choice(*a, **kw)
        return value.model_copy(update={'tool_policy': {**value.tool_policy, 'allowed_tool_ids': TOOLS, 'preferred_tool_ids': TOOLS}})
    logger = cli.SuperRunEventLogger(path=events, session_id='comparison', turn_id='1', task_id=case, organism_id='pilot', organ_id='pilot', trace_id='pilot')
    report = cli.run_super_organism_demo(prompt)
    async def cheap_validation(**kwargs):
        gate = check(root, case, public=True)
        return {'passed': gate['passed'], 'status': 'completed', 'overall_score': float(gate['passed']), 'repair_brief': '' if gate['passed'] else gate['detail'], 'missing_requirements': [] if gate['passed'] else [gate['detail']], 'token_usage': {}, 'tool_calls': 0, 'event_count': 0, 'validation_source': 'public_deterministic_check'}
    with ExitStack() as stack:
        stack.enter_context(patch.object(cli, '_super_live_choice', choice))
        if arm == 'selective' and case in ('short-note-create', 'medium-source-repair'):
            stack.enter_context(patch.object(cli, '_super_should_run_planner', lambda *a, **kw: False))
            stack.enter_context(patch.object(cli, '_run_live_validation', cheap_validation))
        return await cli._run_live_generic_execution(report, args, model=meter.model, provider=meter, run_trace_id='pilot', run_task_id=case, event_logger=logger)


async def run_one(output, case, arm, model, config, timeout):
    directory = output / case / arm
    directory.mkdir(parents=True, exist_ok=False)
    root = directory / 'workspace'
    initial = seed(root, case)
    provider = cli._build_live_provider(model, api_key=config.get('api_key'), base_url=config.get('base_url'))
    meter = Meter(provider, model, directory / 'calls.jsonl')
    started = time.monotonic()
    try:
        work = direct(root, CASES[case]['prompt'], meter) if arm == 'direct' else pipeline(root, case, arm, meter, directory / 'events.jsonl')
        result = await asyncio.wait_for(work, timeout=timeout)
    except Exception as exc:
        result = {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'}
    finally:
        await cli._close_live_provider(provider)
    elapsed = time.monotonic() - started
    gate = check(root, case)
    protected = [name for name in initial if name != 'stats.py']
    preserved = all((root/name).is_file() and digest(root/name)==initial[name] for name in protected)
    gate['passed'] &= preserved
    gate['inputs_preserved'] = preserved
    changed = [str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and not any(part.startswith('.') or part=='__pycache__' for part in p.relative_to(root).parts) and (str(p.relative_to(root)) not in initial or digest(p)!=initial[str(p.relative_to(root))])]
    observation = SuperDanCapabilityObservation(case_id=case, status=result['status'], wall_time_seconds=elapsed, token_usage={'total_tokens':meter.tokens}, validation_passed=gate['passed'], artifacts_changed=tuple(changed), output_quality_score=float(gate['passed']), answer_present=bool(result.get('summary')), domain_acceptance_passed=gate['passed'], domain_acceptance_detail=gate['detail'])
    spec = next(c for c in _benchmark_cases() if c.case_id==case)
    row = {'case':case, 'arm':arm, 'model':model, 'seconds':elapsed, 'calls':meter.calls, 'tokens':meter.tokens, 'calls_without_usage':meter.usage_missing, 'runtime_status':result['status'], 'acceptance':gate, 'framework_score':asdict(score_observed_run(spec,observation)), 'result':result, 'changed':changed}
    (directory/'result.json').write_text(json.dumps(row, indent=2, default=str))
    print(json.dumps({k:row[k] for k in ('case','arm','seconds','calls','tokens','runtime_status','acceptance')}), flush=True)
    return row


async def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2]/'.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--model', default=os.environ.get('DAN_LLM_MODEL',''))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', choices=CASES, default=list(CASES))
    parser.add_argument('--arms', nargs='+', choices=ARMS, default=list(ARMS))
    parser.add_argument('--timeout', type=int, default=240)
    args = parser.parse_args()
    config = resolve_config()
    manifest = {'model':args.model,'provider':config.get('base_url'),'cases':args.cases,'arms':args.arms,'timeout':args.timeout,'temperature':0,'max_output_tokens':4096,'max_calls_per_run':30,'total_token_soft_cap_per_run':150000,'tools':TOOLS,'research_scope':'frozen synthetic sources; mechanical checks need human review','selective_policy':'retain request understanding; skip optional planner and replace model validation with public deterministic checks for note/repair; unchanged current pipeline for research/project','repetitions':1,'runner_sha256':digest(Path(__file__)),'runtime_sha256':{str(Path(cli.__file__).name):digest(Path(cli.__file__)), 'local_runtime.py':digest(Path(cli.__file__).parents[1]/'worker/organisms/local_runtime.py')},'source_revision':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'input_hashes':{case:{name:hashlib.sha256(value.encode()).hexdigest() for name,value in CASES[case]['files'].items()} for case in args.cases}}
    print(json.dumps(manifest, indent=2), flush=True)
    if not args.execute:
        return
    if not args.model:
        raise ValueError('A configured model is required')
    output=args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    rows=[]
    for index,case in enumerate(args.cases):
        arms=args.arms[index%len(args.arms):]+args.arms[:index%len(args.arms)]
        for arm in arms:
            rows.append(await run_one(output,case,arm,args.model,config,args.timeout))
            (output/'results.json').write_text(json.dumps(rows,indent=2,default=str))


if __name__=='__main__':
    asyncio.run(main())
