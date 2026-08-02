"""Integration tests for the messaging adapter framework.

Verifies the full flow:
adapter receives message → starts workflow → HumanNode fires →
adapter sends prompt → adapter receives response → workflow completes
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from dan.adapters.base import (
    AdapterConfig,
    AdapterSessionStore,
    MessagingAdapter,
    MessagingHumanRenderer,
    SessionState,
    format_prompt_for_messaging,
    parse_response_text,
    should_trigger,
)
from dan.engine.executor import HumanRenderRequest, HumanRenderResponse


# ---------------------------------------------------------------------------
# Minimal fake adapter
# ---------------------------------------------------------------------------


class FakeMessagingAdapter:
    """Adapter that records interactions and returns canned responses."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.prompts_sent: list[tuple[str, str]] = []
        self.results_sent: list[tuple[str, dict]] = []
        self._response: dict[str, Any] = {"response": "user reply"}

    def set_response(self, resp: dict[str, Any]) -> None:
        self._response = resp

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_prompt(
        self, session_id: str, prompt: str, schema: dict | None = None,
    ) -> None:
        self.prompts_sent.append((session_id, prompt))

    async def wait_for_response(
        self, session_id: str, timeout: float,
    ) -> dict[str, Any]:
        return dict(self._response)

    async def send_result(self, session_id: str, result: dict[str, Any]) -> None:
        self.results_sent.append((session_id, result))

    def set_message_callback(self, callback: Any) -> None:
        self._on_new_message = callback


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_fake_adapter_is_protocol_compliant(self):
        adapter = FakeMessagingAdapter()
        assert isinstance(adapter, MessagingAdapter)


# ---------------------------------------------------------------------------
# Full render cycle
# ---------------------------------------------------------------------------


class TestRendererFullCycle:
    @pytest.mark.asyncio
    async def test_render_cycle(self):
        """Simulate: create session → render HumanNode → receive response."""
        adapter = FakeMessagingAdapter()
        store = AdapterSessionStore()

        session = await store.create("ext-123")
        assert session.state == SessionState.IDLE

        renderer = MessagingHumanRenderer(adapter, store)
        renderer.active_session_id = session.session_id

        request = HumanRenderRequest(
            request_id="req-1",
            node_id="human-node",
            prompt="Please approve the draft",
            render_mode="approval",
        )

        adapter.set_response({"approved": True, "response": "yes"})
        response = await renderer.render(request)

        assert isinstance(response, HumanRenderResponse)
        assert response.request_id == "req-1"
        assert response.data.get("approved") is True
        assert response.source == "human"

        assert len(adapter.prompts_sent) == 1
        _, prompt_text = adapter.prompts_sent[0]
        assert "approve" in prompt_text.lower() or "YES" in prompt_text

    @pytest.mark.asyncio
    async def test_text_render_mode(self):
        adapter = FakeMessagingAdapter()
        store = AdapterSessionStore()
        session = await store.create("ext-text")

        renderer = MessagingHumanRenderer(adapter, store)
        renderer.active_session_id = session.session_id

        request = HumanRenderRequest(
            request_id="req-2",
            node_id="text-node",
            prompt="What is your name?",
        )
        adapter.set_response({"response": "Alice"})
        response = await renderer.render(request)

        assert response.data["response"] == "Alice"

    @pytest.mark.asyncio
    async def test_selection_render_mode(self):
        adapter = FakeMessagingAdapter()
        store = AdapterSessionStore()
        session = await store.create("ext-sel")

        renderer = MessagingHumanRenderer(adapter, store)
        renderer.active_session_id = session.session_id

        request = HumanRenderRequest(
            request_id="req-3",
            node_id="select-node",
            prompt="Pick a color",
            render_mode="selection",
            options=["Red", "Blue", "Green"],
        )
        adapter.set_response({"selected": "Blue", "index": 1, "response": "2"})
        response = await renderer.render(request)

        assert response.data.get("selected") == "Blue"
        assert response.data.get("index") == 1

    @pytest.mark.asyncio
    async def test_session_state_transitions(self):
        """Verify session state goes through IDLE → AWAITING_HUMAN → RUNNING."""
        adapter = FakeMessagingAdapter()
        store = AdapterSessionStore()
        session = await store.create("ext-state")
        assert session.state == SessionState.IDLE

        renderer = MessagingHumanRenderer(adapter, store)
        renderer.active_session_id = session.session_id

        request = HumanRenderRequest(
            request_id="req-state",
            node_id="n1",
            prompt="test",
        )

        response = await renderer.render(request)

        refreshed = await store.get(session.session_id)
        assert refreshed is not None
        assert refreshed.state == SessionState.RUNNING

    @pytest.mark.asyncio
    async def test_no_active_session_raises(self):
        adapter = FakeMessagingAdapter()
        store = AdapterSessionStore()
        renderer = MessagingHumanRenderer(adapter, store)

        request = HumanRenderRequest(
            request_id="req-none",
            node_id="n1",
            prompt="test",
        )
        with pytest.raises(RuntimeError, match="No active session"):
            await renderer.render(request)


