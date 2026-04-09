from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dan.engine.conditions import apply_feedback_selector
from dan.engine.executor import ExecutorRegistry
from dan.executor_defaults import (
    DEFAULT_DIRECT_LEGACY_COMPUTE_TYPES,
    DEFAULT_WORKER_RUNTIME_COMPUTE_TYPES,
    build_default_tool_registry,
    register_default_executors,
)
from dan.models.context import FeedbackSelector
from dan.worker.adapters import build_default_worker_executors
from dan.worker.adapters import LegacyWorkerAdapterExecutor
from dan.worker.adapters import WorkerBackedLegacyComputeExecutor
from dan.worker.executor import WorkerExecutor


def _run_without_openai(script: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{repo_root / 'src'}:{repo_root}"
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )


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


def test_default_compute_families_use_worker_runtime_for_ready_families() -> None:
    registry = ExecutorRegistry()

    register_default_executors(registry)

    tool_executor = registry.get("tool_operator")
    llm_executor = registry.get("llm_operator")
    code_executor = registry.get("code_operator")
    input_executor = registry.get("input")
    router_executor = registry.get("router")
    validator_executor = registry.get("validator")
    reflection_executor = registry.get("reflection")
    rag_executor = registry.get("rag_operator")
    human_executor = registry.get("human")
    human_in_the_loop_executor = registry.get("human_in_the_loop")
    vote_executor = registry.get("vote")
    reduce_executor = registry.get("reduce")
    worker_executor = registry.get("worker")

    assert isinstance(worker_executor, WorkerExecutor)
    for node_type in DEFAULT_WORKER_RUNTIME_COMPUTE_TYPES:
        executor = registry.get(node_type)
        assert isinstance(executor, WorkerBackedLegacyComputeExecutor)
        assert executor.worker_executor is worker_executor
    for node_type in DEFAULT_DIRECT_LEGACY_COMPUTE_TYPES:
        assert not isinstance(registry.get(node_type), LegacyWorkerAdapterExecutor)
        assert not isinstance(registry.get(node_type), WorkerBackedLegacyComputeExecutor)
    assert tool_executor.registry is worker_executor._tool.registry
    assert llm_executor.worker_executor is worker_executor
    assert code_executor.worker_executor is worker_executor
    assert input_executor.worker_executor is worker_executor
    assert router_executor is worker_executor._router
    assert validator_executor is worker_executor._validator
    assert reflection_executor is worker_executor._reflection
    assert rag_executor is worker_executor._rag
    assert human_executor is worker_executor._human
    assert human_in_the_loop_executor is worker_executor._human
    assert vote_executor is worker_executor._vote
    assert reduce_executor is worker_executor._reduce
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

    tool_executor = registry.get("tool_operator")
    worker_executor = registry.get("worker")

    assert isinstance(worker_executor, WorkerExecutor)
    assert isinstance(tool_executor, WorkerBackedLegacyComputeExecutor)
    assert tool_executor.worker_executor is worker_executor
    assert tool_executor.registry is worker_executor._tool.registry
    assert worker_executor._tool.registry.has("custom_tool")


def test_build_default_worker_executors_shares_worker_and_legacy_adapter() -> None:
    from dan.executors.tool import ToolRegistry

    async def _custom_tool(**kwargs):
        return kwargs

    tool_registry = ToolRegistry()
    tool_registry.register("custom_tool", _custom_tool)

    bundle = build_default_worker_executors(tool_registry=tool_registry)

    assert isinstance(bundle.worker_executor, WorkerExecutor)
    assert isinstance(bundle.legacy_worker_adapter, LegacyWorkerAdapterExecutor)
    assert bundle.legacy_worker_adapter.worker_executor is bundle.worker_executor
    assert bundle.llm_worker_runtime.worker_executor is bundle.worker_executor
    assert bundle.tool_worker_runtime.worker_executor is bundle.worker_executor
    assert bundle.tool_worker_runtime.registry is bundle.tool_executor.registry
    assert bundle.code_worker_runtime.worker_executor is bundle.worker_executor
    assert bundle.input_worker_runtime.worker_executor is bundle.worker_executor
    assert bundle.worker_executor._tool.registry.has("custom_tool")


def test_llm_executor_module_import_is_lazy_about_openai() -> None:
    result = _run_without_openai(
        """
import importlib.abc

class _OpenAIBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "openai" or fullname.startswith("openai."):
            raise ModuleNotFoundError("blocked openai import for lazy-import test")
        return None

import sys
sys.meta_path.insert(0, _OpenAIBlocker())

from dan.executors.llm import LLMExecutor

LLMExecutor()
print("ok")
"""
    )

    assert result.returncode == 0, result.stderr


def test_default_executor_registration_is_lazy_about_openai() -> None:
    result = _run_without_openai(
        """
import importlib.abc

class _OpenAIBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "openai" or fullname.startswith("openai."):
            raise ModuleNotFoundError("blocked openai import for default-registry test")
        return None

import sys
sys.meta_path.insert(0, _OpenAIBlocker())

from dan.engine.executor import ExecutorRegistry
from dan.executor_defaults import register_default_executors
from dan.server.run_manager import RunManager

registry = ExecutorRegistry()
register_default_executors(registry)
assert registry.has("worker")

manager = RunManager()
executor_registry = manager._make_executor_registry()
assert executor_registry.has("worker")
print("ok")
"""
    )

    assert result.returncode == 0, result.stderr


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
