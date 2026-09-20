"""Parent-scoped concurrent CLI workers with durable event logs."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import json
import os
from pathlib import Path
import signal
import time
from uuid import uuid4

from .catalog import launch

current_team: ContextVar["NativeTeam | None"] = ContextVar("native_team", default=None)
active_teams: dict[str, "NativeTeam"] = {}


class NativeTeam:
    def __init__(self, parent_id: str, workspace: str, profiles: dict, base: Path, emit=None, parent_request=None):
        self.parent_id, self.workspace, self.profiles = parent_id, workspace, profiles
        self.base, self.emit = base, emit
        self.parent_request = parent_request
        self.tasks: dict[str, asyncio.Task] = {}
        self.records: dict[str, dict] = {}
        self.steering: dict[str, object] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        base.mkdir(parents=True, exist_ok=True)

    def save(self, record: dict):
        path = self.base / f'{record["worker_id"]}.json'
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False))
        temporary.replace(path)

    def event(self, record: dict, row: dict):
        with (self.base / f'{record["worker_id"]}.jsonl').open("a") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
        phrase = describe(row)
        if phrase and phrase != record.get("activity"):
            record["activity"] = phrase
            record["actions"] = (record.get("actions", []) + [{"text": phrase, "at": time.time()}])[-30:]
            self.save(record)
        if self.emit:
            self.emit(record, row)

    async def start(self, backend: str, prompt: str, worker_id: str = "") -> dict:
        profile = self.profiles.get(backend, {})
        if not profile.get("enabled"):
            raise ValueError("Enable this worker in the workbench first")
        if not prompt.strip():
            raise ValueError("A worker task is required")
        if sum(not task.done() for task in self.tasks.values()) >= 4:
            raise ValueError("Four native workers are already running; inspect or stop one first")
        previous = self.records.get(worker_id) if worker_id else None
        if worker_id and (not previous or (previous["backend"] != "dan" and not previous.get("native_session_id"))):
            raise ValueError("No resumable session for this worker")
        if worker_id and not self.tasks[worker_id].done():
            raise ValueError("Worker is still running; stop it before sending a follow-up")
        if previous and previous["backend"] != backend:
            raise ValueError("Cannot change a worker's runtime")
        selected_profile = previous["profile"] if previous else profile
        session = previous.get("native_session_id", "") if previous else str(profile.get("resume_session") or "")
        if backend == "dan":
            record = previous or {"worker_id": uuid4().hex, "parent_run_id": self.parent_id, "backend": backend,
                                  "profile": dict(profile), "workspace_root": self.workspace, "created_at": time.time(), "native_session_id": ""}
            history = (record.get("history", []) + [{"role": "user", "content": record["prompt"]}, {"role": "assistant", "content": record["response"]}] if previous else [])
            record["history"] = history
            record.update(status="running", prompt=prompt, response="", error="", activity="Starting", actions=[{"text": "Starting", "at": time.time()}])
            self.records[record["worker_id"]] = record
            self.save(record)
            self.tasks[record["worker_id"]] = asyncio.create_task(self.run_dan(record, history))
            return dict(record)
        command, env = launch(backend, selected_profile, prompt, self.workspace, session)
        source = selected_profile.get("source_session") if not previous else None
        if source:
            if backend == "codex":
                from .sessions import fork_codex
                session = await fork_codex(source, self.workspace, env)
                command, env = launch(backend, selected_profile, prompt, self.workspace, session)
            elif backend == "claude":
                command, env = launch(backend, selected_profile, prompt, self.workspace, source)
                command += ["--fork-session"]
            else:
                raise ValueError("Headless forking is not available for this runtime")
        record = previous or {"worker_id": uuid4().hex, "parent_run_id": self.parent_id, "backend": backend,
                              "profile": dict(profile), "workspace_root": self.workspace, "created_at": time.time(), "native_session_id": ""}
        if session:
            record["native_session_id"] = session
        record.update(status="running", prompt=prompt, response="", error="", activity="Starting", actions=[{"text": "Starting", "at": time.time()}])
        self.records[record["worker_id"]] = record
        self.save(record)
        self.tasks[record["worker_id"]] = asyncio.create_task(self.run(record, command, env))
        return dict(record)

    async def run_dan(self, record: dict, history: list):
        from dan.server.chat_v2_backend import AgentBackendRunRequest, SuperDanBackendAdapter
        profile = record["profile"]
        policy = {"model": profile.get("model", ""), "native_workers": {}}
        if profile.get("base_url"):
            policy["base_url"] = profile["base_url"]
        inherited = {key: getattr(self.parent_request, key, {}) for key in ("mutation_policy", "approval_policy", "tool_policy")}
        request = AgentBackendRunRequest(**inherited, task_id=record["worker_id"], run_id=record["worker_id"],
            objective=record["prompt"], workspace_root=self.workspace, history=history, profile_policy=policy)
        # Team members are leaves: a DAN child must not inherit its parent's team.
        token = current_team.set(None)
        try:
            result = await SuperDanBackendAdapter()._run(request, lambda event: self.event(record, event.model_dump(mode="json")))
            record.update(status=result.status, response=result.summary)
        except asyncio.CancelledError:
            record["status"] = "stopped"
        except Exception as exc:
            record.update(status="failed", error=str(exc))
        finally:
            current_team.reset(token)
            self.save(record)
            self.event(record, {"type": "worker.finished", "status": record["status"], "text": record["response"], "error": record["error"]})

    async def run(self, record: dict, command: list[str], env: dict):
        if record["backend"] == "codex" and record["profile"].get("_live_steering"):
            from .codex_live import run_live
            return await run_live(self, record, command, env)
        process = None
        stderr_task = None
        terminal = ""
        try:
            process = await asyncio.create_subprocess_exec(*command, cwd=self.workspace, env=env,
                       stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                       stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=4 * 1024 * 1024)
            self.processes[record["worker_id"]] = process
            async def drain_errors():
                tail = b""
                while chunk := await process.stderr.read(8192):
                    tail = (tail + chunk)[-16000:]
                return tail.decode(errors="replace")
            stderr_task = asyncio.create_task(drain_errors())
            async with asyncio.timeout(1800):
                async for raw in process.stdout:
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        row = {"type": "stdout", "text": raw.decode(errors="replace")}
                    if not isinstance(row, dict):
                        continue
                    result = row.get("result") if isinstance(row.get("result"), dict) else row
                    if row.get("type") in {"result", "turn.completed"} or row.get("event") == "result":
                        terminal = str(result.get("status") or "SUCCESS")
                    session = row.get("session_id") or row.get("thread_id") or result.get("conversation_id")
                    if session:
                        record["native_session_id"] = session
                    step = row.get("step_update") or {}
                    if isinstance(step, dict) and step.get("step_type") == "agent_response":
                        record["response"] += str(step.get("text_delta") or "")
                    partial = row.get("event")
                    if isinstance(partial, dict) and partial.get("type") == "content_block_delta":
                        record["response"] += str(partial.get("delta", {}).get("text") or "")
                    if row.get("type") == "assistant":
                        content = row.get("message", {}).get("content", [])
                        if isinstance(content, list):
                            record["response"] = "\n".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
                    item = row.get("item") or {}
                    if item.get("type") == "agent_message":
                        record["response"] = str(item.get("text", ""))
                    if isinstance(row.get("result"), str):
                        record["response"] = row["result"]
                    if result.get("response"):
                        record["response"] = result["response"]
                    if row.get("is_error") or row.get("type") in {"error", "turn.failed"} or result.get("status") in {"ERROR", "INVALID"}:
                        record["error"] = str(result.get("error") or row.get("result") or "Native worker failed")[:16000]
                    self.event(record, row)
                    self.save(record)
                code = await process.wait()
                stderr = await stderr_task
                if code and not record["error"]:
                    record["error"] = stderr or f"CLI exited with status {code}"
                if not terminal and not record["error"]:
                    record["error"] = "CLI exited without a completion result"
                record["status"] = "failed" if code or record["error"] else "completed"
                if terminal in {"WAITING", "CANCELED", "INTERRUPTED", "RUNNING", "INVALID"}:
                    record["status"] = {"WAITING": "needs_input", "CANCELED": "stopped", "INTERRUPTED": "interrupted"}.get(terminal, "failed")
        except asyncio.CancelledError:
            record["status"] = "stopped"
        except Exception as exc:
            record.update(status="failed", error=str(exc) or type(exc).__name__)
        finally:
            if process and process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    await asyncio.wait_for(process.wait(), timeout=3)
                except (ProcessLookupError, asyncio.TimeoutError):
                    if process.returncode is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        await process.wait()
            if stderr_task and not stderr_task.done():
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
            self.processes.pop(record["worker_id"], None)
            self.save(record)
            self.event(record, {"type": "worker.finished", "status": record["status"], "text": record["response"], "error": record["error"]})

    async def stop(self, worker_id: str):
        task = self.tasks.get(worker_id)
        if task is None:
            raise ValueError("Worker does not belong to this parent run")
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self.records[worker_id]["status"] == "running":
                self.records[worker_id]["status"] = "stopped"
                self.save(self.records[worker_id])
        return dict(self.records[worker_id])

    async def close(self):
        for worker_id in list(self.tasks):
            await self.stop(worker_id)


def _short(value: object, limit: int = 60) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tool_phrase(name: str, args: dict) -> str:
    target = args.get("file_path") or args.get("path") or args.get("notebook_path") or ""
    base = Path(str(target)).name if target else ""
    if name in {"Read", "NotebookRead"}:
        return f"Reading {base}" if base else "Reading files"
    if name in {"Edit", "MultiEdit", "Write", "NotebookEdit"}:
        return f"Editing {base}" if base else "Editing files"
    if name == "Bash":
        return f"Running {_short(args.get('description') or args.get('command'), 48)}"
    if name in {"Grep", "Glob", "LS"}:
        return "Searching files"
    if name == "WebSearch":
        return f"Searching {_short(args.get('query'), 44)}" if args.get("query") else "Searching the web"
    if name == "WebFetch":
        from urllib.parse import urlparse
        host = urlparse(str(args.get("url", ""))).netloc
        return f"Reading {host}" if host else "Reading a web page"
    if name in {"Task", "Agent"}:
        return "Delegating a subtask"
    if name == "TodoWrite":
        return "Updating its plan"
    return f"Using {name.split('__')[-1].replace('_', ' ')}"


def describe(row: dict) -> str:
    """Summarize one native event as a short present-tense action, or '' when it is not a new action."""
    kind = row.get("type")
    item = row.get("item") if isinstance(row.get("item"), dict) else {}
    if kind == "item.started" and item:  # Codex JSONL
        item_type = item.get("type")
        if item_type == "command_execution":
            return f"Running {_short(item.get('command'), 48)}"
        if item_type == "file_change":
            return "Editing files"
        if item_type == "web_search":
            return f"Searching {_short(item.get('query'), 44)}" if item.get("query") else "Searching the web"
        if item_type == "mcp_tool_call":
            return f"Using {item.get('tool') or 'a tool'}"
        if item_type == "reasoning":
            return "Thinking"
    if kind == "item.completed" and item.get("type") == "agent_message":
        return "Writing response"
    if kind == "assistant":  # Claude stream-json
        content = (row.get("message") or {}).get("content")
        blocks = [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []
        tools = [block for block in blocks if block.get("type") == "tool_use"]
        if tools:
            return _tool_phrase(str(tools[-1].get("name") or "tool"), tools[-1].get("input") or {})
        if any(block.get("type") == "text" and block.get("text") for block in blocks):
            return "Writing response"
        if any(block.get("type") == "thinking" for block in blocks):
            return "Thinking"
    call = row.get("tool_call") if kind == "tool_call" and row.get("subtype") == "started" else None
    if isinstance(call, dict) and call:  # Cursor stream-json: {"readToolCall": {"args": {...}}}
        key, value = next(iter(call.items()))
        args = (value or {}).get("args") or {} if isinstance(value, dict) else {}
        name = {"readToolCall": "Read", "editToolCall": "Edit", "writeToolCall": "Write", "shellToolCall": "Bash",
                "grepToolCall": "Grep", "globToolCall": "Glob", "lsToolCall": "LS", "webSearchToolCall": "WebSearch",
                "todoToolCall": "TodoWrite"}.get(key, key.removesuffix("ToolCall"))
        return _tool_phrase(name, {**args, "file_path": args.get("path") or args.get("file_path") or ""})
    step = row.get("step_update")  # Antigravity
    if isinstance(step, dict) and step.get("step_type"):
        return "Writing response" if step["step_type"] == "agent_response" else _short(str(step["step_type"]).replace("_", " ").capitalize())
    if kind == "tool_used" and row.get("summary"):  # DAN child
        return _short(row["summary"])
    if kind == "model_text_delta":
        return "Writing response"
    return ""


def read_workers(base: Path, parent_id: str) -> list[dict]:
    records = []
    if not base.is_dir():
        return records
    for path in base.glob("*.json"):
        try:
            row = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if row.get("parent_run_id") == parent_id:
            if row["status"] == "running" and parent_id not in active_teams:
                row["status"] = "interrupted"
            records.append(row)
    return sorted(records, key=lambda row: row["created_at"])
