from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dan.engine.conditions import apply_feedback_selector
from dan.engine.executor import ExecutorRegistry
from dan.executor_defaults import build_default_tool_registry, register_default_executors
from dan.executors.tool import ToolExecutor
from dan.models.context import FeedbackSelector
from dan.worker.adapters import build_default_worker_executors
from dan.worker.executor import LegacyWorkerAdapterExecutor, WorkerExecutor


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
