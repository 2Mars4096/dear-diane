"""Tests for the adapter base module — protocol, config, renderer, sessions."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.adapters.base import (
    AdapterConfig,
    AdapterSession,
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
# Fixtures
# ---------------------------------------------------------------------------

class FakeAdapter:
    """Minimal ``MessagingAdapter`` for testing."""

    def __init__(self) -> None:
        self.sent_prompts: list[tuple[str, str]] = []
        self.sent_results: list[tuple[str, dict]] = []
        self._response: dict[str, Any] = {"response": "hello"}

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send_prompt(self, session_id: str, prompt: str, schema: dict | None = None) -> None:
        self.sent_prompts.append((session_id, prompt))

    async def wait_for_response(self, session_id: str, timeout: float) -> dict[str, Any]:
        return dict(self._response)

    async def send_result(self, session_id: str, result: dict[str, Any]) -> None:
        self.sent_results.append((session_id, result))

    def set_message_callback(self, callback: Any) -> None:
        self._on_new_message = callback


@pytest.fixture
def fake_adapter() -> FakeAdapter:
    return FakeAdapter()


@pytest.fixture
def session_store() -> AdapterSessionStore:
    return AdapterSessionStore()


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

class TestMessagingAdapterProtocol:
    def test_fake_adapter_is_protocol_compliant(self, fake_adapter: FakeAdapter) -> None:
        assert isinstance(fake_adapter, MessagingAdapter)

    def test_protocol_requires_all_methods(self) -> None:
        class Incomplete:
            async def start(self) -> None: ...
            async def stop(self) -> None: ...

        assert not isinstance(Incomplete(), MessagingAdapter)


# ---------------------------------------------------------------------------
# AdapterConfig
# ---------------------------------------------------------------------------

class TestAdapterConfig:
    def test_defaults(self) -> None:
        cfg = AdapterConfig()
        assert cfg.timeout == 300.0
        assert cfg.trigger_mode == "always"
        assert cfg.trigger_pattern is None

    def test_custom_values(self) -> None:
        cfg = AdapterConfig(
            workflow_path="/tmp/wf.md",
            trigger_mode="keyword",
            trigger_pattern="/run",
            timeout=60,
        )
        assert cfg.workflow_path == "/tmp/wf.md"
        assert cfg.trigger_mode == "keyword"


# ---------------------------------------------------------------------------
# AdapterSessionStore
# ---------------------------------------------------------------------------

class TestAdapterSessionStore:
    @pytest.mark.asyncio
    async def test_create_and_get(self, session_store: AdapterSessionStore) -> None:
        session = await session_store.create("ext-123")
        assert session.external_id == "ext-123"
        assert session.state == SessionState.IDLE

        fetched = await session_store.get_by_external("ext-123")
        assert fetched is not None
        assert fetched.session_id == session.session_id

    @pytest.mark.asyncio
    async def test_update_state(self, session_store: AdapterSessionStore) -> None:
        session = await session_store.create("ext-456")
        await session_store.update_state(session.session_id, SessionState.RUNNING)
        fetched = await session_store.get(session.session_id)
        assert fetched is not None
        assert fetched.state == SessionState.RUNNING

    @pytest.mark.asyncio
    async def test_remove(self, session_store: AdapterSessionStore) -> None:
        session = await session_store.create("ext-789")
        await session_store.remove(session.session_id)
        assert await session_store.get(session.session_id) is None
        assert await session_store.get_by_external("ext-789") is None

    @pytest.mark.asyncio
    async def test_all_sessions(self, session_store: AdapterSessionStore) -> None:
        await session_store.create("a")
        await session_store.create("b")
        all_s = await session_store.all_sessions()
        assert len(all_s) == 2

    @pytest.mark.asyncio
    async def test_concurrent_creates(self, session_store: AdapterSessionStore) -> None:
        tasks = [session_store.create(f"c-{i}") for i in range(20)]
        sessions = await asyncio.gather(*tasks)
        assert len(sessions) == 20
        ids = {s.session_id for s in sessions}
        assert len(ids) == 20


# ---------------------------------------------------------------------------
# MessagingHumanRenderer
# ---------------------------------------------------------------------------

class TestMessagingHumanRenderer:
    @pytest.mark.asyncio
    async def test_render_sends_prompt_and_returns_response(
        self, fake_adapter: FakeAdapter, session_store: AdapterSessionStore,
    ) -> None:
        renderer = MessagingHumanRenderer(fake_adapter, session_store)
        session = await session_store.create("ext-1")
        renderer.active_session_id = session.session_id

        request = HumanRenderRequest(
            request_id="req-1",
            node_id="human-1",
            prompt="What is your name?",
            render_mode="text",
        )
        response = await renderer.render(request)

        assert response.request_id == "req-1"
        assert response.source == "human"
        assert "response" in response.data
        assert len(fake_adapter.sent_prompts) == 1

    @pytest.mark.asyncio
    async def test_render_no_session_raises(
        self, fake_adapter: FakeAdapter, session_store: AdapterSessionStore,
    ) -> None:
        renderer = MessagingHumanRenderer(fake_adapter, session_store)
        request = HumanRenderRequest(request_id="req-2", prompt="hi")
        with pytest.raises(RuntimeError, match="No active session"):
            await renderer.render(request)

    @pytest.mark.asyncio
    async def test_as_callback(
        self, fake_adapter: FakeAdapter, session_store: AdapterSessionStore,
    ) -> None:
        renderer = MessagingHumanRenderer(fake_adapter, session_store)
        session = await session_store.create("ext-cb")
        renderer.active_session_id = session.session_id

        callback = renderer.as_callback()
        result = await callback({"node_id": "n1", "prompt": "hello"})
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_render_timeout_with_default(
        self, session_store: AdapterSessionStore,
    ) -> None:
        class SlowAdapter(FakeAdapter):
            async def wait_for_response(self, session_id: str, timeout: float) -> dict:
                raise asyncio.TimeoutError()

        adapter = SlowAdapter()
        renderer = MessagingHumanRenderer(adapter, session_store)
        session = await session_store.create("ext-timeout")
        renderer.active_session_id = session.session_id

        request = HumanRenderRequest(
            request_id="req-t",
            prompt="approve?",
            default_action="yes",
            timeout_seconds=0.01,
        )
        response = await renderer.render(request)
        assert response.source == "timeout"
        assert response.data["response"] == "yes"

    @pytest.mark.asyncio
    async def test_render_timeout_no_default_raises(
        self, session_store: AdapterSessionStore,
    ) -> None:
        class SlowAdapter(FakeAdapter):
            async def wait_for_response(self, session_id: str, timeout: float) -> dict:
                raise asyncio.TimeoutError()

        adapter = SlowAdapter()
        renderer = MessagingHumanRenderer(adapter, session_store)
        session = await session_store.create("ext-timeout2")
        renderer.active_session_id = session.session_id

        request = HumanRenderRequest(
            request_id="req-t2",
            prompt="approve?",
            timeout_seconds=0.01,
        )
        with pytest.raises(asyncio.TimeoutError):
            await renderer.render(request)


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------

class TestFormatPromptForMessaging:
    def test_text_mode(self) -> None:
        req = HumanRenderRequest(prompt="Hello world", render_mode="text")
        text = format_prompt_for_messaging(req)
        assert "Hello world" in text

    def test_approval_mode(self) -> None:
        req = HumanRenderRequest(prompt="Deploy?", render_mode="approval")
        text = format_prompt_for_messaging(req)
        assert "YES" in text and "NO" in text

    def test_selection_mode(self) -> None:
        req = HumanRenderRequest(
            prompt="Pick one:", render_mode="selection", options=["A", "B", "C"],
        )
        text = format_prompt_for_messaging(req)
        assert "1. A" in text
        assert "3. C" in text

    def test_form_mode(self) -> None:
        req = HumanRenderRequest(
            prompt="Fill form:", render_mode="form",
            output_schema={"properties": {"name": {"description": "Your name"}, "age": {"description": "Your age"}}},
        )
        text = format_prompt_for_messaging(req)
        assert "Your name" in text
        assert "Your age" in text


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

class TestParseResponseText:
    def test_approval_yes(self) -> None:
        req = HumanRenderRequest(render_mode="approval")
        data = parse_response_text("YES", req)
        assert data["approved"] is True

    def test_approval_no(self) -> None:
        req = HumanRenderRequest(render_mode="approval")
        data = parse_response_text("no", req)
        assert data["approved"] is False

    def test_selection_by_number(self) -> None:
        req = HumanRenderRequest(render_mode="selection", options=["A", "B", "C"])
        data = parse_response_text("2", req)
        assert data["selected"] == "B"
        assert data["index"] == 1

    def test_selection_by_name(self) -> None:
        req = HumanRenderRequest(render_mode="selection", options=["Alpha", "Beta"])
        data = parse_response_text("beta", req)
        assert data["selected"] == "Beta"

    def test_form_parsing(self) -> None:
        req = HumanRenderRequest(
            render_mode="form",
            output_schema={"properties": {"name": {}, "age": {}}},
        )
        data = parse_response_text("Alice\n30", req)
        assert data["name"] == "Alice"
        assert data["age"] == "30"

    def test_text_mode_passthrough(self) -> None:
        req = HumanRenderRequest(render_mode="text")
        data = parse_response_text("free text here", req)
        assert data["response"] == "free text here"


# ---------------------------------------------------------------------------
# Trigger matching
# ---------------------------------------------------------------------------

class TestShouldTrigger:
    def test_always_mode(self) -> None:
        cfg = AdapterConfig(trigger_mode="always")
        assert should_trigger("anything", cfg) is True

    def test_keyword_mode_match(self) -> None:
        cfg = AdapterConfig(trigger_mode="keyword", trigger_pattern="/run")
        assert should_trigger("/run my workflow", cfg) is True

    def test_keyword_mode_no_match(self) -> None:
        cfg = AdapterConfig(trigger_mode="keyword", trigger_pattern="/run")
        assert should_trigger("hello", cfg) is False

    def test_pattern_mode(self) -> None:
        cfg = AdapterConfig(trigger_mode="pattern", trigger_pattern=r"run\s+\w+")
        assert should_trigger("please run workflow", cfg) is True
        assert should_trigger("hello", cfg) is False
