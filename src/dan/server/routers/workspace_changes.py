"""Read-only change review, with explicit local snapshot capture."""
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dan.server.paths import resolve_graphs_dir
from dan.server.routers.misc import _resolve_workspace_file_path
from dan import workspace_changes as review
router = APIRouter()


def root_path(root: str) -> str:
    resolved, _ = _resolve_workspace_file_path('.', root_path=root)
    return str(resolved)


@router.get('/api/workspace-changes')
def get_changes(root: str, thread: str, snapshot: str = ''):
    root = root_path(root)
    base = Path(resolve_graphs_dir())
    try:
        rows = review.snapshots(base, root, thread)
        selected = snapshot or (rows[0]['id'] if rows else '')
        return {'snapshots': rows, 'selected': selected, **(review.changes(base, root, thread, selected) if selected else {'files': [], 'omitted': []})}
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


class Capture(BaseModel):
    root: str
    thread: str


@router.post('/api/workspace-changes')
def capture_changes(body: Capture):
    root = root_path(body.root)
    try:
        review.capture(Path(resolve_graphs_dir()), root, body.thread, uuid4().hex, 'Manual review boundary')
        return get_changes(root, body.thread)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
