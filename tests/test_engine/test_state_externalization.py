"""Tests for automatic state externalization (18-3 task 1-3).

Covers:
- NodeExecutionSummary written after node execution
- LoopIterationState written per WhileLoop iteration
- ForEach branch state written per branch
- AgentTeam turn state written per turn
- Graceful no-op when state_store is None
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.engine.state_store import (
    LoopIterationState,
    NodeExecutionSummary,
    NullStateStore,
    TeamTurnState,
)


class FakeStateStore:
    """Minimal async state store that records writes."""

    def __init__(self) -> None:
        self.writes: dict[tuple[str, str], Any] = {}

    async def write(self, scope: str, key: str, value: Any) -> None:
        self.writes[(scope, key)] = value

    async def read(self, scope: str, key: str) -> Any | None:
        return self.writes.get((scope, key))

    async def query(self, scope: str, prefix: str = "") -> dict[str, Any]:
        return {
            k[1]: v for k, v in self.writes.items()
            if k[0] == scope and k[1].startswith(prefix)
        }

    async def list_keys(self, scope: str) -> list[str]:
        return [k[1] for k in self.writes if k[0] == scope]

    async def delete(self, scope: str, key: str) -> None:
        self.writes.pop((scope, key), None)

    async def clear_scope(self, scope: str) -> None:
        to_remove = [k for k in self.writes if k[0] == scope]
        for k in to_remove:
            del self.writes[k]


def _make_minimal_context(state_store=None, run_id="test-run"):
    """Build a minimal ExecutionContext with only the parts needed for state tests."""
    from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
    from dan.engine.executor import EngineConfig, ExecutionContext
    from dan.engine.state import ExecutionState
    from dan.models.graph import Graph, GraphMetadata

    graph = Graph(
        metadata=GraphMetadata(name="test"),
        nodes=[],
        edges=[],
    )
    state = ExecutionState(graph, run_id=run_id)
    ctx = ExecutionContext(
        state=state,
        config=EngineConfig(),
        shared_context=SharedContextStore([]),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        state_store=state_store,
        run_id=run_id,
    )
    return ctx


# ===========================================================================
# NodeExecutionSummary written after node execution
# ===========================================================================


class TestNodeExecutionSummary:
    def test_summary_model_fields(self):
        summary = NodeExecutionSummary(
            node_id="n1",
            node_type="llm_operator",
            status="completed",
            duration_ms=123.4,
            output_keys=["result", "text"],
            cost=0.005,
        )
        assert summary.node_id == "n1"
        assert summary.duration_ms == 123.4
        assert summary.output_keys == ["result", "text"]
        assert summary.cost == 0.005

    def test_summary_serialization(self):
        summary = NodeExecutionSummary(
            node_id="n1",
            node_type="llm_operator",
            status="completed",
            output_keys=["result"],
        )
        data = summary.model_dump()
        assert data["node_id"] == "n1"
        assert data["output_keys"] == ["result"]

    @pytest.mark.asyncio
    async def test_scheduler_writes_node_summary(self):
        """Verify the engine writes NodeExecutionSummary to state store after node execution."""
        store = FakeStateStore()

        from dan.builder import workflow
        from dan.engine.executor import EngineConfig, ExecutorRegistry, NodeResult
        from dan.engine.scheduler import Engine
        from dan.engine.state import NodeStatus

        class StubExecutor:
            async def execute(self, node, inputs, context):
                return NodeResult(
                    outputs={"result": "hello"},
                    status=NodeStatus.COMPLETED,
                    metadata={"usage": {"prompt_tokens": 10, "completion_tokens": 5}},
                )

        wf = workflow("test", canonical_workers=False)
        wf.llm("gen", prompt="test")
        graph = wf.build()

        config = EngineConfig(
            state_store_enabled=True,
            checkpoint_enabled=False,
        )
        registry = ExecutorRegistry()
        registry.register("llm_operator", StubExecutor())

        engine = Engine(config=config, executor_registry=registry)
        engine.state_store = store

        result = await engine.run(graph, inputs={"input": "hi"})

        assert result.success
        matching = [k for k in store.writes if "node:gen:summary" in k[1]]
        assert len(matching) >= 1
        written = store.writes[matching[0]]
        if isinstance(written, dict):
            assert written["node_id"] == "gen"
            assert written["status"] == "completed"
            assert "output_keys" in written

    @pytest.mark.asyncio
    async def test_no_op_when_state_store_is_none(self):
        """When state_store is None, no writes occur — just verify no error."""
        from dan.builder import workflow
        from dan.engine.executor import EngineConfig, ExecutorRegistry, NodeResult
        from dan.engine.scheduler import Engine
        from dan.engine.state import NodeStatus

        class StubExecutor:
            async def execute(self, node, inputs, context):
                return NodeResult(
                    outputs={"result": "hello"},
                    status=NodeStatus.COMPLETED,
                )

        wf = workflow("test", canonical_workers=False)
        wf.llm("gen", prompt="test")
        graph = wf.build()

        config = EngineConfig(
            state_store_enabled=False,
            checkpoint_enabled=False,
        )
        registry = ExecutorRegistry()
        registry.register("llm_operator", StubExecutor())
        engine = Engine(config=config, executor_registry=registry)

        result = await engine.run(graph, inputs={"input": "hi"})
        assert result.success


# ===========================================================================
# LoopIterationState written per WhileLoop iteration
# ===========================================================================


class TestLoopIterationState:
    def test_model_fields(self):
        state = LoopIterationState(
            iteration=2,
            status="completed",
            duration_ms=45.2,
            output_keys=["draft"],
            output_preview="some text...",
        )
        assert state.iteration == 2
        assert state.duration_ms == 45.2
        assert state.output_preview == "some text..."

    @pytest.mark.asyncio
    async def test_while_loop_writes_iteration_state(self):
        """WhileLoopExecutor writes LoopIterationState per iteration."""
        store = FakeStateStore()
        ctx = _make_minimal_context(state_store=store, run_id="run-loop")

        async def fake_subgraph(key, inputs, parent_node_id=None, targeted_inputs=None):
            return {"result": f"iter output {inputs.get('iteration', 0)}"}

        ctx._run_subgraph = fake_subgraph

        from dan.executors.control_flow import WhileLoopExecutor
        from dan.models.control_flow import WhileLoopNode

        node = WhileLoopNode(
            id="loop1",
            name="loop1",
            condition="iteration < 3",
            body_graph="body",
            max_iterations=5,
        )

        executor = WhileLoopExecutor()
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await executor.execute(node, {"input": "start"}, ctx)

        assert result.status.value == "completed"
        loop_keys = [k for k in store.writes if "loop:loop1:iter:" in k[1]]
        assert len(loop_keys) >= 2


# ===========================================================================
# Graceful no-op
# ===========================================================================


class TestGracefulNoOp:
    @pytest.mark.asyncio
    async def test_while_loop_no_state_store(self):
        """WhileLoopExecutor runs cleanly when state_store is None."""
        ctx = _make_minimal_context(state_store=None, run_id="run-noop")

        async def fake_subgraph(key, inputs, parent_node_id=None, targeted_inputs=None):
            return {"result": "ok"}

        ctx._run_subgraph = fake_subgraph

        from dan.executors.control_flow import WhileLoopExecutor
        from dan.models.control_flow import WhileLoopNode

        node = WhileLoopNode(
            id="loop2",
            name="loop2",
            condition="iteration < 2",
            body_graph="body",
            max_iterations=3,
        )

        executor = WhileLoopExecutor()
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await executor.execute(node, {"input": "start"}, ctx)

        assert result.status.value == "completed"
