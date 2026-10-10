"""Local Todo handoff. Diane owns projects, profiles, sessions and execution."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from diane.server.chat_store import ChatMessage, ChatThread, TaskRunRef
from diane.server.chat_request import ChatMessageRequest
from diane.server.chat_v2 import AgentRunCommand
from diane.server.routers.chat_v2 import (
    AgentRunExecuteRequest, create_agent_run, execute_agent_run, append_agent_run_command,
)


def local_only(request: Request):
    local = {"localhost", "127.0.0.1", "::1", "testserver", "testclient"}
    origin = request.headers.get("origin")
    if (not request.client or request.client.host not in local
        or request.url.hostname not in local
        or (origin and urlparse(origin).hostname not in local)):
        raise HTTPException(403, "Todo handoff is local-only")


router = APIRouter(prefix="/api/sticky-todo", dependencies=[Depends(local_only)])


class Project(BaseModel):
    id: str = Field(pattern=r"^[\w-]{1,120}$")
    name: str = Field(min_length=1, max_length=200)
    root: str = Field(max_length=4096)


class Defaults(BaseModel):
    projects: list[Project] = Field(max_length=500)
    active_project_id: str | None = None
    execute: AgentRunExecuteRequest


class Dispatch(BaseModel):
    request_id: UUID
    task_key: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=20000)
    steps: list[str] = Field(default_factory=list, max_length=100)
    parent: str = Field(default="", max_length=20000)
    project_id: str | None = None


RULES = """Execute this request using the project's established conventions and configured defaults.
Resolve routine choices independently. Stay within the request. For exploration, return findings and a recommendation; for implementation, finish the change and appropriate checks.
Ask only when missing facts or authorization prevent progress. Ask in this Diane session with a recommended Yes/No decision when possible. Do not ask whether to start.
Do not assume permission to publish, delete unrelated data, or perform external actions beyond the request.
Finish with the outcome, verification evidence, artifact paths, and unresolved issues. The originating todo remains user-controlled.
"""


def directory(request: Request) -> Path:
    path = request.app.state.chat_store.base_dir / "sticky_todo"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write(path: Path, data: dict):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(path)


def read(path: Path) -> dict:
    if not path.exists():
        raise HTTPException(404, "Handoff not found")
    return json.loads(path.read_text(encoding="utf-8"))


@router.put("/defaults")
async def save_defaults(body: Defaults, request: Request):
    # Published by the workbench from the same payload builder as its composer.
    write(directory(request) / "defaults.json", body.model_dump(mode="json"))
    return {"ok": True}


@router.get("/projects")
async def projects(request: Request):
    path = directory(request) / "defaults.json"
    if not path.exists():
        raise HTTPException(409, "Open Diane and select a project first")
    data = read(path)
    return {"projects": data["projects"], "active_project_id": data["active_project_id"], "version": 1}


def status(request: Request, record: dict) -> dict:
    run = request.app.state.chat_v2_store.get_run(record["run_id"]) if record.get("run_id") else None
    store = request.app.state.chat_v2_store
    latest = store.list_task_records(thread_id=record["session_id"], limit=1)
    if latest and latest[0].active_run_id:
        run = store.get_run(latest[0].active_run_id) or run
    snapshot = store.get_task_snapshot(run.task_id) if run else None
    raw = run.status if run else "needs_input"
    mapping = {"queued": "queued", "waiting_dependency": "queued", "running": "running", "completed": "ready", "failed": "failed",
               "stopped": "stopped", "cancelled": "stopped", "needs_input": "attention", "blocked": "attention", "paused": "attention"}
    state = mapping.get(raw, "attention")
    summary = (snapshot.blocker or snapshot.latest_progress) if snapshot else record.get("error", "Dispatch interrupted. Open the session in Diane.")
    backend = run.metadata.get("backend_result", {}) if run else {}
    raw_result = backend.get("raw_result", {})
    final_text = raw_result.get("final_text") or raw_result.get("final_response") or backend.get("summary")
    if state == "ready" and isinstance(final_text, str) and final_text:
        summary = final_text
    # Fill only our untouched placeholder. Later Diane follow-ups remain authoritative.
    if state in {"ready", "failed", "stopped", "attention"} and run and run.run_id == record.get("run_id"):
        chats = request.app.state.chat_store
        thread = chats.get_thread(record["project"]["id"], record["session_id"])
        if thread:
            for message in thread.messages:
                if message.id == "todo-result" and not message.content and summary:
                    message.content = summary
                    if message.task_run_ref:
                        message.task_run_ref.status = raw
                    chats.save_thread(thread, auto_title=False)
                    break
    if record.get("phase") == "prepared" and (not run or run.run_id == record.get("run_id")) and raw not in {"running", "completed", "stopped", "failed"}:
        state, summary = "attention", "Dispatch interrupted before execution. Continue in Diane."
    return {"request_id": record["request_id"], "task_key": record["task_key"], "session_id": record["session_id"],
            "workflow_id": record["project"]["id"], "project_name": record["project"]["name"],
            "run_id": run.run_id if run else record.get("run_id"), "status": state, "summary": summary,
            "artifacts": snapshot.latest_artifact_refs if snapshot else [],
            "updated_at": run.updated_at if run else record["created_at"],
            "stopping": bool(run and record.get("stopping_run_id") == run.run_id) and state in {"queued", "running"}}


@router.post("/requests")
async def dispatch(body: Dispatch, request: Request):
    # One process-wide lock serializes admission; no network awaits occur until the
    # durable receipt exists. Retries never start a second execution.
    if not hasattr(request.app.state, "sticky_todo_lock"):
        request.app.state.sticky_todo_lock = asyncio.Lock()
    async with request.app.state.sticky_todo_lock:
        path = directory(request) / f"{body.request_id}.json"
        fingerprint = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        if path.exists():
            record = read(path)
            if record["fingerprint"] != fingerprint:
                raise HTTPException(409, "This dispatch ID already has a different request")
            return status(request, record)
        defaults_path = directory(request) / "defaults.json"
        if not defaults_path.exists():
            raise HTTPException(409, "Open Diane and select a project first")
        defaults = Defaults.model_validate(read(defaults_path))
        project_id = body.project_id or defaults.active_project_id
        project = next((p for p in defaults.projects if p.id == project_id), None)
        if not project or not Path(project.root).is_absolute() or not Path(project.root).is_dir():
            raise HTTPException(409, "Select an existing project folder in Diane before running this task")
        session_id = "todo-" + body.request_id.hex
        record = {"request_id": str(body.request_id), "task_key": body.task_key, "fingerprint": fingerprint,
                  "project": project.model_dump(), "session_id": session_id, "phase": "prepared",
                  "created_at": datetime.now(timezone.utc).isoformat()}
        text = RULES + "\nRequest\n" + body.text
        if body.parent:
            text += "\nParent context\n" + body.parent
        if body.steps:
            text += "\nSteps\n" + "\n".join("- " + step for step in body.steps)
        chats = request.app.state.chat_store
        thread = ChatThread(id=session_id, workflow_id=project.id, title=body.text[:100],
                            messages=[ChatMessage(role="user", content=text)])
        chats.save_thread(thread, auto_title=False)
        chats.set_thread_meta(project.id, session_id, {"mode": "agent", "workspace_id": project.id, "workspace_root": project.root})
        write(path, record)
        req = ChatMessageRequest(workflow_id=project.id, thread_id=session_id, message=text, mode="agent",
            surface_context={"workspace_root": project.root, "workspace_id": project.id, "workspace_mode": "work"})
        created = await create_agent_run(request=request, req=req)
        ref = created.get("task_run_ref") or {}
        record["run_id"] = ref.get("run_id")
        if not record["run_id"]:
            record["error"] = "Request needs attention in Diane."
            write(path, record)
            return status(request, record)
        thread.messages.append(ChatMessage(id="todo-result", role="assistant", content="",
            task_run_ref=TaskRunRef(task_id=ref.get("task_id"), run_id=record["run_id"], status="queued", workspace_id=project.id, workspace_root=project.root)))
        chats.save_thread(thread, auto_title=False)
        record["phase"] = "submitted"
        write(path, record)
        execution = defaults.execute.model_copy(update={"background": True})
        try:
            await execute_agent_run(record["run_id"], execute=execution, request=request)
        except Exception:
            record["phase"] = "prepared"
            write(path, record)
            raise
        return status(request, record)


@router.get("/requests/{request_id}")
async def get_request(request_id: UUID, request: Request):
    return status(request, read(directory(request) / f"{request_id}.json"))


@router.post("/requests/{request_id}/stop")
async def stop_request(request_id: UUID, request: Request):
    path = directory(request) / f"{request_id}.json"
    record = read(path)
    current = status(request, record)
    if current.get("run_id") and current["status"] in {"queued", "running"} and not current["stopping"]:
        run_id = current["run_id"]
        await append_agent_run_command(run_id, AgentRunCommand(command="stop", idempotency_key=f"todo-stop-{request_id}-{run_id}"), request=request)
        record["stopping_run_id"] = run_id
        write(path, record)
    return status(request, record)
