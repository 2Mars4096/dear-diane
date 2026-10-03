"""Reader persistence: OCR text layers for project PDFs, keyed by file identity."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from diane.server.paths import resolve_graphs_dir

router = APIRouter(tags=["reader"])


def _key(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:32]


def _store() -> Path:
    base = Path(resolve_graphs_dir()) / "reader_ocr"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _identity(path: str) -> dict[str, Any]:
    file = Path(path)
    if not file.is_file():
        raise HTTPException(404, "PDF not found")
    stat = file.stat()
    return {"size": stat.st_size, "mtime": int(stat.st_mtime)}


@router.get("/api/reader/ocr")
def read_ocr(path: str) -> dict[str, Any]:
    """Saved OCR pages for this PDF, or empty when the file changed since they were made."""
    identity = _identity(path)
    target = _store() / f"{_key(path)}.json"
    if not target.is_file():
        return {"pages": [], "identity": identity}
    try:
        saved = json.loads(target.read_text())
    except (OSError, ValueError):
        return {"pages": [], "identity": identity}
    if saved.get("identity") != identity:
        return {"pages": [], "identity": identity, "stale": True}
    return {"pages": saved.get("pages", []), "identity": identity}


@router.put("/api/reader/ocr")
def write_ocr(path: str, body: dict[str, Any]) -> dict[str, Any]:
    identity = _identity(path)
    pages = [page for page in body.get("pages", []) if isinstance(page, dict) and isinstance(page.get("spans"), list)]
    target = _store() / f"{_key(path)}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"path": path, "identity": identity, "pages": pages}, ensure_ascii=False))
    temporary.replace(target)
    return {"saved": len(pages), "identity": identity}


from pydantic import BaseModel, Field


class TranscriptionInput(BaseModel):
    image: str = Field(max_length=8_000_100)
    text: str = Field(default='', max_length=4000)


@router.post('/api/reader/transcribe')
async def transcribe_selection(body: TranscriptionInput):
    from diane.server.reader_transcription import transcribe
    try:
        return await transcribe(body.image, body.text)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from None
