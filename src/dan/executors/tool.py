"""Tool executor — function-registry-based dispatch for ToolOperator nodes."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.models.nodes import NodeBase, ToolOperator

logger = logging.getLogger(__name__)

ToolFunction = Callable[..., Awaitable[Any]]


class ToolRegistry:
    """Maps tool_id strings to async callables."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolFunction] = {}

    def register(self, tool_id: str, fn: ToolFunction) -> None:
        self._tools[tool_id] = fn

    def get(self, tool_id: str) -> ToolFunction:
        try:
            return self._tools[tool_id]
        except KeyError:
            raise KeyError(
                f"No tool registered for id '{tool_id}'. "
                f"Registered: {sorted(self._tools)}"
            )

    def has(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def registered_ids(self) -> list[str]:
        return sorted(self._tools)


class ToolExecutor:
    """Executes ToolOperator nodes by looking up the tool in a ToolRegistry."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or ToolRegistry()

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, ToolOperator)

        if not self.registry.has(node.tool_id):
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Unknown tool: '{node.tool_id}'",
            )

        fn = self.registry.get(node.tool_id)
        merged_args = {**node.tool_config, **inputs}

        # -- 5-3: Rich logging -------------------------------------------------
        await context.emit_event(
            event_type="tool_call_started",
            node_id=node.id,
            node_type="tool_operator",
            data={"tool_id": node.tool_id, "args": {k: str(v)[:100] for k, v in merged_args.items()}},
        )

        try:
            result = await fn(**merged_args)
        except Exception as exc:
            logger.exception("Tool '%s' raised an exception", node.tool_id)
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Tool '{node.tool_id}' failed: {exc}",
            )

        # -- 5-3: Rich logging -------------------------------------------------
        await context.emit_event(
            event_type="tool_call_result",
            node_id=node.id,
            node_type="tool_operator",
            data={
                "tool_id": node.tool_id,
                "result": str(result)[:500] if not isinstance(result, dict) else {k: str(v)[:200] for k, v in result.items()},
            },
        )

        if isinstance(result, dict):
            outputs = result
        else:
            outputs = {"result": result}

        return NodeResult(outputs=outputs, status=NodeStatus.COMPLETED)
