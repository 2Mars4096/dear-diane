"""Default executor registration helpers.

This module is intentionally outside ``engine`` so the scheduler can wire the
built-in executor set without statically importing ``dan.executors``.
"""

from __future__ import annotations

from typing import Any


def build_default_tool_registry() -> Any:
    """Create the built-in tool registry used by default tool executors."""
    from dan.executors.tool import ToolRegistry

    registry = ToolRegistry()
    registry.register_builtin_tools()
    return registry


def register_default_executors(
    executor_registry: Any,
    *,
    tool_registry: Any | None = None,
) -> None:
    """Register built-in executors for all standard node types."""
    from dan.executors.control_flow import (
        AgentTeamExecutor,
        CompositeExecutor,
        ForEachExecutor,
        GoalLoopExecutor,
        IfElseExecutor,
        OrchestratorExecutor,
        ParallelSubagentsExecutor,
        WhileLoopExecutor,
    )
    from dan.worker.adapters import build_default_worker_executors

    worker_bundle = build_default_worker_executors(
        tool_registry=tool_registry or build_default_tool_registry(),
    )
    defaults: list[tuple[str, Any]] = [
        ("llm_operator", worker_bundle.legacy_worker_adapter),
        ("tool_operator", worker_bundle.legacy_worker_adapter),
        ("code_operator", worker_bundle.legacy_worker_adapter),
        ("worker", worker_bundle.worker_executor),
        ("rag_operator", worker_bundle.legacy_worker_adapter),
        ("input", worker_bundle.legacy_worker_adapter),
        ("if_else", IfElseExecutor()),
        ("gate", worker_bundle.gate_executor),
        ("while_loop", WhileLoopExecutor()),
        ("for_each", ForEachExecutor()),
        ("parallel_subagents", ParallelSubagentsExecutor()),
        ("orchestrator", OrchestratorExecutor()),
        ("router", worker_bundle.legacy_worker_adapter),
        ("human", worker_bundle.legacy_worker_adapter),
        ("human_in_the_loop", worker_bundle.legacy_worker_adapter),
        ("validator", worker_bundle.legacy_worker_adapter),
        ("composite", CompositeExecutor()),
        ("vote", worker_bundle.legacy_worker_adapter),
        ("reduce", worker_bundle.legacy_worker_adapter),
        ("agent_team", AgentTeamExecutor()),
        ("reflection", worker_bundle.legacy_worker_adapter),
        ("goal_loop", GoalLoopExecutor()),
    ]

    for node_type, executor in defaults:
        if not executor_registry.has(node_type):
            executor_registry.register(node_type, executor)
