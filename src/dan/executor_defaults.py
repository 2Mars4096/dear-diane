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
    from dan.executors.code import CodeExecutor
    from dan.executors.control_flow import (
        AgentTeamExecutor,
        CompositeExecutor,
        ForEachExecutor,
        GateExecutor,
        GoalLoopExecutor,
        HumanNodeExecutor,
        IfElseExecutor,
        OrchestratorExecutor,
        ParallelSubagentsExecutor,
        ReduceExecutor,
        RouterExecutor,
        VoteExecutor,
        WhileLoopExecutor,
    )
    from dan.executors.llm import LLMExecutor
    from dan.executors.rag import RAGExecutor
    from dan.executors.reflection import ReflectionExecutor
    from dan.executors.tool import ToolExecutor
    from dan.executors.validator import ValidatorExecutor
    from dan.worker.executor import LegacyWorkerAdapterExecutor, WorkerExecutor

    llm_executor = LLMExecutor()
    code_executor = CodeExecutor()
    human_executor = HumanNodeExecutor()
    router_executor = RouterExecutor()
    validator_executor = ValidatorExecutor()
    reflection_executor = ReflectionExecutor()
    tool_executor = ToolExecutor(tool_registry or build_default_tool_registry())
    worker_executor = WorkerExecutor(
        llm_executor=llm_executor,
        tool_executor=tool_executor,
        code_executor=code_executor,
        router_executor=router_executor,
        validator_executor=validator_executor,
        reflection_executor=reflection_executor,
        human_executor=human_executor,
        vote_executor=VoteExecutor(),
        rag_executor=RAGExecutor(),
    )
    legacy_worker_adapter = LegacyWorkerAdapterExecutor(worker_executor)
    defaults: list[tuple[str, Any]] = [
        ("llm_operator", legacy_worker_adapter),
        ("tool_operator", legacy_worker_adapter),
        ("code_operator", legacy_worker_adapter),
        ("worker", worker_executor),
        ("rag_operator", legacy_worker_adapter),
        ("input", legacy_worker_adapter),
        ("if_else", IfElseExecutor()),
        ("gate", GateExecutor()),
        ("while_loop", WhileLoopExecutor()),
        ("for_each", ForEachExecutor()),
        ("parallel_subagents", ParallelSubagentsExecutor()),
        ("orchestrator", OrchestratorExecutor()),
        ("router", legacy_worker_adapter),
        ("human", legacy_worker_adapter),
        ("human_in_the_loop", legacy_worker_adapter),
        ("validator", legacy_worker_adapter),
        ("composite", CompositeExecutor()),
        ("vote", legacy_worker_adapter),
        ("reduce", legacy_worker_adapter),
        ("agent_team", AgentTeamExecutor()),
        ("reflection", legacy_worker_adapter),
        ("goal_loop", GoalLoopExecutor()),
    ]

    for node_type, executor in defaults:
        if not executor_registry.has(node_type):
            executor_registry.register(node_type, executor)
