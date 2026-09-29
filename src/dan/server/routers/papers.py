"""Paper sources, metadata, and resumable reading sessions on this Dear Diane host."""
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from dan.server import paper_library as library
from dan.server.routers.sessions import _store

router = APIRouter(prefix="/api/papers", tags=["papers"])
WORKFLOW = "_dan_reading"


class Source(BaseModel):
    kind: Literal["hugo", "pdf"]
    path: str = Field(min_length=1, max_length=4096)
    name: str = Field(default="", max_length=200)


class Settings(BaseModel):
    sources: list[Source] = Field(max_length=20)


@router.get("")
def get_catalogue():
    with library.LOCK:
        result = library.catalogue()
        for paper in result["papers"]:
            saved = library.read_json(library.session_path(paper["id"]), {})
            paper["reading_notes"] = "\n".join(str(comment.get("text", "")) + " " + str(comment.get("quote", "")) for comment in saved.get("comments", []))
        return {**result, "sessions": library.sessions()}


@router.put("/settings")
def save_settings(body: Settings):
    sources = []
    for source in body.sources:
        path = Path(source.path).expanduser().resolve()
        expected = path / "content" / "papers" if source.kind == "hugo" else path
        if not expected.is_dir():
            raise HTTPException(422, f"Folder not found on this host: {expected}")
        sources.append({"kind": source.kind, "path": str(path), "name": source.name.strip() or path.name})
    with library.LOCK:
        library.save_json(library.storage() / "settings.json", {"sources": sources})
        library._cache.clear()
    return {"sources": sources}


@router.get("/sessions/{identifier}")
def get_session(identifier: str):
    with library.LOCK:
        session = library.read_json(library.session_path(identifier), None)
        if session is None:
            raise HTTPException(404, "Reading session not found")
        return session


@router.post("/{identifier}/open")
def open_paper(identifier: str, request: Request):
    with library.LOCK:
        paper = library.find_paper(identifier)
        if not paper["available"]:
            raise HTTPException(404, "The PDF is missing. Check the paper link or reconnect its source folder.")
        path = library.session_path(identifier)
        session = library.read_json(path, None)
        if session is None:
            thread = _store(request).create_thread(WORKFLOW, title=f"Reading: {paper['title']}")
            _store(request).set_mode(WORKFLOW, thread.id, "agent")
            session = {"id": identifier, "paper_id": identifier, "title": paper["title"], "revision": 0,
                       "position": None, "comments": [], "references": {}, "pinned": False,
                       "link": {"threadId": thread.id}, "workflow_id": WORKFLOW}
        session.update(last_opened=datetime.now(timezone.utc).isoformat(), title=paper["title"])
        library.save_json(path, session)
        return {"paper": paper, "session": session}


class Position(BaseModel):
    page: int = Field(ge=1)
    top: float = Field(ge=-2, le=2, allow_inf_nan=False)
    left: float = Field(ge=0, le=1, allow_inf_nan=False)
    zoom: float = Field(ge=0.7, le=4, allow_inf_nan=False)


class Progress(BaseModel):
    revision: int = Field(ge=0)
    position: Position | None = None
    comments: list[dict] = Field(default_factory=list, max_length=200)
    references: dict = Field(default_factory=dict)


@router.put("/sessions/{identifier}/progress")
def save_progress(identifier: str, body: Progress):
    if len(body.model_dump_json()) > 2_000_000:
        raise HTTPException(413, "Reading notes are too large")
    with library.LOCK:
        session = get_session(identifier)
        if session["revision"] != body.revision:
            raise HTTPException(409, "This reading session changed elsewhere. Reopen it to load the saved version; local notes are retained on this device.")
        session.update(position=body.position.model_dump() if body.position else None,
                       comments=body.comments, references=body.references, revision=session["revision"] + 1)
        library.save_json(library.session_path(identifier), session)
        return session


class Link(BaseModel):
    threadId: str = Field(min_length=1, max_length=200)
    runId: str | None = Field(default=None, max_length=200)
    assistantId: str | None = Field(default=None, max_length=200)


@router.put("/sessions/{identifier}/link")
def save_link(identifier: str, body: Link, request: Request):
    with library.LOCK:
        session = get_session(identifier)
        if body.threadId != session["link"]["threadId"]:
            raise HTTPException(409, "Reading conversation identity cannot be changed")
        if body.runId:
            run = request.app.state.chat_v2_store.get_run(body.runId)
            if run is None or run.thread_id != body.threadId:
                raise HTTPException(422, "Run does not belong to this reading conversation")
        session["link"] = body.model_dump(exclude_none=True)
        library.save_json(library.session_path(identifier), session)
        return session["link"]


class Pin(BaseModel):
    pinned: bool


@router.put("/{identifier}/pin")
def pin_paper(identifier: str, body: Pin, request: Request):
    with library.LOCK:
        if not library.session_path(identifier).exists():
            open_paper(identifier, request)
        session = get_session(identifier)
        session["pinned"] = body.pinned
        library.save_json(library.session_path(identifier), session)
        return {"pinned": body.pinned}


@router.get("/{identifier}/pdf")
def paper_pdf(identifier: str):
    with library.LOCK:
        paper = library.find_paper(identifier)
        if not paper["available"]:
            raise HTTPException(404, "PDF unavailable")
        return FileResponse(paper["path"], media_type="application/pdf", filename=Path(paper["path"]).name, content_disposition_type="inline")
