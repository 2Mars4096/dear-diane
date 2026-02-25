"""Code executor — sandboxed Python exec for CodeOperator nodes."""

from __future__ import annotations

import contextlib
import io
import logging
from typing import Any

from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.models.nodes import CodeOperator, NodeBase

logger = logging.getLogger(__name__)

_ALLOWED_BUILTINS: dict[str, Any] = {
    "len": len,
    "min": min,
    "max": max,
    "abs": abs,
    "all": all,
    "any": any,
    "sum": sum,
    "round": round,
    "sorted": sorted,
    "reversed": reversed,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "range": range,
    "isinstance": isinstance,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "print": print,
    "True": True,
    "False": False,
    "None": None,
}


class CodeExecutor:
    """Executes CodeOperator nodes in a restricted Python environment.

    Input data is injected as variables. The code is expected to assign
    its output to a variable named ``result``.

    No retry loop: ``exec()`` is deterministic with no timeout mechanism,
    so retrying produces identical results. ``on_failure`` is still honored.
    """

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, CodeOperator)

        if node.language != "python":
            return self._fail_result(
                node,
                f"Unsupported language: '{node.language}' (only 'python' is supported)",
            )

        namespace: dict[str, Any] = {"__builtins__": _ALLOWED_BUILTINS}
        namespace.update(inputs)

        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        try:
            with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                exec(node.code, namespace)  # noqa: S102
        except Exception as exc:
            logger.exception("Code execution failed for node '%s'", node.id)
            stdout_str = stdout_capture.getvalue()
            stderr_str = stderr_capture.getvalue()
            if stdout_str or stderr_str:
                await context.emit_event(
                    event_type="code_output",
                    node_id=node.id,
                    node_type="code_operator",
                    data={"stdout": stdout_str[:2000], "stderr": stderr_str[:2000]},
                )
            return self._fail_result(node, f"Code execution failed: {exc}")

        stdout_str = stdout_capture.getvalue()
        stderr_str = stderr_capture.getvalue()
        if stdout_str or stderr_str:
            await context.emit_event(
                event_type="code_output",
                node_id=node.id,
                node_type="code_operator",
                data={"stdout": stdout_str[:2000], "stderr": stderr_str[:2000]},
            )

        if "result" in namespace:
            result_value = namespace["result"]
            if isinstance(result_value, dict):
                outputs = result_value
            else:
                outputs = {"result": result_value}
        else:
            outputs = {}

        return NodeResult(outputs=outputs, status=NodeStatus.COMPLETED)

    @staticmethod
    def _fail_result(node: CodeOperator, error: str) -> NodeResult:
        policy = node.retry_policy
        on_failure = policy.on_failure if policy else "error"
        if on_failure == "skip":
            return NodeResult(outputs={}, status=NodeStatus.SKIPPED)
        if on_failure == "halt":
            return NodeResult(
                outputs={}, status=NodeStatus.FAILED,
                error=error, metadata={"halt": True},
            )
        return NodeResult(outputs={}, status=NodeStatus.FAILED, error=error)
