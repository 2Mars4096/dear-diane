"""Tests for the GateNode model and GateExecutor."""

import pytest

from dan.engine.context_runtime import LocalStateManager
from dan.engine.executor import NodeResult
from dan.engine.state import NodeStatus
from dan.executors.control_flow import GateExecutor
from dan.models.control_flow import GateNode


class TestGateNodeModel:
    def test_if_else_defaults(self):
        node = GateNode(id="g1", name="test", condition="x > 0")
        assert node.node_type == "gate"
        assert node.gate_mode == "if_else"
        assert node.max_iterations == 10

    def test_while_mode(self):
        node = GateNode(
            id="g2", name="loop", condition="not done",
            gate_mode="while", max_iterations=5,
        )
        assert node.gate_mode == "while"
        assert node.max_iterations == 5

    def test_max_iterations_minimum(self):
        with pytest.raises(Exception):
            GateNode(id="g3", name="bad", condition="True", max_iterations=0)


class TestGateExecutor:
    @pytest.fixture
    def executor(self):
        return GateExecutor()

    @pytest.fixture
    def mock_context(self):
        class MockContext:
            def __init__(self):
                self.local_state = LocalStateManager()

            async def emit_event(self, **kwargs):
                pass
        return MockContext()

    @pytest.mark.asyncio
    async def test_if_else_true_branch(self, executor, mock_context):
        node = GateNode(
            id="g1", name="test", condition="x > 0",
            input_ports=[{"name": "x", "schema": {}}],
            output_ports=[{"name": "true", "schema": {}}, {"name": "false", "schema": {}}],
        )
        result = await executor.execute(node, {"x": 5}, mock_context)
        assert result.status == NodeStatus.COMPLETED
        assert "true" in result.outputs
        assert "false" not in result.outputs
        assert result.outputs["true"] == {"x": 5}

    @pytest.mark.asyncio
    async def test_if_else_false_branch(self, executor, mock_context):
        node = GateNode(
            id="g1", name="test", condition="x > 0",
            input_ports=[{"name": "x", "schema": {}}],
            output_ports=[{"name": "true", "schema": {}}, {"name": "false", "schema": {}}],
        )
        result = await executor.execute(node, {"x": -1}, mock_context)
        assert result.status == NodeStatus.COMPLETED
        assert "false" in result.outputs
        assert "true" not in result.outputs

    @pytest.mark.asyncio
    async def test_while_continue(self, executor, mock_context):
        node = GateNode(
            id="g1", name="loop", condition="count < 3", gate_mode="while",
            input_ports=[{"name": "count", "schema": {}}],
            output_ports=[{"name": "continue", "schema": {}}, {"name": "done", "schema": {}}],
        )
        result = await executor.execute(node, {"count": 1}, mock_context)
        assert result.outputs.get("continue") == {"count": 1}
        assert "done" not in result.outputs

    @pytest.mark.asyncio
    async def test_while_done(self, executor, mock_context):
        node = GateNode(
            id="g1", name="loop", condition="count < 3", gate_mode="while",
            input_ports=[{"name": "count", "schema": {}}],
            output_ports=[{"name": "continue", "schema": {}}, {"name": "done", "schema": {}}],
        )
        result = await executor.execute(node, {"count": 5}, mock_context)
        assert result.outputs.get("done") == {"count": 5}
        assert "continue" not in result.outputs

    @pytest.mark.asyncio
    async def test_invalid_condition(self, executor, mock_context):
        node = GateNode(id="g1", name="test", condition="undefined_var > 0")
        result = await executor.execute(node, {}, mock_context)
        assert result.status == NodeStatus.FAILED
        assert "undefined_var" in result.error

    @pytest.mark.asyncio
    async def test_metadata_contains_branch(self, executor, mock_context):
        node = GateNode(id="g1", name="test", condition="True")
        result = await executor.execute(node, {}, mock_context)
        assert result.metadata["active_branch"] == "true"
        assert result.metadata["condition_result"] is True

    @pytest.mark.asyncio
    async def test_only_active_branch_in_outputs(self, executor, mock_context):
        node = GateNode(id="g1", name="test", condition="False")
        result = await executor.execute(node, {"data": 42}, mock_context)
        assert list(result.outputs.keys()) == ["false"]
        assert result.outputs["false"] == {"data": 42}
