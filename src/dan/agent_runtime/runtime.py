"""Base agent runtime — protocol and initial stub implementation.

The ``AgentRuntime`` protocol defines the contract that any runtime must
satisfy.  ``BaseAgentRuntime`` is the first concrete implementation: a
thin wrapper around the model gateway that will grow as logic is
extracted from ``ChatManager`` in later phases.

Surfaces (ChatManager, CLI) should *compose* this rather than
reimplementing the agent loop.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol, runtime_checkable

from dan.agent_runtime.types import AgentEvent, AgentRequest, AgentResult

if TYPE_CHECKING:
    from dan.llm_core.gateway import ModelGateway

logger = logging.getLogger(__name__)


@runtime_checkable
class AgentRuntime(Protocol):
    """Protocol for agent runtime implementations."""

    async def run_turn(
        self,
        request: AgentRequest,
    ) -> AgentResult:
        """Execute one agent turn (non-streaming)."""
        ...

    async def stream_turn(
        self,
        request: AgentRequest,
    ) -> AsyncIterator[AgentEvent]:
        """Execute one agent turn (streaming)."""
        ...


class BaseAgentRuntime:
    """Base implementation of the agent runtime.

    Provides the core agent loop: prompt -> model call -> tool dispatch ->
    follow-up -> result.  Surfaces (ChatManager, CLI) should compose this
    rather than reimplementing the loop.

    Phase 1: delegates directly to the gateway for simple completions.
    Tool-loop extraction, prompt assembly, and memory injection will be
    added in later phases (tasks 2-1 through 2-6 of plan 41-2).
    """

    def __init__(
        self,
        gateway: ModelGateway | None = None,
        tool_registry: Any | None = None,
        memory: Any | None = None,
    ) -> None:
        self._gateway = gateway
        self._tool_registry = tool_registry
        self._memory = memory

    async def run_turn(self, request: AgentRequest) -> AgentResult:
        """Execute one non-streaming agent turn."""
        if self._gateway is None:
            return AgentResult(error="No model gateway configured")

        kwargs: dict[str, Any] = {}
        if request.tools:
            kwargs["tools"] = request.tools

        result = await self._gateway.complete(
            request.messages,
            request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            **kwargs,
        )
        return AgentResult(
            text=result.text,
            model_used=result.model or request.model,
            usage=result.usage,
        )

    async def stream_turn(
        self, request: AgentRequest
    ) -> AsyncIterator[AgentEvent]:
        """Execute one streaming agent turn."""
        if self._gateway is None:
            yield AgentEvent(kind="error", data="No model gateway configured")
            return

        kwargs: dict[str, Any] = {}
        if request.tools:
            kwargs["tools"] = request.tools

        async for chunk in self._gateway.stream(
            request.messages,
            request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            **kwargs,
        ):
            yield AgentEvent(
                kind="text_delta" if not chunk.done else "complete",
                data=chunk.delta if not chunk.done else chunk.accumulated,
            )
