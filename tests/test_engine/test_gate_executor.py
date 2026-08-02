"""Tests for GateNode model and GateExecutor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.executor import EngineConfig, ExecutionContext
from dan.engine.state import NodeStatus
from dan.executors.control_flow import GateExecutor
from dan.models.control_flow import GateNode


def _make_gate_node(
    *,
    gate_mode: str = "if_else",
    condition: str = "True",
    max_iterations: int = 10,
    node_id: str = "gate-1",
) -> GateNode:
    return GateNode(
        id=node_id,
        name="test-gate",
        condition=condition,
        gate_mode=gate_mode,
        max_iterations=max_iterations,
    )


def _make_context(
    local_state: LocalStateManager | None = None,
) -> ExecutionContext:
    """Build a minimal ExecutionContext with mocked internals."""
    return ExecutionContext(
        state=MagicMock(),
        config=EngineConfig(llm_api_key="test-key"),
        shared_context=SharedContextStore(),
        artifacts=ArtifactStore(),
        local_state=local_state or LocalStateManager(),
        event_callback=AsyncMock(),
        run_id="test-run",
    )


# ---------------------------------------------------------------------------
# if_else mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_if_else_true_branch():
    """Condition evaluates true — outputs on 'true' port only."""
    node = _make_gate_node(condition="x > 0")
    ctx = _make_context()
    executor = GateExecutor()

    result = await executor.execute(node, {"x": 5}, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert "true" in result.outputs
    assert "false" not in result.outputs
    assert result.outputs["true"] == {"x": 5}
    assert result.metadata["active_branch"] == "true"
    assert result.metadata["condition_result"] is True


@pytest.mark.asyncio
async def test_gate_if_else_false_branch():
    """Condition evaluates false — outputs on 'false' port only."""
    node = _make_gate_node(condition="x > 0")
    ctx = _make_context()
    executor = GateExecutor()

    result = await executor.execute(node, {"x": -1}, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert "false" in result.outputs
    assert "true" not in result.outputs
    assert result.outputs["false"] == {"x": -1}
    assert result.metadata["active_branch"] == "false"
    assert result.metadata["condition_result"] is False


# ---------------------------------------------------------------------------
# while mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_while_continue():
    """While mode, condition true — outputs on 'continue' port."""
    node = _make_gate_node(gate_mode="while", condition="iteration < 5")
    ls = LocalStateManager()
    ls.get_scope("gate-1")["gate_iteration"] = 2
    ctx = _make_context(local_state=ls)
    executor = GateExecutor()

    result = await executor.execute(node, {"data": "hello"}, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert "continue" in result.outputs
    assert "done" not in result.outputs
    assert result.outputs["continue"] == {"data": "hello"}
    assert result.metadata["active_branch"] == "continue"
    assert result.metadata["iteration"] == 2


@pytest.mark.asyncio
async def test_gate_while_done():
    """While mode, condition false — outputs on 'done' port."""
    node = _make_gate_node(gate_mode="while", condition="iteration < 5")
    ls = LocalStateManager()
    ls.get_scope("gate-1")["gate_iteration"] = 5
    ctx = _make_context(local_state=ls)
    executor = GateExecutor()

    result = await executor.execute(node, {"data": "hello"}, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert "done" in result.outputs
    assert "continue" not in result.outputs
    assert result.outputs["done"] == {"data": "hello"}
    assert result.metadata["active_branch"] == "done"
    assert result.metadata["iteration"] == 5


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_condition_error():
    """Bad condition expression returns FAILED status."""
    node = _make_gate_node(condition="undefined_var > 0")
    ctx = _make_context()
    executor = GateExecutor()

    result = await executor.execute(node, {}, ctx)

    assert result.status == NodeStatus.FAILED
    assert result.error is not None
    assert "undefined_var" in result.error
    assert result.outputs == {}


# ---------------------------------------------------------------------------
# Iteration counter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_while_iteration_counter():
    """Verify iteration from local state is available in condition vars."""
    node = _make_gate_node(gate_mode="while", condition="iteration == 3")
    ls = LocalStateManager()
    ls.get_scope("gate-1")["gate_iteration"] = 3
    ctx = _make_context(local_state=ls)
    executor = GateExecutor()

    result = await executor.execute(node, {}, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert result.metadata["condition_result"] is True
    assert result.metadata["active_branch"] == "continue"
    assert result.metadata["iteration"] == 3


# ---------------------------------------------------------------------------
# Model output ports
# ---------------------------------------------------------------------------


def test_gate_node_if_else_output_ports():
    """if_else mode derives 'true' and 'false' output ports."""
    node = _make_gate_node(gate_mode="if_else")
    port_names = {p.name for p in node.output_ports}
    assert port_names == {"true", "false"}


def test_gate_node_while_output_ports():
    """while mode derives 'continue' and 'done' output ports."""
    node = _make_gate_node(gate_mode="while")
    port_names = {p.name for p in node.output_ports}
    assert port_names == {"continue", "done"}


def test_gate_node_preserves_explicit_output_ports():
    """Explicit output_ports are not overwritten by model_post_init."""
    from dan.models.ports import OutputPort

    custom_ports = [OutputPort(name="custom")]
    node = GateNode(
        id="g",
        name="g",
        condition="True",
        output_ports=custom_ports,
    )
    assert len(node.output_ports) == 1
    assert node.output_ports[0].name == "custom"


# ---------------------------------------------------------------------------
# Input flattening / unwrapping (Batch 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dict_input_with_result_key_flattened():
    """Dict input containing a 'result' sub-dict is flattened into condition_vars."""
    node = _make_gate_node(condition="verdict == 'revise'")
    ctx = _make_context()
    executor = GateExecutor()

    result = await executor.execute(
        node, {"input": {"result": {"verdict": "revise"}}}, ctx
    )

    assert result.status == NodeStatus.COMPLETED
    assert result.metadata["condition_result"] is True


@pytest.mark.asyncio
async def test_dict_input_without_result_key_flattened():
    """Dict input without a 'result' key uses the dict itself for flattening."""
    node = _make_gate_node(condition="verdict == 'revise'")
    ctx = _make_context()
    executor = GateExecutor()

    result = await executor.execute(
        node, {"input": {"verdict": "revise"}}, ctx
    )

    assert result.status == NodeStatus.COMPLETED
    assert result.metadata["condition_result"] is True


@pytest.mark.asyncio
async def test_plain_dict_input_flattened():
    """Plain dict input (no nesting) is used directly as condition_vars."""
    node = _make_gate_node(condition="verdict == 'accept'")
    ctx = _make_context()
    executor = GateExecutor()

    result = await executor.execute(node, {"verdict": "accept"}, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert result.metadata["condition_result"] is True


# ---------------------------------------------------------------------------
# Single-value input port mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_value_input_mapped_to_condition_name_true():
    """Scalar on 'input' port is mapped to the condition variable name."""
    node = _make_gate_node(condition="use_builtin")
    ctx = _make_context()
    executor = GateExecutor()

    result = await executor.execute(node, {"input": True}, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert result.metadata["condition_result"] is True


@pytest.mark.asyncio
async def test_single_value_input_mapped_to_condition_name_false():
    """Scalar False on 'input' port maps to the condition variable name."""
    node = _make_gate_node(condition="use_builtin")
    ctx = _make_context()
    executor = GateExecutor()

    result = await executor.execute(node, {"input": False}, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert result.metadata["condition_result"] is False


# ---------------------------------------------------------------------------
# try_more default injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_more_defaults_to_true():
    """When 'try_more' is absent from inputs but referenced in condition, it defaults to True."""
    node = _make_gate_node(
        gate_mode="while", condition="try_more and iteration < 5"
    )
    ls = LocalStateManager()
    ls.get_scope("gate-1")["gate_iteration"] = 2
    ctx = _make_context(local_state=ls)
    executor = GateExecutor()

    result = await executor.execute(node, {}, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert result.metadata["condition_result"] is True


# ---------------------------------------------------------------------------
# Done output unwrapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_while_done_unwraps_input_key():
    """While-gate done branch unwraps the 'input' key from the dict."""
    node = _make_gate_node(gate_mode="while", condition="False")
    ls = LocalStateManager()
    ls.get_scope("gate-1")["gate_iteration"] = 1
    ctx = _make_context(local_state=ls)
    executor = GateExecutor()

    result = await executor.execute(
        node, {"input": "body_value"}, ctx
    )

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs["done"] == "body_value"


@pytest.mark.asyncio
async def test_while_done_passes_full_inputs_without_input_key():
    """While-gate done branch passes full inputs when no 'input' key is present."""
    node = _make_gate_node(gate_mode="while", condition="False")
    ls = LocalStateManager()
    ls.get_scope("gate-1")["gate_iteration"] = 1
    ctx = _make_context(local_state=ls)
    executor = GateExecutor()

    result = await executor.execute(
        node, {"data": "hello", "count": 3}, ctx
    )

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs["done"] == {"data": "hello", "count": 3}


# ---------------------------------------------------------------------------
# gate_evaluated event payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_while_gate_emits_gate_evaluated_event():
    """While-gate emits a gate_evaluated event with correct payload."""
    node = _make_gate_node(gate_mode="while", condition="iteration < 5")
    ls = LocalStateManager()
    ls.get_scope("gate-1")["gate_iteration"] = 2
    ctx = _make_context(local_state=ls)
    executor = GateExecutor()

    await executor.execute(node, {"data": "hello"}, ctx)

    ctx._event_callback.assert_called_once()
    event = ctx._event_callback.call_args[0][0]
    assert event.event_type.value == "gate_evaluated"
    assert event.node_id == "gate-1"
    assert event.data["gate_mode"] == "while"
    assert event.data["active_branch"] == "continue"
    assert event.data["iteration"] == 2
    assert event.data["condition"] == "iteration < 5"
    assert "condition_vars" in event.data
    assert event.data["condition_vars"]["iteration"] == 2
