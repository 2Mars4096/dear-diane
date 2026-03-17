"""Persistent store for furnace training sessions (Plan 36-1).

Implements the FurnaceSessionStore for resumable recipe training lifecycle:
sessions as JSON files in ~/.dan/furnace/sessions/, with create, load, save,
checkpoint, paper-status updates, resume, and pause operations.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from dan.engine.recipe.models import BatchCheckpoint, FurnaceSession, PaperStatus

logger = logging.getLogger(__name__)


class FurnaceSessionStore:
    """Persistent store for furnace training sessions.

    Stores sessions as JSON files in ~/.dan/furnace/sessions/.
    Each session file: {session_id}.json
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        # Default to ~/.dan/furnace/sessions/
        self._base_dir = Path(base_dir or os.path.expanduser("~/.dan/furnace/sessions"))
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, session: FurnaceSession) -> None:
        """Persist session state to disk."""
        path = self._base_dir / f"{session.session_id}.json"
        session.updated_at = time.time()
        path.write_text(session.model_dump_json(indent=2), encoding="utf-8")

    def load(self, session_id: str) -> FurnaceSession | None:
        """Load session by ID. Returns None if not found."""
        path = self._base_dir / f"{session_id}.json"
        if not path.exists():
            return None
        return FurnaceSession.model_validate_json(path.read_text(encoding="utf-8"))

    def list_sessions(
        self,
        corpus_id: str | None = None,
        recipe_id: str | None = None,
        status: str | None = None,
    ) -> list[FurnaceSession]:
        """List all sessions, optionally filtered by corpus_id, recipe_id, or status."""
        sessions: list[FurnaceSession] = []
        for p in self._base_dir.glob("*.json"):
            try:
                s = FurnaceSession.model_validate_json(p.read_text(encoding="utf-8"))
                if corpus_id and s.corpus_id != corpus_id:
                    continue
                if recipe_id and s.recipe_id != recipe_id:
                    continue
                if status and s.status != status:
                    continue
                sessions.append(s)
            except Exception as exc:
                logger.warning("Skipping corrupt session file %s: %s", p, exc)
                continue
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)

    def delete(self, session_id: str) -> bool:
        """Delete session file. Returns True if deleted."""
        path = self._base_dir / f"{session_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def create_session(
        self,
        corpus_id: str,
        recipe_id: str,
        paper_ids: list[str] | None = None,
        *,
        name: str = "",
        topic: str = "",
        description: str = "",
        variant_label: str = "",
        tags: list[str] | None = None,
        target_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> FurnaceSession:
        """Create a new session with papers in PENDING status."""
        queue = {pid: PaperStatus.PENDING for pid in (paper_ids or [])}
        session_metadata = dict(metadata or {})
        if target_count:
            session_metadata["target_count"] = target_count
        session = FurnaceSession(
            corpus_id=corpus_id,
            recipe_id=recipe_id,
            name=name or topic or corpus_id,
            topic=topic,
            description=description,
            variant_label=variant_label,
            tags=list(tags or []),
            status="paused",
            paper_queue=queue,
            metadata=session_metadata,
        )
        self.save(session)
        return session

    def find_by_name(self, name: str) -> FurnaceSession | None:
        """Find first session matching *name* (case-insensitive substring)."""
        needle = name.lower()
        for p in self._base_dir.glob("*.json"):
            try:
                s = FurnaceSession.model_validate_json(p.read_text(encoding="utf-8"))
                if needle in s.name.lower():
                    return s
            except Exception:
                continue
        return None

    def add_checkpoint(
        self,
        session_id: str,
        checkpoint: BatchCheckpoint,
    ) -> FurnaceSession | None:
        """Add a checkpoint to a session."""
        session = self.load(session_id)
        if session is None:
            return None
        session.checkpoints.append(checkpoint)
        session.total_token_usage += checkpoint.token_usage
        session.total_cost_usd += checkpoint.cost_usd
        self.save(session)
        return session

    def update_paper_status(
        self,
        session_id: str,
        paper_id: str,
        status: PaperStatus,
    ) -> bool:
        """Update a paper's processing status idempotently."""
        session = self.load(session_id)
        if session is None or paper_id not in session.paper_queue:
            return False
        session.paper_queue[paper_id] = status
        self.save(session)
        return True

    def add_papers(
        self,
        session_id: str,
        paper_ids: list[str],
    ) -> FurnaceSession | None:
        """Add new papers to an existing session (for resume-with-new-batch)."""
        session = self.load(session_id)
        if session is None:
            return None
        for pid in paper_ids:
            if pid not in session.paper_queue:
                session.paper_queue[pid] = PaperStatus.PENDING
        self.save(session)
        return session

    def set_tags(
        self,
        session_id: str,
        tags: list[str],
    ) -> FurnaceSession | None:
        """Replace a session's user-defined tags."""
        session = self.load(session_id)
        if session is None:
            return None
        session.tags = list(tags)
        self.save(session)
        return session

    def resume_session(self, session_id: str) -> FurnaceSession | None:
        """Resume a paused/failed session. Marks status active, keeps existing progress."""
        session = self.load(session_id)
        if session is None:
            return None
        if session.status in ("paused", "failed"):
            session.status = "active"
            self.save(session)
        return session

    def pause_session(self, session_id: str) -> bool:
        """Pause an active session."""
        session = self.load(session_id)
        if session is None:
            return False
        session.status = "paused"
        self.save(session)
        return True

    def get_resume_point(self, session_id: str) -> dict[str, Any] | None:
        """Get the resume point for a session: current phase, next pending papers."""
        session = self.load(session_id)
        if session is None:
            return None
        pending = session.papers_by_status(PaperStatus.PENDING)
        return {
            "session_id": session.session_id,
            "current_phase": session.current_phase.value,
            "current_batch_index": session.current_batch_index,
            "pending_count": len(pending),
            "pending_papers": pending,
            "last_checkpoint": (
                session.checkpoints[-1].model_dump() if session.checkpoints else None
            ),
            "total_ingested": (
                len(session.papers_by_status(PaperStatus.INGESTED))
                + len(session.papers_by_status(PaperStatus.EXTRACTED))
            ),
        }
