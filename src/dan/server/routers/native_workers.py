"""Workbench runtime discovery and parent-scoped worker inspection."""
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from dan.native_workers.sessions import discover, messages
from dan.server.chat_store import ChatMessage
import json
from dan.native_workers.catalog import catalog
from dan.native_workers.service import active_teams, read_workers
from dan.server.paths import resolve_graphs_dir

router = APIRouter(tags=["native-workers"])

@router.get("/api/native-workers/catalog")
def runtime_catalog():
    return catalog()

@router.get("/api/native-sessions")
def native_sessions(workspace: str):
    if not workspace or not Path(workspace).is_dir():
        raise HTTPException(400, "Choose an existing project folder first")
    return {"sessions": [{key: value for key, value in row.items() if key != "path"} for row in discover(workspace)]}

class SessionImport(BaseModel):
    source_id: str
    workspace: str
    workspace_id: str
    fork: bool = True

@router.post("/api/native-sessions/import")
def import_session(body: SessionImport, request: Request):
    if not body.fork:
        raise HTTPException(400, "Only forked imports are supported; originals stay untouched")
    source = next((row for row in discover(body.workspace) if row["id"] == body.source_id), None)
    if not source or not source["can_import"]:
        raise HTTPException(400, source["reason"] if source else "Session is no longer available for this folder")
    try:
        history = messages(source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not body.workspace_id or "/" in body.workspace_id or "\\" in body.workspace_id or body.workspace_id in {".", ".."}:
        raise HTTPException(400, "Invalid project ID")
    store = request.app.state.chat_store
    thread = store.create_thread(body.workspace_id, title=source["title"] + " (fork)")
    thread.messages = [ChatMessage(**message) for message in history]
    store.save_thread(thread)
    base = Path(resolve_graphs_dir()) / "native_imports"
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{thread.id}.json").write_text(json.dumps({**source, "fork": True}))
    return {"id": thread.id, "workflow_id": thread.workflow_id, "title": thread.title,
            "message_count": len(thread.messages), "created_at": thread.created_at.isoformat(), "updated_at": thread.updated_at.isoformat()}

@router.get("/api/native-workers/{parent_id}")
def list_workers(parent_id: str):
    return {"workers": read_workers(Path(resolve_graphs_dir()) / "native_workers", parent_id)}

@router.post("/api/native-workers/{parent_id}/{worker_id}/stop")
async def stop_worker(parent_id: str, worker_id: str):
    team = active_teams.get(parent_id)
    if not team or worker_id not in team.records:
        raise HTTPException(404, "No active worker with that parent")
    return await team.stop(worker_id)

@router.get("/api/native-workers/{parent_id}/{worker_id}/events")
def worker_events(parent_id: str, worker_id: str):
    base = Path(resolve_graphs_dir()) / "native_workers"
    if not any(row["worker_id"] == worker_id for row in read_workers(base, parent_id)):
        raise HTTPException(404, "Unknown worker")
    path = base / f"{worker_id}.jsonl"
    # Display a bounded tail; the complete durable log remains on disk.
    import json
    from collections import deque
    if not path.exists():
        return {"events": []}
    with path.open() as stream:
        return {"events": [json.loads(line) for line in deque(stream, maxlen=200)]}
