"""Remote project metadata shared across browsers; transient tabs stay local."""
import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dan._atomic_file import atomic_write_text
from dan.server.paths import resolve_graphs_dir, resolve_workspace_root

router = APIRouter(prefix="/api/remote/registry")
FIELDS = {"id", "name", "removedFromDan", "icon", "color", "pinnedPaths", "researchConfig", "createdAt"}


def read():
    path = Path(resolve_graphs_dir()) / "remote-projects.json"
    if path.exists():
        return json.loads(path.read_text())
    workspace = resolve_workspace_root()
    return {"projects": {"remote-default": {"id": "remote-default", "name": Path(workspace).name, "pinnedPaths": [workspace], "createdAt": int(time.time() * 1000)}}}


@router.get("")
async def get_registry():
    if not os.environ.get("DAN_REMOTE_CONFIG"):
        return {"enabled": False}
    return {"enabled": True, **read()}


class Patch(BaseModel):
    projects: dict[str, dict] = Field(default_factory=dict, max_length=1000)


@router.patch("")
async def patch_registry(patch: Patch):
    if not os.environ.get("DAN_REMOTE_CONFIG"):
        raise HTTPException(404, "Project sync is only enabled on remote hosts")
    if len(patch.model_dump_json()) > 1024 * 1024:
        raise HTTPException(413, "Project update too large")
    value = read()
    # No awaits between read and atomic write: updates serialize in this process.
    for key, changes in patch.projects.items():
        if len(key) > 200 or any(field not in FIELDS for field in changes) or changes.get("id", key) != key:
            raise HTTPException(422, "Invalid project update")
        current = value["projects"].get(key, {})
        value["projects"][key] = {**current, **changes, "id": key}
    atomic_write_text(Path(resolve_graphs_dir()) / "remote-projects.json", json.dumps(value), mode=0o600)
    return {"enabled": True, **value}
