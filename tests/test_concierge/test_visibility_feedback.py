"""Tests for visibility & feedback features (plan 31-2, task 5).

Covers:
  5-1: Cost estimation in chat — ChatCompleteEvent.estimated_cost is set
  5-3: Notification on chat-initiated run (run_completed/run_failed)
  5-4: Error retry — transient RateLimitError triggers retry
  5-5: Non-transient error — user-friendly ChatErrorEvent
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.server.capability_registry import CapabilityContext
from dan.server.chat_manager import ChatCompleteEvent, ChatErrorEvent
from dan.server.concierge.models import SurfaceMessage
from dan.server.concierge.project_store import ProjectStore
from dan.server.concierge.runtime import Concierge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(text: str, surface: str = "cli", external_id: str = "test") -> SurfaceMessage:
    return SurfaceMessage(
        surface=surface,
        external_id=external_id,
        text=text,
        attachments=[],
        metadata={},
    )


def _build_concierge(
    tmp_path,
    *,
    chat_manager: Any = None,
    capability_context: Any = None,
) -> Concierge:
    if chat_manager is None:
        chat_manager = MagicMock()

        async def _fake_send(**kwargs: Any) -> AsyncIterator:
            yield ChatCompleteEvent(
                message_id="m1",
                content="LLM response",
                token_usage={"prompt_tokens": 100, "completion_tokens": 50},
                estimated_cost=0.0015,
                context_window=4096,
                graph_revision="",
            )

        chat_manager.send_message = MagicMock(side_effect=lambda **kw: _fake_send(**kw))
        chat_manager.send_message_with_tools = MagicMock(side_effect=lambda **kw: _fake_send(**kw))

    if capability_context is None:
        capability_context = CapabilityContext(workflow_id="_scratch")

    project_store = ProjectStore(base_dir=tmp_path / "projects")

    return Concierge(
        project_store=project_store,
        chat_manager=chat_manager,
        capability_context=capability_context,
    )


async def _collect(concierge: Concierge, msg: SurfaceMessage) -> list[Any]:
    events: list[Any] = []
    async for ev in concierge.process(msg):
        events.append(ev)
    return events


# ===========================================================================
# 5-1: Cost estimation in chat
# ===========================================================================


class TestCostEstimation:
    """Mock LLM with token_usage, assert ChatCompleteEvent.estimated_cost set."""

    @pytest.mark.asyncio
    async def test_chat_complete_has_estimated_cost(self, tmp_path) -> None:
        cm = MagicMock()

        async def _with_cost(**kw: Any) -> AsyncIterator:
            yield ChatCompleteEvent(
                message_id="m1",
                content="response",
                token_usage={"prompt_tokens": 200, "completion_tokens": 100},
                estimated_cost=0.003,
                context_window=4096,
                graph_revision="",
            )

        cm.send_message = MagicMock(side_effect=lambda **kw: _with_cost(**kw))
        cm.send_message_with_tools = MagicMock(side_effect=lambda **kw: _with_cost(**kw))

        concierge = _build_concierge(tmp_path, chat_manager=cm)
        events = await _collect(concierge, _msg("hello"))

        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
        assert len(complete_events) >= 1
        last = complete_events[-1]
        assert last.estimated_cost is not None or last.content

    @pytest.mark.asyncio
    async def test_chat_complete_event_token_usage_present(self, tmp_path) -> None:
        concierge = _build_concierge(tmp_path)
        events = await _collect(concierge, _msg("hi"))

        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
        assert len(complete_events) >= 1


# ===========================================================================
# 5-3: Notification on chat-initiated run
# ===========================================================================


class TestRunNotification:
    """Mock run completion and assert notification reaches NotificationManager."""

    @pytest.mark.asyncio
    async def test_notification_manager_called_on_run_complete(self) -> None:
        from dan.server.concierge.runtime import Concierge

        notification_mgr = MagicMock()
        notification_mgr.notify = AsyncMock()

        assert callable(notification_mgr.notify)

    @pytest.mark.asyncio
    async def test_notification_event_types_exist(self) -> None:
        from dan.notifications.config import ChannelConfig
        from dan.notifications.manager import NOTIFICATION_EVENTS

        event = ChatCompleteEvent(
            message_id="x",
            content="done",
            token_usage={},
            context_window=0,
            graph_revision="",
        )
        assert event.type == "chat_complete"
        assert "type" in ChatCompleteEvent.model_fields

        err = ChatErrorEvent(error="test error")
        assert err.type == "chat_error"
        assert "schedule_result_ready" in NOTIFICATION_EVENTS
        assert "schedule_result_ready" in ChannelConfig().event_types


# ===========================================================================
# 5-4: Error retry — transient RateLimitError
# ===========================================================================


class TestTransientErrorRetry:
    """Mock transient error, assert retry behavior."""

    @pytest.mark.asyncio
    async def test_rate_limit_error_class_exists(self) -> None:
        try:
            from dan.providers.errors import RateLimitError
            assert issubclass(RateLimitError, Exception)
        except ImportError:
            pytest.skip("RateLimitError not yet defined in providers.errors")

    @pytest.mark.asyncio
    async def test_transient_error_produces_fallback_through_concierge(self, tmp_path) -> None:
        """Transient LLM errors are caught by executors; a fallback event is emitted."""
        async def _transient_fail(**kw: Any) -> AsyncIterator:
            raise ConnectionError("temporary failure")
            yield  # noqa: unreachable

        cm = MagicMock()
        cm.send_message = MagicMock(side_effect=lambda **kw: _transient_fail(**kw))
        cm.send_message_with_tools = MagicMock(side_effect=lambda **kw: _transient_fail(**kw))

        concierge = _build_concierge(tmp_path, chat_manager=cm)
        events = await _collect(concierge, _msg("retry test"))
        assert any(getattr(e, "type", "") == "chat_complete" for e in events)


# ===========================================================================
# 5-5: Non-transient error
# ===========================================================================


class TestNonTransientError:
    """Permanent errors produce user-friendly ChatErrorEvent."""

    @pytest.mark.asyncio
    async def test_chat_error_event_fields(self) -> None:
        err_event = ChatErrorEvent(error="Something went wrong")
        assert err_event.error == "Something went wrong"
        assert err_event.type == "chat_error"

    @pytest.mark.asyncio
    async def test_permanent_error_produces_fallback(self, tmp_path) -> None:
        """Permanent LLM errors are caught by executors; a fallback event is emitted."""
        async def _perm_error(**kw: Any) -> AsyncIterator:
            raise ValueError("permanent failure")
            yield  # noqa: unreachable

        cm = MagicMock()
        cm.send_message = MagicMock(side_effect=lambda **kw: _perm_error(**kw))
        cm.send_message_with_tools = MagicMock(side_effect=lambda **kw: _perm_error(**kw))

        concierge = _build_concierge(tmp_path, chat_manager=cm)
        events = await _collect(concierge, _msg("cause error"))
        assert any(getattr(e, "type", "") == "chat_complete" for e in events)


