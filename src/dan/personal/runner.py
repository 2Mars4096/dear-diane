"""One lifespan-owned extraction worker; browser closure does not own its task."""
from __future__ import annotations
import asyncio
from contextlib import suppress
import os
import inspect
import logging
from pathlib import Path
import re

from dan.cli import resolve_config
from dan.cli.live_gateway import build_gateway_backed_live_provider
from dan.server.cell_backend import BriefCellAdapter
from dan.server.chat_v2_backend import run_agent_backend
from dan.server.chat_v2_dispatch import reserve_dispatch
from dan.server.paths import resolve_graphs_dir
from .extraction import extraction_brief, validate_extraction
from .extraction_queue import ExtractionQueue
from .store import PersonalStore
from .reminders import Reminders
from .budgets import completion_options


def extraction_model():
    # Explicit opt-in prevents a capture from silently choosing a paid model.
    return os.environ.get('DAN_PERSONAL_MODEL', '').strip()


def redact_source(source):
    source = re.sub(r'(?i)\b(?:sk-[a-z0-9_-]{12,}|Bearer\s+[a-z0-9._-]+)', '[credential redacted]', source)
    return re.sub(r'(?i)((?:password|verification code|login code|reset code|验证码|驗證碼|密码|密碼)\s*[:：]?\s*)[^\s,;。]+', r'\1[redacted]', source)


class PersonalRunner:
    def __init__(self, app):
        self.app = app
        self.task = None
        self.reminder_task = None
        self.conversation_task = None

    def start(self):
        if os.environ.get('DAN_PERSONAL_ENABLED', '0').lower() in {'1', 'true', 'yes'}:
            from .conversation import run_conversations
            self.conversation_task = asyncio.create_task(run_conversations(self.app))
            self.task = asyncio.create_task(self.run())
            self.reminder_task = asyncio.create_task(self.run_reminders())

    async def close(self):
        if self.conversation_task:
            self.conversation_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.conversation_task
        if self.reminder_task:
            self.reminder_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.reminder_task
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def run_reminders(self):
        reminders = Reminders(PersonalStore(Path(resolve_graphs_dir()) / 'personal'))
        while True:
            if os.environ.get('DAN_PERSONAL_ENABLED', '0').lower() in {'1', 'true', 'yes'}:
                try:
                    reminders.deliver_due()
                except Exception:
                    # Atomic transaction rolled back. Next tick retries without duplicate receipts.
                    logging.getLogger(__name__).warning('Personal reminder tick failed; retrying in five seconds')
            await asyncio.sleep(5)

    async def run(self):
        queue = ExtractionQueue(PersonalStore(Path(resolve_graphs_dir()) / 'personal'))
        while True:
            if os.environ.get('DAN_PERSONAL_ENABLED', '0').lower() not in {'1', 'true', 'yes'}:
                await asyncio.sleep(1)
                continue
            job = None
            try:
                job = queue.claim()
                if job is None:
                    await asyncio.sleep(1)
                    continue
                await self.process(queue, job)
            except asyncio.CancelledError:
                # Leave the lease for reconciliation; do not replay a paid call blindly.
                raise
            except Exception:
                # Provider exceptions can include request payloads; keep them out of UI.
                if job is not None:
                    try:
                        queue.finish(job, error='Extraction could not finish. Review manually or try again.', state='failed')
                    except Exception:
                        # Keep the durable lease for later reconciliation if SQLite is unavailable.
                        logging.getLogger(__name__).warning('Personal extraction receipt could not be saved; recovery will retry')
                logging.getLogger(__name__).warning('Personal extraction tick failed; retrying in five seconds')
                await asyncio.sleep(5)

    async def process(self, queue, job):
        if not queue.active(job):
            return
        store = self.app.state.chat_v2_store
        source = redact_source(job['source'])
        run = reserve_dispatch(store, key='personal:' + job['owner'] + ':' + job['id'],
            objective='Extract captured commitment for human review', thread_id='_dan_personal_' + job['commitment_id'],
            metadata={'personal_job_id': job['id'], 'source_hash': job['source_hash'], 'model': job['model'], 'template': 'personal.capture.v1'})
        if not queue.bind(job, run):
            return
        cached = run.metadata.get('backend_result')
        if cached:
            if cached.get('status') != 'completed':
                queue.finish(job, error='The previous extraction did not complete. Review manually or retry.', state='interrupted')
                return
            raw = cached.get('raw_result', {}).get('result') or cached.get('summary')
        elif job['reconcile_only'] or run.status not in {'queued'}:
            queue.finish(job, error='Diane restarted during extraction. No model call was repeated. Review manually or retry.', state='interrupted')
            return
        else:
            config = resolve_config()
            try:
                if not queue.authorize_spend(job):
                    return
                options = completion_options(job.get('budget'), config['base_url'], job['model'])
            except ValueError as exc:
                from dan.server.chat_v2 import AgentRunEvent
                store.record_agent_event(AgentRunEvent(type='stopped', run_id=run.run_id, task_id=run.task_id, summary=str(exc)))
                queue.finish(job, error=str(exc), state='blocked')
                return
            provider = build_gateway_backed_live_provider(job['model'], api_key=config['api_key'], base_url=config['base_url'])
            adapter = BriefCellAdapter(extraction_brief(source, job['locale']), job['model'], provider, completion_options=options, max_input_bytes=job['budget']['max_input_bytes'], before_completion=lambda: queue.authorize_spend(job))
            async def execute():
                return await run_agent_backend(store, run.run_id, adapter=adapter, overrides={
                    'mutation_policy': {'mode': 'plan', 'permission': 'forbidden'}, 'tool_policy': {'allowed_tool_ids': []}})
            work = asyncio.create_task(execute())
            try:
                while not work.done():
                    await asyncio.wait({work}, timeout=0.5)
                    if not queue.active(job):
                        work.cancel()
                        with suppress(asyncio.CancelledError):
                            await work
                        from dan.server.chat_v2 import AgentRunEvent
                        store.record_agent_event(AgentRunEvent(type='stopped', run_id=run.run_id, task_id=run.task_id, summary='Extraction stopped'))
                        return
                result = await work
            finally:
                if not work.done():
                    work.cancel()
                    with suppress(asyncio.CancelledError):
                        await work
                close = getattr(provider, 'aclose', None)
                if callable(close):
                    closed = close()
                    if inspect.isawaitable(closed):
                        await closed
            if result.status != 'completed':
                queue.finish(job, error='The model could not finish extraction. Review manually or retry.', state='failed')
                return
            raw = result.raw_result.get('result') or result.summary
        draft = validate_extraction(raw, source)
        queue.finish(job, draft=draft.model_dump(mode='json'))
