"""End-to-end chat audit record and filesystem-backed store.

Captures full provenance for every chat turn: user request, classifier/intent
decision, tool calls with args/results, cited sources, final answer, and links
to project/task/run/memory IDs.

Layout:
    {base_dir}/
        {surface_id}/
            {YYYY-MM-DD}.jsonl   # one record per chat turn, append-only
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_SECRET_PATTERN = re.compile(
    r"""(?x)
    (?:                         # env-var style: KEY=value
        (?:API_?KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)[A-Z_]*
        \s*[=:]\s*
    )
    \S+
    |
    (?:sk-[A-Za-z0-9_-]{12,})   # OpenAI-style or sk-prefixed API keys
    |
    (?:ghp_[A-Za-z0-9]{36,})    # GitHub PATs
    |
    (?:Bearer\s+\S{20,})        # Bearer tokens
    """,
    re.IGNORECASE,
)


def _redact_text(value: str) -> str:
    return _SECRET_PATTERN.sub("[REDACTED]", value)


def _redact_any(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_any(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact_any(v) for k, v in value.items()}
    return value


def _redact_secrets(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return a copy of *messages* with env-var-like secrets replaced."""
    out: list[dict[str, str]] = []
    for msg in messages:
        redacted = {}
        for k, v in msg.items():
            if isinstance(v, str):
                redacted[k] = _redact_text(v)
            else:
                redacted[k] = _redact_any(v)
        out.append(redacted)
    return out


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned or "default"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ToolCallRecord(BaseModel):
    """Single tool invocation within a chat turn."""

    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = ""
    status: str = "success"
    duration_ms: int = 0
    source_urls: list[str] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)


class ChatAuditRecord(BaseModel):
    """Durable provenance record for a single chat turn."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    timestamp: float = Field(default_factory=time.time)

    surface_id: str = ""
    project_id: str = ""
    task_id: str = ""
    turn_id: str = ""
    workflow_id: str = ""
    run_id: str = ""

    user_message: str = ""

    intent: str = ""
    mode: str = ""
    reuse_decision: str = ""

    prompt_messages: list[dict[str, str]] = Field(default_factory=list)
    model: str = ""

    tool_calls: list[ToolCallRecord] = Field(default_factory=list)

    assistant_message: str = ""
    cited_sources: list[str] = Field(default_factory=list)

    memory_item_ids: list[str] = Field(default_factory=list)

    def with_redacted(self) -> ChatAuditRecord:
        """Return a copy with secrets scrubbed from all string fields."""
        payload = self.model_dump(mode="python")
        payload["prompt_messages"] = _redact_secrets(self.prompt_messages)
        payload["user_message"] = _redact_text(self.user_message)
        payload["assistant_message"] = _redact_text(self.assistant_message)
        payload["cited_sources"] = [_redact_text(s) for s in self.cited_sources]
        payload["tool_calls"] = [
            ToolCallRecord.model_validate(
                {
                    "tool_name": tc.tool_name,
                    "args": _redact_any(tc.args),
                    "result_summary": _redact_text(tc.result_summary),
                    "status": tc.status,
                    "duration_ms": tc.duration_ms,
                    "source_urls": [_redact_text(u) for u in tc.source_urls],
                    "source_files": [_redact_text(p) for p in tc.source_files],
                }
            ).model_dump(mode="python")
            for tc in self.tool_calls
        ]
        return ChatAuditRecord.model_validate(payload)

    def with_redacted_prompt(self) -> ChatAuditRecord:
        """Backward-compatible alias for older tests/callers."""
        return self.with_redacted()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ChatAuditStore:
    """Append-only, filesystem-backed audit store for chat turns."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base = Path(base_dir or (Path.home() / ".dan" / "audit"))
        self._base.mkdir(parents=True, exist_ok=True)

    def _surface_dir(self, surface_id: str) -> Path:
        d = self._base / _safe_segment(surface_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _date_key(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

    # ---- write ----------------------------------------------------------

    def append(self, record: ChatAuditRecord) -> None:
        """Atomically append *record*; never raises to the caller."""
        try:
            safe = record.with_redacted()
            d = self._surface_dir(safe.surface_id)
            path = d / f"{self._date_key(safe.timestamp)}.jsonl"
            with path.open("a", encoding="utf-8") as f:
                f.write(safe.model_dump_json() + "\n")
        except Exception:
            logger.warning("Audit append failed", exc_info=True)

    # ---- read -----------------------------------------------------------

    def load_by_surface(
        self,
        surface_id: str,
        date: str | None = None,
        limit: int = 200,
    ) -> list[ChatAuditRecord]:
        """Load records for a surface, optionally filtered to a single date."""
        d = self._base / _safe_segment(surface_id)
        if not d.exists():
            return []
        if date:
            files = [d / f"{date}.jsonl"]
        else:
            files = sorted(d.glob("*.jsonl"), reverse=True)
        return self._load_from_files(files, limit=limit)

    def load_by_project(
        self,
        project_id: str,
        limit: int = 200,
    ) -> list[ChatAuditRecord]:
        """Scan all surfaces and return records matching *project_id*."""
        if not self._base.exists():
            return []
        files: list[Path] = []
        for surface_dir in sorted(self._base.iterdir(), reverse=True):
            if surface_dir.is_dir():
                files.extend(sorted(surface_dir.glob("*.jsonl"), reverse=True))
        return self._load_from_files(
            files,
            limit=limit,
            predicate=lambda r: r.project_id == project_id,
        )

    def load_by_turn(self, turn_id: str) -> ChatAuditRecord | None:
        """Find a single record by *turn_id* (scans all files)."""
        if not self._base.exists():
            return None
        for surface_dir in self._base.iterdir():
            if not surface_dir.is_dir():
                continue
            for path in surface_dir.glob("*.jsonl"):
                for rec in self._parse_file(path):
                    if rec.turn_id == turn_id:
                        return rec
        return None

    # ---- helpers --------------------------------------------------------

    def _load_from_files(
        self,
        files: list[Path],
        *,
        limit: int = 200,
        predicate: Any | None = None,
    ) -> list[ChatAuditRecord]:
        results: list[ChatAuditRecord] = []
        for path in files:
            for rec in self._parse_file(path):
                if predicate and not predicate(rec):
                    continue
                results.append(rec)
                if len(results) >= limit:
                    return results
        return results

    @staticmethod
    def _parse_file(path: Path) -> list[ChatAuditRecord]:
        records: list[ChatAuditRecord] = []
        if not path.exists():
            return records
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            logger.warning("Failed to read audit file %s", path, exc_info=True)
            return records
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(ChatAuditRecord.model_validate_json(line))
            except Exception:
                logger.warning("Skipping malformed audit line in %s", path)
        return records
