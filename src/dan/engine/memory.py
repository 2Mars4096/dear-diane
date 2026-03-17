"""Cross-run session memory — models and contracts.

Provides the identity model (session_id, scopes) and data models
(MemoryEntry, MemoryWriteRequest) used by MemoryStore and engine
integration.  Part of Plan 14-1.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MemoryScope(str, Enum):
    """Where a memory entry is visible."""

    GLOBAL = "global"
    WORKFLOW = "workflow"
    SESSION = "session"


class WriteMode(str, Enum):
    """How a memory write is applied."""

    SET = "set"
    APPEND = "append"
    MERGE = "merge"
    DELETE = "delete"


class MemoryEntry(BaseModel):
    """A single persisted memory entry."""

    key: str
    value: Any = None
    scope: MemoryScope = MemoryScope.SESSION
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    source_run_id: str | None = None
    writer_node_id: str | None = None
    owner_node_id: str | None = None
    write_mode: WriteMode = WriteMode.SET


class MemoryWriteRequest(BaseModel):
    """Request from an executor to write a memory entry."""

    key: str
    value: Any = None
    scope: MemoryScope = MemoryScope.SESSION
    mode: WriteMode = WriteMode.SET
    writer_node_id: str | None = None
    owner_node_id: str | None = None
