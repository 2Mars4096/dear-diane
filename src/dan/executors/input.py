"""Input node executor — passes pre-run variable values through as outputs."""

from __future__ import annotations

from typing import Any

from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.models.legacy import InputNode
from dan.models.nodes import NodeBase


class InputExecutor:
    """Trivial pass-through: reads variable values from inputs, falls back to defaults."""

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, InputNode)
        outputs: dict[str, Any] = {}
        for var in node.variables:
            outputs[var.name] = inputs.get(var.name, var.default)
        # Provide an aggregate payload for "input" output-port wiring.
        # This keeps generated plans like input.input -> llm.input functional
        # while preserving variable-specific outputs.
        if "input" not in outputs:
            outputs["input"] = dict(outputs)
        return NodeResult(outputs=outputs, status=NodeStatus.COMPLETED)
