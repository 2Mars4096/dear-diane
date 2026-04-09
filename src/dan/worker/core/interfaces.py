"""Provider interfaces for the reusable worker core."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from dan.worker.core.capabilities import CapabilityManifest
from dan.worker.core.contracts import (
    AcquisitionPolicy,
    AcquisitionSelection,
    AcquisitionSource,
    ContinuationPayload,
    DiscoveryCatalog,
    EvidenceBlock,
    ExecutionRequest,
    ExpandedContext,
    MemorySnapshot,
    OutputContract,
    ToolUseContract,
)


@dataclass(slots=True)
class CompletionRequest:
    """Normalized completion call emitted by the worker core."""

    model: str | None
    system_prompt: str
    user_prompt: str
    temperature: float = 0.7
    max_tokens: int | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    capabilities: list[CapabilityManifest] = field(default_factory=list)
    evidence: list[EvidenceBlock] = field(default_factory=list)
    continuation: ContinuationPayload | None = None
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
    contract: ToolUseContract = field(default_factory=ToolUseContract)


@dataclass(slots=True)
class ToolCallResponse:
    """Normalized direct-tool response."""

    output: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DiscoveryRequest:
    """Discover compact catalog entries for one acquisition source."""

    worker_id: str
    request: ExecutionRequest
    source: AcquisitionSource
    policy: AcquisitionPolicy = field(default_factory=AcquisitionPolicy)
    continuation: ContinuationPayload | None = None


@dataclass(slots=True)
class DiscoveryResponse:
    """Compact catalog output for one discover pass."""

    catalog: DiscoveryCatalog


@dataclass(slots=True)
class ExpansionRequest:
    """Expand selected refs into detailed evidence for one source."""

    worker_id: str
    request: ExecutionRequest
    source: AcquisitionSource
    selections: list[AcquisitionSelection]
    catalog: DiscoveryCatalog | None = None
    policy: AcquisitionPolicy = field(default_factory=AcquisitionPolicy)
    continuation: ContinuationPayload | None = None


@dataclass(slots=True)
class ExpansionResponse:
    """Expanded evidence payload for selected refs."""

    expanded_context: list[ExpandedContext] = field(default_factory=list)


class CompletionProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Run a completion for the provided request."""


class ToolProvider(Protocol):
    async def call(self, request: ToolCallRequest) -> ToolCallResponse:
        """Run a direct-tool call."""


class MemoryProvider(Protocol):
    async def recall(self, worker: "WorkerDefinition", request: ExecutionRequest) -> MemorySnapshot:
        """Return the layered memory snapshot available to the worker."""

    async def persist(
        self,
        worker: "WorkerDefinition",
        request: ExecutionRequest,
        outputs: dict[str, Any],
        *,
        status: str,
        continuation: ContinuationPayload | None = None,
    ) -> MemorySnapshot | None:
        """Persist post-run observations and return the updated snapshot."""


class AcquisitionProvider(Protocol):
    async def discover(self, request: DiscoveryRequest) -> DiscoveryResponse:
        """Return a compact catalog for the provided acquisition source."""

    async def expand(self, request: ExpansionRequest) -> ExpansionResponse:
        """Expand a selected subset of discovered refs into detailed context."""


class EventSink(Protocol):
    async def record(self, event: str, payload: dict[str, Any]) -> None:
        """Receive best-effort execution lifecycle events."""


class WorkerDefinition(Protocol):
    """Structural protocol for the worker description used by the core."""

    id: str
