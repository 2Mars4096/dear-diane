"""Structured execution state persistence — protocol and filesystem implementation.

Part of Plan 18-3.  Storage layout::

    {base_dir}/state/{scope}/{key}.json

Values are serialized as JSON.  Pydantic models are converted via
``.model_dump()`` before serialization.  Atomic writes use the same
temp-file-then-rename strategy as ``FileSystemMemoryStore``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class StateStore(Protocol):
    """Protocol for structured execution state persistence."""

    async def write(self, scope: str, key: str, value: Any) -> None:
        """Write a typed value under scope/key."""
        ...

    async def read(self, scope: str, key: str) -> Any | None:
        """Read a value by scope/key.  Returns None if not found."""
        ...

    async def query(self, scope: str, prefix: str = "") -> dict[str, Any]:
        """Query all keys matching prefix within scope."""
        ...

    async def list_keys(self, scope: str) -> list[str]:
        """List all keys in a scope."""
        ...

    async def delete(self, scope: str, key: str) -> None:
        """Delete a specific entry."""
        ...

    async def clear_scope(self, scope: str) -> None:
        """Clear all entries in a scope."""
        ...


# ---------------------------------------------------------------------------
# Path sanitization (parallel to memory_store — no shared code)
# ---------------------------------------------------------------------------


def _safe_segment(segment: str) -> str:
    """Sanitize a path segment to prevent directory traversal."""
    sanitized = (
        segment
        .replace("..", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace("\0", "")
    )
    if not sanitized or sanitized in (".", ".."):
        raise ValueError(f"Invalid path segment: {segment!r}")
    return sanitized


def _safe_key(key: str) -> str:
    """Sanitize a key into a filesystem-safe name."""
    return (
        key
        .replace("..", "_")
        .replace("/", "__")
        .replace("\\", "__")
        .replace("\0", "")
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize(value: Any) -> str:
    """Serialize a value to a JSON string."""
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=2)
    return json.dumps(value, indent=2, default=str)


def _deserialize(raw: str) -> Any:
    """Deserialize a JSON string to a Python object."""
    return json.loads(raw)


# ---------------------------------------------------------------------------
# FileSystemStateStore
# ---------------------------------------------------------------------------


class FileSystemStateStore:
    """Filesystem-backed StateStore — one JSON file per entry.

    Atomic writes via temp-file-then-rename.  Thread-safe for concurrent
    reads because writes land atomically (``os.replace``).
    """

    def __init__(self, base_dir: str = "./state") -> None:
        self._base = Path(base_dir)

    def _scope_dir(self, scope: str) -> Path:
        return self._base / _safe_segment(scope)

    def _entry_path(self, scope: str, key: str) -> Path:
        return self._scope_dir(scope) / f"{_safe_key(key)}.json"

    # -- atomic write helper -----------------------------------------------

    def _atomic_write(self, path: Path, data: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        closed = False
        try:
            os.write(fd, data.encode("utf-8"))
            os.close(fd)
            closed = True
            os.replace(tmp, str(path))
        except BaseException:
            if not closed:
                os.close(fd)
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # -- public API --------------------------------------------------------

    async def write(self, scope: str, key: str, value: Any) -> None:
        self._atomic_write(self._entry_path(scope, key), _serialize(value))

    async def read(self, scope: str, key: str) -> Any | None:
        path = self._entry_path(scope, key)
        if not path.exists():
            return None
        try:
            return _deserialize(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read state entry %s: %s", path, exc)
            return None

    async def query(self, scope: str, prefix: str = "") -> dict[str, Any]:
        sdir = self._scope_dir(scope)
        if not sdir.exists():
            return {}
        safe_prefix = _safe_key(prefix) if prefix else ""
        result: dict[str, Any] = {}
        for p in sorted(sdir.glob("*.json")):
            stem = p.stem
            if safe_prefix and not stem.startswith(safe_prefix):
                continue
            try:
                result[stem] = _deserialize(p.read_text("utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Skipping corrupt state file %s: %s", p, exc)
        return result

    async def list_keys(self, scope: str) -> list[str]:
        sdir = self._scope_dir(scope)
        if not sdir.exists():
            return []
        return sorted(p.stem for p in sdir.glob("*.json"))

    async def delete(self, scope: str, key: str) -> None:
        path = self._entry_path(scope, key)
        if path.exists():
            path.unlink()

    async def clear_scope(self, scope: str) -> None:
        sdir = self._scope_dir(scope)
        if not sdir.exists():
            return
        for p in sdir.glob("*.json"):
            p.unlink()
        for p in sdir.glob("*.tmp"):
            p.unlink()
        try:
            sdir.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# NullStateStore
# ---------------------------------------------------------------------------


class NullStateStore:
    """No-op state store for when state externalization is disabled."""

    async def write(self, scope: str, key: str, value: Any) -> None:
        pass

    async def read(self, scope: str, key: str) -> Any | None:
        return None

    async def query(self, scope: str, prefix: str = "") -> dict[str, Any]:
        return {}

    async def list_keys(self, scope: str) -> list[str]:
        return []

    async def delete(self, scope: str, key: str) -> None:
        pass

    async def clear_scope(self, scope: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Typed state schemas
# ---------------------------------------------------------------------------


class LoopIterationState(BaseModel):
    """State for a single loop iteration."""

    model_config = ConfigDict(extra="allow")

    iteration: int
    status: str  # "completed", "failed", "skipped"
    started_at: float | None = None
    elapsed_seconds: float | None = None
    duration_ms: float = 0.0
    result_summary: str = ""
    output_keys: list[str] = Field(default_factory=list)
    output_preview: str = ""
    error: str | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)


class TeamTurnState(BaseModel):
    """State for a single agent team turn."""

    model_config = ConfigDict(extra="allow")

    turn_number: int
    agent_id: str
    status: str
    started_at: float | None = None
    elapsed_seconds: float | None = None
    message_preview: str = ""
    handoff_to: str | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)


class NodeExecutionSummary(BaseModel):
    """Summary of a completed node execution."""

    model_config = ConfigDict(extra="allow")

    node_id: str
    node_type: str
    status: str
    started_at: float | None = None
    elapsed_seconds: float | None = None
    duration_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    output_keys: list[str] = Field(default_factory=list)
    output_preview: str = ""
    error: str | None = None
