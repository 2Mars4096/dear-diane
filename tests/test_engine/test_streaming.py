"""Tests for 6-6: LLM streaming events and intermediate_text coalescing."""

from __future__ import annotations

import pytest

from dan.engine.events import EventType
from dan.server.run_manager import RunManager, RunRecord
from dan.engine.executor import EngineConfig


class TestEventCoalescing:
    @pytest.mark.asyncio
    async def test_intermediate_text_coalesced_in_buffer(self):
        rm = RunManager(engine_config=EngineConfig(checkpoint_enabled=False))

        from dan.engine.events import EngineEvent

        record = RunRecord(run_id="test-stream", graph_id="g1")
        rm._runs["test-stream"] = record

        await rm._event_callback(EngineEvent(
            event_type=EventType.INTERMEDIATE_TEXT,
            run_id="test-stream",
            node_id="node1",
            node_type="llm_operator",
            data={"delta": "Hello", "text": "Hello", "attempt": 0},
        ))

        assert len(record.events) == 1
        assert record.events[0]["data"]["text"] == "Hello"

        await rm._event_callback(EngineEvent(
            event_type=EventType.INTERMEDIATE_TEXT,
            run_id="test-stream",
            node_id="node1",
            node_type="llm_operator",
            data={"delta": " world", "text": "Hello world", "attempt": 0},
        ))

        coalesced = [e for e in record.events if e["event_type"] == "intermediate_text" and e["node_id"] == "node1"]
        assert len(coalesced) == 1
        assert coalesced[0]["data"]["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_done_event_replaces_partial(self):
        """The done event replaces the in-progress partial; buffer stays compact."""
        rm = RunManager(engine_config=EngineConfig(checkpoint_enabled=False))
        from dan.engine.events import EngineEvent

        record = RunRecord(run_id="test-done", graph_id="g1")
        rm._runs["test-done"] = record

        await rm._event_callback(EngineEvent(
            event_type=EventType.INTERMEDIATE_TEXT,
            run_id="test-done",
            node_id="node1",
            data={"text": "partial", "attempt": 0},
        ))

        await rm._event_callback(EngineEvent(
            event_type=EventType.INTERMEDIATE_TEXT,
            run_id="test-done",
            node_id="node1",
            data={"text": "final", "attempt": 0, "done": True},
        ))

        text_events = [e for e in record.events if e["event_type"] == "intermediate_text"]
        assert len(text_events) == 1
        assert text_events[0]["data"]["text"] == "final"
        assert text_events[0]["data"]["done"] is True

    @pytest.mark.asyncio
    async def test_new_done_event_after_done_appends(self):
        """A second stream (new node/attempt) appends after a done event."""
        rm = RunManager(engine_config=EngineConfig(checkpoint_enabled=False))
        from dan.engine.events import EngineEvent

        record = RunRecord(run_id="test-multi", graph_id="g1")
        rm._runs["test-multi"] = record

        await rm._event_callback(EngineEvent(
            event_type=EventType.INTERMEDIATE_TEXT,
            run_id="test-multi",
            node_id="node1",
            data={"text": "done1", "attempt": 0, "done": True},
        ))

        await rm._event_callback(EngineEvent(
            event_type=EventType.INTERMEDIATE_TEXT,
            run_id="test-multi",
            node_id="node2",
            data={"text": "hello", "attempt": 0},
        ))

        text_events = [e for e in record.events if e["event_type"] == "intermediate_text"]
        assert len(text_events) == 2

    @pytest.mark.asyncio
    async def test_max_event_buffer_increased(self):
        rm = RunManager(engine_config=EngineConfig(checkpoint_enabled=False))
        assert rm._max_event_buffer == 10000


class TestNewEventTypes:
    def test_iteration_event_types_exist(self):
        assert EventType.ITERATION_STARTED == "iteration_started"
        assert EventType.ITERATION_COMPLETED == "iteration_completed"
        assert EventType.HUMAN_INPUT_NEEDED == "human_input_needed"

    def test_all_event_types_are_strings(self):
        for et in EventType:
            assert isinstance(et.value, str)
