"""Core types for the agent runtime contract.

These types define the boundary between agent surfaces (ChatManager, CLI,
concierge) and the reusable single-agent loop.  Transport concerns (HTTP
channels, thread IDs, UI event formatting) stay outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentProfile(Enum):
    """Operating mode for an agent turn.

    Each profile may influence prompt overlays, tool availability, and
    post-processing behavior — but the base runtime loop is the same
    regardless of profile.
    """

    DIRECT_TASK = "direct_task"
    BUILD = "build"
    PLANNING = "planning"
    DEBUG = "debug"
    REVIEW = "review"


@dataclass
class AgentRequest:
    """Input for one agent turn."""

    messages: list[dict[str, Any]]
    model: str = "gpt-4o"
    profile: AgentProfile = AgentProfile.DIRECT_TASK
    temperature: float = 0.7
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_budget: int = 25
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentEvent:
    """An event emitted during agent execution.

    ``kind`` values: ``"text_delta"``, ``"tool_call"``, ``"tool_result"``,
    ``"complete"``, ``"error"``.
    """

    kind: str
    data: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Output of one agent turn."""

    text: str = ""
    tool_calls_made: int = 0
    model_used: str = ""
    usage: dict[str, int] | None = None
    events: list[AgentEvent] = field(default_factory=list)
    error: str | None = None
