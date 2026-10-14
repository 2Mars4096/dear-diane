"""Claude stream-json input with acknowledged, same-process redirection."""
from __future__ import annotations
import asyncio
import json
import os
import signal
from uuid import uuid4

from .codex_live import _safe_stderr


class ClaudeLive:
    def __init__(self, process):
        self.process = process
        self.finished = asyncio.get_running_loop().create_future()
        self.turn_id = ''
        self.thread_id = ''
        self.requests = {}
        self.lock = asyncio.Lock()
        self.controls = {}
        self.acks = {}
        self.latest_input = ''
        self.redirecting = False

    async def send(self, row):
        if self.process.returncode is not None or self.finished.done():
            raise ConnectionError('Claude live connection is closed; message remains queued')
        self.process.stdin.write((json.dumps(row) + '\n').encode())
        await self.process.stdin.drain()

    async def initial(self, text):
        self.latest_input = str(uuid4())
        await self.send({'type': 'user', 'uuid': self.latest_input,
                         'message': {'role': 'user', 'content': text}})

    async def steer(self, items):
        async with self.lock:
            if not self.turn_id or self.finished.done():
                raise ValueError('Claude has finished; message remains queued')
            self.redirecting = True  # Intermediate interrupted result is not final.
            request_id, message_id = str(uuid4()), str(uuid4())
            control = self.controls[request_id] = asyncio.get_running_loop().create_future()
            acknowledged = self.acks[message_id] = asyncio.get_running_loop().create_future()
            self.latest_input = message_id
            try:
                await self.send({'type': 'control_request', 'request_id': request_id, 'request': {'subtype': 'interrupt'}})
                await asyncio.wait_for(asyncio.shield(control), 15)
                text = '\n\n'.join(item.text + ('\nAttachments/context: ' + json.dumps(item.metadata.get('command_payload', {}), ensure_ascii=False) if item.metadata.get('command_payload') else '') for item in items)
                await self.send({'type': 'user', 'uuid': message_id, 'message': {'role': 'user', 'content': text}})
                await asyncio.wait_for(asyncio.shield(acknowledged), 30)
                return {'turnId': message_id}
            except Exception:
                self.redirecting = False
                if self.process.returncode is None:
                    try: self.process.terminate()
                    except ProcessLookupError: pass
                raise
            finally:
                self.controls.pop(request_id, None)
                self.acks.pop(message_id, None)
                for future in (control, acknowledged):
                    if not future.done(): future.cancel()
                    elif not future.cancelled(): future.exception()

    def receive(self, row):
        if row.get('session_id'):
            self.thread_id = row['session_id']
        if row.get('type') == 'system' and row.get('subtype') == 'init':
            self.turn_id = self.latest_input
        if row.get('type') == 'control_response':
            response = row.get('response') or {}
            future = self.controls.get(response.get('request_id'))
            if future and not future.done():
                if response.get('subtype') == 'error': future.set_exception(ValueError(response.get('error') or 'Claude rejected interruption'))
                else: future.set_result(response)
        if row.get('type') == 'user' and row.get('uuid') == self.latest_input:
            self.redirecting = False
            self.turn_id = self.latest_input
            future = self.acks.get(self.latest_input)
            if future and not future.done(): future.set_result(True)
        if row.get('type') == 'result' and not self.redirecting:
            if not self.finished.done(): self.finished.set_result(row)
            self.turn_id = ''
            return True
        return False

    def close(self):
        for future in [*self.controls.values(), *self.acks.values()]:
            if not future.done(): future.set_exception(ConnectionError('Claude disconnected before acknowledging the message'))
        if not self.finished.done(): self.finished.cancel()
        self.turn_id = ''


async def run_live(team, record, command, env):
    process = client = errors = None
    tail = bytearray()
    identity = record['worker_id']
    try:
        command = list(command)
        index = command.index('-p')
        del command[index + 1]  # Send the initial prompt through stdin, not argv.
        command += ['--input-format', 'stream-json', '--replay-user-messages']
        process = await asyncio.create_subprocess_exec(*command, cwd=record['workspace_root'], env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, limit=8 * 1024 * 1024)
        team.processes[identity] = process
        async def drain():
            while chunk := await process.stderr.read(8192):
                tail.extend(chunk); del tail[:-16000]
        errors = asyncio.create_task(drain())
        client = ClaudeLive(process)
        team.steering[identity] = client
        await client.initial(record['prompt'])
        terminal = False
        async for line in process.stdout:
            row = json.loads(line)
            if not isinstance(row, dict): continue
            terminal = client.receive(row)
            if client.thread_id: record['native_session_id'] = client.thread_id
            if row.get('type') == 'assistant':
                blocks = (row.get('message') or {}).get('content', [])
                body = '\n'.join(block.get('text', '') for block in blocks if block.get('type') == 'text')
                if body: record['response'] = body
            event = row.get('event') or {}
            if isinstance(event, dict) and event.get('type') == 'content_block_delta':
                record['response'] += str((event.get('delta') or {}).get('text') or '')
            if terminal:
                if row.get('result'): record['response'] = row['result']
                failed = bool(row.get('is_error')) or row.get('subtype', '').startswith('error')
                record['status'] = 'failed' if failed else 'completed'
                record['error'] = str(row.get('result') or '; '.join(row.get('errors') or []) or 'Claude request failed') if failed else ''
            # Suppress the expected intermediate interrupted-result failure.
            if row.get('type') != 'result' or terminal:
                team.event(record, row)
            team.save(record)
            if terminal: break
        if not terminal:
            raise ConnectionError('Claude live connection closed before completion')
    except asyncio.CancelledError:
        record['status'] = 'stopped'
    except Exception as exc:
        record.update(status='failed', error=str(exc) or type(exc).__name__)
    finally:
        team.steering.pop(identity, None)
        if client: client.close()
        if process and process.returncode is None:
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                await process.wait()
        if errors:
            try:
                await asyncio.wait_for(asyncio.shield(errors), 2)
            except asyncio.TimeoutError:
                errors.cancel()
            await asyncio.gather(errors, return_exceptions=True)
        if record.get('status') == 'failed' and tail:
            record['error'] += ': ' + _safe_stderr(bytes(tail), env)
        team.processes.pop(identity, None)
        team.save(record)
        team.event(record, {'type': 'worker.finished', 'status': record['status'], 'text': record.get('response', ''), 'error': record.get('error', '')})
