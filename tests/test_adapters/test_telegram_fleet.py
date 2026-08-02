"""Tests for Telegram bot fleet coordination."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import dan.adapters.telegram_fleet as telegram_fleet_module
import dan.cli.bot as bot_cli
from dan.adapters.telegram_config import (
    TelegramBotConfig,
    TelegramFleetConfig,
    TelegramSettings,
)
from dan.adapters.telegram_fleet import (
    BotFleet,
    BotInstance,
    _conversation_lane_key,
    _conversation_thread_key,
)
from dan.adapters.telegram_adapter import MessageContext


@pytest.mark.asyncio
async def test_fleet_dispatch_sends_lane_key_as_session_id() -> None:
    fleet = BotFleet(
        TelegramFleetConfig(settings=TelegramSettings(use_reactions=False)),
    )
    fleet._ensure_scratch = AsyncMock(return_value="_fleet_test")  # type: ignore[method-assign]

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {}

    http = MagicMock()
    http.post = AsyncMock(return_value=response)
    fleet._http = http

    adapter = SimpleNamespace(
        _send_text=AsyncMock(return_value=123),
        set_reaction=AsyncMock(),
        send_or_edit=AsyncMock(return_value=123),
    )
    bot = BotInstance(name="dan", token="fake", adapter=adapter)

    ctx = MessageContext(
        chat_id=42,
        message_id=99,
        chat_type="private",
    )

    await fleet._dispatch(
        bot,
        "telegram:dan",
        "hello there",
        ctx,
        conversation_key="42:main:dan",
        lane_key="42:main:dan:m99",
    )

    payload = http.post.await_args.kwargs["json"]
    assert payload["thread_id"] == "42:main:dan"
    assert payload["session_id"] == "42:main:dan:m99"


@pytest.mark.asyncio
async def test_first_callback_dispatches_selected_winner_once() -> None:
    fleet = BotFleet(TelegramFleetConfig())
    bot_a = BotInstance(name="research-bot", token="a", bot_username="research_bot")
    bot_b = BotInstance(
        name="data-bot",
        token="b",
        bot_username="data_bot",
        projects=["equity"],
        is_default=True,
    )
    fleet._bots = {"research-bot": bot_a, "data-bot": bot_b}

    dispatched: list[str] = []

    async def _dispatch(bot: BotInstance, ext_id: str, text: str, ctx: MessageContext) -> None:
        dispatched.append(bot.name)

    fleet._dispatch = _dispatch  # type: ignore[method-assign]

    callback_a = fleet._make_callback(bot_a)
    callback_b = fleet._make_callback(bot_b)
    ctx = MessageContext(
        chat_id=-1001,
        message_id=55,
        mentions=["data_bot"],
        chat_type="group",
        from_user_id=42,
    )

    await callback_a("-1001", "@data_bot analyze this", ctx)
    await callback_b("-1001", "@data_bot analyze this", ctx)
    await asyncio.sleep(0)

    assert dispatched == ["data-bot"]


@pytest.mark.asyncio
async def test_group_message_copies_from_multiple_bots_dedup_by_ingress_key() -> None:
    fleet = BotFleet(TelegramFleetConfig())
    scholar = BotInstance(name="scholar", token="a", bot_username="dan_scholar_bot")
    engineer = BotInstance(name="engineer", token="b", bot_username="dan_engineer_bot")
    quant = BotInstance(name="quant", token="c", bot_username="dan_quant_bot")
    chief = BotInstance(
        name="chief",
        token="d",
        bot_username="dan_chief_bot",
        is_default=True,
    )
    fleet._bots = {
        "scholar": scholar,
        "engineer": engineer,
        "quant": quant,
        "chief": chief,
    }

    dispatched: list[str] = []

    async def _dispatch(bot: BotInstance, ext_id: str, text: str, ctx: MessageContext) -> None:
        dispatched.append(bot.name)

    fleet._dispatch = _dispatch  # type: ignore[method-assign]

    shared_ingress_key = "group-copy-key"
    callbacks = [
        fleet._make_callback(scholar),
        fleet._make_callback(engineer),
        fleet._make_callback(quant),
        fleet._make_callback(chief),
    ]
    contexts = [
        MessageContext(
            chat_id=-5252028417,
            message_id=26,
            chat_type="group",
            from_user_id=1914429317,
            ingress_dedup_key=shared_ingress_key,
        ),
        MessageContext(
            chat_id=-5252028417,
            message_id=18,
            chat_type="group",
            from_user_id=1914429317,
            ingress_dedup_key=shared_ingress_key,
        ),
        MessageContext(
            chat_id=-5252028417,
            message_id=14,
            chat_type="group",
            from_user_id=1914429317,
            ingress_dedup_key=shared_ingress_key,
        ),
        MessageContext(
            chat_id=-5252028417,
            message_id=48,
            chat_type="group",
            from_user_id=1914429317,
            ingress_dedup_key=shared_ingress_key,
        ),
    ]

    for callback, ctx in zip(callbacks, contexts, strict=False):
        await callback(str(ctx.chat_id), "Hello", ctx)
    await asyncio.sleep(0)

    assert dispatched == ["chief"]


@pytest.mark.asyncio
async def test_callback_ignores_messages_from_known_bot_username_when_bot_flag_missing() -> None:
    fleet = BotFleet(TelegramFleetConfig())
    chief = BotInstance(name="chief", token="a", bot_username="dan_chief_bot", is_default=True)
    scholar = BotInstance(name="scholar", token="b", bot_username="dan_scholar_bot")
    fleet._bots = {"chief": chief, "scholar": scholar}

    dispatched: list[str] = []

    async def _dispatch(bot: BotInstance, ext_id: str, text: str, ctx: MessageContext) -> None:
        dispatched.append(bot.name)

    fleet._dispatch = _dispatch  # type: ignore[method-assign]

    callback = fleet._make_callback(scholar)
    ctx = MessageContext(
        chat_id=-1001,
        message_id=56,
        chat_type="group",
        from_user_id=999,
        from_user_is_bot=False,
        from_user_username="dan_chief_bot",
    )

    await callback("-1001", "hello", ctx)
    await asyncio.sleep(0)

    assert dispatched == []


@pytest.mark.asyncio
async def test_callback_ignores_messages_from_known_bot_sender_chat_username() -> None:
    fleet = BotFleet(TelegramFleetConfig())
    chief = BotInstance(name="chief", token="a", bot_username="dan_chief_bot", is_default=True)
    scholar = BotInstance(name="scholar", token="b", bot_username="dan_scholar_bot")
    fleet._bots = {"chief": chief, "scholar": scholar}

    dispatched: list[str] = []

    async def _dispatch(bot: BotInstance, ext_id: str, text: str, ctx: MessageContext) -> None:
        dispatched.append(bot.name)

    fleet._dispatch = _dispatch  # type: ignore[method-assign]

    callback = fleet._make_callback(scholar)
    ctx = MessageContext(
        chat_id=-1001,
        message_id=57,
        chat_type="group",
        from_user_id=None,
        from_user_is_bot=False,
        sender_chat_username="dan_chief_bot",
    )

    await callback("-1001", "hello", ctx)
    await asyncio.sleep(0)

    assert dispatched == []


@pytest.mark.asyncio
async def test_callback_ignores_messages_from_known_bot_id_when_username_missing() -> None:
    fleet = BotFleet(TelegramFleetConfig())
    chief = BotInstance(
        name="chief",
        token="a",
        bot_id=123456,
        bot_username="dan_chief_bot",
        is_default=True,
    )
    scholar = BotInstance(name="scholar", token="b", bot_username="dan_scholar_bot")
    fleet._bots = {"chief": chief, "scholar": scholar}

    dispatched: list[str] = []

    async def _dispatch(bot: BotInstance, ext_id: str, text: str, ctx: MessageContext) -> None:
        dispatched.append(bot.name)

    fleet._dispatch = _dispatch  # type: ignore[method-assign]

    callback = fleet._make_callback(scholar)
    ctx = MessageContext(
        chat_id=-1001,
        message_id=58,
        chat_type="group",
        from_user_id=123456,
        from_user_is_bot=False,
        from_user_username=None,
    )

    await callback("-1001", "hello", ctx)
    await asyncio.sleep(0)

    assert dispatched == []


def test_load_from_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = TelegramFleetConfig(
        bots={"dan": TelegramBotConfig(token="abc", default=True)},
    )
    path.write_text(config.model_dump_json(by_alias=True))

    fleet = BotFleet.load_from_config(path)
    assert fleet._config.bots["dan"].token == "abc"


def test_topic_creation_refreshes_router_and_assigns_project(tmp_path: Path) -> None:
    config = TelegramFleetConfig(
        bots={"research-bot": TelegramBotConfig(token="abc", default=True)},
    )
    fleet = BotFleet(config)
    fleet._config_path = tmp_path / "fleet.json"
    fleet._bots = {
        "research-bot": BotInstance(
            name="research-bot",
            token="abc",
            bot_username="research_bot",
            is_default=True,
        ),
    }

    asyncio.run(fleet._on_topic_created("research-bot", -1001, 77, "new-project"))

    winner = fleet._router.route(
        text="hello",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=77,
        from_user_is_bot=False,
        from_user_id=42,
        bots=fleet._routable_bots(),
    )
    assert winner is not None
    assert winner.name == "research-bot"
    assert config.bots["research-bot"].projects == ["new-project"]


def test_extract_attachment_ignores_reply_prefix_lines() -> None:
    from dan.adapters.telegram_fleet import _extract_attachment

    text = '[Replying to: "earlier"]\n[Attachment: /tmp/doc.pdf]\nplease review'
    assert _extract_attachment(text) == "/tmp/doc.pdf"


def test_topic_creation_updates_live_bot_projects_for_non_default_bot(
    tmp_path: Path,
) -> None:
    config = TelegramFleetConfig(
        bots={
            "creator-bot": TelegramBotConfig(token="abc", default=False),
            "default-bot": TelegramBotConfig(token="def", default=True),
        },
    )
    fleet = BotFleet(config)
    fleet._config_path = tmp_path / "fleet.json"
    fleet._bots = {
        "creator-bot": BotInstance(
            name="creator-bot",
            token="abc",
            bot_username="creator_bot",
            is_default=False,
        ),
        "default-bot": BotInstance(
            name="default-bot",
            token="def",
            bot_username="default_bot",
            is_default=True,
        ),
    }

    asyncio.run(fleet._on_topic_created("creator-bot", -1001, 88, "special-project"))

    winner = fleet._router.route(
        text="hello",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=88,
        from_user_is_bot=False,
        from_user_id=42,
        bots=fleet._routable_bots(),
    )
    assert winner is not None
    assert winner.name == "creator-bot"


@pytest.mark.asyncio
async def test_stream_with_edits_does_not_resend_full_reply_when_final_edit_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fleet = BotFleet(TelegramFleetConfig())
    sent_edits: list[tuple[int, str, int | None]] = []
    sent_replies: list[str] = []

    class _FakeAdapter:
        async def send_or_edit(
            self,
            chat_id: int,
            text: str,
            message_id: int | None = None,
            *,
            reply_to=None,
            thread_id=None,
        ):
            sent_edits.append((chat_id, text, message_id))
            return 999 if message_id is None else message_id

        async def _send_file_to_chat(self, chat_id: int, path: str, *, thread_id=None) -> None:
            return None

    bot = BotInstance(
        name="dan",
        token="abc",
        bot_username="dan_bot",
        is_default=True,
        adapter=_FakeAdapter(),
    )
    ctx = MessageContext(chat_id=123, message_id=7)

    async def _send_reply(_bot, _ctx, text: str) -> None:
        sent_replies.append(text)

    fleet._send_reply = _send_reply  # type: ignore[method-assign]

    class _FakeWs:
        def __init__(self, messages):
            self._messages = iter(messages)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    monkeypatch.setattr(
        "websockets.connect",
        lambda *args, **kwargs: _FakeWs(
            [
                '{"type":"chat_token","delta":"hello","accumulated":"hello"}',
                '{"type":"chat_complete","content":"hello"}',
            ],
        ),
    )

    full = await fleet._stream_with_edits(bot, ctx, "chan-1")

    assert full == "hello"
    assert sent_edits
    assert sent_replies == []


@pytest.mark.asyncio
async def test_send_reply_marks_outbound_message_as_seen_for_dedup() -> None:
    fleet = BotFleet(TelegramFleetConfig())

    class _FakeAdapter:
        async def _send_text(self, chat_id: int, text: str, reply_to=None, thread_id=None) -> int | None:
            return 777

    chief = BotInstance(
        name="chief",
        token="abc",
        bot_username="dan_chief_bot",
        is_default=True,
        adapter=_FakeAdapter(),
    )
    scholar = BotInstance(
        name="scholar",
        token="def",
        bot_username="dan_scholar_bot",
    )
    fleet._bots = {"chief": chief, "scholar": scholar}

    dispatched: list[str] = []

    async def _dispatch(bot: BotInstance, ext_id: str, text: str, ctx: MessageContext) -> None:
        dispatched.append(bot.name)

    fleet._dispatch = _dispatch  # type: ignore[method-assign]

    await fleet._send_reply(chief, MessageContext(chat_id=-1001, message_id=7), "hello")

    callback = fleet._make_callback(scholar)
    await callback(
        "-1001",
        "hello",
        MessageContext(
            chat_id=-1001,
            message_id=777,
            chat_type="group",
            from_user_id=999,
            from_user_is_bot=False,
        ),
    )
    await asyncio.sleep(0)

    assert dispatched == []


@pytest.mark.asyncio
async def test_run_fleet_rejects_live_startup_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "fleet.lock"
    lock_path.write_text(str(os.getpid()))
    monkeypatch.setattr(
        telegram_fleet_module,
        "_FLEET_LOCK_FILE",
        lock_path,
        raising=False,
    )

    started = False

    class _FakeFleet:
        def __init__(self) -> None:
            self._stop_event = asyncio.Event()

        async def start(self) -> None:
            nonlocal started
            started = True
            self._stop_event.set()

        async def stop(self) -> None:
            return None

    fake_fleet = _FakeFleet()
    monkeypatch.setattr(
        telegram_fleet_module.BotFleet,
        "from_config",
        classmethod(lambda cls, *_args, **_kwargs: fake_fleet),
    )
    monkeypatch.setattr(
        telegram_fleet_module,
        "_print_fleet_status",
        lambda _fleet: None,
    )

    with pytest.raises(RuntimeError, match="already running"):
        await telegram_fleet_module.run_fleet()

    assert started is False


@pytest.mark.asyncio
async def test_run_fleet_replaces_stale_startup_lock_and_releases_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "fleet.lock"
    lock_path.write_text("999999")
    monkeypatch.setattr(
        telegram_fleet_module,
        "_FLEET_LOCK_FILE",
        lock_path,
        raising=False,
    )

    class _FakeFleet:
        def __init__(self) -> None:
            self._stop_event = asyncio.Event()
            self.started = False
            self.stopped = False

        async def start(self) -> None:
            self.started = True
            self._stop_event.set()

        async def stop(self) -> None:
            self.stopped = True

    fake_fleet = _FakeFleet()
    monkeypatch.setattr(
        telegram_fleet_module.BotFleet,
        "from_config",
        classmethod(lambda cls, *_args, **_kwargs: fake_fleet),
    )
    monkeypatch.setattr(
        telegram_fleet_module,
        "_print_fleet_status",
        lambda _fleet: None,
    )

    def _fake_kill(pid: int, sig: int) -> None:
        if pid == 999999 and sig == 0:
            raise ProcessLookupError()
        return None

    monkeypatch.setattr(telegram_fleet_module.os, "kill", _fake_kill)

    await telegram_fleet_module.run_fleet()

    assert fake_fleet.started is True
    assert fake_fleet.stopped is True
    assert not lock_path.exists()


def test_acquire_fleet_lock_raises_after_retry_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "fleet.lock"
    lock_path.write_text("999999")
    attempts = {"count": 0}

    def fake_open(*_args, **_kwargs):
        attempts["count"] += 1
        raise FileExistsError()

    monkeypatch.setattr(telegram_fleet_module.os, "open", fake_open)
    monkeypatch.setattr(telegram_fleet_module, "_pid_is_running", lambda _pid: False)
    monkeypatch.setattr(
        telegram_fleet_module,
        "_FLEET_LOCK_ACQUIRE_MAX_ATTEMPTS",
        5,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="Failed to acquire fleet lock"):
        telegram_fleet_module._acquire_fleet_lock(lock_path)

    assert attempts["count"] == 5


def test_acquire_fleet_lock_does_not_unlink_fresh_pending_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "fleet.lock"
    lock_path.write_text("")
    attempts = {"count": 0}
    unlinked: list[Path] = []
    real_unlink = Path.unlink

    def fake_open(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 2:
            lock_path.write_text("4242")
        raise FileExistsError()

    def fake_unlink(self: Path, missing_ok: bool = False):
        if self == lock_path:
            unlinked.append(self)
            return None
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(telegram_fleet_module.os, "open", fake_open)
    monkeypatch.setattr(telegram_fleet_module, "_pid_is_running", lambda pid: pid == 4242)
    monkeypatch.setattr(telegram_fleet_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(Path, "unlink", fake_unlink)

    with pytest.raises(RuntimeError, match="PID 4242"):
        telegram_fleet_module._acquire_fleet_lock(lock_path)

    assert unlinked == []


def test_cmd_start_all_exits_cleanly_when_fleet_already_running(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(bot_cli, "_warn_privacy_for_fleet", lambda _path: None)

    def _fake_asyncio_run(_awaitable) -> None:
        _awaitable.close()
        raise telegram_fleet_module.FleetAlreadyRunningError(
            "Telegram fleet already running (PID 123).",
        )

    monkeypatch.setattr(bot_cli.asyncio, "run", _fake_asyncio_run)

    with pytest.raises(SystemExit) as exc:
        bot_cli._cmd_start_all(
            SimpleNamespace(daemon=False, config=None, server=None),
        )

    assert exc.value.code == 1
    assert "already running" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_stop_bot_removes_from_fleet() -> None:
    fleet = BotFleet(TelegramFleetConfig())

    class _FakeAdapter:
        stopped = False

        async def stop(self) -> None:
            self.stopped = True

    adapter = _FakeAdapter()
    bot = BotInstance(
        name="research",
        token="abc",
        bot_username="research_bot",
        is_default=False,
        adapter=adapter,
    )
    fleet._bots = {"research": bot}
    fleet._refresh_router = lambda: None  # type: ignore[assignment]

    result = await fleet.stop_bot("research")
    assert result is True
    assert "research" not in fleet._bots
    assert adapter.stopped is True
    assert fleet._stop_event.is_set()


@pytest.mark.asyncio
async def test_stop_bot_not_found() -> None:
    fleet = BotFleet(TelegramFleetConfig())
    result = await fleet.stop_bot("nonexistent")
    assert result is False


def test_lookup_reply_to_bot_extracts_name_from_lane_key() -> None:
    """_lookup_reply_to_bot should return the bot name from a tracked message."""
    fleet = BotFleet(TelegramFleetConfig())
    fleet._remember_message_lane(-1001, 42, "-1001:main:research-bot")
    assert fleet._lookup_reply_to_bot(-1001, 42) == "research-bot"


def test_lookup_reply_to_bot_returns_none_for_unknown_message() -> None:
    fleet = BotFleet(TelegramFleetConfig())
    assert fleet._lookup_reply_to_bot(-1001, 999) is None


def test_lookup_reply_to_bot_returns_none_for_none_message_id() -> None:
    fleet = BotFleet(TelegramFleetConfig())
    assert fleet._lookup_reply_to_bot(-1001, None) is None


@pytest.mark.asyncio
async def test_reply_to_bot_message_routes_to_that_bot() -> None:
    """When user replies to scholar's message, scholar should handle it —
    not the default bot."""
    fleet = BotFleet(TelegramFleetConfig())
    chief = BotInstance(
        name="chief", token="a", bot_username="dan_chief_bot", is_default=True,
    )
    scholar = BotInstance(
        name="scholar", token="b", bot_username="dan_scholar_bot",
        projects=["literature"],
    )
    fleet._bots = {"chief": chief, "scholar": scholar}

    fleet._remember_outbound_message(-1001, 100, lane_key="-1001:main:scholar")

    dispatched: list[str] = []

    async def _dispatch(bot: BotInstance, ext_id: str, text: str, ctx: MessageContext) -> None:
        dispatched.append(bot.name)

    fleet._dispatch = _dispatch  # type: ignore[method-assign]

    callback = fleet._make_callback(chief)
    ctx = MessageContext(
        chat_id=-1001,
        message_id=101,
        chat_type="group",
        from_user_id=42,
        reply_to_message_id=100,
    )
    await callback("-1001", "Can you explain more?", ctx)
    await asyncio.sleep(0)

    assert dispatched == ["scholar"]


@pytest.mark.asyncio
async def test_mention_overrides_reply_sticky_in_callback() -> None:
    """Explicit @mention should override reply-sticky routing."""
    fleet = BotFleet(TelegramFleetConfig())
    chief = BotInstance(
        name="chief", token="a", bot_username="dan_chief_bot", is_default=True,
    )
    scholar = BotInstance(
        name="scholar", token="b", bot_username="dan_scholar_bot",
        projects=["literature"],
    )
    fleet._bots = {"chief": chief, "scholar": scholar}

    fleet._remember_outbound_message(-1001, 100, lane_key="-1001:main:scholar")

    dispatched: list[str] = []

    async def _dispatch(bot: BotInstance, ext_id: str, text: str, ctx: MessageContext) -> None:
        dispatched.append(bot.name)

    fleet._dispatch = _dispatch  # type: ignore[method-assign]

    callback = fleet._make_callback(chief)
    ctx = MessageContext(
        chat_id=-1001,
        message_id=102,
        chat_type="group",
        from_user_id=42,
        reply_to_message_id=100,
        mentions=["dan_chief_bot"],
    )
    await callback("-1001", "@dan_chief_bot help me instead", ctx)
    await asyncio.sleep(0)

    assert dispatched == ["chief"]


def test_conversation_thread_key_includes_topic_id() -> None:
    base_ctx = MessageContext(chat_id=-1001, message_id=1, thread_id=None)
    topic_ctx = MessageContext(chat_id=-1001, message_id=2, thread_id=77)

    assert _conversation_thread_key(base_ctx, "research-bot") == "-1001:main:research-bot"
    assert _conversation_thread_key(topic_ctx, "research-bot") == "-1001:77:research-bot"


def test_conversation_lane_key_uses_reply_chains_and_forked_per_message_lanes() -> None:
    dm_ctx = MessageContext(chat_id=123, message_id=7, chat_type="private")
    reply_ctx = MessageContext(
        chat_id=123,
        message_id=8,
        chat_type="private",
        reply_to_message_id=900,
    )
    group_ctx = MessageContext(chat_id=-1001, message_id=3, chat_type="group")
    topic_ctx = MessageContext(chat_id=-1001, message_id=4, chat_type="group", thread_id=77)

    assert _conversation_lane_key(dm_ctx, "dan", fork_for_parallel=True) == "123:main:dan:m7"
    assert (
        _conversation_lane_key(
            reply_ctx,
            "dan",
            reply_lane_key="123:main:dan:m7",
        )
        == "123:main:dan:m7"
    )
    assert _conversation_lane_key(group_ctx, "dan", fork_for_parallel=True) == "-1001:main:dan:m3"
    assert _conversation_lane_key(topic_ctx, "dan", fork_for_parallel=True) == "-1001:77:dan:m4"


def test_latest_outbound_id_prefers_lane_over_chat() -> None:
    fleet = BotFleet(TelegramFleetConfig())
    fleet._remember_outbound_message(-1001, 100, lane_key="-1001:main:dan:m1")
    fleet._remember_outbound_message(-1001, 200, lane_key="-1001:main:dan:m2")

    assert fleet._latest_outbound_id(-1001, lane_key="-1001:main:dan:m1") == 100
    assert fleet._latest_outbound_id(-1001, lane_key="-1001:main:dan:m2") == 200


@pytest.mark.asyncio
async def test_dispatch_rolls_back_user_turn_when_server_request_fails() -> None:
    fleet = BotFleet(TelegramFleetConfig())
    reactions: list[str] = []

    class _FakeAdapter:
        async def set_reaction(self, chat_id: int, message_id: int, emoji: str) -> None:
            reactions.append(emoji)

        async def _send_text(self, chat_id: int, text: str, reply_to=None, thread_id=None) -> None:
            return None

    class _FakeHttp:
        async def post(self, path: str, json: dict | None = None):
            return SimpleNamespace(status_code=500, json=lambda: {})

        async def get(self, path: str):
            return SimpleNamespace(status_code=404)

    bot = BotInstance(
        name="dan",
        token="abc",
        bot_username="dan_bot",
        is_default=True,
        adapter=_FakeAdapter(),
    )
    fleet._http = _FakeHttp()
    fleet._bots = {"dan": bot}
    ctx = MessageContext(chat_id=123, message_id=7, thread_id=88)

    await fleet._dispatch(bot, "123", "hello", ctx)

    key = _conversation_thread_key(ctx, "dan")
    assert fleet._conversation_history.get(key, []) == []
    assert "❌" in reactions


@pytest.mark.asyncio
async def test_dispatch_allows_parallel_private_dm_turns() -> None:
    fleet = BotFleet(TelegramFleetConfig())
    started_messages: list[str] = []
    both_started = asyncio.Event()
    release_posts = asyncio.Event()

    class _FakeAdapter:
        async def set_reaction(self, chat_id: int, message_id: int, emoji: str) -> None:
            return None

        async def _send_text(self, chat_id: int, text: str, reply_to=None, thread_id=None) -> int | None:
            return None

    class _FakeHttp:
        async def get(self, path: str):
            return SimpleNamespace(status_code=404)

        async def post(self, path: str, json: dict | None = None):
            if path == "/api/graphs":
                return SimpleNamespace(status_code=200, json=lambda: {})
            if path == "/api/v2/chat/message":
                started_messages.append(json["message"])
                if len(started_messages) == 2:
                    both_started.set()
                await release_posts.wait()
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"content": f"reply:{json['message']}"},
                )
            raise AssertionError(f"unexpected path: {path}")

    bot = BotInstance(
        name="dan",
        token="abc",
        bot_username="dan_bot",
        is_default=True,
        adapter=_FakeAdapter(),
    )
    fleet._http = _FakeHttp()
    fleet._bots = {"dan": bot}

    ctx1 = MessageContext(chat_id=123, message_id=7, chat_type="private")
    ctx2 = MessageContext(chat_id=123, message_id=8, chat_type="private")

    task1 = asyncio.create_task(fleet._dispatch(bot, "123", "first", ctx1))
    await asyncio.sleep(0)
    task2 = asyncio.create_task(fleet._dispatch(bot, "123", "second", ctx2))

    await asyncio.wait_for(both_started.wait(), timeout=0.2)
    release_posts.set()
    await asyncio.gather(task1, task2)

    assert started_messages == ["first", "second"]


@pytest.mark.asyncio
async def test_dispatch_sends_surface_context_without_system_history() -> None:
    fleet = BotFleet(TelegramFleetConfig())
    captured_body: dict[str, object] = {}

    class _FakeAdapter:
        async def set_reaction(self, chat_id: int, message_id: int, emoji: str) -> None:
            return None

        async def _send_text(self, chat_id: int, text: str, reply_to=None, thread_id=None) -> int:
            return 999

    class _FakeHttp:
        async def get(self, path: str):
            return SimpleNamespace(status_code=404)

        async def post(self, path: str, json: dict | None = None):
            if path == "/api/v2/chat/message":
                captured_body.update(json or {})
                captured_body["path"] = path
                return SimpleNamespace(status_code=200, json=lambda: {"content": "ok"})
            raise AssertionError(f"unexpected path: {path}")

    bot = BotInstance(
        name="scholar",
        token="abc",
        bot_username="dan_scholar_bot",
        personality="scholarly",
        projects=["thesis"],
        adapter=_FakeAdapter(),
    )
    chief = BotInstance(
        name="chief",
        token="def",
        bot_username="dan_chief_bot",
        is_default=True,
    )
    fleet._bots = {"scholar": bot, "chief": chief}
    fleet._http = _FakeHttp()

    async def _fake_ensure_scratch(_conversation_key: str) -> str:
        return "_scratch"

    fleet._ensure_scratch = _fake_ensure_scratch  # type: ignore[method-assign]

    ctx = MessageContext(chat_id=123, message_id=7, chat_type="group")

    await fleet._dispatch(bot, "123", "hello", ctx)

    assert captured_body["path"] == "/api/v2/chat/message"
    assert captured_body["control_plane_mode"] == "v2"
    assert captured_body["history"] == [{"role": "user", "content": "hello"}]
    surface_context = captured_body["surface_context"]
    assert surface_context["identity"] == {
        "name": "scholar",
        "role": "messaging_assistant",
        "username": "dan_scholar_bot",
        "personality": "scholarly",
        "project_focus": ["thesis"],
    }
    assert surface_context["peers"] == [
        {
            "name": "chief",
            "username": "dan_chief_bot",
        },
    ]
    assert surface_context["conversation"]["conversation_key"] == "123:main:scholar"
    assert surface_context["telegram"]["chat_id"] == 123
    assert surface_context["telegram"]["message_id"] == 7
    assert "system" not in {item["role"] for item in captured_body["history"]}


# ---------------------------------------------------------------------------
# _stream_with_edits: progress_ack events should NOT terminate the stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_with_edits_progress_ack_does_not_break(
    monkeypatch,
) -> None:
    """progress_ack events should be edited in place, not treated as terminal."""
    import json as _json

    fleet = BotFleet(TelegramFleetConfig())

    sent: list[tuple[int, str, int | None, int | None]] = []
    call_counter = 0

    class _FakeAdapter:
        async def send_or_edit(
            self, chat_id, text, message_id=None, *, reply_to=None, thread_id=None,
        ):
            nonlocal call_counter
            call_counter += 1
            sent.append((chat_id, text, message_id, reply_to))
            return call_counter * 100

        async def _send_text(self, chat_id, text, reply_to=None, thread_id=None):
            nonlocal call_counter
            call_counter += 1
            sent.append((chat_id, text, None, reply_to))
            return call_counter * 100

    ws_events = [
        _json.dumps({
            "type": "chat_complete",
            "content": "Working on it...",
            "detected_mode": "progress_ack",
        }),
        _json.dumps({
            "type": "chat_complete",
            "content": "The answer is 42.",
        }),
    ]

    class _FakeWS:
        def __aiter__(self):
            return _FakeWSIter(ws_events)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    class _FakeWSIter:
        def __init__(self, items):
            self._it = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    def _fake_connect(*a, **kw):
        return _FakeWS()

    import websockets as _ws_mod
    monkeypatch.setattr(_ws_mod, "connect", _fake_connect)

    bot = BotInstance(
        name="dan", token="t", bot_username="dan_bot",
        is_default=True, adapter=_FakeAdapter(),
    )
    fleet._bots = {"dan": bot}
    fleet._server_url = "http://localhost:8080"
    ctx = MessageContext(chat_id=999, message_id=5)

    result = await fleet._stream_with_edits(bot, ctx, "ch-1")

    assert result == "The answer is 42."
    progress_sends = [(cid, txt, mid, rto) for cid, txt, mid, rto in sent if txt]
    assert len(progress_sends) >= 2
    assert progress_sends[0][1] == "Working on it..."
    assert progress_sends[0][3] == 5  # reply_to original message
    assert progress_sends[1][1] == "The answer is 42."
    assert progress_sends[1][2] == 100  # edited the progress message


@pytest.mark.asyncio
async def test_stream_with_edits_missing_terminal_emits_fallback(
    monkeypatch,
) -> None:
    """A clean stream close without terminal content should still surface a reply."""
    import json as _json

    fleet = BotFleet(TelegramFleetConfig())

    sent: list[tuple[int, str, int | None, int | None]] = []
    call_counter = 0

    class _FakeAdapter:
        async def send_or_edit(
            self, chat_id, text, message_id=None, *, reply_to=None, thread_id=None,
        ):
            nonlocal call_counter
            call_counter += 1
            sent.append((chat_id, text, message_id, reply_to))
            return call_counter * 100

        async def _send_text(self, chat_id, text, reply_to=None, thread_id=None):
            nonlocal call_counter
            call_counter += 1
            sent.append((chat_id, text, None, reply_to))
            return call_counter * 100

    ws_events = [
        _json.dumps({
            "type": "chat_complete",
            "content": "Understanding your request",
            "detected_mode": "progress_ack",
        }),
    ]

    class _FakeWS:
        def __aiter__(self):
            return _FakeWSIter(ws_events)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _FakeWSIter:
        def __init__(self, items):
            self._it = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    def _fake_connect(*a, **kw):
        return _FakeWS()

    import websockets as _ws_mod
    monkeypatch.setattr(_ws_mod, "connect", _fake_connect)

    bot = BotInstance(
        name="dan", token="t", bot_username="dan_bot",
        is_default=True, adapter=_FakeAdapter(),
    )
    fleet._bots = {"dan": bot}
    fleet._server_url = "http://localhost:8080"
    ctx = MessageContext(chat_id=999, message_id=5)

    result = await fleet._stream_with_edits(bot, ctx, "ch-1")

    assert "Please ask me to continue from the latest progress." in result
    assert sent[0][1] == "Understanding your request"
    assert sent[0][3] == 5
    assert sent[-1][2] == 100
    assert "Please ask me to continue from the latest progress." in sent[-1][1]


@pytest.mark.asyncio
async def test_stream_with_edits_adapter_timer_fires_on_slow_reply(
    monkeypatch,
) -> None:
    """When the server takes longer than _PROGRESS_INITIAL_DELAY, the adapter
    (not the concierge) shows a progress bubble with elapsed time."""
    import json as _json

    fleet = BotFleet(TelegramFleetConfig())
    monkeypatch.setattr(BotFleet, "_PROGRESS_INITIAL_DELAY", 0.01)
    monkeypatch.setattr(BotFleet, "_PROGRESS_REPEAT_INTERVAL", 0.02)

    sent: list[tuple[int, str, int | None, int | None]] = []
    call_counter = 0

    class _FakeAdapter:
        async def send_or_edit(
            self, chat_id, text, message_id=None, *, reply_to=None, thread_id=None,
        ):
            nonlocal call_counter
            call_counter += 1
            sent.append((chat_id, text, message_id, reply_to))
            return call_counter * 100

        async def _send_text(self, chat_id, text, reply_to=None, thread_id=None):
            nonlocal call_counter
            call_counter += 1
            sent.append((chat_id, text, None, reply_to))
            return call_counter * 100

    class _SlowWSIter:
        def __init__(self):
            self._delivered = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._delivered:
                await asyncio.sleep(0.04)
                self._delivered = True
                return _json.dumps({
                    "type": "chat_complete",
                    "content": "The slow answer.",
                })
            raise StopAsyncIteration

    class _FakeWS:
        def __aiter__(self):
            return _SlowWSIter()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    def _fake_connect(*a, **kw):
        return _FakeWS()

    import websockets as _ws_mod
    monkeypatch.setattr(_ws_mod, "connect", _fake_connect)

    bot = BotInstance(
        name="dan", token="t", bot_username="dan_bot",
        is_default=True, adapter=_FakeAdapter(),
    )
    fleet._bots = {"dan": bot}
    fleet._server_url = "http://localhost:8080"
    ctx = MessageContext(chat_id=999, message_id=5)

    result = await fleet._stream_with_edits(bot, ctx, "ch-1")

    assert result == "The slow answer."
    progress_texts = [txt for _cid, txt, _mid, _rto in sent if "elapsed" in txt.lower()]
    assert progress_texts, "Adapter should show progress with elapsed time for slow replies"
    assert "Working on it" in progress_texts[0]


@pytest.mark.asyncio
async def test_stream_with_edits_fast_reply_no_progress(
    monkeypatch,
) -> None:
    """Fast replies should produce no progress bubble at all."""
    import json as _json

    fleet = BotFleet(TelegramFleetConfig())
    monkeypatch.setattr(BotFleet, "_PROGRESS_INITIAL_DELAY", 5.0)

    sent: list[tuple[int, str, int | None, int | None]] = []
    call_counter = 0

    class _FakeAdapter:
        async def send_or_edit(
            self, chat_id, text, message_id=None, *, reply_to=None, thread_id=None,
        ):
            nonlocal call_counter
            call_counter += 1
            sent.append((chat_id, text, message_id, reply_to))
            return call_counter * 100

        async def _send_text(self, chat_id, text, reply_to=None, thread_id=None):
            nonlocal call_counter
            call_counter += 1
            sent.append((chat_id, text, None, reply_to))
            return call_counter * 100

    ws_events = [
        _json.dumps({
            "type": "chat_complete",
            "content": "Hi there!",
        }),
    ]

    class _FakeWSIter:
        def __init__(self, items):
            self._it = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class _FakeWS:
        def __aiter__(self):
            return _FakeWSIter(ws_events)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    def _fake_connect(*a, **kw):
        return _FakeWS()

    import websockets as _ws_mod
    monkeypatch.setattr(_ws_mod, "connect", _fake_connect)

    bot = BotInstance(
        name="dan", token="t", bot_username="dan_bot",
        is_default=True, adapter=_FakeAdapter(),
    )
    fleet._bots = {"dan": bot}
    fleet._server_url = "http://localhost:8080"
    ctx = MessageContext(chat_id=999, message_id=5)

    result = await fleet._stream_with_edits(bot, ctx, "ch-1")

    assert result == "Hi there!"
    progress_texts = [txt for _cid, txt, _mid, _rto in sent if "Working on it" in txt]
    assert not progress_texts, "Fast replies must NOT show progress"


@pytest.mark.asyncio
async def test_stream_with_edits_attachment_only_replaces_progress_bubble(
    monkeypatch,
) -> None:
    import json as _json

    fleet = BotFleet(TelegramFleetConfig())
    monkeypatch.setattr(BotFleet, "_PROGRESS_INITIAL_DELAY", 0.01)
    monkeypatch.setattr(BotFleet, "_PROGRESS_REPEAT_INTERVAL", 0.02)

    sent: list[tuple[int, str, int | None, int | None]] = []
    sent_files: list[str] = []
    call_counter = 0

    class _FakeAdapter:
        async def send_or_edit(
            self, chat_id, text, message_id=None, *, reply_to=None, thread_id=None,
        ):
            nonlocal call_counter
            call_counter += 1
            sent.append((chat_id, text, message_id, reply_to))
            return message_id or (call_counter * 100)

        async def _send_file_to_chat(self, chat_id, path, *, thread_id=None):
            sent_files.append(path)
            return 777

    class _AttachmentOnlyIter:
        def __init__(self):
            self._events = [
                _json.dumps({
                    "type": "chat_file_attachment",
                    "path": "/tmp/report.pdf",
                }),
                _json.dumps({
                    "type": "chat_complete",
                    "content": "",
                }),
            ]
            self._idx = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._idx == 0:
                await asyncio.sleep(0.04)
            if self._idx >= len(self._events):
                raise StopAsyncIteration
            event = self._events[self._idx]
            self._idx += 1
            return event

    class _FakeWS:
        def __aiter__(self):
            return _AttachmentOnlyIter()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def _fake_connect(*a, **kw):
        return _FakeWS()

    import websockets as _ws_mod

    monkeypatch.setattr(_ws_mod, "connect", _fake_connect)

    bot = BotInstance(
        name="dan", token="t", bot_username="dan_bot",
        is_default=True, adapter=_FakeAdapter(),
    )
    fleet._bots = {"dan": bot}
    fleet._server_url = "http://localhost:8080"
    ctx = MessageContext(chat_id=999, message_id=5)

    result = await fleet._stream_with_edits(bot, ctx, "ch-1")

    assert result == "Delivered the requested file."
    assert sent_files == ["/tmp/report.pdf"]
    assert sent[0][1].startswith("Working on it")
    assert sent[-1][1] == "Delivered the requested file."
    assert sent[-1][2] == 100


@pytest.mark.asyncio
async def test_stream_with_edits_poll_only_replaces_progress_bubble(
    monkeypatch,
) -> None:
    import json as _json

    fleet = BotFleet(TelegramFleetConfig())
    monkeypatch.setattr(BotFleet, "_PROGRESS_INITIAL_DELAY", 0.01)
    monkeypatch.setattr(BotFleet, "_PROGRESS_REPEAT_INTERVAL", 0.02)

    sent: list[tuple[int, str, int | None, int | None]] = []
    sent_polls: list[tuple[str, list[str], int | None]] = []
    call_counter = 0

    class _FakeAdapter:
        async def send_or_edit(
            self, chat_id, text, message_id=None, *, reply_to=None, thread_id=None,
        ):
            nonlocal call_counter
            call_counter += 1
            sent.append((chat_id, text, message_id, reply_to))
            return message_id or (call_counter * 100)

        async def send_poll(
            self,
            chat_id,
            question,
            options,
            *,
            is_anonymous=False,
            allows_multiple=False,
            reply_to=None,
            thread_id=None,
        ):
            sent_polls.append((question, options, reply_to))
            return "poll-1"

    class _PollOnlyIter:
        def __init__(self):
            self._events = [
                _json.dumps({
                    "type": "chat_poll_request",
                    "question": "Pick one",
                    "options": ["A", "B"],
                }),
                _json.dumps({
                    "type": "chat_complete",
                    "content": "",
                }),
            ]
            self._idx = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._idx == 0:
                await asyncio.sleep(0.04)
            if self._idx >= len(self._events):
                raise StopAsyncIteration
            event = self._events[self._idx]
            self._idx += 1
            return event

    class _FakeWS:
        def __aiter__(self):
            return _PollOnlyIter()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def _fake_connect(*a, **kw):
        return _FakeWS()

    import websockets as _ws_mod

    monkeypatch.setattr(_ws_mod, "connect", _fake_connect)

    bot = BotInstance(
        name="dan", token="t", bot_username="dan_bot",
        is_default=True, adapter=_FakeAdapter(),
    )
    fleet._bots = {"dan": bot}
    fleet._server_url = "http://localhost:8080"
    ctx = MessageContext(chat_id=999, message_id=5)

    result = await fleet._stream_with_edits(bot, ctx, "ch-1")

    assert result == "Sent the requested poll."
    assert sent_polls == [("Pick one", ["A", "B"], 5)]
    assert sent[0][1].startswith("Working on it")
    assert sent[-1][1] == "Sent the requested poll."
    assert sent[-1][2] == 100


@pytest.mark.asyncio
async def test_stream_with_edits_poll_failure_surfaces_terminal_error(
    monkeypatch,
) -> None:
    import json as _json

    fleet = BotFleet(TelegramFleetConfig())
    monkeypatch.setattr(BotFleet, "_PROGRESS_INITIAL_DELAY", 0.01)
    monkeypatch.setattr(BotFleet, "_PROGRESS_REPEAT_INTERVAL", 0.02)

    sent: list[tuple[int, str, int | None, int | None]] = []
    call_counter = 0

    class _FakeAdapter:
        async def send_or_edit(
            self, chat_id, text, message_id=None, *, reply_to=None, thread_id=None,
        ):
            nonlocal call_counter
            call_counter += 1
            sent.append((chat_id, text, message_id, reply_to))
            return message_id or (call_counter * 100)

        async def send_poll(
            self,
            chat_id,
            question,
            options,
            *,
            is_anonymous=False,
            allows_multiple=False,
            reply_to=None,
            thread_id=None,
        ):
            return None

    class _PollOnlyIter:
        def __init__(self):
            self._events = [
                _json.dumps({
                    "type": "chat_poll_request",
                    "question": "Pick one",
                    "options": ["A", "B"],
                }),
                _json.dumps({
                    "type": "chat_complete",
                    "content": "",
                }),
            ]
            self._idx = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._idx == 0:
                await asyncio.sleep(0.04)
            if self._idx >= len(self._events):
                raise StopAsyncIteration
            event = self._events[self._idx]
            self._idx += 1
            return event

    class _FakeWS:
        def __aiter__(self):
            return _PollOnlyIter()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def _fake_connect(*a, **kw):
        return _FakeWS()

    import websockets as _ws_mod

    monkeypatch.setattr(_ws_mod, "connect", _fake_connect)

    bot = BotInstance(
        name="dan", token="t", bot_username="dan_bot",
        is_default=True, adapter=_FakeAdapter(),
    )
    fleet._bots = {"dan": bot}
    fleet._server_url = "http://localhost:8080"
    ctx = MessageContext(chat_id=999, message_id=5)

    result = await fleet._stream_with_edits(bot, ctx, "ch-1")

    assert result == "I couldn't send the requested poll."
    assert sent[0][1].startswith("Working on it")
    assert sent[-1][1] == "I couldn't send the requested poll."
    assert sent[-1][2] == 100


@pytest.mark.asyncio
async def test_stream_with_edits_fast_poll_failure_still_emits_error(
    monkeypatch,
) -> None:
    import json as _json

    fleet = BotFleet(TelegramFleetConfig())
    monkeypatch.setattr(BotFleet, "_PROGRESS_INITIAL_DELAY", 5.0)

    sent: list[tuple[int, str, int | None]] = []

    class _FakeAdapter:
        async def send_or_edit(
            self, chat_id, text, message_id=None, *, reply_to=None, thread_id=None,
        ):
            sent.append((chat_id, text, message_id))
            return message_id or 777

        async def _send_text(self, chat_id, text, reply_to=None, thread_id=None):
            sent.append((chat_id, text, reply_to))
            return 777

        async def send_poll(
            self,
            chat_id,
            question,
            options,
            *,
            is_anonymous=False,
            allows_multiple=False,
            reply_to=None,
            thread_id=None,
        ):
            return None

    ws_events = [
        _json.dumps({
            "type": "chat_poll_request",
            "question": "Pick one",
            "options": ["A", "B"],
        }),
        _json.dumps({
            "type": "chat_complete",
            "content": "",
        }),
    ]

    class _FakeWSIter:
        def __init__(self, items):
            self._it = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class _FakeWS:
        def __aiter__(self):
            return _FakeWSIter(ws_events)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def _fake_connect(*a, **kw):
        return _FakeWS()

    import websockets as _ws_mod

    monkeypatch.setattr(_ws_mod, "connect", _fake_connect)

    bot = BotInstance(
        name="dan", token="t", bot_username="dan_bot",
        is_default=True, adapter=_FakeAdapter(),
    )
    fleet._bots = {"dan": bot}
    fleet._server_url = "http://localhost:8080"
    ctx = MessageContext(chat_id=999, message_id=5)

    result = await fleet._stream_with_edits(bot, ctx, "ch-1")

    assert result == "I couldn't send the requested poll."
    assert sent == [(999, "I couldn't send the requested poll.", 5)]


@pytest.mark.asyncio
async def test_stream_collect_progress_ack_does_not_break(
    monkeypatch,
) -> None:
    import json as _json

    fleet = BotFleet(TelegramFleetConfig())
    sent: list[tuple[int, str, int | None]] = []

    class _FakeAdapter:
        async def _send_text(self, chat_id, text, reply_to=None, thread_id=None):
            sent.append((chat_id, text, reply_to))
            return 777

        async def pin_message(self, chat_id, message_id):
            return True

        _last_bot_message_id = {}

    ws_events = [
        _json.dumps({
            "type": "chat_complete",
            "content": "Got it. what's the stock price of AAPL right now?",
            "detected_mode": "progress_ack",
        }),
        _json.dumps({
            "type": "chat_complete",
            "content": "AAPL is trading at $123.45 right now.",
        }),
    ]

    class _FakeWSIter:
        def __init__(self, items):
            self._it = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class _FakeWS:
        def __aiter__(self):
            return _FakeWSIter(ws_events)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def _fake_connect(*args, **kwargs):
        return _FakeWS()

    import websockets as _ws_mod

    monkeypatch.setattr(_ws_mod, "connect", _fake_connect)

    bot = BotInstance(
        name="dan",
        token="abc",
        bot_username="dan_bot",
        adapter=_FakeAdapter(),
    )
    ctx = MessageContext(chat_id=999, message_id=5)

    result = await fleet._stream_collect(bot, ctx, "ch-1")

    assert result == "AAPL is trading at $123.45 right now."
    assert sent == [(999, "AAPL is trading at $123.45 right now.", 5)]


@pytest.mark.asyncio
async def test_stream_with_edits_follows_redirected_queued_channel(
    monkeypatch,
) -> None:
    import json as _json

    fleet = BotFleet(TelegramFleetConfig())
    sent: list[tuple[int, str, int | None, int | None]] = []
    call_counter = 0

    class _FakeAdapter:
        async def send_or_edit(
            self, chat_id, text, message_id=None, *, reply_to=None, thread_id=None,
        ):
            nonlocal call_counter
            call_counter += 1
            sent.append((chat_id, text, message_id, reply_to))
            return call_counter * 100

        async def _send_text(self, chat_id, text, reply_to=None, thread_id=None):
            nonlocal call_counter
            call_counter += 1
            sent.append((chat_id, text, None, reply_to))
            return call_counter * 100

    ws_events_by_channel = {
        "ch-1": [
            _json.dumps({
                "type": "chat_queued",
                "stream_channel_id": "chat-q1",
                "queue_position": 2,
            }),
        ],
        "chat-q1": [
            _json.dumps({
                "type": "chat_complete",
                "content": "Queued answer is ready.",
            }),
        ],
    }

    class _FakeWSIter:
        def __init__(self, items):
            self._it = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class _FakeWS:
        def __init__(self, items):
            self._items = items

        def __aiter__(self):
            return _FakeWSIter(self._items)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def _fake_connect(url, *a, **kw):
        channel = url.split("/api/chat/", 1)[1].split("/events", 1)[0]
        return _FakeWS(ws_events_by_channel[channel])

    import websockets as _ws_mod

    monkeypatch.setattr(_ws_mod, "connect", _fake_connect)

    bot = BotInstance(
        name="dan", token="t", bot_username="dan_bot",
        is_default=True, adapter=_FakeAdapter(),
    )
    fleet._bots = {"dan": bot}
    fleet._server_url = "http://localhost:8080"
    ctx = MessageContext(chat_id=999, message_id=5)

    result = await fleet._stream_with_edits(bot, ctx, "ch-1")

    assert result == "Queued answer is ready."
    assert sent[0][1].startswith("Queued (position 2)")
    assert sent[0][3] == 5
    assert any(
        text == "Queued answer is ready." and message_id == 100
        for _chat_id, text, message_id, _reply_to in sent
    )
    assert any(text == "Queued answer is ready." for _chat_id, text, _message_id, _reply_to in sent)


def test_format_queue_hint_text() -> None:
    """_format_queue_hint produces standardized queue position copy."""
    fleet = BotFleet(TelegramFleetConfig())
    hint = fleet._format_queue_hint(2, 12.0, hint_count=0)
    assert "queued (position 2)" in hint.lower()
    assert "i'll reply when ready" in hint.lower()

    repeat = fleet._format_queue_hint(1, 35.0, hint_count=1)
    assert "queued (position 1)" in repeat.lower()
    assert "i'll reply when ready" in repeat.lower()


@pytest.mark.asyncio
async def test_iter_chat_stream_events_emits_queue_hint_on_timeout(
    monkeypatch,
) -> None:
    """_iter_chat_stream_events should yield a progress_ack queue hint when
    the redirected channel takes longer than _QUEUE_HINT_INITIAL_DELAY."""
    import json as _json

    fleet = BotFleet(TelegramFleetConfig())
    monkeypatch.setattr(BotFleet, "_QUEUE_HINT_INITIAL_DELAY", 0.01)
    monkeypatch.setattr(BotFleet, "_QUEUE_HINT_REPEAT_INTERVAL", 0.02)

    queued = _json.dumps({
        "type": "chat_queued",
        "stream_channel_id": "chat-q1",
        "queue_position": 2,
    })
    complete = _json.dumps({
        "type": "chat_complete",
        "content": "Queued answer is ready.",
    })

    _ConnectionClosed = type("ConnectionClosedOK", (Exception,), {})

    class _FakeWS:
        def __init__(self, channel: str):
            self._channel = channel
            self._delivered_queued = False
            self._slow_attempted = False
            self._delivered_complete = False

        async def recv(self):
            if self._channel == "ch-1":
                if not self._delivered_queued:
                    self._delivered_queued = True
                    return queued
                raise _ConnectionClosed()
            if self._delivered_complete:
                raise _ConnectionClosed()
            if not self._slow_attempted:
                self._slow_attempted = True
                await asyncio.sleep(0.04)
            self._delivered_complete = True
            return complete

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def _fake_connect(url, *a, **kw):
        channel = url.split("/api/chat/", 1)[1].split("/events", 1)[0]
        return _FakeWS(channel)

    import websockets as _ws_mod
    monkeypatch.setattr(_ws_mod, "connect", _fake_connect)

    fleet._server_url = "http://localhost:8080"
    events: list[dict] = []
    async for ev in fleet._iter_chat_stream_events("ch-1"):
        events.append(ev)

    hints = [e for e in events if e.get("detected_mode") == "progress_ack"]
    terminals = [e for e in events if e.get("detected_mode") != "progress_ack"]

    assert hints, "Should emit at least one queue hint before the answer"
    assert "queued (position 2)" in hints[0]["content"].lower()
    assert "i'll reply when ready" in hints[0]["content"].lower()
    assert terminals[-1]["content"] == "Queued answer is ready."


@pytest.mark.asyncio
async def test_iter_chat_stream_events_emits_immediate_queue_hint_on_redirect(
    monkeypatch,
) -> None:
    import json as _json

    fleet = BotFleet(TelegramFleetConfig())

    queued = _json.dumps({
        "type": "chat_queued",
        "stream_channel_id": "chat-q1",
        "queue_position": 2,
    })
    complete = _json.dumps({
        "type": "chat_complete",
        "content": "Queued answer is ready.",
    })

    class _FakeWSIter:
        def __init__(self, items):
            self._it = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class _FakeWS:
        def __init__(self, items):
            self._items = items

        def __aiter__(self):
            return _FakeWSIter(self._items)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    ws_events_by_channel = {
        "ch-1": [queued],
        "chat-q1": [complete],
    }

    def _fake_connect(url, *a, **kw):
        channel = url.split("/api/chat/", 1)[1].split("/events", 1)[0]
        return _FakeWS(ws_events_by_channel[channel])

    import websockets as _ws_mod
    monkeypatch.setattr(_ws_mod, "connect", _fake_connect)

    fleet._server_url = "http://localhost:8080"
    events: list[dict[str, Any]] = []
    async for ev in fleet._iter_chat_stream_events("ch-1"):
        events.append(ev)

    assert events[0]["detected_mode"] == "progress_ack"
    assert "queued (position 2)" in events[0]["content"].lower()
    assert events[-1]["content"] == "Queued answer is ready."


@pytest.mark.asyncio
async def test_stream_collect_follows_redirected_queued_channel(
    monkeypatch,
) -> None:
    import json as _json

    fleet = BotFleet(TelegramFleetConfig())
    sent: list[tuple[int, str, int | None]] = []

    class _FakeAdapter:
        async def _send_text(self, chat_id, text, reply_to=None, thread_id=None):
            sent.append((chat_id, text, reply_to))
            return 777

        async def pin_message(self, chat_id, message_id):
            return True

        _last_bot_message_id = {}

    ws_events_by_channel = {
        "ch-1": [
            _json.dumps({
                "type": "chat_queued",
                "stream_channel_id": "chat-q1",
                "queue_position": 2,
            }),
        ],
        "chat-q1": [
            _json.dumps({
                "type": "chat_complete",
                "content": "Queued answer is ready.",
            }),
        ],
    }

    class _FakeWSIter:
        def __init__(self, items):
            self._it = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class _FakeWS:
        def __init__(self, items):
            self._items = items

        def __aiter__(self):
            return _FakeWSIter(self._items)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def _fake_connect(url, *args, **kwargs):
        channel = url.split("/api/chat/", 1)[1].split("/events", 1)[0]
        return _FakeWS(ws_events_by_channel[channel])

    import websockets as _ws_mod

    monkeypatch.setattr(_ws_mod, "connect", _fake_connect)

    bot = BotInstance(
        name="dan",
        token="abc",
        bot_username="dan_bot",
        adapter=_FakeAdapter(),
    )
    ctx = MessageContext(chat_id=999, message_id=5)

    result = await fleet._stream_collect(bot, ctx, "ch-1")

    assert result == "Queued answer is ready."
    assert sent == [(999, "Queued answer is ready.", 5)]


@pytest.mark.asyncio
async def test_ensure_scratch_does_not_cache_failed_workflow_create() -> None:
    fleet = BotFleet(TelegramFleetConfig())
    fleet._http = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status_code=404)),
        post=AsyncMock(return_value=SimpleNamespace(status_code=500)),
    )

    with pytest.raises(RuntimeError, match="Unable to initialize workflow"):
        await fleet._ensure_scratch("chat:main:dan")

    assert "chat:main:dan" not in fleet._conversation_workflows
