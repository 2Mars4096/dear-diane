"""Furnace distillation API router (Plan 36-7, Task 1).

Endpoints for creating, managing, and monitoring furnace training sessions.
Progress is streamed via Server-Sent Events (SSE).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from dan.server.routers.dependencies import (
    get_furnace_session_store,
    is_furnace_enabled,
)

logger = logging.getLogger(__name__)
router = APIRouter()

ARTIFACTS_ROOT = Path(
    os.environ.get("DAN_FURNACE_ARTIFACTS_DIR", "~/.dan/furnace/artifacts")
).expanduser()
PHASE_TIMEOUT_SECONDS = float(os.environ.get("DAN_FURNACE_PHASE_TIMEOUT_SECONDS", "120"))
READ_CHUNK_PAGES = int(os.environ.get("DAN_FURNACE_READ_CHUNK_PAGES", "20"))
READ_CHUNK_TIMEOUT_SECONDS = float(
    os.environ.get("DAN_FURNACE_READ_CHUNK_TIMEOUT_SECONDS", "0")
)
RECIPE_PILL_MAX_CHARS = int(os.environ.get("DAN_FURNACE_RECIPE_PILL_MAX_CHARS", "8000"))

_session_locks: dict[str, asyncio.Lock] = {}
_session_progress: dict[str, asyncio.Queue[dict[str, Any]]] = {}


def _get_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


def _require_furnace() -> None:
    if not is_furnace_enabled():
        raise HTTPException(status_code=403, detail="Furnace API is disabled (set DAN_FURNACE_API_ENABLED=1)")


def _publish_progress(session_id: str, event: dict[str, Any]) -> None:
    """Push a progress event to any listening SSE clients."""
    event.setdefault("timestamp", time.time())
    queue = _session_progress.get(session_id)
    if queue is not None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Progress queue full for session %s", session_id)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    name: str = ""
    topic: str = ""
    description: str = ""
    corpus_id: str = ""
    recipe_id: str = ""
    parent_session_id: str = ""
    inherit_sources: bool = False
    variant_label: str = ""
    target_count: int = 10


class AddSourcesRequest(BaseModel):
    source_ids: list[str] = Field(default_factory=list)
    pdf_paths: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class SetBudgetRequest(BaseModel):
    budget_limit_usd: float


class UpdateSessionTagsRequest(BaseModel):
    tags: list[str] | None = None
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class SessionSummary(BaseModel):
    session_id: str
    recipe_id: str
    name: str
    topic: str
    status: str
    current_phase: str
    source_count: int
    processed_count: int
    total_cost_usd: float
    variant_label: str = ""
    parent_session_id: str = ""
    family_session_id: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: float
    updated_at: float


def _session_to_summary(s: Any) -> dict[str, Any]:
    from dan.engine.recipe.models import PaperStatus

    total = len(s.paper_queue)
    processed = sum(
        1 for st in s.paper_queue.values() if st == PaperStatus.EXTRACTED
    )
    metadata = getattr(s, "metadata", {}) or {}
    return SessionSummary(
        session_id=s.session_id,
        recipe_id=s.recipe_id,
        name=s.name,
        topic=s.topic,
        status=s.status,
        current_phase=s.current_phase.value if hasattr(s.current_phase, "value") else str(s.current_phase),
        source_count=total,
        processed_count=processed,
        total_cost_usd=s.total_cost_usd,
        variant_label=s.variant_label or "",
        parent_session_id=str(metadata.get("parent_session_id") or ""),
        family_session_id=str(metadata.get("family_session_id") or s.session_id),
        tags=list(getattr(s, "tags", []) or []),
        created_at=s.created_at,
        updated_at=s.updated_at,
    ).model_dump()


# ---------------------------------------------------------------------------
# Source type detection (Task 6)
# ---------------------------------------------------------------------------


def _detect_source_type(source_id: str) -> str:
    """Classify source type from ID heuristics."""
    sid = source_id.lower()
    if sid.endswith(".ipynb") or "notebook" in sid:
        return "notebook"
    if sid.endswith(".pdf"):
        return "paper"
    if any(sid.endswith(ext) for ext in (".md", ".rst", ".txt")):
        return "documentation"
    return "paper"


def _detect_source_type_from_url(url: str) -> str:
    """Classify source type from URL pattern."""
    u = url.lower()
    if "kaggle.com" in u and "/code/" in u:
        return "notebook"
    if "kaggle.com" in u:
        return "notebook"
    if "arxiv.org" in u:
        return "paper"
    if "github.com" in u and ".ipynb" in u:
        return "notebook"
    if any(domain in u for domain in ("medium.com", "towardsdatascience.com", "blog.", "substack.com")):
        return "blog"
    if any(domain in u for domain in ("docs.", "readthedocs", "documentation")):
        return "documentation"
    if u.endswith(".pdf"):
        return "paper"
    return "blog"


def _url_to_source_id(url: str) -> str:
    """Derive a source ID from a URL."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    if path_parts:
        stem = path_parts[-1].split(".")[0][:40]
        if stem:
            return stem
    return f"url-{int(time.time())}"


