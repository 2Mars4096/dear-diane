"""Tests for chat-mode event handling in ``dan.cli.adapter``."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from dan.cli.adapter import (
    _consume_chat_stream_events,
    _run_adapter_chat_mode,
    _strip_dan_prefix,
    _surface_name_for_adapter_type,
    _translate_slash_command,
)


def test_chat_complete_content_used_when_no_tokens() -> None:
    reply, mutation_plan, _files, _polls = _consume_chat_stream_events([
        {"type": "chat_complete", "content": "Hello from DAN"},
    ])

    assert reply == "Hello from DAN"
    assert mutation_plan is None


def test_streamed_tokens_preferred_when_present() -> None:
    reply, mutation_plan, _files, _polls = _consume_chat_stream_events([
        {"type": "chat_token", "token": "Hello"},
        {"type": "chat_token", "token": " world"},
        {"type": "chat_complete", "content": "Hello world"},
    ])

    assert reply == "Hello world"
    assert mutation_plan is None


def test_chat_error_returns_visible_reply() -> None:
    reply, mutation_plan, _files, _polls = _consume_chat_stream_events([
        {"type": "chat_tool_call_start", "tool_name": "search_workflow_history"},
        {"type": "chat_error", "error": "Tool execution failed"},
    ])

    assert "internal error" in reply.lower()
    assert "search_workflow_history" in reply
    assert "Tool execution failed" in reply
    assert mutation_plan is None


def test_strip_dan_prefix_handles_shared_prefix_forms() -> None:
    assert _strip_dan_prefix("[DAN] hello") == "hello"
    assert _strip_dan_prefix("[DAN - Project] hello") == "hello"
    assert _strip_dan_prefix("[DAN - Project / Task] hello") == "hello"
    assert _strip_dan_prefix("hello") == "hello"
    assert _strip_dan_prefix("[DAN] [DAN] hello") == "hello"
    assert _strip_dan_prefix("[DAN - Proj] [DAN] hello") == "hello"
    assert _strip_dan_prefix("[DAN]text without space") == "[DAN]text without space"
    assert _strip_dan_prefix("[DAN - incomplete text") == "incomplete text"


class _FakeLoop:
    def add_signal_handler(self, *_args, **_kwargs) -> None:
        return None


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


class _BrokenHttpClient:
    def __init__(self, *args, **kwargs) -> None:
        self.base_url = kwargs.get("base_url", "")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def aclose(self) -> None:
        pass

    async def get(self, _path: str):
        return _FakeResponse(status_code=404)

    async def post(self, path: str, json: dict | None = None):
        if path == "/api/chat/message":
            raise httpx.ConnectError("All connection attempts failed")
        return _FakeResponse(status_code=200, payload={})


class _StreamingHttpClient:
    def __init__(self, *args, **kwargs) -> None:
        self.base_url = kwargs.get("base_url", "")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def aclose(self) -> None:
        pass

    async def get(self, _path: str):
        return _FakeResponse(status_code=404)

    async def post(self, path: str, json: dict | None = None):
        if path == "/api/chat/message":
            return _FakeResponse(status_code=200, payload={"stream_channel_id": "chan-1"})
        return _FakeResponse(status_code=200, payload={})


class _DirectReplyHttpClient:
    last_chat_body: dict | None = None

    def __init__(self, *args, **kwargs) -> None:
        self.base_url = kwargs.get("base_url", "")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def aclose(self) -> None:
        pass

    async def get(self, _path: str):
        return _FakeResponse(status_code=404)

    async def post(self, path: str, json: dict | None = None):
        if path == "/api/chat/message":
            _DirectReplyHttpClient.last_chat_body = json or {}
            return _FakeResponse(status_code=200, payload={"content": "forwarded"})
        return _FakeResponse(status_code=200, payload={})


class _FakeAdapter:
    def __init__(self) -> None:
        self.callback = None
        self.prompts: list[tuple[str, str]] = []
        self.files: list[tuple[str, str]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def set_message_callback(self, callback) -> None:
        self.callback = callback

    async def send_prompt(self, session_id: str, prompt: str, _schema) -> None:
        self.prompts.append((session_id, prompt))

    async def send_file(self, session_id: str, path: str) -> dict:
        self.files.append((session_id, path))
        return {"ok": True, "size_mb": "1.59"}


@pytest.mark.asyncio
async def test_chat_mode_reports_server_unavailable_explicitly() -> None:
    adapter = _FakeAdapter()
    config = SimpleNamespace(
        server_url="http://127.0.0.1:8000",
        error_message="Something went wrong. Please try again.",
        auto_approve=False,
    )

    with patch("dan.cli.adapter.asyncio.get_running_loop", return_value=_FakeLoop()):
        with patch("httpx.AsyncClient", _BrokenHttpClient):
            task = asyncio.create_task(_run_adapter_chat_mode(adapter, config, "whatsapp-web"))
            try:
                while adapter.callback is None:
                    await asyncio.sleep(0)
                await adapter.callback("u1", "hello")
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if adapter.prompts:
                        break
                assert adapter.prompts
                assert "server" in adapter.prompts[-1][1].lower()
                assert "http://127.0.0.1:8000" in adapter.prompts[-1][1]
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task


@pytest.mark.asyncio
async def test_chat_mode_reports_stream_failure_explicitly() -> None:
    adapter = _FakeAdapter()
    config = SimpleNamespace(
        server_url="http://127.0.0.1:8000",
        error_message="Something went wrong. Please try again.",
        auto_approve=False,
    )

    with patch("dan.cli.adapter.asyncio.get_running_loop", return_value=_FakeLoop()):
        with patch("httpx.AsyncClient", _StreamingHttpClient):
            with patch("websockets.connect", side_effect=OSError("ws down")):
                task = asyncio.create_task(_run_adapter_chat_mode(adapter, config, "whatsapp-web"))
                try:
                    while adapter.callback is None:
                        await asyncio.sleep(0)
                    await adapter.callback("u1", "hello")
                    for _ in range(50):
                        await asyncio.sleep(0.01)
                        if adapter.prompts:
                            break
                    assert adapter.prompts
                    assert "reply stream" in adapter.prompts[-1][1].lower()
                finally:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task


# ── _translate_slash_command tests ──────────────────────────────────


def test_translate_slash_find() -> None:
    assert _translate_slash_command("/find late payment") == "Find the file matching 'late payment' on my computer"


def test_translate_slash_send() -> None:
    assert _translate_slash_command("/send ~/report.pdf") == "Send me the file at ~/report.pdf"


def test_translate_slash_status() -> None:
    # /status is a registry chat command — forwarded as raw slash text, not NL-translated
    assert _translate_slash_command("/status") is None


def test_translate_slash_cancel() -> None:
    # /cancel is a registry chat command — forwarded as raw slash text
    assert _translate_slash_command("/cancel") is None


def test_translate_slash_show() -> None:
    # /show is a registry repl/chat command — forwarded as raw slash text
    assert _translate_slash_command("/show") is None


def test_translate_unknown_command_returns_none() -> None:
    assert _translate_slash_command("/foobar xyz") is None


def test_translate_find_empty_query_returns_none() -> None:
    assert _translate_slash_command("/find ") is None


# ── _surface_name_for_adapter_type tests (migrated from intent tests) ──


def test_surface_name_for_adapter_type_preserves_known_surface() -> None:
    assert _surface_name_for_adapter_type("whatsapp-web") == "whatsapp-web"


def test_surface_name_for_adapter_type_unknown_becomes_server() -> None:
    assert _surface_name_for_adapter_type("smoke-signal") == "server"


# ── _consume_chat_stream_events with mutation (migrated from intent tests) ──


def test_consume_chat_stream_events_captures_poll_requests() -> None:
    reply, _mutation, _files, polls = _consume_chat_stream_events([
        {"type": "chat_poll_request", "question": "Pick one", "options": ["A", "B"]},
        {"type": "chat_complete", "content": "Here's the poll."},
    ])
    assert reply == "Here's the poll."
    assert len(polls) == 1
    assert polls[0]["question"] == "Pick one"
    assert polls[0]["options"] == ["A", "B"]


def test_consume_chat_stream_events_keeps_final_reply_with_mutation() -> None:
    reply, mutation_plan, _files, _polls = _consume_chat_stream_events([
        {"type": "chat_mutation", "mutation_plan": {"description": "Create workflow", "operations": [1, 2]}},
        {"type": "chat_complete", "content": "I found the paper and can summarize it after apply."},
    ])
    assert "I found the paper" in reply
    assert mutation_plan is not None


# ── Slash command dispatch integration ─────────────────────────────


@pytest.mark.asyncio
async def test_chat_mode_slash_find_dispatches_to_server() -> None:
    """Verify /find X gets translated and dispatched to server (not handled locally)."""
    adapter = _FakeAdapter()
    config = SimpleNamespace(
        server_url="http://127.0.0.1:8000",
        error_message="Something went wrong. Please try again.",
        auto_approve=False,
    )

    with patch("dan.cli.adapter.asyncio.get_running_loop", return_value=_FakeLoop()):
        with patch("httpx.AsyncClient", _BrokenHttpClient):
            task = asyncio.create_task(_run_adapter_chat_mode(adapter, config, "whatsapp-web"))
            try:
                while adapter.callback is None:
                    await asyncio.sleep(0)
                await adapter.callback("u1", "/find late payment")
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if adapter.prompts:
                        break
                assert adapter.prompts
                assert "server" in adapter.prompts[-1][1].lower()
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task


@pytest.mark.asyncio
async def test_chat_mode_unknown_command_sends_help() -> None:
    """Verify unknown /commands are forwarded to the server."""
    adapter = _FakeAdapter()
    config = SimpleNamespace(
        server_url="http://127.0.0.1:8000",
        error_message="Something went wrong. Please try again.",
        auto_approve=False,
    )

    with patch("dan.cli.adapter.asyncio.get_running_loop", return_value=_FakeLoop()):
        with patch("httpx.AsyncClient", _DirectReplyHttpClient):
            task = asyncio.create_task(_run_adapter_chat_mode(adapter, config, "whatsapp-web"))
            try:
                while adapter.callback is None:
                    await asyncio.sleep(0)
                await adapter.callback("u1", "/foobar something")
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if adapter.prompts:
                        break
                assert adapter.prompts
                assert adapter.prompts[-1][1] == "forwarded"
                assert _DirectReplyHttpClient.last_chat_body is not None
                assert _DirectReplyHttpClient.last_chat_body["message"] == "/foobar something"
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
