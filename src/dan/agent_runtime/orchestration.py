"""Pure multi-step execution helpers shared by orchestration surfaces."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, AsyncIterator, Callable

from dan.chat_events import ChatInterruptedEvent, ChatStreamEvent

_SINGLE_ACTION_VERBS = frozenset({
    "search", "read", "find", "lookup", "check", "get",
    "fetch", "calculate", "compute", "summarize", "translate",
})
_MULTI_ACTION_KEYWORDS = frozenset({
    "research", "analyze", "write", "build", "create",
    "develop", "design", "investigate",
})
_MIXED_DEPENDENT_PREFIXES = frozenset({
    "summarize", "synthesize", "combine", "review", "finalize",
    "draft", "write", "prepare", "present", "compile",
})
_MIXED_DEPENDENCY_RE = re.compile(
    r"\b(?:based on|using|from the|from previous|from findings|review the|summarize|synthesize|combine|finalize)\b",
    re.IGNORECASE,
)
_WORKFLOW_TASK_RE = re.compile(
    r"\b(?:workflow|graph|node|edge|mutation|build|edit)\b",
    re.IGNORECASE,
)
_WORKFLOW_ROUTE_HINTS = frozenset({
    "workflow_edit",
    "workflow_build",
    "workflow_query",
})
_MUTATION_TOOL_HINTS = frozenset({
    "workflow_edit",
    "workflow_build",
})


@dataclass(frozen=True)
class ChildSessionPlan:
    """Primitive child-session envelope computed without concierge types."""

    child_tier: int
    route_target: str | None
    action_hints: tuple[str, ...]
    allow_mutation_tool: bool
    parent_thread_id: str | None
    handoff: dict[str, object]
    task_context: dict[str, object]
    metadata_patch: dict[str, object]


@dataclass(frozen=True)
class ChildExecutionResolution:
    """Resolved state after running one child-execution policy pass."""

    children: tuple[Any, ...]
    child_results: dict[str, Any]
    interrupted: bool = False
    interrupted_emitted: bool = False


def estimate_child_tier(task_desc: str) -> int:
    """Classify a decomposed child task into a fast single-step or multi-step tier."""
    first_word = task_desc.strip().split()[0].lower() if task_desc.strip() else ""
    if first_word in _SINGLE_ACTION_VERBS:
        return 1

    lower = task_desc.lower()
    if len(re.split(r"\s+\band\b\s+", lower, flags=re.IGNORECASE)) > 1:
        return 2
    for keyword in _MULTI_ACTION_KEYWORDS:
        if keyword in lower:
            return 2
    return 1


def mixed_subtask_depends_on_prior(task_desc: str) -> bool:
    """Return True when a mixed-mode subtask should wait for prior group output."""
    lower = task_desc.strip().lower()
    if not lower:
        return False
    first_word = lower.split()[0]
    if first_word in _MIXED_DEPENDENT_PREFIXES:
        return True
    return bool(_MIXED_DEPENDENCY_RE.search(lower))


def build_mixed_execution_groups(subtasks: Sequence[str]) -> list[list[str]]:
    """Group independent subtasks so mixed execution can parallelize only safe slices."""
    groups: list[list[str]] = []
    current_parallel_group: list[str] = []
    for index, task_desc in enumerate(subtasks):
        task_text = str(task_desc or "").strip()
        if not task_text:
            continue
        if index > 0 and mixed_subtask_depends_on_prior(task_text):
            if current_parallel_group:
                groups.append(current_parallel_group)
                current_parallel_group = []
            groups.append([task_text])
            continue
        current_parallel_group.append(task_text)
    if current_parallel_group:
        groups.append(current_parallel_group)
    return groups


def _task_mentions_workflow(task_desc: str) -> bool:
    return bool(_WORKFLOW_TASK_RE.search(task_desc))


def plan_child_session(
    *,
    task_desc: str,
    parent_session_id: str,
    root_session_id: str,
    parent_task: str,
    parent_task_context: Mapping[str, object] | None,
    has_parent_route: bool = False,
    parent_route_target: str | None = None,
    parent_action_hints: Sequence[str] | None = None,
    parent_message_session_id: str | None = None,
    parent_message_thread_id: str | None = None,
) -> ChildSessionPlan:
    """Compute the child-session envelope using only primitive inputs."""
    child_tier = estimate_child_tier(task_desc)
    cleaned_action_hints = tuple(
        str(hint).strip()
        for hint in parent_action_hints or []
        if str(hint).strip()
    )
    route_target = str(parent_route_target or "").strip() or None
    route_default = route_target if route_target is not None else ("general" if has_parent_route else None)

    if _task_mentions_workflow(task_desc):
        filtered_action_hints = cleaned_action_hints
        filtered_route_target = route_default
    else:
        filtered_action_hints = tuple(
            hint for hint in cleaned_action_hints
            if hint not in _WORKFLOW_ROUTE_HINTS
        )
        filtered_route_target = "general" if route_target == "workflow" else route_default

    allow_mutation_tool = bool(
        has_parent_route
        and filtered_route_target == "workflow"
        and any(hint in _MUTATION_TOOL_HINTS for hint in filtered_action_hints)
    )
    parent_thread_id = str(
        parent_message_session_id or parent_message_thread_id or ""
    ).strip() or None
    handoff = {
        "parent_session_id": parent_session_id,
        "root_session_id": root_session_id,
        "parent_task": parent_task,
        "child_task": task_desc,
        "route_target": filtered_route_target,
        "action_hints": list(filtered_action_hints),
        "parent_thread_id": parent_thread_id,
    }
    task_context = {
        "parent_task": parent_task,
        "parent_context": copy.deepcopy(parent_task_context or {}),
        "handoff": handoff,
    }
    metadata_patch: dict[str, object] = {
        "tiered_parent_session_id": parent_session_id,
        "tiered_root_session_id": root_session_id,
        "tiered_child_task": task_desc,
        "tiered_handoff": handoff,
        "parent_thread_id": parent_thread_id,
    }
    if has_parent_route:
        metadata_patch["route_target"] = filtered_route_target
        metadata_patch["allow_mutation_tool"] = allow_mutation_tool

    return ChildSessionPlan(
        child_tier=child_tier,
        route_target=filtered_route_target,
        action_hints=filtered_action_hints,
        allow_mutation_tool=allow_mutation_tool,
        parent_thread_id=parent_thread_id,
        handoff=handoff,
        task_context=task_context,
        metadata_patch=metadata_patch,
    )


def _child_key(child: object, fallback_index: int | None = None) -> str:
    child_id = str(getattr(child, "id", "") or "").strip()
    if child_id:
        return child_id
    if fallback_index is not None:
        return f"child-{fallback_index}"
    return f"child-{id(child)}"


async def execute_child_execution_policy(
    subtasks: Sequence[str],
    *,
    child_execution: str,
    build_child: Callable[[str, str | None], Any],
    run_child: Callable[[Any], AsyncIterator[ChatStreamEvent]],
    run_parallel_children: Callable[[Sequence[Any]], AsyncIterator[ChatStreamEvent]],
    child_progress_event: Callable[[Any], ChatStreamEvent | None] | None,
    child_result: Callable[[Any], Any | None],
    child_result_content: Callable[[Any], str | None],
    synthesize_results: Callable[[Mapping[str, Any]], str],
    is_cancelled: Callable[[], bool],
    can_spawn_child: Callable[[], bool],
    mixed_groups: Sequence[Sequence[str]] | None = None,
    on_single_child_error: Callable[[Any, Exception], None] | None = None,
) -> AsyncIterator[ChatStreamEvent | ChildExecutionResolution]:
    """Run child tasks according to serial, parallel, or mixed policy."""
    children: list[Any] = []
    child_results: dict[str, Any] = {}
    interrupted = False
    interrupted_emitted = False

    async def emit_single_child(child: Any) -> AsyncIterator[ChatStreamEvent]:
        if child_progress_event is not None:
            progress_event = child_progress_event(child)
            if progress_event is not None:
                yield progress_event
        async for event in run_child(child):
            yield event

    def record_child_result(child: Any, *, fallback_index: int | None = None) -> Any | None:
        result = child_result(child)
        if result is not None:
            child_results[_child_key(child, fallback_index)] = result
        return result

    try:
        if child_execution == "parallel":
            for task_desc in subtasks:
                if is_cancelled() or not can_spawn_child():
                    break
                children.append(build_child(task_desc, None))

            if is_cancelled():
                interrupted = True
            elif children:
                async for event in run_parallel_children(children):
                    if isinstance(event, ChatInterruptedEvent):
                        interrupted = True
                        interrupted_emitted = True
                        yield event
                        break
                    yield event
        elif child_execution == "mixed":
            previous_group_summary: str | None = None
            groups = [list(group) for group in mixed_groups] if mixed_groups is not None else []
            if not groups:
                groups = build_mixed_execution_groups(subtasks) or [list(subtasks)]
            for group in groups:
                if is_cancelled():
                    interrupted = True
                    break

                group_children: list[Any] = []
                for task_desc in group:
                    if is_cancelled() or not can_spawn_child():
                        break
                    child = build_child(task_desc, previous_group_summary)
                    children.append(child)
                    group_children.append(child)

                if is_cancelled():
                    interrupted = True
                    break
                if not group_children:
                    break

                if len(group_children) == 1:
                    child = group_children[0]
                    try:
                        async for event in emit_single_child(child):
                            if isinstance(event, ChatInterruptedEvent):
                                interrupted = True
                                interrupted_emitted = True
                                yield event
                                break
                            yield event
                    except Exception as exc:
                        if on_single_child_error is not None:
                            on_single_child_error(child, exc)
                else:
                    async for event in run_parallel_children(group_children):
                        if isinstance(event, ChatInterruptedEvent):
                            interrupted = True
                            interrupted_emitted = True
                            yield event
                            break
                        yield event

                group_results: dict[str, Any] = {}
                for index, child in enumerate(group_children):
                    result = child_result(child)
                    if result is None:
                        continue
                    child_key = _child_key(child, index)
                    child_results[child_key] = result
                    group_results[child_key] = result
                if interrupted:
                    break
                if group_results:
                    previous_group_summary = synthesize_results(child_results)
        else:
            previous_result: str | None = None
            for task_desc in subtasks:
                if is_cancelled():
                    interrupted = True
                    break
                if not can_spawn_child():
                    break

                child = build_child(task_desc, previous_result)
                children.append(child)
                try:
                    async for event in emit_single_child(child):
                        if isinstance(event, ChatInterruptedEvent):
                            interrupted = True
                            interrupted_emitted = True
                            yield event
                            break
                        yield event
                except Exception as exc:
                    if on_single_child_error is not None:
                        on_single_child_error(child, exc)

                result = record_child_result(child)
                if result is not None:
                    content = child_result_content(result)
                    previous_result = content if content else None
                if interrupted:
                    break

        for index, child in enumerate(children):
            record_child_result(child, fallback_index=index)
    finally:
        yield ChildExecutionResolution(
            children=tuple(children),
            child_results=child_results,
            interrupted=interrupted,
            interrupted_emitted=interrupted_emitted,
        )


def should_llm_synthesize(*, autonomy_level: str, child_results_count: int) -> bool:
    """Decide whether a multi-step surface should ask an LLM to unify child results."""
    if child_results_count <= 0:
        return False
    if autonomy_level == "aggressive":
        return True
    if autonomy_level == "balanced":
        return child_results_count >= 3
    return False


def synthesize_child_results(
    child_outputs: Sequence[tuple[str, str | None]],
    *,
    include_uncertainties: bool = False,
    uncertainties: Sequence[str] | None = None,
) -> str:
    """Render child-session outputs into a stable combined report."""
    parts = [
        f"## {label}\n\n{content}"
        for label, content in child_outputs
        if str(label or "").strip() and str(content or "").strip()
    ]
    if not parts:
        return "Task completed but no content was produced."
    combined = "\n\n".join(parts)
    if include_uncertainties:
        cleaned_uncertainties = [
            str(item).strip()
            for item in uncertainties or []
            if str(item).strip()
        ]
        if cleaned_uncertainties:
            combined = f"{combined}\n\n## Uncertainties\n" + "\n".join(cleaned_uncertainties)
    return combined


def merge_token_usage(*usage_maps: Mapping[str, int] | None) -> dict[str, int]:
    """Sum token-usage mappings while tolerating missing or empty inputs."""
    merged: dict[str, int] = {}
    for usage in usage_maps:
        if not usage:
            continue
        for key, value in usage.items():
            merged[str(key)] = merged.get(str(key), 0) + int(value or 0)
    return merged


def merge_child_result_usage(child_results: Iterable[object]) -> dict[str, int]:
    """Roll up token usage from result objects that expose ``token_usage``."""
    return merge_token_usage(
        *[
            getattr(result, "token_usage", {}) or {}
            for result in child_results
        ]
    )


__all__ = [
    "ChildExecutionResolution",
    "ChildSessionPlan",
    "build_mixed_execution_groups",
    "estimate_child_tier",
    "execute_child_execution_policy",
    "merge_child_result_usage",
    "merge_token_usage",
    "mixed_subtask_depends_on_prior",
    "plan_child_session",
    "should_llm_synthesize",
    "synthesize_child_results",
]
