"""Tests for the Telegram messaging adapter."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.adapters.telegram_adapter import (
    TelegramAdapter,
    TelegramAdapterConfig,
    MessageContext,
    _split_message,
)


@pytest.fixture
def tg_config() -> TelegramAdapterConfig:
    return TelegramAdapterConfig(
        workflow_path="/tmp/wf.md",
        bot_token="123456:FAKE_TOKEN",
        allowed_chat_ids=[111, 222],
        progress_throttle=0.01,
    )


@pytest.fixture
def tg_adapter(tg_config: TelegramAdapterConfig) -> TelegramAdapter:
    return TelegramAdapter(tg_config)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestTelegramAdapterConfig:
    def test_defaults(self) -> None:
        cfg = TelegramAdapterConfig()
        assert cfg.bot_token == ""
        assert cfg.allowed_chat_ids == []
        assert cfg.webhook_url is None
        assert cfg.progress_throttle == 5.0

    def test_custom(self) -> None:
        cfg = TelegramAdapterConfig(bot_token="tok", allowed_chat_ids=[1])
        assert cfg.bot_token == "tok"
        assert cfg.allowed_chat_ids == [1]


# ---------------------------------------------------------------------------
# Message splitting
# ---------------------------------------------------------------------------

class TestSplitMessage:
    def test_short_message_unchanged(self) -> None:
        assert _split_message("hello") == ["hello"]

    def test_long_message_split_at_newline(self) -> None:
        text = "A" * 100 + "\n" + "B" * 100
        chunks = _split_message(text, max_len=150)
        assert len(chunks) == 2
        assert chunks[0] == "A" * 100
        assert "B" in chunks[1]

    def test_long_message_split_at_space(self) -> None:
        text = "word " * 50
        chunks = _split_message(text, max_len=30)
        assert all(len(c) <= 30 for c in chunks)

    def test_no_split_point(self) -> None:
        text = "A" * 200
        chunks = _split_message(text, max_len=50)
        assert len(chunks) > 1
        assert "".join(chunks) == text


# ---------------------------------------------------------------------------
# Session mapping
# ---------------------------------------------------------------------------

class TestSessionMapping:
    def test_register_and_lookup(self, tg_adapter: TelegramAdapter) -> None:
        tg_adapter.register_session("sess-1", 111)
        assert tg_adapter._chat_id_from_session("sess-1") == 111
        assert tg_adapter._session_id_from_chat(111) == "sess-1"

    def test_unregister(self, tg_adapter: TelegramAdapter) -> None:
        tg_adapter.register_session("sess-2", 222)
        tg_adapter.unregister_session("sess-2")
        assert tg_adapter._chat_id_from_session("sess-2") is None
        assert tg_adapter._session_id_from_chat(222) is None


# ---------------------------------------------------------------------------
# Allowed chat filtering
# ---------------------------------------------------------------------------

class TestAllowedChats:
    def test_allowed(self, tg_adapter: TelegramAdapter) -> None:
        assert tg_adapter._is_allowed(111) is True
        assert tg_adapter._is_allowed(222) is True

    def test_not_allowed(self, tg_adapter: TelegramAdapter) -> None:
        assert tg_adapter._is_allowed(999) is False

    def test_empty_allows_all(self) -> None:
        adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
        assert adapter._is_allowed(12345) is True


# ---------------------------------------------------------------------------
# Wait for response
# ---------------------------------------------------------------------------

class TestWaitForResponse:
    @pytest.mark.asyncio
    async def test_response_resolved(self, tg_adapter: TelegramAdapter) -> None:
        async def _feed() -> None:
            await asyncio.sleep(0.05)
            fut = tg_adapter._pending.get("sess-1")
            if fut and not fut.done():
                fut.set_result({"response": "my answer"})

        asyncio.create_task(_feed())
        result = await tg_adapter.wait_for_response("sess-1", timeout=2.0)
        assert result["response"] == "my answer"

    @pytest.mark.asyncio
    async def test_response_timeout(self, tg_adapter: TelegramAdapter) -> None:
        with pytest.raises(asyncio.TimeoutError):
            await tg_adapter.wait_for_response("sess-timeout", timeout=0.1)


# ---------------------------------------------------------------------------
# Send helpers (mock bot)
# ---------------------------------------------------------------------------

class TestSendMethods:
    @pytest.mark.asyncio
    async def test_send_prompt(self, tg_adapter: TelegramAdapter) -> None:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        tg_adapter._application = mock_app

        tg_adapter.register_session("sess-send", 111)
        await tg_adapter.send_prompt("sess-send", "What is your name?")
        mock_bot.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_result(self, tg_adapter: TelegramAdapter) -> None:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        tg_adapter._application = mock_app

        tg_adapter.register_session("sess-res", 222)
        await tg_adapter.send_result("sess-res", {"answer": "42"})
        mock_bot.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_progress_throttled(self, tg_adapter: TelegramAdapter) -> None:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        tg_adapter._application = mock_app

        tg_adapter.register_session("sess-prog", 111)

        await tg_adapter.send_progress("sess-prog", "Step 1")
        await tg_adapter.send_progress("sess-prog", "Step 2")

        assert mock_bot.send_message.await_count == 1


# ---------------------------------------------------------------------------
# Command handlers (simulated updates)
# ---------------------------------------------------------------------------

class TestCommandHandlers:
    def _make_update(self, chat_id: int, text: str) -> MagicMock:
        update = MagicMock()
        update.effective_chat.id = chat_id
        update.message.text = text
        update.message.caption = None
        update.message.chat.type = "private"
        update.message.reply_text = AsyncMock()
        return update

    @pytest.mark.asyncio
    async def test_cmd_start(self, tg_adapter: TelegramAdapter) -> None:
        update = self._make_update(111, "/start")
        await tg_adapter._cmd_start(update, None)
        update.message.reply_text.assert_awaited_once()
        args = update.message.reply_text.call_args[0]
        assert tg_adapter.config.welcome_message in args[0]

    @pytest.mark.asyncio
    async def test_cmd_status_no_active(self, tg_adapter: TelegramAdapter) -> None:
        update = self._make_update(111, "/status")
        await tg_adapter._cmd_status(update, None)
        update.message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cmd_cancel_no_active(self, tg_adapter: TelegramAdapter) -> None:
        update = self._make_update(111, "/cancel")
        await tg_adapter._cmd_cancel(update, None)
        update.message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_text_message_resolves_pending(self, tg_adapter: TelegramAdapter) -> None:
        tg_adapter.register_session("sess-msg", 111)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        tg_adapter._pending["sess-msg"] = fut

        update = self._make_update(111, "my reply")
        await tg_adapter._on_text_message(update, None)

        assert fut.done()
        assert fut.result()["response"] == "my reply"

    @pytest.mark.asyncio
    async def test_callback_query_approval(self, tg_adapter: TelegramAdapter) -> None:
        tg_adapter.register_session("sess-cb", 111)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        tg_adapter._pending["sess-cb"] = fut

        query = MagicMock()
        query.message.chat_id = 111
        query.data = "__approve_yes"
        query.answer = AsyncMock()

        update = MagicMock()
        update.callback_query = query

        await tg_adapter._on_callback_query(update, None)
        assert fut.done()
        assert fut.result()["approved"] is True


class TestFormatForTelegram:
    """Test that _format_for_telegram converts [DAN - Project] to compact [Project]."""

    def test_scoped_prefix_converted(self):
        from dan.adapters.telegram_fleet import _format_for_telegram
        result = _format_for_telegram("[DAN - Research] Some answer here")
        assert result.startswith("[Research]\n")
        assert "Some answer here" in result

    def test_scoped_with_task(self):
        from dan.adapters.telegram_fleet import _format_for_telegram
        result = _format_for_telegram("[DAN - Proj / Task] hello")
        assert result.startswith("[Proj / Task]\n")
        assert "hello" in result

    def test_bare_prefix_stripped(self):
        from dan.adapters.telegram_fleet import _format_for_telegram
        result = _format_for_telegram("[DAN] hello world")
        assert result == "hello world"

    def test_no_prefix_preserved(self):
        from dan.adapters.telegram_fleet import _format_for_telegram
        result = _format_for_telegram("plain text response")
        assert result == "plain text response"

    def test_legacy_alias_works(self):
        from dan.adapters.telegram_fleet import _strip_prefix_and_html
        result = _strip_prefix_and_html("[DAN - MyProj] answer")
        assert result.startswith("[MyProj]\n")


class TestTelegramStreamErrorFormatting:
    def test_rate_limit_errors_are_friendly(self):
        from dan.adapters.telegram_fleet import _format_telegram_stream_error

        result = _format_telegram_stream_error(
            "Error code: 429 - {'error': {'message': '当前分组上游负载已饱和，请稍后再试'}}"
        )
        assert "temporarily overloaded" in result
        assert "429" not in result

    def test_generic_errors_preserve_message(self):
        from dan.adapters.telegram_fleet import _format_telegram_stream_error

        result = _format_telegram_stream_error("something unexpected happened")
        assert result == "I hit an error: something unexpected happened"

    def test_blank_errors_use_fallback_copy(self):
        from dan.adapters.telegram_fleet import _format_telegram_stream_error

        result = _format_telegram_stream_error("")
        assert "Please try again" in result


class TestTelegramFleetLaneBehavior:
    def test_non_reply_uses_shared_lane_until_parallel_fork_is_needed(self):
        from dan.adapters.telegram_config import TelegramFleetConfig
        from dan.adapters.telegram_fleet import (
            BotFleet,
            _conversation_lane_key,
            _conversation_thread_key,
        )

        fleet = BotFleet(TelegramFleetConfig())
        ctx1 = MessageContext(chat_id=111, message_id=10, chat_type="private")
        conversation_key = _conversation_thread_key(ctx1, "dan")

        lane1 = _conversation_lane_key(
            ctx1,
            "dan",
            fork_for_parallel=fleet._conversation_has_active_dispatch(conversation_key),
        )
        assert lane1 == conversation_key

        fleet._mark_conversation_dispatch_started(conversation_key)
        ctx2 = MessageContext(chat_id=111, message_id=11, chat_type="private")
        lane2 = _conversation_lane_key(
            ctx2,
            "dan",
            fork_for_parallel=fleet._conversation_has_active_dispatch(conversation_key),
        )
        assert lane2 == f"{conversation_key}:m11"

        fleet._mark_conversation_dispatch_finished(conversation_key)
        ctx3 = MessageContext(chat_id=111, message_id=12, chat_type="private")
        lane3 = _conversation_lane_key(
            ctx3,
            "dan",
            fork_for_parallel=fleet._conversation_has_active_dispatch(conversation_key),
        )
        assert lane3 == conversation_key

    def test_reply_lane_wins_over_parallel_fork(self):
        from dan.adapters.telegram_fleet import _conversation_lane_key

        ctx = MessageContext(
            chat_id=111,
            message_id=12,
            reply_to_message_id=99,
            chat_type="private",
        )
        lane = _conversation_lane_key(
            ctx,
            "dan",
            reply_lane_key="111:main:dan:m99",
            fork_for_parallel=True,
        )
        assert lane == "111:main:dan:m99"

    @pytest.mark.asyncio
    async def test_forked_lane_keeps_broad_follow_up_continuity(self):
        from dan.adapters.telegram_config import TelegramFleetConfig
        from dan.adapters.telegram_fleet import BotFleet, _conversation_thread_key

        fleet = BotFleet(TelegramFleetConfig())
        conversation_key = _conversation_thread_key(
            MessageContext(chat_id=111, message_id=10, chat_type="private"),
            "dan",
        )

        first_history = await fleet._record_user_turn(
            conversation_key,
            conversation_key,
            {"role": "user", "content": "Build a workflow"},
        )
        assert [turn["content"] for turn in first_history] == ["Build a workflow"]

        await fleet._append_assistant_turn(
            conversation_key,
            "Sure, I can do that.",
            conversation_key=conversation_key,
        )

        forked_lane = f"{conversation_key}:m11"
        forked_history = await fleet._record_user_turn(
            conversation_key,
            forked_lane,
            {"role": "user", "content": "Also summarize this file"},
        )
        assert [turn["content"] for turn in forked_history] == [
            "Build a workflow",
            "Sure, I can do that.",
            "Also summarize this file",
        ]

        await fleet._append_assistant_turn(
            forked_lane,
            "Summary ready.",
            conversation_key=conversation_key,
        )

        follow_up_history = await fleet._record_user_turn(
            conversation_key,
            conversation_key,
            {"role": "user", "content": "Now run the workflow"},
        )
        assert [turn["content"] for turn in follow_up_history][-5:] == [
            "Build a workflow",
            "Sure, I can do that.",
            "Also summarize this file",
            "Summary ready.",
            "Now run the workflow",
        ]
