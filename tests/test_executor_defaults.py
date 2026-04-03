from __future__ import annotations

from dan.engine.conditions import apply_feedback_selector
from dan.engine.executor import ExecutorRegistry
from dan.executor_defaults import build_default_tool_registry, register_default_executors
from dan.executors.tool import ToolExecutor
from dan.models.context import FeedbackSelector
from dan.worker.executor import LegacyWorkerAdapterExecutor, WorkerExecutor


def test_register_default_executors_populates_builtin_node_types() -> None:
    registry = ExecutorRegistry()

    register_default_executors(registry)

    expected = {
        "agent_team",
        "code_operator",
        "composite",
        "for_each",
        "gate",
        "goal_loop",
        "human",
        "human_in_the_loop",
        "if_else",
        "input",
        "llm_operator",
        "orchestrator",
        "parallel_subagents",
        "rag_operator",
        "reduce",
        "reflection",
        "router",
        "tool_operator",
        "validator",
        "vote",
        "while_loop",
    }

    assert expected.issubset(set(registry.registered_types()))


def test_default_compute_family_adapters_share_worker_executor_and_builtin_tools() -> None:
    registry = ExecutorRegistry()

    register_default_executors(registry)

    tool_adapter = registry.get("tool_operator")
    llm_adapter = registry.get("llm_operator")
    code_adapter = registry.get("code_operator")
    reduce_adapter = registry.get("reduce")
    worker_executor = registry.get("worker")

    assert isinstance(tool_adapter, LegacyWorkerAdapterExecutor)
    assert isinstance(llm_adapter, LegacyWorkerAdapterExecutor)
    assert isinstance(code_adapter, LegacyWorkerAdapterExecutor)
    assert isinstance(reduce_adapter, LegacyWorkerAdapterExecutor)
    assert isinstance(worker_executor, WorkerExecutor)
    assert tool_adapter.worker_executor is worker_executor
    assert llm_adapter.worker_executor is worker_executor
    assert code_adapter.worker_executor is worker_executor
    assert reduce_adapter.worker_executor is worker_executor
    assert isinstance(worker_executor._tool, ToolExecutor)
    assert worker_executor._tool.registry.has("pdf_read")


def test_build_default_tool_registry_registers_builtin_tools() -> None:
    registry = build_default_tool_registry()

    assert registry.has("pdf_read")
    assert registry.has("web_search")


def test_register_default_executors_can_use_custom_tool_registry() -> None:
    from dan.executors.tool import ToolRegistry

    async def _custom_tool(**kwargs):
        return kwargs

    tool_registry = ToolRegistry()
    tool_registry.register("custom_tool", _custom_tool)
    registry = ExecutorRegistry()

    register_default_executors(registry, tool_registry=tool_registry)

    tool_adapter = registry.get("tool_operator")
    worker_executor = registry.get("worker")

    assert isinstance(tool_adapter, LegacyWorkerAdapterExecutor)
    assert isinstance(worker_executor, WorkerExecutor)
    assert tool_adapter.worker_executor is worker_executor
    assert worker_executor._tool.registry.has("custom_tool")


def test_apply_feedback_selector_filters_renames_and_transforms() -> None:
    selector = FeedbackSelector(
        include=["draft", "score"],
        rename={"draft": "previous_draft"},
        transform="{'previous_draft': inputs['previous_draft'], 'score': inputs['score'] + 1}",
    )

    result = apply_feedback_selector(
        {"draft": "v1", "score": 2, "ignored": "nope"},
        selector,
    )

    assert result == {"previous_draft": "v1", "score": 3}