def _split_pasted_entries(value: str) -> list[str]:
    """Split accidental multi-entry pasted strings into individual entries."""
    text = str(value or "").strip()
    if not text:
        return []

    # Prefer explicit separators first.
    if any(ch in text for ch in ("\n", ",", ";", "\t")):
        parts = re.split(r"[\n,;\t]+", text)
        return [p.strip() for p in parts if p.strip()]

    # Handle accidental one-line pasted paths/urls separated by spaces.
    url_matches = re.findall(r"https?://\S+", text)
    if len(url_matches) > 1:
        return url_matches
    path_matches = re.findall(r"(?:~/|/)\S+", text)
    if len(path_matches) > 1:
        return path_matches

    parts = [p.strip() for p in re.split(r"\s+", text) if p.strip()]
    if len(parts) > 1 and all(
        p.startswith(("http://", "https://", "/", "~/", "./", "../")) for p in parts
    ):
        return parts

    return [text]


def _normalize_tags(tags: list[str]) -> list[str]:
    """Normalize user-defined session tags for consistent filtering."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = re.sub(r"\s+", " ", str(raw or "").strip().lower())
        if not tag:
            continue
        tag = tag[:32]
        if tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized[:12]


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _slug_fragment(value: str, fallback: str) -> str:
    """Build a filesystem/API-friendly slug fragment."""
    text = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return (text or fallback)[:30]


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------


@router.post("/api/furnace/sessions")
async def create_session(body: CreateSessionRequest):
    """Create a new furnace training session with ingredient ledger."""
    _require_furnace()
    store = get_furnace_session_store()
    provided = body.model_fields_set

    parent_session = None
    if body.parent_session_id:
        parent_session = store.load(body.parent_session_id)
        if parent_session is None:
            raise HTTPException(
                status_code=404,
                detail=f"Parent session {body.parent_session_id} not found",
            )

    effective_topic = (
        body.topic
        if "topic" in provided
        else (parent_session.topic if parent_session is not None else body.topic)
    )
    effective_description = (
        body.description
        if "description" in provided
        else (
            parent_session.description
            if parent_session is not None
            else body.description
        )
    )
    effective_name = (
        body.name
        if "name" in provided
        else (
            parent_session.name
            if parent_session is not None
            else (effective_topic or "Untitled Session")
        )
    )
    effective_target_count = (
        body.target_count
        if "target_count" in provided
        else int(
            ((parent_session.metadata or {}).get("target_count") if parent_session is not None else None)
            or body.target_count
        )
    )
    effective_variant_label = body.variant_label.strip()
    if not effective_variant_label and parent_session is not None:
        effective_variant_label = effective_name.strip()

    corpus_id = (
        body.corpus_id
        or (
            parent_session.corpus_id
            if parent_session is not None
            else f"corpus-{_slug_fragment(effective_topic, 'session')}"
            if effective_topic
            else f"corpus-{int(time.time())}"
        )
    )

    if body.recipe_id:
        recipe_id = body.recipe_id
    elif effective_variant_label:
        recipe_id = (
            f"{_slug_fragment(effective_topic, 'recipe')}-pill-"
            f"{_slug_fragment(effective_variant_label, 'variant')}"
        )
    elif effective_topic:
        recipe_id = f"{_slug_fragment(effective_topic, 'recipe')}-pill"
    else:
        recipe_id = f"recipe-{int(time.time())}"

    inherited_source_ids: list[str] = []
    inherited_tags: list[str] = []
    session_metadata: dict[str, Any] = {}
    if parent_session is not None:
        session_metadata = copy.deepcopy(parent_session.metadata or {})
        inherited_tags = list(getattr(parent_session, "tags", []) or [])
        session_metadata.pop("last_error", None)
        session_metadata.pop("cancelled", None)
        session_metadata["parent_session_id"] = parent_session.session_id
        session_metadata["family_session_id"] = (
            session_metadata.get("family_session_id")
            or parent_session.metadata.get("family_session_id")
            or parent_session.session_id
        )
        session_metadata["forked_from_recipe_id"] = parent_session.recipe_id
        if parent_session.variant_label:
            session_metadata["forked_from_variant_label"] = parent_session.variant_label
        if body.inherit_sources:
            inherited_source_ids = list(parent_session.paper_queue.keys())
            session_metadata["inherited_source_count"] = len(inherited_source_ids)
        else:
            session_metadata.pop("pdf_paths", None)
            session_metadata.pop("urls", None)
            session_metadata.pop("source_types", None)
            session_metadata.pop("inherited_source_count", None)

    session = store.create_session(
        corpus_id=corpus_id,
        recipe_id=recipe_id,
        paper_ids=inherited_source_ids,
        name=effective_name,
        topic=effective_topic,
        description=effective_description,
        variant_label=effective_variant_label,
        tags=inherited_tags,
        target_count=effective_target_count,
        metadata=session_metadata,
    )

    artifact_dir = ARTIFACTS_ROOT / session.session_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    return {
        "session": session.model_dump(),
        "artifact_dir": str(artifact_dir),
    }


@router.post("/api/furnace/sessions/{session_id}/sources")
async def add_sources(session_id: str, body: AddSourcesRequest):
    """Add papers/sources to an existing session."""
    _require_furnace()
    store = get_furnace_session_store()
    session = store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    all_ids: list[str] = []
    pdf_paths: dict[str, str] = session.metadata.get("pdf_paths", {})
    urls: dict[str, str] = session.metadata.get("urls", {})
    source_types: dict[str, str] = session.metadata.get("source_types", {})

    parsed_source_ids: list[str] = []
    for raw in body.source_ids:
        parsed_source_ids.extend(_split_pasted_entries(raw))
    for sid in _dedupe_keep_order(parsed_source_ids):
        all_ids.append(sid)
        source_types[sid] = _detect_source_type(sid)

    parsed_pdf_paths: list[str] = []
    for raw in body.pdf_paths:
        parsed_pdf_paths.extend(_split_pasted_entries(raw))
    for pdf_path in _dedupe_keep_order(parsed_pdf_paths):
        expanded = os.path.expanduser(pdf_path)
        if not os.path.isabs(expanded):
            raise HTTPException(status_code=400, detail=f"PDF path must be absolute or use ~/: {pdf_path}")
        paper_id = Path(expanded).stem
        all_ids.append(paper_id)
        pdf_paths[paper_id] = expanded
        source_types[paper_id] = "paper"

    parsed_urls: list[str] = []
    for raw in body.urls:
        parsed_urls.extend(_split_pasted_entries(raw))
    for url in _dedupe_keep_order(parsed_urls):
        if not url.lower().startswith(("http://", "https://")):
            continue
        source_id = _url_to_source_id(url)
        all_ids.append(source_id)
        urls[source_id] = url
        source_types[source_id] = _detect_source_type_from_url(url)

    if not all_ids:
        raise HTTPException(status_code=400, detail="No sources provided")

    updated = store.add_papers(session_id, all_ids)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    updated.metadata["pdf_paths"] = pdf_paths
    updated.metadata["urls"] = urls
    updated.metadata["source_types"] = source_types
    store.save(updated)

    return {"session_id": session_id, "added": len(all_ids), "total_sources": len(updated.paper_queue)}


@router.post("/api/furnace/sessions/{session_id}/start")
async def start_session(session_id: str, request: Request):
    """Trigger the furnace distillation pipeline for a session."""
    _require_furnace()
    store = get_furnace_session_store()

    lock = _get_lock(session_id)
    if lock.locked():
        return {"session_id": session_id, "status": "already_running"}

    session = store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    if session.status == "active":
        return {"session_id": session_id, "status": "already_running"}

    if session.status == "completed":
        raise HTTPException(status_code=409, detail="Session is already completed; create a new session or variant")

    async def _run_furnace() -> None:
        async with lock:
            s = store.load(session_id)
            if s is None:
                return
            s.status = "active"
            store.save(s)
            _publish_progress(session_id, {"type": "session_started", "session_id": session_id})

            try:
                await _execute_furnace_pipeline(session_id, store, request.app)
            except Exception as exc:
                logger.exception("Furnace pipeline failed for session %s", session_id)
                s = store.load(session_id)
                if s is not None:
                    s.status = "failed"
                    s.metadata["last_error"] = str(exc)
                    store.save(s)
                _publish_progress(session_id, {"type": "session_failed", "error": str(exc)})

    asyncio.create_task(_run_furnace())
    return {"session_id": session_id, "status": "starting"}


@router.post("/api/furnace/sessions/{session_id}/pause")
async def pause_session(session_id: str):
    """Pause an active session."""
    _require_furnace()
    store = get_furnace_session_store()
    ok = store.pause_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found or not active")
    _publish_progress(session_id, {"type": "session_paused"})
    return {"session_id": session_id, "status": "paused"}


@router.post("/api/furnace/sessions/{session_id}/resume")
async def resume_session(session_id: str, request: Request):
    """Resume a paused/failed session."""
    _require_furnace()
    store = get_furnace_session_store()
    session = store.resume_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    lock = _get_lock(session_id)

    async def _resume() -> None:
        async with lock:
            _publish_progress(session_id, {"type": "session_resumed"})
            try:
                await _execute_furnace_pipeline(session_id, store, request.app)
            except Exception as exc:
                logger.exception("Furnace resume failed for session %s", session_id)
                s = store.load(session_id)
                if s is not None:
                    s.status = "failed"
                    s.metadata["last_error"] = str(exc)
                    store.save(s)
                _publish_progress(session_id, {"type": "session_failed", "error": str(exc)})

    asyncio.create_task(_resume())
    return {"session_id": session_id, "status": "resuming"}


@router.post("/api/furnace/sessions/{session_id}/cancel")
async def cancel_session(session_id: str):
    """Cancel / fail a session."""
    _require_furnace()
    store = get_furnace_session_store()
    session = store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    session.status = "failed"
    session.metadata["cancelled"] = True
    store.save(session)
    _publish_progress(session_id, {"type": "session_cancelled"})
    return {"session_id": session_id, "status": "failed"}


@router.delete("/api/furnace/sessions/{session_id}")
async def delete_session(session_id: str, delete_artifacts: bool = True):
    """Delete a furnace session and optionally its persisted artifacts."""
    _require_furnace()
    store = get_furnace_session_store()
    session = store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    if session.status == "active":
        raise HTTPException(
            status_code=409,
            detail="Session is currently active. Pause/cancel it before deleting.",
        )

    deleted = store.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    queue = _session_progress.pop(session_id, None)
    if queue is not None:
        try:
            queue.put_nowait({"type": "session_deleted", "session_id": session_id})
        except asyncio.QueueFull:
            pass
    _session_locks.pop(session_id, None)

    artifact_dir = ARTIFACTS_ROOT / session_id
    artifacts_deleted = False
    if delete_artifacts and artifact_dir.exists():
        shutil.rmtree(artifact_dir, ignore_errors=True)
        artifacts_deleted = not artifact_dir.exists()

    return {
        "session_id": session_id,
        "deleted": True,
        "artifacts_deleted": artifacts_deleted if delete_artifacts else False,
    }


@router.get("/api/furnace/sessions")
async def list_sessions(
    corpus_id: str | None = None,
    recipe_id: str | None = None,
    status: str | None = None,
):
    """List furnace sessions with optional filters."""
    _require_furnace()
    store = get_furnace_session_store()
    sessions = store.list_sessions(corpus_id=corpus_id, recipe_id=recipe_id, status=status)
    return {"sessions": [_session_to_summary(s) for s in sessions]}


@router.get("/api/furnace/sessions/{session_id}")
async def get_session(session_id: str):
    """Get full session detail including progress and source queue."""
    _require_furnace()
    store = get_furnace_session_store()
    session = store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return {"session": session.model_dump()}


@router.get("/api/furnace/sessions/{session_id}/recipe")
async def get_recipe(session_id: str):
    """Return compiled recipe.md and skill.md artifacts for a session."""
    _require_furnace()
    store = get_furnace_session_store()
    session = store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    artifact_dir = ARTIFACTS_ROOT / session_id
    recipe_path = artifact_dir / "recipe.md"
    skill_path = artifact_dir / "skill.md"

    recipe_md = recipe_path.read_text(encoding="utf-8") if recipe_path.exists() else None
    skill_md = skill_path.read_text(encoding="utf-8") if skill_path.exists() else None

    if recipe_md is None and skill_md is None:
        raise HTTPException(
            status_code=404,
            detail="No compiled artifacts yet. Run the session first.",
        )

    return {
        "session_id": session_id,
        "recipe_md": recipe_md,
        "skill_md": skill_md,
        "artifact_dir": str(artifact_dir),
    }


# ---------------------------------------------------------------------------
# Quality & cost controls (Task 8)
# ---------------------------------------------------------------------------


_COST_PER_PAGE_TEXT = 0.001
_COST_PER_PAGE_VISION = 0.02
_COST_PER_PHASE_LLM = 0.05


@router.get("/api/furnace/sessions/{session_id}/estimate")
async def cost_estimate(session_id: str):
    """Pre-run cost estimate based on source count and estimated pages."""
    _require_furnace()
    store = get_furnace_session_store()
    session = store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    source_count = len(session.paper_queue)
    est_pages_per_source = 20
    total_pages = source_count * est_pages_per_source
    text_cost = total_pages * _COST_PER_PAGE_TEXT
    vision_cost = (total_pages * 0.15) * _COST_PER_PAGE_VISION
    phase_cost = 5 * _COST_PER_PHASE_LLM * max(1, source_count / 5)
    total_estimate = text_cost + vision_cost + phase_cost

    return {
        "session_id": session_id,
        "source_count": source_count,
        "estimated_pages": total_pages,
        "estimated_cost_usd": round(total_estimate, 4),
        "breakdown": {
            "text_extraction": round(text_cost, 4),
            "vision_extraction": round(vision_cost, 4),
            "llm_phases": round(phase_cost, 4),
        },
        "budget_limit_usd": session.budget_limit_usd,
    }


@router.post("/api/furnace/sessions/{session_id}/budget")
async def set_budget(session_id: str, body: SetBudgetRequest):
    """Set or update the budget ceiling for a session."""
    _require_furnace()
    store = get_furnace_session_store()
    session = store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    session.budget_limit_usd = body.budget_limit_usd
    store.save(session)
    return {
        "session_id": session_id,
        "budget_limit_usd": session.budget_limit_usd,
    }


@router.post("/api/furnace/sessions/{session_id}/tags")
async def update_session_tags(session_id: str, body: UpdateSessionTagsRequest):
    """Add, remove, or replace user-defined tags on a session."""
    _require_furnace()
    store = get_furnace_session_store()
    session = store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    if body.tags is not None:
        next_tags = _normalize_tags(body.tags)
    else:
        current = _normalize_tags(list(getattr(session, "tags", []) or []))
        remove_set = set(_normalize_tags(body.remove))
        next_tags = [tag for tag in current if tag not in remove_set]
        for tag in _normalize_tags(body.add):
            if tag not in next_tags:
                next_tags.append(tag)

    session.tags = next_tags
    store.save(session)
    return {
        "session_id": session_id,
        "tags": next_tags,
        "session": _session_to_summary(session),
    }


# ---------------------------------------------------------------------------
# SSE progress stream
# ---------------------------------------------------------------------------


@router.get("/api/furnace/sessions/{session_id}/events")
async def session_events(session_id: str, request: Request):
    """Server-Sent Events stream for real-time session progress."""
    _require_furnace()
    store = get_furnace_session_store()
    session = store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
    _session_progress[session_id] = queue

    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'connected', 'session_id': session_id})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
        finally:
            _session_progress.pop(session_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------


async def _execute_furnace_pipeline(
    session_id: str,
    store: Any,
    app: Any,
) -> None:
    """Run the full furnace pipeline: read → normalize → extract → aggregate → infer → project.

    Updates session state and publishes progress events throughout.
    """
    from dan.engine.recipe.models import FurnacePhase, PaperStatus

    session = store.load(session_id)
    if session is None:
        return

    pending = session.papers_by_status(PaperStatus.PENDING)
    ingested = session.papers_by_status(PaperStatus.INGESTED)
    all_to_process = pending + ingested

    if not all_to_process and not session.papers_by_status(PaperStatus.EXTRACTED):
        _publish_progress(session_id, {
            "type": "session_warning",
            "message": "No sources in queue. Add sources before starting.",
        })
        session.status = "paused"
        store.save(session)
        return

    # --- Phase: Read / Ingest ---
    _publish_progress(session_id, {"type": "phase_started", "phase": "read"})

    for source_id in pending:
        _publish_progress(session_id, {
            "type": "source_status",
            "source_id": source_id,
            "status": "ingesting",
        })

        try:
            # Source readers apply their own bounded timeouts (per-request or per-chunk).
            # Avoid wrapping the whole source read in one global timeout window, which can
            # incorrectly fail long PDFs even when chunked progress is healthy.
            read_result = await _read_source(source_id, session, app)
            text = read_result.get("text", "") if isinstance(read_result, dict) else str(read_result or "")
            if text:
                artifact_dir = ARTIFACTS_ROOT / session_id
                artifact_dir.mkdir(parents=True, exist_ok=True)
                (artifact_dir / f"{source_id}.txt").write_text(text, encoding="utf-8")
                if isinstance(read_result, dict):
                    chunk_summaries = read_result.get("chunk_summaries") or []
                    if chunk_summaries:
                        (artifact_dir / f"{source_id}.chunks.json").write_text(
                            json.dumps(chunk_summaries, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                store.update_paper_status(session_id, source_id, PaperStatus.INGESTED)
                _publish_progress(session_id, {
                    "type": "source_status",
                    "source_id": source_id,
                    "status": "ingested",
                })
            else:
                store.update_paper_status(session_id, source_id, PaperStatus.SKIPPED)
                _publish_progress(session_id, {
                    "type": "source_status",
                    "source_id": source_id,
                    "status": "skipped",
                    "reason": "No text extracted",
                })
        except Exception as exc:
            logger.warning("Failed to read source %s: %s", source_id, exc)
            store.update_paper_status(session_id, source_id, PaperStatus.SKIPPED)
            _publish_progress(session_id, {
                "type": "source_status",
                "source_id": source_id,
                "status": "skipped",
                "reason": str(exc),
            })

        session = store.load(session_id)
        if session is None or session.status == "paused":
            return

    _publish_progress(session_id, {"type": "phase_completed", "phase": "read"})

    # --- Collect all ingested texts ---
    session = store.load(session_id)
    if session is None:
        return

    artifact_dir = ARTIFACTS_ROOT / session_id
    all_texts: list[str] = []
    for sid in session.papers_by_status(PaperStatus.INGESTED) + session.papers_by_status(PaperStatus.EXTRACTED):
        txt_path = artifact_dir / f"{sid}.txt"
        if txt_path.exists():
            all_texts.append(f"=== Source: {sid} ===\n{txt_path.read_text(encoding='utf-8')}")

    if not all_texts:
        _publish_progress(session_id, {"type": "session_warning", "message": "No text to distill."})
        session.status = "paused"
        store.save(session)
        return

    combined_input = "\n\n".join(all_texts)

    # --- Five-pass furnace ---
    phases = [
        FurnacePhase.NORMALIZE,
        FurnacePhase.EXTRACT,
        FurnacePhase.AGGREGATE,
        FurnacePhase.INFER,
        FurnacePhase.PROJECT,
    ]

    phase_output = combined_input
    for phase in phases:
        session = store.load(session_id)
        if session is None or session.status == "paused":
            return

        if session.total_cost_usd >= session.budget_limit_usd:
            _publish_progress(session_id, {
                "type": "budget_exceeded",
                "total_cost": session.total_cost_usd,
                "limit": session.budget_limit_usd,
            })
            session.status = "paused"
            store.save(session)
            return

        session.current_phase = phase
        store.save(session)
        _publish_progress(session_id, {
            "type": "phase_started",
            "phase": phase.value,
        })

        try:
            phase_output = await asyncio.wait_for(
                _run_furnace_phase(
                    phase,
                    phase_output,
                    session,
                    app,
                ),
                timeout=PHASE_TIMEOUT_SECONDS,
            )
            (artifact_dir / f"phase_{phase.value}.json").write_text(
                json.dumps({"output": phase_output[:50000]}, ensure_ascii=False),
                encoding="utf-8",
            )
        except asyncio.TimeoutError as exc:
            logger.error(
                "Phase %s timed out for session %s after %.1fs",
                phase.value,
                session_id,
                PHASE_TIMEOUT_SECONDS,
            )
            timeout_msg = (
                f"Phase {phase.value} timed out after {PHASE_TIMEOUT_SECONDS:.0f}s. "
                "Check model provider latency/availability and retry."
            )
            _publish_progress(
                session_id,
                {
                    "type": "phase_failed",
                    "phase": phase.value,
                    "error": timeout_msg,
                },
            )
            raise RuntimeError(timeout_msg) from exc
        except Exception as exc:
            logger.exception("Phase %s failed for session %s", phase.value, session_id)
            _publish_progress(session_id, {
                "type": "phase_failed",
                "phase": phase.value,
                "error": str(exc),
            })
            raise

        for sid in session.papers_by_status(PaperStatus.INGESTED):
            store.update_paper_status(session_id, sid, PaperStatus.EXTRACTED)

        _publish_progress(session_id, {
            "type": "phase_completed",
            "phase": phase.value,
        })

    # --- Persist recipe artifacts ---
    recipe_full_md = _strip_markdown_fence(phase_output)
    recipe_md = recipe_full_md
    if len(recipe_full_md) > RECIPE_PILL_MAX_CHARS:
        try:
            recipe_md = await _compress_recipe_to_pill(
                recipe_full_md,
                app=app,
                max_chars=RECIPE_PILL_MAX_CHARS,
            )
            _publish_progress(
                session_id,
                {
                    "type": "recipe_compacted",
                    "max_chars": RECIPE_PILL_MAX_CHARS,
                    "full_chars": len(recipe_full_md),
                    "pill_chars": len(recipe_md),
                },
            )
        except Exception:
            logger.warning(
                "Recipe compaction failed; falling back to trimmed full recipe",
                exc_info=True,
            )
            recipe_md = recipe_full_md[:RECIPE_PILL_MAX_CHARS].strip()

    (artifact_dir / "recipe.md").write_text(recipe_md, encoding="utf-8")
    (artifact_dir / "recipe_full.md").write_text(recipe_full_md, encoding="utf-8")

    try:
        from dan.engine.recipe.recipe_compiler import RecipeCompiler
        from dan.engine.recipe.ingredient_ledger import IngredientLedger
        from dan.engine.recipe.corpus import CorpusReader

        session = store.load(session_id)
        if session is not None:
            ledger = IngredientLedger(session.corpus_id)
            memory_store = _get_memory_store_safe(app)
            if memory_store:
                from dan.engine.memory_store import MemoryKernel
                kernel = MemoryKernel(memory_store)
                reader = CorpusReader(kernel)
                compiler = RecipeCompiler(
                    session.corpus_id, session.recipe_id, reader, ledger
                )
                skill_md = compiler.compile_skill_md()
                (artifact_dir / "skill.md").write_text(skill_md, encoding="utf-8")
    except Exception:
        logger.debug("Skill compilation skipped", exc_info=True)

    # --- Mark completed ---
    session = store.load(session_id)
    if session is not None:
        session.status = "completed"
        store.save(session)
    _publish_progress(session_id, {
        "type": "session_completed",
        "session_id": session_id,
        "artifact_dir": str(artifact_dir),
    })


async def _read_source(source_id: str, session: Any, app: Any) -> dict[str, Any] | None:
    """Read a single source (PDF, URL, etc.) and return its text content."""
    metadata = session.metadata or {}
    pdf_paths = metadata.get("pdf_paths", {})
    urls = metadata.get("urls", {})

    if source_id in pdf_paths:
        return await _read_pdf_source(
            pdf_paths[source_id],
            source_id=source_id,
            session_id=getattr(session, "session_id", ""),
        )

    if source_id in urls:
        text = await _read_url_source(urls[source_id])
        if text:
            return {"text": text, "chunk_summaries": []}
        return None

    from dan.engine.recipe.acquisition import resolve_paper_paths
    paths = resolve_paper_paths(source_id)
    if paths.get("pdf_path") and os.path.isfile(paths["pdf_path"]):
        return await _read_pdf_source(
            paths["pdf_path"],
            source_id=source_id,
            session_id=getattr(session, "session_id", ""),
        )

    return None


def _extractive_chunk_summary(chunk_text: str, *, max_len: int = 500) -> str:
    lines = [ln.strip() for ln in chunk_text.splitlines() if ln.strip()]
    if not lines:
        return ""
    summary = " ".join(lines[:6]).strip()
    return summary[:max_len]


async def _read_pdf_source(
    pdf_path: str, *, source_id: str = "", session_id: str = ""
) -> dict[str, Any] | None:
    """Read a PDF file in page chunks and return aggregated text + chunk summaries."""
    try:
        from pypdf import PdfReader
    except Exception as exc:
        logger.warning("pypdf unavailable for chunked reading (%s); falling back", exc)
        try:
            from dan.tools.pdf_read import read_pdf_file
            result = await read_pdf_file(pdf_path, mode="text")
            return {"text": result.get("text", ""), "chunk_summaries": []}
        except Exception as inner_exc:
            logger.warning("PDF fallback read failed for %s: %s", pdf_path, inner_exc)
            return None

    try:
        total_pages = len(PdfReader(pdf_path).pages)
    except Exception as exc:
        logger.warning("Failed to open PDF %s: %s", pdf_path, exc)
        return None

    if total_pages <= 0:
        return {"text": "", "chunk_summaries": []}

    from dan.tools.pdf_read import read_pdf_file

    chunk_texts: list[str] = []
    chunk_summaries: list[dict[str, Any]] = []

    for start in range(0, total_pages, max(1, READ_CHUNK_PAGES)):
        end = min(total_pages, start + max(1, READ_CHUNK_PAGES))
        try:
            if READ_CHUNK_TIMEOUT_SECONDS > 0:
                chunk_result = await asyncio.wait_for(
                    read_pdf_file(
                        pdf_path,
                        mode="text",
                        start_page=start,
                        end_page=end,
                    ),
                    timeout=READ_CHUNK_TIMEOUT_SECONDS,
                )
            else:
                chunk_result = await read_pdf_file(
                    pdf_path,
                    mode="text",
                    start_page=start,
                    end_page=end,
                )
        except Exception as exc:
            logger.warning(
                "Chunk read failed for %s pages %d-%d: %s",
                pdf_path,
                start,
                end,
                exc,
            )
            continue

        chunk_text = str(chunk_result.get("text", "") or "").strip()
        if not chunk_text:
            continue
        chunk_texts.append(f"[Pages {start + 1}-{end}]\n{chunk_text}")
        chunk_summaries.append(
            {
                "source_id": source_id,
                "start_page": start + 1,
                "end_page": end,
                "summary": _extractive_chunk_summary(chunk_text),
            }
        )
        if session_id:
            _publish_progress(
                session_id,
                {
                    "type": "source_chunk",
                    "source_id": source_id,
                    "start_page": start + 1,
                    "end_page": end,
                    "summary": chunk_summaries[-1]["summary"],
                },
            )

    if not chunk_texts:
        return None
    return {
        "text": "\n\n".join(chunk_texts),
        "chunk_summaries": chunk_summaries,
    }


async def _read_url_source(url: str) -> str | None:
    """Fetch a URL and return its text content."""
    try:
        from dan.tools.web_fetch import web_fetch
        result = await web_fetch(url=url)
        return result.get("text") or result.get("content", "")
    except Exception as exc:
        logger.warning("URL fetch failed for %s: %s", url, exc)
        return None


async def _run_furnace_phase(
    phase: Any,
    input_text: str,
    session: Any,
    app: Any,
) -> str:
    """Run a single furnace phase via LLM."""
    from dan.engine.recipe.models import FurnacePhase

    prompts = {
        FurnacePhase.NORMALIZE: (
            "You are a bibliographic normalizer. For each source:\n"
            "- Verify or generate canonical ID (authorYEARkeyword format for papers)\n"
            "- Normalize author names, title, year, venue\n"
            "- Flag issues (missing metadata, duplicates)\n"
            "Return JSON: {sources: [{source_id, title, authors, year, venue, status, issues}]}"
        ),
        FurnacePhase.EXTRACT: (
            "You are a domain knowledge extractor. For each source:\n"
            "1. Extract claims (key findings)\n"
            "2. Extract methods (approaches used)\n"
            "3. Extract terminology (domain-specific terms + definitions)\n"
            "4. Extract measures (variables, metrics, outcomes)\n"
            "5. Identify rhetorical moves and question patterns\n"
            "6. Note citation norms\n"
            "7. Extract document structure: [{heading, level, one_sentence_summary}]\n"
            "Tag each with confidence (0-1) and evidence. Return structured JSON."
        ),
        FurnacePhase.AGGREGATE: (
            "You are a domain knowledge aggregator. Given extractions from multiple sources:\n"
            "1. Identify recurring patterns (3+ sources)\n"
            "2. Weight by venue quality and author diversity\n"
            "3. Merge duplicates, reconcile conflicts\n"
            "4. Promote high-recurrence items to domain-level\n"
            "5. Flag low-confidence or contradictory findings\n"
            "Return domain-level patterns as JSON with support counts."
        ),
        FurnacePhase.INFER: (
            "You are a domain taste analyst. Given aggregated patterns:\n"
            "1. Build directional association vectors between concepts\n"
            "2. Identify unexplored question zones\n"
            "3. Promote strong patterns into taste signals and writing rules\n"
            "4. Identify anti-patterns (weak moves, reviewer triggers)\n"
            "5. Characterize domain rhetorical taste\n"
            "Return taste_signals, writing_rules, anti_patterns, association_vectors as JSON."
        ),
        FurnacePhase.PROJECT: (
            "You are a recipe compiler. Given domain patterns, taste signals, writing rules, "
            "and anti-patterns, compile a recipe.md with these sections:\n"
            "## Domain Thesis\n## Core Concepts\n## Association Vectors\n"
            "## Methods And Identification\n## Rhetorical Taste\n"
            "## Writing Rules\n## Anti-Patterns\n"
            "Each section: concise, actionable, evidence-linked."
        ),
    }

    system_prompt = prompts.get(phase, "Process the following input.")
    user_prompt = f"Process the following for the {phase.value} phase:\n\n{input_text[:100000]}"

    try:
        result = await _llm_call(system_prompt, user_prompt, app)
        return result
    except Exception as exc:
        logger.exception("LLM call failed for phase %s", phase.value)
        raise


def _strip_markdown_fence(text: str) -> str:
    """Remove optional top-level markdown code fences."""
    content = str(text or "").strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return content


async def _compress_recipe_to_pill(recipe_text: str, app: Any, max_chars: int) -> str:
    """Compress long recipe output into a concise, execution-ready pill."""
    target_words = max(250, min(900, max_chars // 9))
    system_prompt = (
        "You are a recipe condenser. Rewrite the input into a compact, operational recipe pill. "
        "Keep only high-signal content and remove repetition and narrative filler.\n\n"
        "Output strict markdown with exactly these sections in this order:\n"
        "## Domain Thesis\n## Core Concepts\n## Association Vectors\n"
        "## Methods And Identification\n## Rhetorical Taste\n"
        "## Writing Rules\n## Anti-Patterns\n\n"
        "Rules:\n"
        "- Total length <= target word budget.\n"
        "- Each section 2-5 bullets, one line each.\n"
        "- Every bullet must be actionable or decision-relevant.\n"
        "- Include brief evidence tags like [src: ...] where possible.\n"
        "- Do not use code fences."
    )
    user_prompt = (
        f"Condense this recipe into <= {target_words} words. "
        f"Preserve only the strongest field signals.\n\n{recipe_text[:120000]}"
    )
    compressed = await _llm_call(system_prompt, user_prompt, app)
    cleaned = _strip_markdown_fence(compressed)
    return cleaned[:max_chars].strip()


async def _llm_call(system_prompt: str, user_prompt: str, app: Any) -> str:
    """Make an LLM call using the server's provider registry."""
    try:
        chat_manager = app.state.dan.chat_manager
        if chat_manager is not None:
            provider = chat_manager._providers.resolve(
                os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6")
            )
            model = os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6")
            result = await provider.complete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=model,
                temperature=0.3,
            )
            return result.text
    except Exception:
        logger.debug("Chat provider LLM call failed, falling back", exc_info=True)

    from dan.server.startup import _build_chat_provider_registry
    registry = _build_chat_provider_registry()
    model = os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6")
    provider = registry.resolve(model)
    result = await provider.complete(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=model,
        temperature=0.3,
    )
    return result.text


def _get_memory_store_safe(app: Any) -> Any:
    """Get memory store without raising HTTP exceptions."""
    try:
        state = getattr(app, "state", None)
        dan = getattr(state, "dan", None) if state else None
        if dan and dan.run_manager:
            from dan.engine.memory_store import FileSystemMemoryStore
            cfg = dan.run_manager.engine_config
            memory_dir = cfg.memory_dir if cfg else "./memory"
            return FileSystemMemoryStore(memory_dir)
    except Exception:
        pass
    return None
