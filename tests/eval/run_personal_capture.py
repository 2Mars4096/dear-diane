"""Opt-in synthetic capture evaluation; never loads private inboxes or writes calendars.

PYTHONPATH=src python tests/eval/run_personal_capture.py --model MODEL --output /tmp/result.json
Optional --env-file loads a server configuration without printing credentials.
"""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
import time

from dan.cli import resolve_config
from dan.cli.live_gateway import build_gateway_backed_live_provider
from dan.personal.extraction import extraction_brief, validate_extraction
from dan.server.cell_backend import BriefCellAdapter
from dan.server.chat_v2_backend import run_agent_backend
from dan.server.chat_v2_dispatch import reserve_dispatch
from dan.server.chat_v2_store import ChatV2Store


async def evaluate(args):
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    config = resolve_config()
    provider = build_gateway_backed_live_provider(args.model, api_key=config['api_key'], base_url=config['base_url'])
    cases = json.loads((Path(__file__).parents[1] / 'fixtures/personal/capture-v1.json').read_text())['cases']
    if args.ids:
        selected = set(args.ids.split(',')); cases = [case for case in cases if case['id'] in selected]
    if not cases:
        raise ValueError('No matching fixtures')
    report = {'model': args.model, 'fixture_version': 1, 'cases': []}
    with tempfile.TemporaryDirectory(prefix='diane-capture-eval-') as directory:
        store = ChatV2Store(Path(directory))
        for case in cases:
            run = reserve_dispatch(store, key='eval:' + case['id'], objective='Evaluate synthetic capture', thread_id='fixture-' + case['id'])
            started = time.monotonic()
            result = await run_agent_backend(store, run.run_id, adapter=BriefCellAdapter(extraction_brief(case['source']), args.model, provider))
            row = {'id': case['id'], 'seconds': round(time.monotonic()-started, 2), 'status': result.status, 'usage': result.token_usage, 'model_calls': result.raw_result.get('model_calls')}
            try:
                parsed = validate_extraction(result.raw_result.get('result') or result.summary, case['source']).model_dump(mode='json')
                if parsed.get('time'): parsed['time'] = parsed['time'][:5]
                expected = case['expected']
                mismatches = {key: {'expected': value, 'actual': parsed.get(key)} for key,value in expected.items() if key != 'unresolved_fields' and parsed.get(key) != value}
                clarification = not expected['unresolved_fields'] or bool(parsed['questions'] and parsed['unresolved_fields'])
                unsafe = any(expected.get(key, 'absent') is None and parsed.get(key) is not None for key in ('date','time','timezone'))
                row.update(result=parsed, mismatches=mismatches, clarification=clarification, unsafe_critical_field=unsafe,
                           passed=not mismatches and clarification and not unsafe)
            except Exception as exc:
                row.update(passed=False, error=type(exc).__name__ + ': ' + str(exc)[:250])
            report['cases'].append(row)
            Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
            print(json.dumps({key:row.get(key) for key in ('id','seconds','status','passed','error')}, ensure_ascii=False), flush=True)
    report['passed'] = sum(row['passed'] for row in report['cases'])
    report['total'] = len(report['cases'])
    report['tokens'] = {key: sum(row['usage'].get(key,0) for row in report['cases']) for key in ('prompt_tokens','completion_tokens','total_tokens')}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    close = getattr(provider, 'aclose', None)
    if close: await close()
    print(json.dumps({key: report[key] for key in ('passed','total','tokens')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--env-file')
    parser.add_argument('--ids')
    asyncio.run(evaluate(parser.parse_args()))
