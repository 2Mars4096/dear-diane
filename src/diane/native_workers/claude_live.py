"""Claude stream-json input with acknowledged, same-process redirection."""
from __future__ import annotations
import asyncio
import json
import os
import signal
from uuid import uuid4

from .codex_live import _safe_stderr


class ClaudeLive:
    def __init__(self, process, permission="auto"):
        self.process = process
        self.permission = permission
        self.finished = asyncio.get_running_loop().create_future()
        self.turn_id = ''
        self.thread_id = ''
        self.requests = {}
        self.lock = asyncio.Lock()
        self.controls = {}
        self.acks = {}
        self.latest_input = ''
        self.accepted_input = ''
        self.redirecting = False
        self.background_tasks = set()
        self.waiting_input = ''
        self.owned_task_settled = False

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
            self.waiting_input = ""
            self.owned_task_settled = False
            request_id, message_id = str(uuid4()), str(uuid4())
            control = self.controls[request_id] = asyncio.get_running_loop().create_future()
            acknowledged = self.acks[message_id] = asyncio.get_running_loop().create_future()
            self.latest_input = message_id
            try:
                await self.send({'type': 'control_request', 'request_id': request_id, 'request': {'subtype': 'interrupt'}})
                await asyncio.wait_for(asyncio.shield(control), 15)
                self.requests.clear()
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

    async def reply(self, request_id, decision="", answers=None):
        row = self.requests.get(str(request_id))
        if row is None:
            raise ValueError("This permission request is no longer pending")
        if decision not in {"accept", "decline"}:
            raise ValueError("Choose Allow once or Deny")
        if decision == "accept" and self.permission == "plan" and row["params"].get("tool_name") != "AskUserQuestion":
            raise ValueError("Read-only mode cannot approve additional tool access")
        original = dict(row["params"]["input"])
        if decision == "accept" and row["params"].get("tool_name") == "AskUserQuestion":
            questions = row["params"].get("questions") or []
            if any(not (answers or {}).get(question["id"], "").strip() for question in questions):
                raise ValueError("Answer each question before submitting")
            original["answers"] = answers or {}
        result = ({"behavior": "allow", "updatedInput": original} if decision == "accept" else
                  {"behavior": "deny", "message": "The user denied this action. Do not retry the same action."})
        del self.requests[str(request_id)]
        await self.send({"type": "control_response", "response": {
            "subtype": "success", "request_id": str(request_id), "response": result}})

    def receive(self, row):
        if row.get('session_id'):
            self.thread_id = row['session_id']
        if row.get('type') == 'system' and row.get('subtype') == 'init':
            self.turn_id = self.latest_input
        if row.get('type') == 'control_request' and (row.get('request') or {}).get('subtype') == 'can_use_tool':
            request = row['request']
            identity = str(row['request_id'])
            self.requests[identity] = {'id': identity, 'method': 'claude/canUseTool', 'params': {
                'tool_name': request.get('tool_name', 'Tool'), 'input': request.get('input') or {},
                'reason': request.get('description') or request.get('decision_reason') or 'Approval required'}}
            if request.get('tool_name') == 'AskUserQuestion':
                self.requests[identity]['params']['questions'] = [
                    {'id': question['question'], 'question': question['question'], 'options': question.get('options', []), 'multiSelect': bool(question.get('multiSelect'))}
                    for question in (request.get('input') or {}).get('questions', []) if isinstance(question, dict) and isinstance(question.get('question'), str)]
        if row.get('type') == 'control_cancel_request':
            self.requests.pop(str(row.get('request_id')), None)
        if row.get('type') == 'control_response':
            response = row.get('response') or {}
            future = self.controls.get(response.get('request_id'))
            if future and not future.done():
                if response.get('subtype') == 'error': future.set_exception(ValueError(response.get('error') or 'Claude rejected interruption'))
                else: future.set_result(response)
        if row.get('type') == 'user' and row.get('uuid') == self.latest_input:
            self.redirecting = False
            self.accepted_input = self.latest_input
            self.turn_id = self.latest_input
            future = self.acks.get(self.latest_input)
            if future and not future.done(): future.set_result(True)
        if row.get('type') == 'system' and row.get('subtype') == 'task_started' and self.accepted_input == self.latest_input and self.latest_input:
            if row.get('task_id'):
                self.background_tasks.add(row['task_id'])
        if row.get('type') == 'system' and row.get('subtype') == 'task_notification' and row.get('task_id') in self.background_tasks and row.get('status') in {'completed', 'failed', 'stopped'}:
            self.background_tasks.discard(row['task_id'])
            self.owned_task_settled = True
        origin = row.get('origin') or {}
        background_result = isinstance(origin, dict) and origin.get('kind') == 'task-notification'
        failed = bool(row.get('is_error')) or str(row.get('subtype', '')).startswith('error')
        own_background_result = background_result and self.owned_task_settled and self.waiting_input == self.latest_input and not self.background_tasks
        if row.get('type') == 'result' and (not background_result or own_background_result) and not self.redirecting and (self.accepted_input == self.latest_input or failed):
            if self.background_tasks and not failed:
                self.waiting_input = self.latest_input
                return False
            if not self.finished.done(): self.finished.set_result(row)
            self.turn_id = ''
            return True
        return False

    def close(self):
        self.requests.clear()
        for future in [*self.controls.values(), *self.acks.values()]:
            if not future.done(): future.set_exception(ConnectionError('Claude disconnected before acknowledging the message'))
        if not self.finished.done(): self.finished.cancel()
        self.turn_id = ''


async def run_live(team, record, command, env):
    process = client = errors = None
    tail = bytearray()
    last_snapshot = 0.0
    identity = record['worker_id']
    try:
        command = list(command)
        index = command.index('-p')
        del command[index + 1]  # Send the initial prompt through stdin, not argv.
        command += ['--input-format', 'stream-json', '--replay-user-messages', '--permission-prompt-tool', 'stdio']
        process = await asyncio.create_subprocess_exec(*command, cwd=record['workspace_root'], env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, limit=8 * 1024 * 1024)
        team.processes[identity] = process
        async def drain():
            while chunk := await process.stderr.read(8192):
                tail.extend(chunk); del tail[:-16000]
        errors = asyncio.create_task(drain())
        client = ClaudeLive(process, record["profile"].get("permission", "auto"))
        team.steering[identity] = client
        await client.initial(record['prompt'])
        terminal = False
        async for line in process.stdout:
            row = json.loads(line)
            if not isinstance(row, dict): continue
            terminal = client.receive(row)
            if row.get('type') == 'control_request':
                identity_request = str(row.get('request_id'))
                if identity_request in client.requests and client.permission == 'plan' and client.requests[identity_request]['params'].get('tool_name') != 'AskUserQuestion':
                    await client.reply(identity_request, 'decline')
                elif identity_request not in client.requests:
                    await client.send({'type': 'control_response', 'response': {'subtype': 'error', 'request_id': identity_request, 'error': 'Unsupported control request'}})
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
            # Text deltas remain live in memory and in the append-only event log.
            # Bound expensive full-record snapshots while preserving control/terminal durability.
            now = asyncio.get_running_loop().time()
            if row.get('type') != 'stream_event' or now - last_snapshot >= 0.25:
                team.save(record)
                last_snapshot = now
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
