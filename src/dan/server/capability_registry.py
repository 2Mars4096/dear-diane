"""ChatCapabilityRegistry — multi-tool dispatch for chat-as-control-plane.

Holds {tool_name: (schema, handler, modes)} mappings. ChatManager calls
``get_tools(mode)`` to build the tool list for the LLM, and
``execute(tool_name, args, context)`` to dispatch after the LLM picks a tool.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

ALL_MODES = ("ask", "plan", "debug", "agent", "build", "mutate", "conversation")
READ_ONLY_MODES = ("ask", "plan", "conversation")


@dataclass
class CapabilityContext:
    """Dependency bag passed to every capability handler at call time.

    A single template instance is constructed at server startup (``app.py``
    lifespan) with subsystem references.  Per-request copies are created via
    ``dataclasses.replace(ctx, workflow_id=...)`` in ``ChatManager`` so
    handlers see the correct workflow context without mutating the shared
    template.  **Handlers must not mutate fields on the context object.**
    """

    workflow_id: str
    graph_store: Any = None
    run_manager: Any = None
    run_store: Any = None
    activity_tracker: Any = None
    experience_store: Any = None
    experience_index: Any = None
    discovery_service: Any = None
    principle_store: Any = None
    publish_registry: Any = None
    block_registry: Any = None
    graphs_dir: str = ""
    mcp_bridge: Any = None


@dataclass
class CapabilityResult:
    """Uniform return type from every capability handler."""

    success: bool
    message: str
    data: Any = None
    output_preview: str = ""
    stream_channel_id: str | None = None


CapabilityHandler = Callable[
    [dict[str, Any], CapabilityContext],
    Awaitable[CapabilityResult],
]


@dataclass
class _RegisteredTool:
    name: str
    schema: dict[str, Any]
    handler: CapabilityHandler
    modes: set[str]
    category: str = ""


class ChatCapabilityRegistry:
    """Registry for chat capability tools."""

    def __init__(self) -> None:
        self._tools: dict[str, _RegisteredTool] = {}

    def register(
        self,
        name: str,
        schema: dict[str, Any],
        handler: CapabilityHandler,
        *,
        modes: list[str] | None = None,
        category: str = "",
    ) -> None:
        mode_set = set(modes) if modes else set(ALL_MODES)
        self._tools[name] = _RegisteredTool(
            name=name,
            schema=schema,
            handler=handler,
            modes=mode_set,
            category=category,
        )

    def unregister(self, name: str) -> bool:
        """Remove a tool from the registry. Returns True if it existed."""
        return self._tools.pop(name, None) is not None

    def unregister_by_category(self, category: str) -> int:
        """Remove all tools with the given category. Returns count removed."""
        to_remove = [n for n, t in self._tools.items() if t.category == category]
        for name in to_remove:
            del self._tools[name]
        return len(to_remove)

    def get_tools(self, mode: str) -> list[dict[str, Any]]:
        """Return tool schemas available for the given chat mode."""
        return [t.schema for t in self._tools.values()]

    def get_handler(self, name: str) -> CapabilityHandler | None:
        tool = self._tools.get(name)
        return tool.handler if tool else None

    def is_available(self, name: str, mode: str) -> bool:
        return name in self._tools

    def list_tool_names(self, mode: str | None = None) -> list[str]:
        if mode is None:
            return list(self._tools.keys())
        return [n for n, t in self._tools.items() if mode in t.modes]

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: CapabilityContext,
        mode: str = "agent",
    ) -> CapabilityResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            return CapabilityResult(
                success=False,
                message=f"Unknown tool: {tool_name}",
            )
        t0 = time.monotonic()
        try:
            result = await tool.handler(args, context)
        except Exception as exc:
            logger.exception("Capability handler %s failed", tool_name)
            return CapabilityResult(
                success=False,
                message=f"Tool error: {exc}",
            )
        elapsed = int((time.monotonic() - t0) * 1000)
        logger.debug("Capability %s completed in %dms", tool_name, elapsed)
        return result


def build_tool_schema(
    name: str,
    description: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Helper to build an OpenAI-compatible function-calling tool schema."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }
