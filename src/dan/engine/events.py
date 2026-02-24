"""Typed runtime events emitted during graph execution.

Events are fire-and-forget: the engine emits them via an optional async
callback.  Consumers (e.g. the run manager / WebSocket layer) subscribe
to the callback; the engine itself never blocks on delivery.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    NODE_SKIPPED = "node_skipped"
    NODE_OUTPUT = "node_output"
    LOG = "log"
    # -- 5-3: Rich logging event types -----------------------------------------
    LLM_THINKING = "llm_thinking"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_RESULT = "tool_call_result"
    CODE_OUTPUT = "code_output"
    INTERMEDIATE_TEXT = "intermediate_text"


@dataclass(frozen=True)
class EngineEvent:
    """Single event emitted during execution."""

    event_type: EventType
    run_id: str
    timestamp: float = field(default_factory=time.time)
    node_id: str | None = None
    node_type: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "data": self.data,
        }
