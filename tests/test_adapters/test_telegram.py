"""Tests for the Telegram messaging adapter."""

from __future__ import annotations

import asyncio
import json
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
        tg_adapter.config.progress_throttle = 60.0
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

    def test_command_menu_is_v2_focused(self, tg_adapter: TelegramAdapter) -> None:
        assert tg_adapter._default_dm_commands() == [
            ("agent", "Run a V2 Agent task"),
            ("status", "Check current task status"),
            ("cancel", "Cancel current task"),
            ("help", "Show available commands"),
        ]

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
    def test_v2_agent_command_and_workspace_context(self, monkeypatch: pytest.MonkeyPatch):
        from dan.adapters.telegram_config import TelegramFleetConfig
        from dan.adapters.telegram_fleet import (
            BotFleet,
            BotInstance,
            _telegram_effective_requested_mode,
            _telegram_control_plane_override,
            _telegram_requested_mode,
        )

        monkeypatch.delenv("DAN_TELEGRAM_CONTROL_PLANE", raising=False)
        monkeypatch.delenv("DAN_ADAPTERS_CONTROL_PLANE", raising=False)
        monkeypatch.delenv("DAN_TELEGRAM_ALLOW_V1", raising=False)
        monkeypatch.setenv("DAN_TELEGRAM_WORKSPACE_ROOT", "~/dan-work")
        monkeypatch.setenv("DAN_TELEGRAM_WORKSPACE_ID", "dan-work")

        mode, text = _telegram_requested_mode("/agent build the dashboard")
        assert mode == "agent"
        assert text == "build the dashboard"
        assert _telegram_effective_requested_mode(
            "v2",
            "auto",
            "Help me check again about the UAE quitting OPEC?",
        ) == "auto"
        assert _telegram_effective_requested_mode(
            "v2",
            "auto",
            "patch this repo in /tmp/app",
        ) == "agent"
        assert _telegram_effective_requested_mode(
            "v2",
            "auto",
            "I have this path /tmp/app, can you help me do this?",
        ) == "agent"
        assert _telegram_effective_requested_mode(
            "v2",
            "auto",
            "which workspace are we using?",
        ) == "auto"
        assert _telegram_effective_requested_mode(
            "v2",
            "auto",
            "/status",
        ) == "auto"
        assert _telegram_control_plane_override("dan") == "v2"
        monkeypatch.setenv("DAN_TELEGRAM_CONTROL_PLANE", "legacy")
        assert _telegram_control_plane_override("dan") == "v2"
        monkeypatch.setenv("DAN_TELEGRAM_ALLOW_V1", "1")
        assert _telegram_control_plane_override("dan") == "v1"

        fleet = BotFleet(TelegramFleetConfig())
        context = fleet._build_turn_surface_context(
            BotInstance(name="dan", token="fake"),
            MessageContext(
                chat_id=111,
                message_id=42,
                reply_to_message_id=41,
                reply_to_text="Previous answer about the dashboard",
                thread_id=7,
                from_user_id=99,
                from_user_username="alice",
                chat_type="private",
            ),
            conversation_key="111:7:dan",
            lane_key="111:7:dan:m41",
            reply_lane_key="111:7:dan",
            history=[
                {"role": "user", "content": "Build a dashboard"},
                {"role": "assistant", "content": "Accepted."},
            ],
        )

        assert context["workspace_root"] == "~/dan-work"
        assert context["workspace_id"] == "dan-work"
        assert context["telegram"] == {
            "chat_id": 111,
            "message_id": 42,
            "message_thread_id": 7,
            "reply_to_message_id": 41,
            "reply_to_text": "Previous answer about the dashboard",
            "chat_type": "private",
            "from_user_id": 99,
            "from_user_username": "alice",
            "sender_chat_id": None,
            "sender_chat_username": None,
        }
        assert context["conversation"] == {
            "conversation_key": "111:7:dan",
            "lane_key": "111:7:dan:m41",
            "reply_lane_key": "111:7:dan",
            "history_turn_count": 2,
            "history_window": 2,
        }
        assert fleet._infer_project_commands(BotInstance(name="dan", token="fake")) == [
            ("agent", "Run a V2 Agent task"),
            ("status", "Check current task status"),
            ("cancel", "Cancel current task"),
            ("help", "Show available commands"),
        ]

    @pytest.mark.asyncio
    async def test_v2_plain_telegram_turn_uses_chat_not_agent_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from dan.adapters.telegram_config import TelegramFleetConfig
        from dan.adapters.telegram_fleet import BotFleet, BotInstance

        class _Response:
            def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
                self._payload = payload
                self.status_code = status_code

            def json(self) -> dict[str, Any]:
                return self._payload

        class _Http:
            def __init__(self) -> None:
                self.posts: list[tuple[str, dict[str, Any]]] = []

            async def get(self, path: str) -> _Response:
                assert path.startswith("/api/graphs/")
                return _Response({"graph": {"nodes": [], "edges": []}})

            async def post(self, path: str, json: dict[str, Any]) -> _Response:
                self.posts.append((path, json))
                if path == "/api/v2/chat/message":
                    return _Response({"stream_channel_id": "chat-v2-1"})
                raise AssertionError(f"unexpected post path: {path}")

        class _Adapter:
            def __init__(self) -> None:
                self.reactions: list[tuple[int, int, str]] = []

            async def set_reaction(self, chat_id: int, message_id: int, emoji: str) -> None:
                self.reactions.append((chat_id, message_id, emoji))

        monkeypatch.delenv("DAN_TELEGRAM_ALLOW_V1", raising=False)
        monkeypatch.setenv("DAN_TELEGRAM_CONTROL_PLANE", "v2")

        fleet = BotFleet(TelegramFleetConfig(), server_url="http://server.test")
        http = _Http()
        fleet._http = http

        async def _fake_stream(*args: Any, **kwargs: Any) -> str:
            return "Chat reply."

        monkeypatch.setattr(fleet, "_stream_with_edits", _fake_stream)

        adapter = _Adapter()
        bot = BotInstance(name="dan", token="fake", adapter=adapter)
        ctx = MessageContext(
            chat_id=111,
            message_id=42,
            thread_id=7,
            chat_type="private",
        )

        await fleet._dispatch(
            bot,
            "telegram:111",
            "Help me check again about the UAE quitting OPEC?",
            ctx,
            conversation_key="111:7:dan",
            lane_key="111:7:dan",
        )

        assert [path for path, _payload in http.posts] == ["/api/v2/chat/message"]
        chat_payload = http.posts[0][1]
        assert chat_payload["mode"] == "auto"
        assert chat_payload["message"] == "Help me check again about the UAE quitting OPEC?"
        assert fleet._conversation_history["111:7:dan"] == [
            {
                "role": "user",
                "content": "Help me check again about the UAE quitting OPEC?",
            },
            {"role": "assistant", "content": "Chat reply."},
        ]
        assert adapter.reactions[-1] == (111, 42, "✅")

    @pytest.mark.asyncio
    async def test_v2_task_like_telegram_turn_uses_agent_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from dan.adapters.telegram_config import TelegramFleetConfig
        from dan.adapters.telegram_fleet import BotFleet, BotInstance

        class _Response:
            def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
                self._payload = payload
                self.status_code = status_code

            def json(self) -> dict[str, Any]:
                return self._payload

        class _Http:
            def __init__(self) -> None:
                self.posts: list[tuple[str, dict[str, Any]]] = []

            async def get(self, path: str) -> _Response:
                assert path.startswith("/api/graphs/")
                return _Response({"graph": {"nodes": [], "edges": []}})

            async def post(self, path: str, json: dict[str, Any]) -> _Response:
                self.posts.append((path, json))
                if path == "/api/v2/agent-runs":
                    return _Response({"v2_control_plane": {"run_id": "run-1"}})
                if path == "/api/v2/agent-runs/run-1/execute":
                    return _Response({"status": "started"})
                raise AssertionError(f"unexpected post path: {path}")

        class _Adapter:
            def __init__(self) -> None:
                self.reactions: list[tuple[int, int, str]] = []

            async def set_reaction(self, chat_id: int, message_id: int, emoji: str) -> None:
                self.reactions.append((chat_id, message_id, emoji))

        monkeypatch.delenv("DAN_TELEGRAM_ALLOW_V1", raising=False)
        monkeypatch.setenv("DAN_TELEGRAM_CONTROL_PLANE", "v2")

        fleet = BotFleet(TelegramFleetConfig(), server_url="http://server.test")
        http = _Http()
        fleet._http = http

        async def _fake_stream(*args: Any, **kwargs: Any) -> str:
            return "Agent done."

        monkeypatch.setattr(fleet, "_stream_v2_agent_run_events", _fake_stream)

        adapter = _Adapter()
        bot = BotInstance(name="dan", token="fake", adapter=adapter)
        ctx = MessageContext(
            chat_id=111,
            message_id=42,
            thread_id=7,
            chat_type="private",
        )

        await fleet._dispatch(
            bot,
            "telegram:111",
            "patch this repo in /tmp/app",
            ctx,
            conversation_key="111:7:dan",
            lane_key="111:7:dan",
        )

        paths = [path for path, _payload in http.posts]
        assert paths == ["/api/v2/agent-runs", "/api/v2/agent-runs/run-1/execute"]
        agent_payload = http.posts[0][1]
        assert agent_payload["mode"] == "agent"
        assert agent_payload["message"] == "patch this repo in /tmp/app"
        assert adapter.reactions[-1] == (111, 42, "✅")

    def test_private_non_reply_stays_on_shared_lane_even_when_parallel(self):
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
        assert lane2 == conversation_key

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

    def test_group_non_reply_can_still_fork_parallel_lane(self):
        from dan.adapters.telegram_fleet import _conversation_lane_key

        ctx = MessageContext(
            chat_id=-1001,
            message_id=13,
            chat_type="group",
        )
        lane = _conversation_lane_key(
            ctx,
            "dan",
            fork_for_parallel=True,
        )
        assert lane == "-1001:main:dan:m13"

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

    @pytest.mark.asyncio
    async def test_v2_agent_stream_sends_quiet_heartbeat_from_backend_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import websockets

        from dan.adapters.telegram_config import TelegramFleetConfig
        from dan.adapters.telegram_fleet import BotFleet, BotInstance

        class _Adapter:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            async def send_or_edit(
                self,
                chat_id,
                text,
                message_id=None,
                *,
                reply_to=None,
                thread_id=None,
            ):
                self.calls.append(
                    {
                        "chat_id": chat_id,
                        "text": text,
                        "message_id": message_id,
                        "reply_to": reply_to,
                        "thread_id": thread_id,
                    }
                )
                return 900 if message_id is None else message_id

        class _FakeWebSocket:
            def __init__(self) -> None:
                self.calls = 0

            async def recv(self) -> str:
                self.calls += 1
                if self.calls == 1:
                    return json.dumps(
                        {
                            "type": "accepted",
                            "run_id": "run-1",
                            "summary": "Accepted.",
                        }
                    )
                if self.calls == 2:
                    return json.dumps(
                        {
                            "type": "tool_used",
                            "run_id": "run-1",
                            "source_event_type": "tool.started",
                            "payload": {
                                "tool_id": "web_search",
                                "arguments": {"query": "current docs"},
                            },
                        }
                    )
                if self.calls == 3:
                    await asyncio.sleep(0.05)
                return json.dumps(
                    {
                        "type": "completed",
                        "run_id": "run-1",
                        "summary": "Done.",
                    }
                )

        class _Connect:
            def __init__(self) -> None:
                self.ws = _FakeWebSocket()

            async def __aenter__(self):
                return self.ws

            async def __aexit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: _Connect())

        fleet = BotFleet(TelegramFleetConfig(), server_url="http://server.test")
        fleet._V2_AGENT_PROGRESS_INTERVAL = 0.01
        adapter = _Adapter()
        bot = BotInstance(name="dan", token="fake", adapter=adapter)
        ctx = MessageContext(
            chat_id=111,
            message_id=42,
            thread_id=7,
            chat_type="private",
        )

        result = await fleet._stream_v2_agent_run_events(
            bot,
            ctx,
            "run-1",
            lane_key="111:main:dan",
        )

        assert result == "Done."
        texts = [call["text"] for call in adapter.calls]
        assert any(
            "Fetching web evidence" in text
            and "Elapsed:" in text
            and "Last backend event" in text
            for text in texts
        )
        assert adapter.calls[0]["message_id"] is None
        assert any(call["message_id"] == 900 for call in adapter.calls[1:])
