"""Tests for Telegram error visibility and forum thread_id threading.

Covers plan 31-25 tasks 1-1, 1-2, 3-1, 3-2, 3-3.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.adapters.telegram_adapter import MessageContext, TelegramAdapter, TelegramAdapterConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_adapter() -> TelegramAdapter:
    config = TelegramAdapterConfig(bot_token="fake:token")
    adapter = TelegramAdapter(config)
    adapter._application = MagicMock()
    adapter._application.bot = MagicMock()
    return adapter


def _make_ctx(
    *,
    chat_id: int = 100,
    message_id: int = 1,
    thread_id: int | None = None,
) -> MessageContext:
    return MessageContext(
        chat_id=chat_id,
        message_id=message_id,
        thread_id=thread_id,
    )


# ---------------------------------------------------------------------------
# Task 3-1: thread_id flows through adapter send methods
# ---------------------------------------------------------------------------


class TestSendOrEditThreadId:
    """send_or_edit passes message_thread_id when thread_id is set."""

    @pytest.mark.asyncio
    async def test_new_message_with_thread_id(self):
        adapter = _make_adapter()
        fake_msg = MagicMock()
        fake_msg.message_id = 42
        adapter._application.bot.send_message = AsyncMock(return_value=fake_msg)

        result = await adapter.send_or_edit(
            chat_id=100, text="hello", thread_id=55,
        )

        assert result == 42
        call_kwargs = adapter._application.bot.send_message.call_args
        assert call_kwargs[1].get("message_thread_id") == 55 or \
            call_kwargs.kwargs.get("message_thread_id") == 55

    @pytest.mark.asyncio
    async def test_new_message_without_thread_id(self):
        adapter = _make_adapter()
        fake_msg = MagicMock()
        fake_msg.message_id = 42
        adapter._application.bot.send_message = AsyncMock(return_value=fake_msg)

        await adapter.send_or_edit(chat_id=100, text="hello")

        kwargs = adapter._application.bot.send_message.call_args
        all_kwargs = {**kwargs.kwargs} if kwargs.kwargs else {}
        if kwargs.args:
            pass
        assert "message_thread_id" not in all_kwargs

    @pytest.mark.asyncio
    async def test_edit_does_not_use_thread_id(self):
        adapter = _make_adapter()
        adapter._application.bot.edit_message_text = AsyncMock()

        result = await adapter.send_or_edit(
            chat_id=100, text="hello", message_id=10, thread_id=55,
        )

        assert result == 10
        adapter._application.bot.edit_message_text.assert_called_once()
        call_kwargs = adapter._application.bot.edit_message_text.call_args.kwargs
        assert "message_thread_id" not in call_kwargs

    @pytest.mark.asyncio
    async def test_edit_fallback_preserves_thread_id(self):
        adapter = _make_adapter()
        adapter._application.bot.edit_message_text = AsyncMock(
            side_effect=RuntimeError("edit failed"),
        )
        fake_msg = MagicMock()
        fake_msg.message_id = 43
        adapter._application.bot.send_message = AsyncMock(return_value=fake_msg)

        result = await adapter.send_or_edit(
            chat_id=100,
            text="hello",
            message_id=10,
            thread_id=55,
        )

        assert result == 43
        call_kwargs = adapter._application.bot.send_message.call_args.kwargs
        assert call_kwargs.get("message_thread_id") == 55


class TestSendTextThreadId:
    """_send_text passes thread_id through to _try_send_markdown."""

    @pytest.mark.asyncio
    async def test_thread_id_forwarded(self):
        adapter = _make_adapter()
        adapter._try_send_markdown = AsyncMock(return_value=42)

        await adapter._send_text(100, "hello", reply_to=1, thread_id=55)

        adapter._try_send_markdown.assert_called_once_with(
            100, "hello", reply_to=1, thread_id=55,
        )

    @pytest.mark.asyncio
    async def test_no_thread_id(self):
        adapter = _make_adapter()
        adapter._try_send_markdown = AsyncMock(return_value=42)

        await adapter._send_text(100, "hello", reply_to=1)

        adapter._try_send_markdown.assert_called_once_with(
            100, "hello", reply_to=1, thread_id=None,
        )


class TestTrySendMarkdownThreadId:
    """_try_send_markdown includes message_thread_id in kwargs."""

    @pytest.mark.asyncio
    async def test_thread_id_in_kwargs(self):
        adapter = _make_adapter()
        fake_msg = MagicMock()
        fake_msg.message_id = 42
        adapter._application.bot.send_message = AsyncMock(return_value=fake_msg)

        await adapter._try_send_markdown(100, "plain text", thread_id=55)

        call_kwargs = adapter._application.bot.send_message.call_args.kwargs
        assert call_kwargs.get("message_thread_id") == 55

    @pytest.mark.asyncio
    async def test_no_thread_id_in_kwargs(self):
        adapter = _make_adapter()
        fake_msg = MagicMock()
        fake_msg.message_id = 42
        adapter._application.bot.send_message = AsyncMock(return_value=fake_msg)

        await adapter._try_send_markdown(100, "plain text")

        call_kwargs = adapter._application.bot.send_message.call_args.kwargs
        assert "message_thread_id" not in call_kwargs


class TestSendPollThreadId:
    """send_poll passes message_thread_id when thread_id is set."""

    @pytest.mark.asyncio
    async def test_poll_with_thread_id(self):
        adapter = _make_adapter()
        fake_msg = MagicMock()
        fake_msg.message_id = 42
        fake_msg.poll = MagicMock()
        fake_msg.poll.id = "poll_123"
        adapter._application.bot.send_poll = AsyncMock(return_value=fake_msg)

        result = await adapter.send_poll(
            chat_id=100,
            question="Pick one",
            options=["A", "B"],
            thread_id=55,
        )

        assert result == "poll_123"
        call_kwargs = adapter._application.bot.send_poll.call_args.kwargs
        assert call_kwargs.get("message_thread_id") == 55

    @pytest.mark.asyncio
    async def test_poll_without_thread_id(self):
        adapter = _make_adapter()
        fake_msg = MagicMock()
        fake_msg.message_id = 42
        fake_msg.poll = MagicMock()
        fake_msg.poll.id = "poll_456"
        adapter._application.bot.send_poll = AsyncMock(return_value=fake_msg)

        result = await adapter.send_poll(
            chat_id=100,
            question="Pick one",
            options=["A", "B"],
        )

        assert result == "poll_456"
        call_kwargs = adapter._application.bot.send_poll.call_args.kwargs
        assert "message_thread_id" not in call_kwargs


class TestSendFileThreadId:
    @pytest.mark.asyncio
    async def test_send_document_with_thread_id(self, tmp_path):
        adapter = _make_adapter()
        fake_msg = MagicMock()
        fake_msg.message_id = 99
        adapter._application.bot.send_document = AsyncMock(return_value=fake_msg)

        path = tmp_path / "notes.txt"
        path.write_text("hello")

        result = await adapter._send_file_to_chat(
            100,
            str(path),
            thread_id=55,
        )

        assert result == 99
        call_kwargs = adapter._application.bot.send_document.call_args.kwargs
        assert call_kwargs.get("message_thread_id") == 55


# ---------------------------------------------------------------------------
# Tasks 1-1 / 1-2 + 3-2: Fleet dispatch error messages & _send_reply threading
# ---------------------------------------------------------------------------


def _make_fleet_stubs():
    """Build minimal stubs for BotFleet._dispatch testing."""
    from dan.adapters.telegram_fleet import BotFleet, BotInstance
    from dan.adapters.telegram_config import TelegramFleetConfig

    config = TelegramFleetConfig(bots={}, groups={})
    fleet = BotFleet(config)
    fleet._http = MagicMock()
    fleet._conversation_workflows = {}
    fleet._conversation_history = {}

    adapter = _make_adapter()
    adapter._send_text = AsyncMock(return_value=42)
    adapter.set_reaction = AsyncMock()

    bot = BotInstance(
        name="test_bot",
        token="fake:token",
        adapter=adapter,
        bot_username="test_bot",
    )

    return fleet, bot, adapter


class TestDispatchHttpError:
    """Task 1-1: error message sent on HTTP non-200."""

    @pytest.mark.asyncio
    async def test_sends_error_on_non_200(self):
        fleet, bot, adapter = _make_fleet_stubs()
        ctx = _make_ctx(thread_id=77)

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        fleet._http.post = AsyncMock(return_value=mock_resp)
        fleet._ensure_scratch = AsyncMock(return_value="wf_1")

        await fleet._dispatch(bot, "ext1", "hello", ctx)

        adapter._send_text.assert_called_once()
        call_args = adapter._send_text.call_args
        assert call_args[0][0] == 100  # chat_id
        assert "couldn't process" in call_args[0][1]
        assert call_args[1].get("thread_id") == 77


class TestDispatchException:
    """Task 1-2: error message sent on dispatch exception."""

    @pytest.mark.asyncio
    async def test_sends_error_on_exception(self):
        fleet, bot, adapter = _make_fleet_stubs()
        ctx = _make_ctx(thread_id=88)

        fleet._ensure_scratch = AsyncMock(side_effect=RuntimeError("boom"))

        await fleet._dispatch(bot, "ext1", "hello", ctx)

        adapter._send_text.assert_called_once()
        call_args = adapter._send_text.call_args
        assert call_args[0][0] == 100
        assert "something went wrong" in call_args[0][1]
        assert call_args[1].get("thread_id") == 88


class TestSendReplyThreadId:
    """Task 3-2: _send_reply passes thread_id through to _send_text."""

    @pytest.mark.asyncio
    async def test_thread_id_forwarded(self):
        fleet, bot, adapter = _make_fleet_stubs()
        ctx = _make_ctx(thread_id=99)

        await fleet._send_reply(
            bot, ctx, "test reply", thread_id=99,
        )

        adapter._send_text.assert_called()
        call_kwargs = adapter._send_text.call_args.kwargs
        assert call_kwargs.get("thread_id") == 99

    @pytest.mark.asyncio
    async def test_no_thread_id(self):
        fleet, bot, adapter = _make_fleet_stubs()
        ctx = _make_ctx()

        await fleet._send_reply(bot, ctx, "test reply")

        adapter._send_text.assert_called()
        call_kwargs = adapter._send_text.call_args.kwargs
        assert call_kwargs.get("thread_id") is None


class TestTopicScopedSessions:
    @pytest.mark.asyncio
    async def test_send_progress_uses_thread_from_session_mapping(self):
        adapter = _make_adapter()
        adapter._send_text = AsyncMock(return_value=42)
        adapter.register_session("sess-1", "100:55")

        await adapter.send_progress("sess-1", "working")

        adapter._send_text.assert_awaited_once_with(
            100,
            "⏳ working",
            thread_id=55,
        )

    @pytest.mark.asyncio
    async def test_on_text_message_uses_topic_scoped_external_id(self):
        adapter = _make_adapter()
        adapter._on_new_message = AsyncMock()
        adapter._callback_with_context = True

        update = MagicMock()
        update.effective_chat.id = 100
        update.message = MagicMock()
        update.message.text = "hello"
        update.message.chat_id = 100
        update.message.message_id = 7
        update.message.message_thread_id = 55
        update.message.from_user = MagicMock(id=1, is_bot=False, username="alice")
        update.message.entities = []
        update.message.caption = None
        update.message.caption_entities = []
        update.message.reply_to_message = None
        update.message.chat = MagicMock(type="supergroup")
        update.message.sender_chat = None

        await adapter._on_text_message(update, MagicMock())

        adapter._on_new_message.assert_awaited_once()
        session_id, text, ctx = adapter._on_new_message.await_args.args
        assert session_id == "100:55"
        assert text == "hello"
        assert ctx.thread_id == 55
