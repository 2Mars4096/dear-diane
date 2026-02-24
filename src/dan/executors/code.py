"""Code executor — sandboxed Python exec for CodeOperator nodes."""

from __future__ import annotations

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
    """

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, CodeOperator)

        if node.language != "python":
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Unsupported language: '{node.language}' (only 'python' is supported)",
            )

        namespace: dict[str, Any] = {"__builtins__": _ALLOWED_BUILTINS}
        namespace.update(inputs)

        try:
            exec(node.code, namespace)  # noqa: S102
        except Exception as exc:
            logger.exception("Code execution failed for node '%s'", node.id)
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Code execution failed: {exc}",
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
