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


def register_default_executors(executor_registry: Any) -> None:
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
    from dan.executors.input import InputExecutor
    from dan.executors.llm import LLMExecutor
    from dan.executors.rag import RAGExecutor
    from dan.executors.reflection import ReflectionExecutor
    from dan.executors.tool import ToolExecutor
    from dan.executors.validator import ValidatorExecutor

    human_executor = HumanNodeExecutor()
    tool_executor = ToolExecutor(build_default_tool_registry())
    defaults: list[tuple[str, Any]] = [
        ("llm_operator", LLMExecutor()),
        ("tool_operator", tool_executor),
        ("code_operator", CodeExecutor()),
        ("rag_operator", RAGExecutor()),
        ("input", InputExecutor()),
        ("if_else", IfElseExecutor()),
        ("gate", GateExecutor()),
        ("while_loop", WhileLoopExecutor()),
        ("for_each", ForEachExecutor()),
        ("parallel_subagents", ParallelSubagentsExecutor()),
        ("orchestrator", OrchestratorExecutor()),
        ("reduce", ReduceExecutor()),
        ("router", RouterExecutor()),
        ("human", human_executor),
        ("human_in_the_loop", human_executor),
        ("validator", ValidatorExecutor()),
        ("composite", CompositeExecutor()),
        ("vote", VoteExecutor()),
        ("agent_team", AgentTeamExecutor()),
        ("reflection", ReflectionExecutor()),
        ("goal_loop", GoalLoopExecutor()),
    ]

    for node_type, executor in defaults:
        if not executor_registry.has(node_type):
            executor_registry.register(node_type, executor)