# ---------------------------------------------------------------------------
# Prompt formatting + response parsing
# ---------------------------------------------------------------------------


class TestPromptFormattingParsing:
    def test_approval_prompt_format(self):
        req = HumanRenderRequest(
            request_id="r1", node_id="n", prompt="Approve?", render_mode="approval",
        )
        text = format_prompt_for_messaging(req)
        assert "YES" in text or "NO" in text

    def test_approval_parse_yes(self):
        req = HumanRenderRequest(
            request_id="r1", node_id="n", prompt="Approve?", render_mode="approval",
        )
        result = parse_response_text("yes", req)
        assert result["approved"] is True

    def test_approval_parse_no(self):
        req = HumanRenderRequest(
            request_id="r1", node_id="n", prompt="Approve?", render_mode="approval",
        )
        result = parse_response_text("no", req)
        assert result["approved"] is False

    def test_selection_parse_by_number(self):
        req = HumanRenderRequest(
            request_id="r2", node_id="n", prompt="Pick",
            render_mode="selection", options=["A", "B", "C"],
        )
        result = parse_response_text("2", req)
        assert result["selected"] == "B"

    def test_form_parse(self):
        req = HumanRenderRequest(
            request_id="r3", node_id="n", prompt="Fill",
            render_mode="form",
            output_schema={"properties": {"name": {}, "age": {}}},
        )
        result = parse_response_text("Alice\n30", req)
        assert result["name"] == "Alice"
        assert result["age"] == "30"


# ---------------------------------------------------------------------------
# Trigger matching
# ---------------------------------------------------------------------------


class TestTriggerMatching:
    def test_always_mode(self):
        cfg = AdapterConfig(trigger_mode="always")
        assert should_trigger("anything", cfg) is True

    def test_keyword_mode(self):
        cfg = AdapterConfig(trigger_mode="keyword", trigger_pattern="run")
        assert should_trigger("run my workflow", cfg) is True
        assert should_trigger("hello", cfg) is False

    def test_pattern_mode(self):
        cfg = AdapterConfig(trigger_mode="pattern", trigger_pattern=r"\bstart\b")
        assert should_trigger("please start now", cfg) is True
        assert should_trigger("begin now", cfg) is False


# ---------------------------------------------------------------------------
# Session store concurrency
# ---------------------------------------------------------------------------


class TestSessionStoreConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_session_creation(self):
        store = AdapterSessionStore()
        sessions = await asyncio.gather(
            *[store.create(f"ext-{i}") for i in range(10)]
        )
        assert len(sessions) == 10
        all_ids = {s.session_id for s in sessions}
        assert len(all_ids) == 10

    @pytest.mark.asyncio
    async def test_get_by_external_id(self):
        store = AdapterSessionStore()
        s = await store.create("ext-abc")
        found = await store.get_by_external("ext-abc")
        assert found is not None
        assert found.session_id == s.session_id

    @pytest.mark.asyncio
    async def test_remove_session(self):
        store = AdapterSessionStore()
        s = await store.create("ext-del")
        await store.remove(s.session_id)
        assert await store.get(s.session_id) is None
        assert await store.get_by_external("ext-del") is None

    @pytest.mark.asyncio
    async def test_as_callback(self):
        adapter = FakeMessagingAdapter()
        store = AdapterSessionStore()
        session = await store.create("ext-cb")

        renderer = MessagingHumanRenderer(adapter, store)
        renderer.active_session_id = session.session_id

        callback = renderer.as_callback()
        result = await callback({"prompt": "hello", "node_id": "n1"})
        assert "response" in result
