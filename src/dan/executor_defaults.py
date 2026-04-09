"""Default executor registration helpers.

This module is intentionally outside ``engine`` so the scheduler can wire the
built-in executor set without statically importing ``dan.executors``.
"""

from __future__ import annotations

from typing import Any

DEFAULT_WORKER_RUNTIME_COMPUTE_TYPES: frozenset[str] = frozenset({
    "llm_operator",
    "tool_operator",
    "code_operator",
    "input",
})

DEFAULT_DIRECT_LEGACY_COMPUTE_TYPES: frozenset[str] = frozenset({
    "rag_operator",
    "router",
    "human",
    "human_in_the_loop",
    "validator",
    "vote",
    "reduce",
    "reflection",
})


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
        # 50-7 / 4-1: the ready simple families now execute through the
        # Worker runtime in the default path, but without routing through the
        # generic LegacyWorkerAdapterExecutor compatibility seam.
        ("llm_operator", worker_bundle.llm_worker_runtime),
        ("tool_operator", worker_bundle.tool_worker_runtime),
        ("code_operator", worker_bundle.code_worker_runtime),
        ("worker", worker_bundle.worker_executor),
        ("rag_operator", worker_bundle.rag_executor),
        ("input", worker_bundle.input_worker_runtime),
        ("if_else", IfElseExecutor()),
        ("gate", worker_bundle.gate_executor),
        ("while_loop", WhileLoopExecutor()),
        ("for_each", ForEachExecutor()),
        ("parallel_subagents", ParallelSubagentsExecutor()),
        ("orchestrator", OrchestratorExecutor()),
        ("router", worker_bundle.router_executor),
        ("human", worker_bundle.human_executor),
        ("human_in_the_loop", worker_bundle.human_executor),
        ("validator", worker_bundle.validator_executor),
        ("composite", CompositeExecutor()),
        ("vote", worker_bundle.vote_executor),
        ("reduce", worker_bundle.reduce_executor),
        ("agent_team", AgentTeamExecutor()),
        ("reflection", worker_bundle.reflection_executor),
        ("goal_loop", GoalLoopExecutor()),
    ]

    for node_type, executor in defaults:
        if not executor_registry.has(node_type):
            executor_registry.register(node_type, executor)
