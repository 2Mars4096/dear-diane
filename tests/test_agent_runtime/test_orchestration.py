from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest

from dan.agent_runtime.orchestration import (
    ChildExecutionResolution,
    build_mixed_execution_groups,
    execute_child_execution_policy,
    estimate_child_tier,
    merge_child_result_usage,
    merge_token_usage,
    plan_child_session,
    should_llm_synthesize,
    synthesize_child_results,
)
from dan.chat_events import ChatCompleteEvent, ChatInterruptedEvent


def test_estimate_child_tier_prefers_single_step_verbs() -> None:
    assert estimate_child_tier("Search the docs") == 1
    assert estimate_child_tier("Translate the paragraph") == 1


def test_estimate_child_tier_promotes_multi_step_work() -> None:
    assert estimate_child_tier("Research the market") == 2
    assert estimate_child_tier("Draft the memo and prepare the changelog") == 2


def test_build_mixed_execution_groups_splits_dependent_followups() -> None:
    groups = build_mixed_execution_groups(
        [
            "Inspect the logs",
            "Check the config",
            "Summarize the root cause",
            "Prepare the rollback note",
        ]
    )

    assert groups == [
        ["Inspect the logs", "Check the config"],
        ["Summarize the root cause"],
        ["Prepare the rollback note"],
    ]


def test_build_mixed_execution_groups_parallelizes_shared_dependent_stage() -> None:
    groups = build_mixed_execution_groups(
        [
            "search docs",
            "inspect repo",
            "summarize docs findings",
            "summarize repo findings",
            "compile final answer",
        ]
    )

    assert groups == [
        ["search docs", "inspect repo"],
        ["summarize docs findings", "summarize repo findings"],
        ["compile final answer"],
    ]


def test_plan_child_session_filters_workflow_hints_for_non_workflow_subtasks() -> None:
    parent_task_context = {"mutable": {"a": 1}}
    parent_handoff_context = {
        "recent_turns": ["user: inspect repo", "assistant: found two hotspots"],
        "file_refs": ["/tmp/app.py"],
        "memory_context": "Prior fix failed because tests were skipped.",
        "file_snippets": {"/tmp/app.py": "def main() -> None:\n    pass"},
    }

    plan = plan_child_session(
        task_desc="search docs",
        parent_session_id="parent-1",
        root_session_id="root-1",
        parent_task="Investigate and summarize",
        parent_task_context=parent_task_context,
        has_parent_route=True,
        parent_route_target="workflow",
        parent_action_hints=["workflow_query", "search_web"],
        parent_message_session_id="thread-42",
        parent_message_thread_id="thread-42",
        parent_handoff_context=parent_handoff_context,
        child_session_id="child-1",
        child_thread_id="tiered-child-child-1",
    )

    assert plan.child_tier == 1
    assert plan.route_target == "general"
    assert plan.action_hints == ("search_web",)
    assert plan.allow_mutation_tool is False
    assert plan.parent_thread_id == "thread-42"
    assert plan.handoff["packet_version"] == 1
    assert plan.handoff["kind"] == "tiered_child_session"
    assert plan.handoff["goal"]["delegated_task"] == "search docs"
    assert plan.handoff["constraints"]["read_only_parent_context"] is True
    assert plan.handoff["constraints"]["copy_on_write_metadata"] is True
    assert plan.handoff["return_channel"]["parent_session_id"] == "parent-1"
    assert plan.task_context["handoff"] == plan.handoff
    assert plan.metadata_patch["route_target"] == "general"
    assert plan.metadata_patch["allow_mutation_tool"] is False
    assert plan.metadata_patch["tiered_child_session_id"] == "child-1"
    assert plan.metadata_patch["tiered_child_thread_id"] == "tiered-child-child-1"
    assert plan.task_context["parent_context"]["file_refs"] == ["/tmp/app.py"]
    plan.task_context["parent_context"]["file_refs"].append("/tmp/other.py")
    assert parent_handoff_context["file_refs"] == ["/tmp/app.py"]
    assert "mutable" not in plan.task_context["parent_context"]
    assert parent_task_context["mutable"]["a"] == 1


def test_plan_child_session_preserves_workflow_route_for_workflow_subtasks() -> None:
    plan = plan_child_session(
        task_desc="Build the workflow graph",
        parent_session_id="parent-1",
        root_session_id="root-1",
        parent_task="Create the workflow",
        parent_task_context={},
        has_parent_route=True,
        parent_route_target="workflow",
        parent_action_hints=["workflow_build"],
        parent_message_session_id=None,
        parent_message_thread_id="thread-99",
    )

    assert plan.child_tier == 2
    assert plan.route_target == "workflow"
    assert plan.action_hints == ("workflow_build",)
    assert plan.allow_mutation_tool is True
    assert plan.parent_thread_id == "thread-99"
    assert plan.metadata_patch["allow_mutation_tool"] is True


def test_should_llm_synthesize_respects_autonomy_policy() -> None:
    assert should_llm_synthesize(autonomy_level="aggressive", child_results_count=1) is True
    assert should_llm_synthesize(autonomy_level="balanced", child_results_count=2) is False
    assert should_llm_synthesize(autonomy_level="balanced", child_results_count=3) is True
    assert should_llm_synthesize(autonomy_level="careful", child_results_count=5) is False


