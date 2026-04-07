"""Provider interfaces for the reusable worker core."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from dan.worker.core.contracts import EvidenceBlock, ExecutionRequest, OutputContract


@dataclass(slots=True)
class CompletionRequest:
    """Normalized completion call emitted by the worker core."""

    model: str | None
    system_prompt: str
    user_prompt: str
    temperature: float = 0.7
    max_tokens: int | None = None
    tools: list[str] = field(default_factory=list)
    output_contract: OutputContract = field(default_factory=OutputContract)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CompletionResponse:
    """Minimal completion response contract for the worker core."""

    text: str
    raw: Any = None


@dataclass(slots=True)
class ToolCallRequest:
    """Direct-tool invocation request emitted by the worker core."""

    worker_id: str
    tool_id: str
    arguments: dict[str, Any]
    request: ExecutionRequest


@dataclass(slots=True)
class ToolCallResponse:
    """Normalized direct-tool response."""

    output: Any
    metadata: dict[str, Any] = field(default_factory=dict)


class CompletionProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Run a completion for the provided request."""


class ToolProvider(Protocol):
    async def call(self, request: ToolCallRequest) -> ToolCallResponse:
        """Run a direct-tool call."""


class MemoryProvider(Protocol):
    async def get_evidence(self, worker: "WorkerDefinition", request: ExecutionRequest) -> list[EvidenceBlock]:
        """Return additional evidence blocks for the execution request."""


class EventSink(Protocol):
    async def record(self, event: str, payload: dict[str, Any]) -> None:
        """Receive best-effort execution lifecycle events."""


class WorkerDefinition(Protocol):
    """Structural protocol for the worker description used by the core."""

    id: str
