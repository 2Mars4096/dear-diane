"""Cross-run memory persistence — protocol and filesystem implementation.

Part of Plan 14-1.  Storage layout::

    {base_dir}/memory/
        {workflow_id}/
            {session_id}/
                {key}.json          # individual MemoryEntry
                _index.json         # key metadata for fast listing
        _global/
            {key}.json              # global-scope entries
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from dan.engine.memory import MemoryEntry, MemoryScope, WriteMode

logger = logging.getLogger(__name__)


@runtime_checkable
class MemoryStore(Protocol):
    """Protocol for cross-run memory persistence backends."""

    async def read(
        self, workflow_id: str, session_id: str, key: str,
    ) -> MemoryEntry | None: ...

    async def write(
        self, workflow_id: str, session_id: str, entry: MemoryEntry,
    ) -> None: ...

    async def delete(
        self, workflow_id: str, session_id: str, key: str,
    ) -> bool: ...

    async def list_keys(
        self, workflow_id: str, session_id: str,
    ) -> list[str]: ...

    async def list_sessions(self, workflow_id: str) -> list[str]: ...

    async def read_all(
        self, workflow_id: str, session_id: str,
    ) -> dict[str, MemoryEntry]: ...

    async def clear_session(
        self, workflow_id: str, session_id: str,
    ) -> None: ...


def _safe_filename(key: str) -> str:
    """Sanitize a key into a safe filename component."""
    return key.replace("/", "__").replace("\\", "__").replace("..", "_")


def _safe_path_segment(segment: str) -> str:
    """Sanitize a path segment (workflow_id / session_id) to prevent traversal."""
    sanitized = segment.replace("/", "_").replace("\\", "_").replace("..", "_").replace("\0", "")
    if not sanitized or sanitized in (".", ".."):
        raise ValueError(f"Invalid path segment: {segment!r}")
    return sanitized


class FileSystemMemoryStore:
    """Filesystem-backed MemoryStore — one JSON file per entry.

    Atomic writes via temp-file-then-rename.  An ``_index.json`` sidecar
    tracks key metadata for fast ``list_keys`` without reading every file.
    """

    def __init__(self, base_dir: str = "./memory") -> None:
        self._base = Path(base_dir)

    def _session_dir(self, workflow_id: str, session_id: str) -> Path:
        wf = _safe_path_segment(workflow_id)
        if wf == "_global":
            return self._base / "_global"
        sess = _safe_path_segment(session_id)
        return self._base / wf / sess

    def _entry_path(
        self, workflow_id: str, session_id: str, key: str,
    ) -> Path:
        return self._session_dir(workflow_id, session_id) / f"{_safe_filename(key)}.json"

    def _index_path(self, workflow_id: str, session_id: str) -> Path:
        return self._session_dir(workflow_id, session_id) / "_index.json"

    # -- atomic write helper -----------------------------------------------

    def _atomic_write(self, path: Path, data: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp",
        )
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

    # -- index management --------------------------------------------------

    def _read_index(self, workflow_id: str, session_id: str) -> dict[str, Any]:
        path = self._index_path(workflow_id, session_id)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt index at %s; rebuilding", path)
            return {}

    def _write_index(
        self, workflow_id: str, session_id: str, index: dict[str, Any],
    ) -> None:
        self._atomic_write(
            self._index_path(workflow_id, session_id),
            json.dumps(index, indent=2, default=str),
        )

    # -- public API --------------------------------------------------------

    async def read(
        self, workflow_id: str, session_id: str, key: str,
    ) -> MemoryEntry | None:
        path = self._entry_path(workflow_id, session_id, key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text("utf-8"))
            return MemoryEntry.model_validate(data)
        except (json.JSONDecodeError, OSError, Exception) as exc:
            logger.warning("Failed to read memory entry %s: %s", path, exc)
            return None

    async def write(
        self, workflow_id: str, session_id: str, entry: MemoryEntry,
    ) -> None:
        if entry.write_mode == WriteMode.DELETE:
            await self.delete(workflow_id, session_id, entry.key)
            return

        existing = await self.read(workflow_id, session_id, entry.key)

        if entry.write_mode == WriteMode.APPEND:
            old_val = existing.value if existing else None
            if old_val is None:
                entry = entry.model_copy(update={"value": [entry.value]})
            elif isinstance(old_val, list):
                entry = entry.model_copy(
                    update={"value": old_val + [entry.value]},
                )
            else:
                entry = entry.model_copy(
                    update={"value": [old_val, entry.value]},
                )
        elif entry.write_mode == WriteMode.MERGE:
            old_val = existing.value if existing else None
            if isinstance(old_val, dict) and isinstance(entry.value, dict):
                merged = {**old_val, **entry.value}
                entry = entry.model_copy(update={"value": merged})

        if existing:
            entry = entry.model_copy(
                update={
                    "created_at": existing.created_at,
                    "updated_at": time.time(),
                },
            )

        self._atomic_write(
            self._entry_path(workflow_id, session_id, entry.key),
            entry.model_dump_json(indent=2),
        )

        index = self._read_index(workflow_id, session_id)
        index[entry.key] = {
            "scope": entry.scope.value,
            "updated_at": entry.updated_at,
            "source_run_id": entry.source_run_id,
        }
        self._write_index(workflow_id, session_id, index)

    async def delete(
        self, workflow_id: str, session_id: str, key: str,
    ) -> bool:
        path = self._entry_path(workflow_id, session_id, key)
        if not path.exists():
            return False
        path.unlink()

        index = self._read_index(workflow_id, session_id)
        index.pop(key, None)
        self._write_index(workflow_id, session_id, index)
        return True

    async def list_keys(
        self, workflow_id: str, session_id: str,
    ) -> list[str]:
        index = self._read_index(workflow_id, session_id)
        if index:
            return sorted(index.keys())
        sdir = self._session_dir(workflow_id, session_id)
        if not sdir.exists():
            return []
        return sorted(
            p.stem
            for p in sdir.glob("*.json")
            if p.name != "_index.json"
        )

    async def list_sessions(self, workflow_id: str) -> list[str]:
        wdir = self._base / _safe_path_segment(workflow_id)
        if not wdir.exists():
            return []
        return sorted(
            d.name for d in wdir.iterdir()
            if d.is_dir() and d.name != "_global"
        )

    async def read_all(
        self, workflow_id: str, session_id: str,
    ) -> dict[str, MemoryEntry]:
        keys = await self.list_keys(workflow_id, session_id)
        result: dict[str, MemoryEntry] = {}
        for key in keys:
            entry = await self.read(workflow_id, session_id, key)
            if entry is not None:
                result[key] = entry
        return result

    async def clear_session(
        self, workflow_id: str, session_id: str,
    ) -> None:
        sdir = self._session_dir(workflow_id, session_id)
        if not sdir.exists():
            return
        for p in sdir.glob("*.json"):
            p.unlink()
        try:
            sdir.rmdir()
        except OSError:
            pass


class NullMemoryStore:
    """No-op memory store for when session memory is disabled."""

    async def read(
        self, workflow_id: str, session_id: str, key: str,
    ) -> MemoryEntry | None:
        return None

    async def write(
        self, workflow_id: str, session_id: str, entry: MemoryEntry,
    ) -> None:
        pass

    async def delete(
        self, workflow_id: str, session_id: str, key: str,
    ) -> bool:
        return False

    async def list_keys(
        self, workflow_id: str, session_id: str,
    ) -> list[str]:
        return []

    async def list_sessions(self, workflow_id: str) -> list[str]:
        return []

    async def read_all(
        self, workflow_id: str, session_id: str,
    ) -> dict[str, MemoryEntry]:
        return {}

    async def clear_session(
        self, workflow_id: str, session_id: str,
    ) -> None:
        pass