def test_synthesize_child_results_optionally_surfaces_uncertainties() -> None:
    base = synthesize_child_results(
        [
            ("Patch code", "Updated the executor."),
            ("Validate changes", "Validation note:\n- [ ] rerun the full integration suite"),
        ]
    )
    careful = synthesize_child_results(
        [
            ("Patch code", "Updated the executor."),
            ("Validate changes", "Validation note:\n- [ ] rerun the full integration suite"),
        ],
        include_uncertainties=True,
        uncertainties=["- Validate changes: contains pending checklist items (rerun the full integration suite)"],
    )

    assert "## Uncertainties" not in base
    assert "## Uncertainties" in careful
    assert "pending checklist items" in careful


def test_merge_token_usage_rolls_up_child_and_extra_usage() -> None:
    class _Result:
        def __init__(self, token_usage: dict[str, int]) -> None:
            self.token_usage = token_usage

    merged = merge_token_usage(
        merge_child_result_usage(
            [
                _Result({"prompt_tokens": 11, "completion_tokens": 3}),
                _Result({"prompt_tokens": 5, "completion_tokens": 7}),
            ]
        ),
        {"prompt_tokens": 2},
        {"completion_tokens": 1, "total_tokens": 29},
    )

    assert merged == {
        "prompt_tokens": 18,
        "completion_tokens": 11,
        "total_tokens": 29,
    }


async def _collect_items(stream: Any) -> list[Any]:
    items: list[Any] = []
    async for item in stream:
        items.append(item)
    return items


@pytest.mark.asyncio
async def test_execute_child_execution_policy_carries_previous_serial_result() -> None:
    built_children: list[Any] = []
    results: dict[str, Any] = {}

    def _build_child(task_desc: str, previous_result: str | None) -> Any:
        child = SimpleNamespace(
            id=f"child-{len(built_children) + 1}",
            task=task_desc,
            previous_result=previous_result,
        )
        built_children.append(child)
        return child

    async def _run_child(child: Any):
        results[child.id] = SimpleNamespace(content=f"{child.task} done")
        yield ChatCompleteEvent(
            message_id=f"{child.id}-done",
            content=results[child.id].content,
            graph_revision="",
        )

    async def _run_parallel_children(children: Any):
        if children:
            pytest.fail("serial policy should not call the parallel runner")
        if False:
            yield children

    items = await _collect_items(
        execute_child_execution_policy(
            ["search docs", "summarize findings"],
            child_execution="serial",
            build_child=_build_child,
            run_child=_run_child,
            run_parallel_children=_run_parallel_children,
            child_progress_event=None,
            child_result=lambda child: results.get(child.id),
            child_result_content=lambda result: getattr(result, "content", None),
            synthesize_results=lambda child_results: "",
            is_cancelled=lambda: False,
            can_spawn_child=lambda: True,
        )
    )

    resolution = items[-1]
    assert isinstance(resolution, ChildExecutionResolution)
    assert built_children[0].previous_result is None
    assert built_children[1].previous_result == "search docs done"
    assert resolution.child_results["child-1"].content == "search docs done"
    assert resolution.child_results["child-2"].content == "summarize findings done"


@pytest.mark.asyncio
async def test_execute_child_execution_policy_carries_mixed_group_summary() -> None:
    built_children: list[Any] = []
    results: dict[str, Any] = {}

    def _build_child(task_desc: str, previous_result: str | None) -> Any:
        child = SimpleNamespace(
            id=f"child-{len(built_children) + 1}",
            task=task_desc,
            previous_result=previous_result,
        )
        built_children.append(child)
        return child

    async def _run_child(child: Any):
        results[child.id] = SimpleNamespace(content=f"{child.task} done")
        yield ChatCompleteEvent(
            message_id=f"{child.id}-done",
            content=results[child.id].content,
            graph_revision="",
        )

    async def _run_parallel_children(children: Any):
        for child in children:
            results[child.id] = SimpleNamespace(content=f"{child.task} done")
            yield ChatCompleteEvent(
                message_id=f"{child.id}-done",
                content=results[child.id].content,
                graph_revision="",
            )

    items = await _collect_items(
        execute_child_execution_policy(
            ["search docs", "inspect repo", "summarize findings"],
            child_execution="mixed",
            build_child=_build_child,
            run_child=_run_child,
            run_parallel_children=_run_parallel_children,
            child_progress_event=None,
            child_result=lambda child: results.get(child.id),
            child_result_content=lambda result: getattr(result, "content", None),
            synthesize_results=lambda child_results: "\n".join(
                getattr(result, "content", "")
                for _child_id, result in sorted(child_results.items())
            ),
            is_cancelled=lambda: False,
            can_spawn_child=lambda: True,
        )
    )

    resolution = items[-1]
    assert isinstance(resolution, ChildExecutionResolution)
    assert built_children[0].previous_result is None
    assert built_children[1].previous_result is None
    assert built_children[2].previous_result == "search docs done\ninspect repo done"
    assert resolution.child_results["child-3"].content == "summarize findings done"


