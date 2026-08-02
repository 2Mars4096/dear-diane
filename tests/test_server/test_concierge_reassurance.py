"""Tests for concierge long-running turn reassurance behavior."""

from __future__ import annotations

import asyncio
import time

import pytest

from dan.server.chat_manager import ChatCompleteEvent
from dan.server.concierge.models import SurfaceMessage
from dan.server.concierge.progress_ux import ProgressSession
from dan.server.concierge.runtime import Concierge


def _make_msg(text: str = "do something slow") -> SurfaceMessage:
    return SurfaceMessage(
        surface="test",
        external_id="test-surface-1",
        text=text,
    )


def _make_concierge(**overrides) -> Concierge:
    from unittest.mock import MagicMock

    defaults = dict(
        project_store=MagicMock(),
        chat_manager=MagicMock(),
        capability_context=MagicMock(),
    )
    defaults.update(overrides)
    c = Concierge(**defaults)
    c._load_concierge_state = MagicMock(
        return_value=MagicMock(active_goals=[], last_interaction_at=0),
    )
    c._save_concierge_state = MagicMock()
    return c


class TestReassuranceTimer:
    @pytest.mark.asyncio
    async def test_fast_response_no_reassurance(self):
        """If _process_inner yields quickly, no reassurance is emitted."""
        c = _make_concierge()
        c._REASSURANCE_INITIAL_DELAY = 0.5

        fast_event = ChatCompleteEvent(
            message_id="fast",
            content="Here is your answer.",
            graph_revision="",
        )

        async def _fast_inner(msg):
            yield fast_event

        c._process_inner = _fast_inner

        events = []
        async for event in c.process(_make_msg()):
            events.append(event)

        assert len(events) == 1
        assert events[0].content == "Here is your answer."

    @pytest.mark.asyncio
    async def test_slow_response_emits_reassurance(self):
        """If _process_inner takes longer than the delay, a reassurance is yielded first."""
        c = _make_concierge()
        c._REASSURANCE_INITIAL_DELAY = 0.1
        c._REASSURANCE_REPEAT_INTERVAL = 10.0

        real_event = ChatCompleteEvent(
            message_id="slow",
            content="Done after long work.",
            graph_revision="",
        )

        async def _slow_inner(msg):
            await asyncio.sleep(0.3)
            yield real_event

        c._process_inner = _slow_inner

        events = []
        async for event in c.process(_make_msg()):
            events.append(event)

        assert len(events) >= 2
        assert "working on it" in events[0].content.lower()
        assert events[-1].content == "Done after long work."

    @pytest.mark.asyncio
    async def test_multiple_reassurances_on_very_long_turn(self):
        """Very long turns get periodic follow-up reassurance messages."""
        c = _make_concierge()
        c._REASSURANCE_INITIAL_DELAY = 0.05
        c._REASSURANCE_REPEAT_INTERVAL = 0.05

        real_event = ChatCompleteEvent(
            message_id="verylong",
            content="Finally done.",
            graph_revision="",
        )

        async def _very_slow_inner(msg):
            await asyncio.sleep(0.25)
            yield real_event

        c._process_inner = _very_slow_inner

        events = []
        async for event in c.process(_make_msg()):
            events.append(event)

        reassurance_events = [e for e in events if e.content != "Finally done."]
        assert len(reassurance_events) >= 2
        assert events[-1].content == "Finally done."

    @pytest.mark.asyncio
    async def test_disabled_when_delay_zero(self):
        """Setting delay to 0 disables reassurance entirely."""
        c = _make_concierge()
        c._REASSURANCE_INITIAL_DELAY = 0

        real_event = ChatCompleteEvent(
            message_id="nodelay",
            content="Instant.",
            graph_revision="",
        )

        async def _inner(msg):
            yield real_event

        c._process_inner = _inner

        events = []
        async for event in c.process(_make_msg()):
            events.append(event)

        assert len(events) == 1
        assert events[0].content == "Instant."

    @pytest.mark.asyncio
    async def test_reassurance_messages_rotate(self):
        """Subsequent reassurance messages evolve as the wait gets longer."""
        c = _make_concierge()
        c._REASSURANCE_INITIAL_DELAY = 0.03
        c._REASSURANCE_REPEAT_INTERVAL = 0.03

        real_event = ChatCompleteEvent(
            message_id="rotate",
            content="Done.",
            graph_revision="",
        )

        async def _slow_inner(msg):
            await asyncio.sleep(0.15)
            yield real_event

        c._process_inner = _slow_inner

        events = []
        async for event in c.process(_make_msg()):
            events.append(event)

        reassurance_events = [e for e in events if e.content != "Done."]
        if len(reassurance_events) >= 2:
            assert reassurance_events[0].content != reassurance_events[1].content

    @pytest.mark.asyncio
    async def test_inner_exception_propagated(self):
        """Exceptions from _process_inner propagate through the wrapper."""
        c = _make_concierge()
        c._REASSURANCE_INITIAL_DELAY = 5.0

        async def _error_inner(msg):
            raise ValueError("inner boom")
            yield  # noqa: unreachable — needed for async generator syntax

        c._process_inner = _error_inner

        with pytest.raises(ValueError, match="inner boom"):
            async for _ in c.process(_make_msg()):
                pass

    @pytest.mark.asyncio
    async def test_multiple_events_from_inner(self):
        """Multiple real events from inner are all forwarded."""
        c = _make_concierge()
        c._REASSURANCE_INITIAL_DELAY = 5.0

        events_to_yield = [
            ChatCompleteEvent(message_id="1", content="Part 1", graph_revision=""),
            ChatCompleteEvent(message_id="2", content="Part 2", graph_revision=""),
        ]

        async def _multi_inner(msg):
            for e in events_to_yield:
                yield e

        c._process_inner = _multi_inner

        events = []
        async for event in c.process(_make_msg()):
            events.append(event)

        assert len(events) == 2
        assert events[0].content == "Part 1"
        assert events[1].content == "Part 2"

    @pytest.mark.asyncio
    async def test_reassurance_uses_progress_phase_detail_when_available(self):
        """Long-running reassurance should use tracked phase detail, not generic filler."""
        c = _make_concierge()
        c._REASSURANCE_INITIAL_DELAY = 0.05
        c._REASSURANCE_REPEAT_INTERVAL = 10.0

        real_event = ChatCompleteEvent(
            message_id="progress-aware",
            content="Done after searching.",
            graph_revision="",
        )

        async def _slow_inner(msg):
            progress = ProgressSession(surface="telegram", verbosity="compact")
            progress.start_phase("context", "Gathering context")
            progress.update_phase("context", "Searching Dropbox notes")
            c._progress_sessions[msg.external_id] = progress
            await asyncio.sleep(0.2)
            yield real_event

        c._process_inner = _slow_inner

        events = []
        async for event in c.process(_make_msg("look into Panama canal expansion")):
            events.append(event)

        assert len(events) >= 2
        assert "searching dropbox notes" in events[0].content.lower()
        assert events[-1].content == "Done after searching."

    def test_set_progress_phase_keeps_detail_on_fast_phase_switch(self):
        """A new phase should retain its first detail even during rapid transitions."""
        c = _make_concierge()
        progress = ProgressSession(surface="telegram", verbosity="compact")
        c._progress_sessions["test-surface-1"] = progress

        c._set_progress_phase(
            "test-surface-1",
            "context",
            "Gathering context",
            "Searching Dropbox notes",
        )
        c._set_progress_phase(
            "test-surface-1",
            "execution",
            "Working on the request",
            "Checking similar past work",
        )

        phases = progress.get_all_phases()
        assert phases[0].sub_steps == ["Searching Dropbox notes"]
        assert phases[1].sub_steps == ["Checking similar past work"]
