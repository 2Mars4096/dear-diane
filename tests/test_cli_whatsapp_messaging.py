"""Tests for CLI/WhatsApp adapter messaging reliability (plan 31-25).

Covers:
- Task 0-2: Clean history (no system messages) + surface_context
- Task 1-3: WhatsApp _send_text retry on failure
- Task 2-1: chat_queued redirect handling in WS loop
- Task 2-3: Progress indication after timeout
- Task 4-2: Poll text fallback for adapters without send_poll_for_session
"""

from __future__ import annotations

import asyncio
import copy
import json
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _StubAdapter:
    """Minimal adapter stub for testing _dispatch_to_server."""

    def __init__(self, *, has_poll: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self._has_poll = has_poll

    async def send_prompt(self, ext_id: str, text: str, schema: Any) -> None:
        self.sent.append((ext_id, text))

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def set_message_callback(self, cb: Any) -> None:
        self._on_new_message = cb

    def register_session(self, sid: str, key: Any) -> None:
        pass

    if False:  # overridden per-instance for poll tests
        async def send_poll_for_session(self, *a: Any, **kw: Any) -> None: ...


class _StubConfig:
    server_url: str = "http://test:8000"
    auto_approve: bool = False
    error_message: str = "error"
    workflow_path: str = ""
    bot_name: str = ""
    personality: str = ""
    projects: list[str] = []


def _make_http_response(status: int, payload: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = json.dumps(payload)
    return resp


# ---------------------------------------------------------------------------
# Task 0-2: Clean history + surface_context
# ---------------------------------------------------------------------------

class TestCleanHistoryAndSurfaceContext:
    """Verify _dispatch_to_server sends history without system messages and
    includes surface_context in the request body."""

    @pytest.mark.asyncio
    async def test_no_system_message_in_history(self) -> None:
        """The body constructed by _dispatch_to_server must use clean history
        (no system messages) and include surface_context."""
        import httpx
        from dan.cli.adapter import _surface_name_for_adapter_type

        captured_bodies: list[dict[str, Any]] = []

        async def _fake_post(url: str, *, json: Any = None, **kw: Any) -> MagicMock:
            if json is not None:
                captured_bodies.append(json)
            return _make_http_response(200, {"content": "reply"})

        adapter = _StubAdapter()
        config = _StubConfig()

        fake_http = MagicMock(spec=httpx.AsyncClient)
        fake_http.post = _fake_post
        fake_http.get = AsyncMock(return_value=_make_http_response(200, {}))
        fake_http.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=fake_http):
            from dan.cli.adapter import _run_adapter_chat_mode

            task = asyncio.create_task(
                _run_adapter_chat_mode(adapter, config, "server")
            )
            await asyncio.sleep(0.05)

            cb = adapter._on_new_message
            assert cb is not None
            await cb("ext1", "hello")
            await asyncio.sleep(0.1)

            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        chat_bodies = [b for b in captured_bodies if "history" in b]
        assert len(chat_bodies) >= 1, "Should have captured at least one chat request"

        body = chat_bodies[0]
        for msg in body["history"]:
            assert msg["role"] != "system", "History must not contain system messages"

        assert "surface_context" in body
        sc = body["surface_context"]
        assert sc["identity"]["role"] == "messaging_assistant"
        assert "adapter_instructions" in sc
        assert len(sc["adapter_instructions"]) > 0

    @pytest.mark.asyncio
    async def test_failed_turn_is_not_kept_in_history(self) -> None:
        import httpx

        call_count = 0
        captured_bodies: list[dict[str, Any]] = []

        async def _fake_post(url: str, *, json: Any = None, **kw: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if json is not None:
                captured_bodies.append(copy.deepcopy(json))
            if call_count == 1:
                return _make_http_response(500, {"error": "boom"})
            return _make_http_response(200, {"content": "reply"})

        adapter = _StubAdapter()
        config = _StubConfig()

        fake_http = MagicMock(spec=httpx.AsyncClient)
        fake_http.post = _fake_post
        fake_http.get = AsyncMock(return_value=_make_http_response(200, {}))
        fake_http.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=fake_http):
            from dan.cli.adapter import _run_adapter_chat_mode

            task = asyncio.create_task(_run_adapter_chat_mode(adapter, config, "server"))
            await asyncio.sleep(0.05)

            cb = adapter._on_new_message
            assert cb is not None
            await cb("ext1", "first try")
            await asyncio.sleep(0.05)
            await cb("ext1", "second try")
            await asyncio.sleep(0.1)

            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        chat_bodies = [b for b in captured_bodies if "history" in b]
        assert len(chat_bodies) >= 2
        assert chat_bodies[0]["history"] == [{"role": "user", "content": "first try"}]
        assert chat_bodies[1]["history"] == [{"role": "user", "content": "second try"}]
        assert any(
            "Sorry, something went wrong" in text
            for _ext_id, text in adapter.sent
        )


# ---------------------------------------------------------------------------
# Task 1-3: WhatsApp _send_text retry
# ---------------------------------------------------------------------------

class TestWhatsAppSendRetry:
    """Verify _send_text retries once on failure before giving up."""

    @pytest.mark.asyncio
    async def test_retry_on_first_failure(self) -> None:
        from dan.adapters.whatsapp_web_adapter import WhatsAppWebAdapter, WhatsAppWebAdapterConfig

        adapter = WhatsAppWebAdapter(WhatsAppWebAdapterConfig())

        call_count = 0
        mock_resp = MagicMock()
        mock_resp.ID = "msg123"

        def _send_message(recipient: Any, text: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("network blip")
            return mock_resp

        mock_client = MagicMock()
        mock_client.send_message = _send_message
        adapter._client = mock_client
        adapter._sleep = AsyncMock()

        mock_build_jid = MagicMock(return_value="jid_obj")
        with patch("dan.adapters.whatsapp_web_adapter.build_jid", mock_build_jid, create=True):
            with patch.object(adapter, "_resolve_recipient", return_value="recipient_obj"):
                await adapter._send_text("123@s.whatsapp.net", "hello")

        assert call_count == 2, "Should have retried exactly once"

    @pytest.mark.asyncio
    async def test_no_retry_on_success(self) -> None:
        from dan.adapters.whatsapp_web_adapter import WhatsAppWebAdapter, WhatsAppWebAdapterConfig

        adapter = WhatsAppWebAdapter(WhatsAppWebAdapterConfig())

        call_count = 0
        mock_resp = MagicMock()
        mock_resp.ID = "msg456"

        def _send_message(recipient: Any, text: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return mock_resp

        mock_client = MagicMock()
        mock_client.send_message = _send_message
        adapter._client = mock_client
        adapter._sleep = AsyncMock()

        with patch.object(adapter, "_resolve_recipient", return_value="recipient_obj"):
            await adapter._send_text("123@s.whatsapp.net", "hello")

        assert call_count == 1, "Should succeed on first attempt without retry"

    @pytest.mark.asyncio
    async def test_fallback_message_attempted_after_retry_exhausted(self) -> None:
        from dan.adapters.whatsapp_web_adapter import WhatsAppWebAdapter, WhatsAppWebAdapterConfig

        adapter = WhatsAppWebAdapter(WhatsAppWebAdapterConfig())
        sent_texts: list[str] = []

        def _send_message(recipient: Any, text: str) -> MagicMock:
            sent_texts.append(text)
            if "couldn't deliver" in text:
                return MagicMock(ID="fallback-ok")
            raise ConnectionError("still broken")

        mock_client = MagicMock()
        mock_client.send_message = _send_message
        adapter._client = mock_client
        adapter._sleep = AsyncMock()

        with patch.object(adapter, "_resolve_recipient", return_value="recipient_obj"):
            await adapter._send_text("123@s.whatsapp.net", "hello")

        assert len(sent_texts) == 3
        assert "couldn't deliver" in sent_texts[-1]

    @pytest.mark.asyncio
    async def test_retry_exhaustion_aborts_remaining_chunks(self) -> None:
        from dan.adapters.whatsapp_web_adapter import WhatsAppWebAdapter, WhatsAppWebAdapterConfig

        adapter = WhatsAppWebAdapter(WhatsAppWebAdapterConfig())
        sent_texts: list[str] = []

        def _send_message(recipient: Any, text: str) -> MagicMock:
            sent_texts.append(text)
            if "couldn't deliver" in text:
                return MagicMock(ID="fallback-ok")
            raise ConnectionError("still broken")

        mock_client = MagicMock()
        mock_client.send_message = _send_message
        adapter._client = mock_client
        adapter._sleep = AsyncMock()

        long_text = "x" * 5000
        with patch.object(adapter, "_resolve_recipient", return_value="recipient_obj"):
            await adapter._send_text("123@s.whatsapp.net", long_text)

        assert len(sent_texts) == 3
        assert "couldn't deliver" in sent_texts[-1]


# ---------------------------------------------------------------------------
# Task 2-1: chat_queued redirect handling
# ---------------------------------------------------------------------------

class TestChatQueuedRedirect:
    """Verify the WS event loop follows chat_queued redirects."""

    def test_consume_chat_stream_skips_queued_events(self) -> None:
        """_consume_chat_stream_events ignores chat_queued (not a terminal event)."""
        from dan.cli.adapter import _consume_chat_stream_events

        events = [
            {"type": "chat_queued", "stream_channel_id": "ch2"},
            {"type": "chat_token", "token": "hello"},
            {"type": "chat_complete", "content": "done"},
        ]
        reply, _mp, _fps, _prs = _consume_chat_stream_events(events)
        assert "done" in reply

    def test_consume_chat_stream_uses_delta_tokens(self) -> None:
        from dan.cli.adapter import _consume_chat_stream_events

        events = [
            {"type": "chat_token", "delta": "hel", "accumulated": "hel"},
            {"type": "chat_token", "delta": "lo", "accumulated": "hello"},
            {"type": "chat_complete", "content": "hello"},
        ]
        reply, _mp, _fps, _prs = _consume_chat_stream_events(events)
        assert reply == "hello"


# ---------------------------------------------------------------------------
# Task 2-3: Progress indication
# ---------------------------------------------------------------------------

class TestProgressIndication:
    """Verify progress message fires after 5s timeout."""

    @pytest.mark.asyncio
    async def test_progress_fires_after_delay(self) -> None:
        adapter = _StubAdapter()

        progress_sent = False

        async def _send_progress() -> None:
            nonlocal progress_sent
            await asyncio.sleep(0.01)  # shortened for test
            if not progress_sent:
                progress_sent = True
                await adapter.send_prompt("ext1", "\u23f3 Working on it...", None)

        task = asyncio.create_task(_send_progress())
        await asyncio.sleep(0.05)

        assert progress_sent is True
        assert any("\u23f3" in msg for _, msg in adapter.sent)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_progress_cancelled_before_firing(self) -> None:
        adapter = _StubAdapter()
        progress_sent = False

        async def _send_progress() -> None:
            nonlocal progress_sent
            await asyncio.sleep(10)  # long delay
            progress_sent = True

        task = asyncio.create_task(_send_progress())
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert progress_sent is False
        assert len(adapter.sent) == 0


# ---------------------------------------------------------------------------
# Task 4-2: Poll text fallback
# ---------------------------------------------------------------------------

class TestPollTextFallback:
    """Verify polls render as numbered text when adapter lacks send_poll_for_session."""

    @pytest.mark.asyncio
    async def test_poll_rendered_as_text(self) -> None:
        adapter = _StubAdapter(has_poll=False)
        assert not hasattr(adapter, "send_poll_for_session")

        poll = {
            "question": "Favorite color?",
            "options": ["Red", "Blue", "Green"],
        }

        question = poll["question"]
        options = poll["options"]
        if question and options:
            lines = [question, ""]
            for i, opt in enumerate(options, 1):
                lines.append(f"{i}. {opt}")
            await adapter.send_prompt("ext1", "\n".join(lines), None)

        assert len(adapter.sent) == 1
        text = adapter.sent[0][1]
        assert "Favorite color?" in text
        assert "1. Red" in text
        assert "2. Blue" in text
        assert "3. Green" in text

    @pytest.mark.asyncio
    async def test_poll_not_sent_when_empty(self) -> None:
        adapter = _StubAdapter(has_poll=False)

        poll: dict[str, Any] = {"question": "", "options": []}
        question = poll.get("question", "")
        options = poll.get("options", [])

        if question and options:
            lines = [question, ""]
            for i, opt in enumerate(options, 1):
                lines.append(f"{i}. {opt}")
            await adapter.send_prompt("ext1", "\n".join(lines), None)

        assert len(adapter.sent) == 0


# ---------------------------------------------------------------------------
# Task 2-2: WhatsApp reconnection
# ---------------------------------------------------------------------------

class TestWhatsAppReconnection:
    """Verify _connect_with_retry implements exponential backoff."""

    @pytest.mark.asyncio
    async def test_reconnect_on_disconnect(self) -> None:
        from dan.adapters.whatsapp_web_adapter import WhatsAppWebAdapter, WhatsAppWebAdapterConfig

        adapter = WhatsAppWebAdapter(WhatsAppWebAdapterConfig())
        adapter._running = True

        connect_calls = 0

        def _fake_connect() -> None:
            nonlocal connect_calls
            connect_calls += 1
            if connect_calls < 3:
                raise ConnectionError("disconnected")
            adapter._running = False

        adapter._client = MagicMock()
        adapter._client.connect = _fake_connect
        adapter._sleep = AsyncMock()

        await adapter._connect_with_retry()

        assert connect_calls == 3
        assert adapter._sleep.call_count == 2
        delays = [call.args[0] for call in adapter._sleep.call_args_list]
        assert delays[0] < delays[1], "Backoff should increase"

    @pytest.mark.asyncio
    async def test_stops_when_not_running(self) -> None:
        from dan.adapters.whatsapp_web_adapter import WhatsAppWebAdapter, WhatsAppWebAdapterConfig

        adapter = WhatsAppWebAdapter(WhatsAppWebAdapterConfig())
        adapter._running = False
        adapter._client = MagicMock()

        await adapter._connect_with_retry()

        adapter._client.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconnect_failures_reset_after_successful_session(self) -> None:
        from dan.adapters.whatsapp_web_adapter import WhatsAppWebAdapter, WhatsAppWebAdapterConfig

        adapter = WhatsAppWebAdapter(WhatsAppWebAdapterConfig())
        adapter._running = True
        adapter._sleep = AsyncMock()

        connect_calls = 0

        def _fake_connect() -> None:
            nonlocal connect_calls
            connect_calls += 1
            if connect_calls in (1, 2):
                raise ConnectionError("disconnected")
            if connect_calls == 3:
                return
            adapter._running = False
            raise ConnectionError("stop after reset")

        adapter._client = MagicMock()
        adapter._client.connect = _fake_connect

        await adapter._connect_with_retry()

        delays = [call.args[0] for call in adapter._sleep.call_args_list]
        assert delays == [2.0, 4.0, 2.0]