@pytest.mark.asyncio
async def test_execute_child_execution_policy_mixed_runs_dependent_stage_in_parallel() -> None:
    subtasks = [
        "search docs",
        "inspect repo",
        "summarize docs findings",
        "summarize repo findings",
        "compile final answer",
    ]
    delays = {
        "search docs": 0.15,
        "inspect repo": 0.15,
        "summarize docs findings": 0.15,
        "summarize repo findings": 0.15,
        "compile final answer": 0.0,
    }
    contents = {
        "search docs": "Search complete.",
        "inspect repo": "Repo complete.",
        "summarize docs findings": "Docs summary complete.",
        "summarize repo findings": "Repo summary complete.",
        "compile final answer": "Final answer complete.",
    }
    children: list[Any] = []
    sequence = 0

    def build_child(task_desc: str, previous_result: str | None) -> Any:
        nonlocal sequence
        sequence += 1
        child = SimpleNamespace(
            id=f"child-{sequence}",
            task=task_desc,
            previous_result=previous_result,
            result=None,
            started_at=None,
        )
        children.append(child)
        return child

    async def run_child(child: Any) -> AsyncIterator[ChatCompleteEvent]:
        child.started_at = time.monotonic()
        await asyncio.sleep(delays[child.task])
        child.result = SimpleNamespace(content=contents[child.task])
        yield ChatCompleteEvent(
            message_id=f"{child.id}-done",
            content=child.result.content,
            graph_revision="",
        )

    async def run_parallel_children(group: list[Any]) -> AsyncIterator[ChatCompleteEvent]:
        queue: asyncio.Queue[ChatCompleteEvent | None] = asyncio.Queue()

        async def _run_one(child: Any) -> None:
            async for event in run_child(child):
                await queue.put(event)
            await queue.put(None)

        tasks = [asyncio.create_task(_run_one(child)) for child in group]
        active = len(tasks)
        try:
            while active:
                event = await queue.get()
                if event is None:
                    active -= 1
                    continue
                yield event
        finally:
            await asyncio.gather(*tasks)

    def child_result(child: Any) -> Any | None:
        return child.result

    def child_result_content(result: Any) -> str | None:
        return getattr(result, "content", None)

    def synthesize_results(results: dict[str, Any]) -> str:
        return " | ".join(
            getattr(result, "content", "")
            for result in results.values()
            if getattr(result, "content", "")
        )

    resolution: ChildExecutionResolution | None = None
    start = time.monotonic()
    async for item in execute_child_execution_policy(
        subtasks,
        child_execution="mixed",
        build_child=build_child,
        run_child=run_child,
        run_parallel_children=run_parallel_children,
        child_progress_event=None,
        child_result=child_result,
        child_result_content=child_result_content,
        synthesize_results=synthesize_results,
        is_cancelled=lambda: False,
        can_spawn_child=lambda: True,
    ):
        if isinstance(item, ChildExecutionResolution):
            resolution = item
    elapsed = time.monotonic() - start

    assert resolution is not None
    assert elapsed < 0.42, f"expected mixed-stage fan-out, got {elapsed:.3f}s"
    assert len(resolution.children) == 5
    assert not resolution.interrupted

    stage_two_a = children[2]
    stage_two_b = children[3]
    final_child = children[4]

    assert "Search complete." in (stage_two_a.previous_result or "")
    assert "Repo complete." in (stage_two_a.previous_result or "")
    assert "Search complete." in (stage_two_b.previous_result or "")
    assert "Repo complete." in (stage_two_b.previous_result or "")
    assert abs(stage_two_a.started_at - stage_two_b.started_at) < 0.05
    assert "Docs summary complete." in (final_child.previous_result or "")
    assert "Repo summary complete." in (final_child.previous_result or "")


@pytest.mark.asyncio
async def test_execute_child_execution_policy_marks_parallel_interruption() -> None:
    built_children: list[Any] = []

    def _build_child(task_desc: str, previous_result: str | None) -> Any:
        child = SimpleNamespace(
            id=f"child-{len(built_children) + 1}",
            task=task_desc,
            previous_result=previous_result,
        )
        built_children.append(child)
        return child

    async def _run_child(child: Any):
        if False:
            yield child

    async def _run_parallel_children(children: Any):
        yield ChatInterruptedEvent(
            message_id="interrupted",
            content="stopped",
            token_usage={},
        )

    items = await _collect_items(
        execute_child_execution_policy(
            ["search docs", "inspect repo"],
            child_execution="parallel",
            build_child=_build_child,
            run_child=_run_child,
            run_parallel_children=_run_parallel_children,
            child_progress_event=None,
            child_result=lambda child: None,
            child_result_content=lambda result: None,
            synthesize_results=lambda child_results: "",
            is_cancelled=lambda: False,
            can_spawn_child=lambda: True,
        )
    )

    assert isinstance(items[0], ChatInterruptedEvent)
    resolution = items[-1]
    assert isinstance(resolution, ChildExecutionResolution)
    assert resolution.interrupted is True
    assert resolution.interrupted_emitted is True
    assert len(resolution.children) == 2
