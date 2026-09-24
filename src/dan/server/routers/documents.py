"""Bounded UTF-8 document editing with optimistic revision checks."""
import hashlib
import os
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from dan.server.routers.misc import _resolve_workspace_file_path

router = APIRouter()
LIMIT = 2_000_000
_write_lock = threading.Lock()


def read_text(path: Path) -> dict:
    try:
        with path.open('rb') as handle:
            raw = handle.read(LIMIT + 1)
    except OSError as exc:
        raise HTTPException(404, 'File could not be read') from exc
    if len(raw) > LIMIT:
        raise HTTPException(413, 'Text editing supports files up to 2 MB')
    try:
        text = raw.decode('utf-8')
        if '\x00' in text:
            raise ValueError('binary')
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(415, 'This file is not UTF-8 text') from exc
    return {'content': text, 'revision': hashlib.sha256(raw).hexdigest()}


@router.get('/api/workspace-files/document')
def get_document(path: str, root_path: str | None = None):
    resolved, _ = _resolve_workspace_file_path(path, root_path=root_path)
    return read_text(resolved)


class DocumentSave(BaseModel):
    path: str
    root_path: str | None = None
    content: str = Field(max_length=LIMIT)
    revision: str


@router.put('/api/workspace-files/document')
def save_document(body: DocumentSave):
    resolved, _ = _resolve_workspace_file_path(body.path, root_path=body.root_path)
    raw = body.content.encode('utf-8')
    if len(raw) > LIMIT or '\x00' in body.content:
        raise HTTPException(413, 'Save requires UTF-8 text up to 2 MB without null bytes')
    with _write_lock:
        current = read_text(resolved)
        if body.revision != current['revision']:
            raise HTTPException(409, 'File changed on disk. Download your edits before reopening it.')
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=resolved.parent, prefix='.dan-edit-', delete=False) as handle:
                temp = handle.name
                os.fchmod(handle.fileno(), resolved.stat().st_mode & 0o777)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, resolved)
        except OSError as exc:
            raise HTTPException(500, 'File could not be saved') from exc
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
    return {'revision': hashlib.sha256(raw).hexdigest()}
