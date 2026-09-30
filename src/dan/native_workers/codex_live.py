"""Persistent Codex app-server transport for live lead steering."""
from __future__ import annotations

import asyncio
import json
import os
import signal


class CodexLive:
    def __init__(self, process, notify):
        self.process, self.notify = process, notify
        self.pending = {}
        self.sequence = 0
        self.thread_id = ""
        self.turn_id = ""
        self.finished = asyncio.get_running_loop().create_future()

    async def send(self, row):
        self.process.stdin.write((json.dumps(row) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method, params, timeout=30):
        self.sequence += 1
        key = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self.send({"id": key, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(key, None)

    async def read(self):
        try:
            async for line in self.process.stdout:
                row = json.loads(line)
                if "id" in row and "method" not in row:
                    future = self.pending.get(row["id"])
                    if future and not future.done():
                        if "error" in row:
                            future.set_exception(ValueError(str(row["error"].get("message", row["error"]))))
                        else:
                            future.set_result(row.get("result", {}))
                    continue
                if "id" in row:
                    # No implicit approval escalation or fabricated user answers.
                    await self.send({"id": row["id"], "error": {"code": -32601, "message": "This request needs interactive approval; unavailable in Dear Diane's headless transport."}})
                    continue
                method, params = row.get("method", ""), row.get("params", {})
                if params.get("threadId") and self.thread_id and params["threadId"] != self.thread_id:
                    continue
                if method == "turn/started":
                    self.turn_id = params["turn"]["id"]
                self.notify(method, params)
                if method == "turn/completed" and not self.finished.done():
                    self.finished.set_result(params["turn"])
                    self.turn_id = ""
        except Exception as exc:
            if not self.finished.done():
                self.finished.set_exception(exc)
        finally:
            error = ConnectionError("Codex live connection closed")
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            if not self.finished.done():
                self.finished.set_exception(error)

    async def steer(self, items):
        if not self.turn_id or self.finished.done():
            raise ValueError("The turn has already finished; message remains queued")
        text = "\n\n".join(item.text + ("\nAttachments/context: " + json.dumps(item.metadata.get("command_payload", {}), ensure_ascii=False) if item.metadata.get("command_payload") else "") for item in items)
        return await self.request("turn/steer", {
            "threadId": self.thread_id, "expectedTurnId": self.turn_id,
            "input": [{"type": "text", "text": text}],
            "clientUserMessageId": items[0].id,
        }, timeout=5)


async def run_live(team, record, command, env):
    process = reader = errors = None
    worker_id = record["worker_id"]
    profile = record["profile"]
    texts = {}
    def notify(method, params):
        item = params.get("item", {})
        kind = item.get("type", "")
        if method == "item/agentMessage/delta":
            key = params.get("itemId", "message")
            texts[key] = texts.get(key, "") + params.get("delta", "")
            record["response"] = texts[key]
        elif method == "item/completed" and kind == "agentMessage":
            record["response"] = item.get("text", "")
        mapped = {"agentMessage": "agent_message", "commandExecution": "command_execution", "fileChange": "file_change"}
        row = {"type": method.replace("/", "."), **params}
        if item:
            row["item"] = {**item, "type": mapped.get(kind, kind)}
        team.event(record, row)
        team.save(record)
    try:
        args = [command[0], "app-server", "--listen", "stdio://"]
        # Reuse validated account/config overrides from the ordinary launcher.
        for index, part in enumerate(command[:-1]):
            if part == "-c":
                args.extend([part, command[index + 1]])
        process = await asyncio.create_subprocess_exec(*args, cwd=team.workspace, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=8 * 1024 * 1024)
        team.processes[worker_id] = process
        async def drain():
            while await process.stderr.read(8192):
                pass
        errors = asyncio.create_task(drain())
        client = CodexLive(process, notify)
        reader = asyncio.create_task(client.read())
        await client.request("initialize", {"clientInfo": {"name": "dan", "version": "0.2.0"}, "capabilities": {"experimentalApi": True}})
        await client.send({"method": "initialized", "params": {}})
        permission = profile.get("permission", "auto")
        params = {"cwd": team.workspace, "approvalPolicy": "never", "sandbox": {"plan":"read-only", "auto":"workspace-write", "full":"danger-full-access"}[permission]}
        if profile.get("model"):
            params["model"] = profile["model"]
        from .models import model_source
        if model_source(profile) != "native":
            params["modelProvider"] = f"dan_{model_source(profile)}"
        elif profile.get("provider") == "native":
            params["modelProvider"] = "openai"
        session = record.get("native_session_id") or profile.get("resume_session", "")
        if session:
            params["threadId"] = session
        result = await client.request("thread/resume" if session else "thread/start", params)
        client.thread_id = result["thread"]["id"]
        record["native_session_id"] = client.thread_id
        team.save(record)
        start = {"threadId": client.thread_id, "input": [{"type":"text", "text":record["prompt"]}]}
        if profile.get("effort"):
            start["effort"] = profile["effort"]
        result = await client.request("turn/start", start)
        if not client.finished.done():
            client.turn_id = result["turn"]["id"]
            team.steering[worker_id] = client
        terminal = await asyncio.wait_for(asyncio.shield(client.finished), 1800)
        record["status"] = {"completed":"completed", "interrupted":"stopped"}.get(terminal["status"], "failed")
        record["error"] = str((terminal.get("error") or {}).get("message", ""))
    except asyncio.CancelledError:
        record["status"] = "stopped"
    except Exception as exc:
        record.update(status="failed", error=str(exc))
    finally:
        team.steering.pop(worker_id, None)
        if process and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                await asyncio.wait_for(process.wait(), 3)
            except (ProcessLookupError, asyncio.TimeoutError):
                if process.returncode is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
        for task in (reader, errors):
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if 'client' in locals() and client.finished.done() and not client.finished.cancelled():
            client.finished.exception()  # consume disconnect errors during cleanup
        team.processes.pop(worker_id, None)
        team.save(record)
        team.event(record, {"type":"worker.finished", "status":record["status"], "text":record["response"], "error":record["error"]})
