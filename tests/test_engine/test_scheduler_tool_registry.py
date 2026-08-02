"""Regression tests for tool-registry wiring into execution context."""

from __future__ import annotations

from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.executor import EngineConfig, ExecutorRegistry
from dan.engine.scheduler import Engine
from dan.engine.state import ExecutionState
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.graph import Graph


def test_scheduler_injects_tool_registry_into_execution_context() -> None:
    registry = ToolRegistry()

    async def echo_tool(text: str) -> dict[str, str]:
        return {"text": text}

    registry.register("echo", echo_tool)

    executors = ExecutorRegistry()
    executors.register("tool_operator", ToolExecutor(registry))

    engine = Engine(
        config=EngineConfig(checkpoint_enabled=False, memory_enabled=False),
        executor_registry=executors,
    )

    graph = Graph()
    state = ExecutionState(graph=graph, run_id="run-test")
    context = engine._make_context(
        state=state,
        shared_context=SharedContextStore(),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        graph=graph,
    )

    assert context.tool_registry is registry
