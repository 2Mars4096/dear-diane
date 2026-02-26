"""Tool executor — function-registry-based dispatch for ToolOperator nodes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.models.nodes import NodeBase, RetryPolicy, ToolOperator

logger = logging.getLogger(__name__)

ToolFunction = Callable[..., Awaitable[Any]]

_TRANSIENT_EXCEPTIONS = (TimeoutError, ConnectionError, OSError)

_TOOL_DEFAULT_RETRY = RetryPolicy(max_retries=0)


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

    def register_builtin_tools(self) -> list[str]:
        """Register all ``dan.tools`` built-in tools. Returns list of registered tool_ids."""
        from dan.tools import get_all_tools

        registered: list[str] = []
        for tool_id, (fn, _metadata) in get_all_tools().items():
            if not self.has(tool_id):
                self.register(tool_id, fn)
                registered.append(tool_id)
        return registered


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
        policy = node.retry_policy or _TOOL_DEFAULT_RETRY

        if not self.registry.has(node.tool_id):
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Unknown tool: '{node.tool_id}'",
            )

        fn = self.registry.get(node.tool_id)
        merged_args = {**node.tool_config, **inputs}

        await context.emit_event(
            event_type="tool_call_started",
            node_id=node.id,
            node_type="tool_operator",
            data={"tool_id": node.tool_id, "args": {k: str(v)[:2000] for k, v in merged_args.items()}},
        )

        max_attempts = 1 + policy.max_retries
        backoff = policy.backoff
        last_exc: Exception | None = None

        for attempt in range(max_attempts):
            try:
                result = await fn(**merged_args)
                last_exc = None
                break
            except _TRANSIENT_EXCEPTIONS as exc:
                last_exc = exc
                if attempt < max_attempts - 1:
                    logger.warning(
                        "Transient tool error (attempt %d/%d) on '%s': %s",
                        attempt + 1, max_attempts, node.tool_id, exc,
                    )
                    await context.emit_event(
                        event_type="retry_attempted",
                        node_id=node.id,
                        node_type="tool_operator",
                        data={
                            "attempt": attempt + 1,
                            "max_retries": policy.max_retries,
                            "error": str(exc),
                            "tool_id": node.tool_id,
                        },
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, policy.backoff_max)
                else:
                    logger.exception("Tool '%s' failed after %d attempts", node.tool_id, max_attempts)
            except Exception as exc:
                last_exc = exc
                logger.exception("Tool '%s' raised a permanent exception", node.tool_id)
                break
        else:
            return self._fail_result(node, policy, last_exc)

        if last_exc is not None:
            return self._fail_result(node, policy, last_exc)

        await context.emit_event(
            event_type="tool_call_result",
            node_id=node.id,
            node_type="tool_operator",
            data={
                "tool_id": node.tool_id,
                "result": str(result)[:5000] if not isinstance(result, dict) else {k: str(v)[:2000] for k, v in result.items()},
            },
        )

        if isinstance(result, dict):
            outputs = dict(result)
            # Also expose full dict on "result" so edges like tool.result → next.input work
            outputs["result"] = result
        else:
            outputs = {"result": result}

        return NodeResult(outputs=outputs, status=NodeStatus.COMPLETED)

    @staticmethod
    def _fail_result(node: ToolOperator, policy: RetryPolicy, exc: Exception | None) -> NodeResult:
        error_msg = f"Tool '{node.tool_id}' failed: {exc}"
        if policy.on_failure == "skip":
            return NodeResult(outputs={}, status=NodeStatus.SKIPPED)
        if policy.on_failure == "halt":
            return NodeResult(
                outputs={}, status=NodeStatus.FAILED,
                error=error_msg, metadata={"halt": True},
            )
        return NodeResult(outputs={}, status=NodeStatus.FAILED, error=error_msg)
