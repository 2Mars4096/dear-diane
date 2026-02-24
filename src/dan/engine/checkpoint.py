"""Checkpointing — persist and restore execution state across runs.

Provides a pluggable CheckpointStore protocol and a default filesystem
implementation that writes JSON snapshots after each scheduling level.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CheckpointStore(Protocol):
    """Protocol for checkpoint persistence backends."""

    async def save(self, run_id: str, state: dict[str, Any]) -> None: ...

    async def load(self, run_id: str) -> dict[str, Any] | None: ...

    async def list_runs(self) -> list[str]: ...


class FileSystemCheckpointStore:
    """Writes checkpoint JSON to ``{base_dir}/{run_id}/checkpoint.json``."""

    def __init__(self, base_dir: str = "./checkpoints") -> None:
        self.base_dir = Path(base_dir)

    def _run_dir(self, run_id: str) -> Path:
        return self.base_dir / run_id

    async def save(self, run_id: str, state: dict[str, Any]) -> None:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "checkpoint.json"
        data = json.dumps(state, indent=2, default=str)
        path.write_text(data, encoding="utf-8")

    async def load(self, run_id: str) -> dict[str, Any] | None:
        path = self._run_dir(run_id) / "checkpoint.json"
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        return json.loads(text)

    async def list_runs(self) -> list[str]:
        if not self.base_dir.exists():
            return []
        return sorted(
            d.name
            for d in self.base_dir.iterdir()
            if d.is_dir() and (d / "checkpoint.json").exists()
        )


class NullCheckpointStore:
    """No-op checkpoint store for when checkpointing is disabled."""

    async def save(self, run_id: str, state: dict[str, Any]) -> None:
        pass

    async def load(self, run_id: str) -> dict[str, Any] | None:
        return None

    async def list_runs(self) -> list[str]:
        return []
