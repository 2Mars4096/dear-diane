from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.chat_events import ChatCompleteEvent, ChatQueuedEvent, ChatTaskAckEvent
from dan.server.concierge.dispatcher import ConcurrentDispatcher
from dan.server.concierge.models import RouteDecision, RouteMode, SurfaceMessage
from dan.server.concierge.project_store import ProjectStore
from dan.server.concierge.runtime import Concierge
from dan.server.concierge.session import SessionResult
from dan.server.concierge.triage import TriageResult


class _BlockingExecutor:
    def __init__(self) -> None:
        self.started: dict[str, asyncio.Event] = {}
        self.released: dict[str, asyncio.Event] = {}

    async def execute(self, session: Any, session_manager: Any) -> AsyncIterator[ChatCompleteEvent]:
        session_manager.update_state(session.id, "running")
        text = str(getattr(getattr(session, "msg", None), "text", "") or "")
        self.started.setdefault(text, asyncio.Event()).set()
        await self.released.setdefault(text, asyncio.Event()).wait()
        session_manager.update_state(session.id, "completed")
        session_manager.set_result(
            session.id,
            SessionResult(content=f"done {text}"),
        )
        yield ChatCompleteEvent(
            message_id=f"done-{text}",
            content=f"done {text}",
            token_usage={},
            context_window=0,
            graph_revision="",
        )

    async def wait_started(self, text: str) -> None:
        await asyncio.wait_for(self.started.setdefault(text, asyncio.Event()).wait(), timeout=2.0)

    def allow_completion(self, text: str) -> None:
        self.released.setdefault(text, asyncio.Event()).set()


def _build_concierge(tmp_path, executor: _BlockingExecutor) -> Concierge:
    chat_manager = MagicMock()

    async def _fake_send(**kwargs: Any) -> AsyncIterator[ChatCompleteEvent]:
        yield ChatCompleteEvent(
            message_id="m1",
            content="ok",
            token_usage={},
            context_window=0,
            graph_revision="",
        )

    chat_manager.send_message = MagicMock(side_effect=lambda **kwargs: _fake_send(**kwargs))
    chat_manager.send_message_with_tools = MagicMock(side_effect=lambda **kwargs: _fake_send(**kwargs))

    cap_ctx = MagicMock()
    cap_ctx.graph_store = None
    cap_ctx.run_manager = None
    cap_ctx.activity_tracker = None
    cap_ctx.experience_store = None
    cap_ctx.experience_index = None
    cap_ctx.llm_provider = None
    cap_ctx.event_bus = MagicMock()

    concierge = Concierge(
        project_store=ProjectStore(base_dir=tmp_path / "projects"),
        chat_manager=chat_manager,
        capability_context=cap_ctx,
    )

    async def _fake_triage(
        text: str,
        context: Any,
        llm_complete: Any,
        **kwargs: Any,
    ) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="agent",
            confidence=0.95,
            goal=text,
            deliverable=text,
            route=RouteDecision(
                mode=RouteMode.AGENT,
                target="workflow",
                action_hints=["workflow_run"],
                rationale="background-test",
            ),
        )

    concierge._tiered_dispatcher._triage_fn = _fake_triage
    concierge._tiered_dispatcher._executors[1] = executor
    concierge._tiered_dispatcher._context_gatherer.gather = AsyncMock(
        side_effect=lambda msg, triage, concierge_obj, autonomy_resolution=None: concierge_obj._context_from_project_ids(
            str(msg.metadata.get("resolved_project_id") or ""),
            task_id=str(msg.metadata.get("resolved_task_id") or ""),
            surface_id=msg.external_id,
        )
    )
    return concierge


def _msg(text: str) -> SurfaceMessage:
    return SurfaceMessage(surface="cli", external_id="cli-user", text=text)


@pytest.mark.asyncio
async def test_background_dispatch_acknowledges_immediately(tmp_path) -> None:
    executor = _BlockingExecutor()
    concierge = _build_concierge(tmp_path, executor)
    dispatcher = ConcurrentDispatcher(concierge)
    concierge._dispatcher = dispatcher

    events = [event async for event in dispatcher.dispatch(_msg("run the workflow"))]
    ack = next(event for event in events if isinstance(event, ChatTaskAckEvent))

    assert ack.dispatch_mode == "background"
    assert ack.state == "running"
    assert ack.task_id.startswith("task_")

    await executor.wait_started("run the workflow")
    executor.allow_completion("run the workflow")
    await asyncio.wait_for(asyncio.gather(*concierge._background_concierge_tasks.values()), timeout=2.0)
    await concierge._task_attention_monitor.stop()
    await dispatcher.close()


@pytest.mark.asyncio
async def test_background_task_does_not_queue_next_unrelated_turn(tmp_path) -> None:
    executor = _BlockingExecutor()
    concierge = _build_concierge(tmp_path, executor)
    dispatcher = ConcurrentDispatcher(concierge)
    concierge._dispatcher = dispatcher

    first_events = [event async for event in dispatcher.dispatch(_msg("background alpha"))]
    assert any(isinstance(event, ChatTaskAckEvent) for event in first_events)
    await executor.wait_started("background alpha")

    second_events = [event async for event in dispatcher.dispatch(_msg("background beta"))]

    assert any(isinstance(event, ChatTaskAckEvent) for event in second_events)
    assert not any(isinstance(event, ChatQueuedEvent) for event in second_events)

    executor.allow_completion("background alpha")
    executor.allow_completion("background beta")
    await asyncio.wait_for(asyncio.gather(*concierge._background_concierge_tasks.values()), timeout=2.0)
    await concierge._task_attention_monitor.stop()
    await dispatcher.close()


@pytest.mark.asyncio
async def test_background_project_cap_returns_queued_ack(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DAN_MAX_BACKGROUND_TASKS", "1")
    executor = _BlockingExecutor()
    concierge = _build_concierge(tmp_path, executor)
    dispatcher = ConcurrentDispatcher(concierge)
    concierge._dispatcher = dispatcher

    first_events = [event async for event in dispatcher.dispatch(_msg("background one"))]
    first_ack = next(event for event in first_events if isinstance(event, ChatTaskAckEvent))
    assert first_ack.state == "running"
    await executor.wait_started("background one")

    second_events = [event async for event in dispatcher.dispatch(_msg("background two"))]
    second_ack = next(event for event in second_events if isinstance(event, ChatTaskAckEvent))

    assert second_ack.state == "queued"
    assert second_ack.queue_position == 1

    executor.allow_completion("background one")
    await executor.wait_started("background two")
    executor.allow_completion("background two")
    await asyncio.wait_for(asyncio.gather(*concierge._background_concierge_tasks.values()), timeout=2.0)
    await concierge._task_attention_monitor.stop()
    await dispatcher.close()
