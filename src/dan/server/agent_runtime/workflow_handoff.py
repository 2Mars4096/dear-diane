"""Server-side helpers for workflow-generation handoff orchestration."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from dan.server.chat.events import ChatStreamEvent

_WORKFLOW_GEN_ALLOWED_MODES = frozenset(
    m.strip()
    for m in os.environ.get(
        "DAN_WORKFLOW_GEN_FAST_PATH_MODES", "agent,build,mutate"
    ).split(",")
)


@dataclass(frozen=True)
class WorkflowGenerationAttemptOutcome:
    """Final outcome for one workflow-generation handoff attempt."""

    message_id: str
    graph_result: dict[str, Any] | None
    runtime_events: list[ChatStreamEvent]


def should_use_workflow_generation_fast_path(
    *,
    is_empty_graph: bool,
    allow_mutation_tool: bool,
    mode: str,
    codegen_enabled: bool,
) -> bool:
    """True when the empty-graph workflow-generation fast path should run."""

    return (
        is_empty_graph
        and codegen_enabled
        and allow_mutation_tool
        and mode in _WORKFLOW_GEN_ALLOWED_MODES
    )


async def iter_workflow_generation_attempt(
    *,
    generate_workflow: Awaitable[tuple[dict[str, Any] | None, list[ChatStreamEvent]]],
    progress_event_factory: Callable[[], ChatStreamEvent],
    message_id: str,
    progress_timeout_seconds: float = 30.0,
) -> AsyncIterator[ChatStreamEvent | WorkflowGenerationAttemptOutcome]:
    """Stream keepalive progress events while workflow generation runs."""

    workflow_task = asyncio.create_task(generate_workflow)
    while not workflow_task.done():
        try:
            await asyncio.wait_for(
                asyncio.shield(workflow_task),
                timeout=progress_timeout_seconds,
            )
            break
        except asyncio.TimeoutError:
            yield progress_event_factory()

    graph_result, runtime_events = await workflow_task
    for event in runtime_events:
        yield event
    yield WorkflowGenerationAttemptOutcome(
        message_id=message_id,
        graph_result=graph_result,
        runtime_events=runtime_events,
    )


__all__ = [
    "WorkflowGenerationAttemptOutcome",
    "iter_workflow_generation_attempt",
    "should_use_workflow_generation_fast_path",
]
