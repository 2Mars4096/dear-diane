from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DAN_DIR = Path.home() / ".dan"
DEFAULT_MEMORY_DIR = DAN_DIR / "conversation_memory"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


class ConversationSummary(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    summary: str
    workflow_id: str = ""
    topic_tags: list[str] = Field(default_factory=list)
    timestamp: float = Field(default_factory=time.time)


class ConversationMemoryStore:
    """Global cross-session conversation memory.

    Stores short summaries of past chat interactions for
    cross-session context injection.
    """

    def __init__(self, base_dir: Path | None = None, max_entries: int = 200) -> None:
        self._dir = base_dir or DEFAULT_MEMORY_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "_index.json"
        self._max_entries = max_entries
        self._entries: list[ConversationSummary] = self._load_index()

    def _load_index(self) -> list[ConversationSummary]:
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
                return [ConversationSummary.model_validate(d) for d in data]
            except Exception:
                logger.warning("Failed to load conversation memory index")
        return []

    def _save_index(self) -> None:
        data = [e.model_dump() for e in self._entries]
        _atomic_write_text(self._index_path, json.dumps(data, indent=2))

    def add_summary(
        self,
        summary: str,
        workflow_id: str = "",
        topic_tags: list[str] | None = None,
    ) -> ConversationSummary:
        """Add a conversation summary. Trims to max_entries."""
        entry = ConversationSummary(
            summary=summary,
            workflow_id=workflow_id,
            topic_tags=topic_tags or [],
        )
        self._entries.insert(0, entry)
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[: self._max_entries]
        self._save_index()
        return entry

    def get_recent(self, n: int = 10) -> list[ConversationSummary]:
        """Get the N most recent summaries."""
        return self._entries[:n]

    def search_by_keywords(
        self, keywords: list[str], limit: int = 5
    ) -> list[ConversationSummary]:
        """Simple keyword-based search over summaries and tags."""
        keywords_lower = [k.lower() for k in keywords]
        scored: list[tuple[int, ConversationSummary]] = []
        for entry in self._entries:
            score = 0
            text = (entry.summary + " " + " ".join(entry.topic_tags)).lower()
            for kw in keywords_lower:
                if kw in text:
                    score += 1
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: (-x[0], -x[1].timestamp))
        return [e for _, e in scored[:limit]]

    def format_context_block(self, n: int = 5) -> str:
        """Format recent summaries for system prompt injection."""
        recent = self.get_recent(n)
        if not recent:
            return ""
        lines = ["Recent conversation context:"]
        for entry in recent:
            wf = f" (workflow: {entry.workflow_id})" if entry.workflow_id else ""
            lines.append(f"- {entry.summary}{wf}")
        return "\n".join(lines)

    @property
    def count(self) -> int:
        return len(self._entries)
