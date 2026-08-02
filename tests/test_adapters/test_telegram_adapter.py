"""Tests for Telegram adapter helpers and media handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from dan.adapters import telegram_adapter as ta
from dan.server.concierge.command_registry import CommandDescriptor, CommandRegistry
from dan.adapters.telegram_adapter import (
    TelegramAdapter,
    TelegramAdapterConfig,
    _merge_command_pairs,
    _escape_markdown_v2,
    _split_message,
    verify_webapp_init_data,
)


class _FakeBot:
    def __init__(self) -> None:
        self.sent_photo = []
        self.sent_document = []
        self.sent_message = []
        self.command_sets = []
        self.reaction_calls = []

    async def send_photo(self, **kwargs):
        self.sent_photo.append(kwargs)
        return SimpleNamespace(message_id=101)

    async def send_document(self, **kwargs):
        self.sent_document.append(kwargs)
        return SimpleNamespace(message_id=202)

    async def send_message(self, **kwargs):
        self.sent_message.append(kwargs)
        return SimpleNamespace(message_id=303)

    async def set_my_commands(self, commands, scope=None):
        self.command_sets.append((commands, scope))

    async def set_message_reaction(self, **kwargs):
        self.reaction_calls.append(kwargs)


@dataclass
class _FakeFile:
    file_unique_id: str = "abc123"
    file_path: str = "photo.jpg"
    file_size: int = 1024

    async def download_to_drive(self, path: str) -> None:
        Path(path).write_bytes(b"image-bytes")


class _FakePhotoSize:
    async def get_file(self):
        return _FakeFile()


class _FakeMessage:
    def __init__(self) -> None:
        self.chat_id = 123
        self.message_id = 7
        self.text = None
        self.caption = "look at this"
        self.photo = [_FakePhotoSize()]
        self.document = None
        self.voice = None
        self.audio = None
        self.video = None
        self.video_note = None
        self.sticker = None
        self.contact = None
        self.location = None
        self.entities = []
        self.caption_entities = []
        self.reply_to_message = None
        self.from_user = SimpleNamespace(id=42, is_bot=False, username="alice")
        self.chat = SimpleNamespace(type="private")

    async def reply_text(self, _text: str) -> None:
        return None


def test_escape_markdown_v2_preserves_code_blocks() -> None:
    text = "Hello (world)\n```python\nx = 1 + 2\n```"
    escaped = _escape_markdown_v2(text)
    assert r"\(" in escaped
    assert "```python\nx = 1 + 2\n```" in escaped


def test_split_message_respects_limit() -> None:
    parts = _split_message("a" * 5000, max_len=4096)
    assert len(parts) == 2
    assert sum(len(part) for part in parts) == 5000


def test_build_reply_prefix_includes_chain() -> None:
    adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
    msg3 = SimpleNamespace(text="original", caption=None, reply_to_message=None)
    msg2 = SimpleNamespace(text="middle", caption=None, reply_to_message=msg3)
    msg1 = SimpleNamespace(text="latest", caption=None, reply_to_message=msg2)

    prefix = adapter._build_reply_prefix(SimpleNamespace(reply_to_message=msg1))
    assert '[Replying to: "original"]' in prefix
    assert '[Replying to: "middle"]' in prefix
    assert '[Replying to: "latest"]' in prefix


@pytest.mark.asyncio
async def test_on_text_message_forwards_clean_text_but_keeps_reply_metadata() -> None:
    adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
    captured: list[tuple[str, str, object]] = []

    async def _callback(session_id: str, text: str, ctx) -> None:
        captured.append((session_id, text, ctx))

    adapter.set_message_callback(_callback, with_context=True)

    msg = _FakeMessage()
    msg.photo = None
    msg.caption = None
    msg.text = (
        "@dan_scholar_bot can you help me create a new project folder under "
        "Dropbox/CUHK-phd/projects for this?"
    )
    msg.reply_to_message = SimpleNamespace(
        text="what assumptions do you have",
        caption=None,
        reply_to_message=None,
        message_id=6,
    )
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        message=msg,
    )

    await adapter._on_text_message(update, None)

    assert len(captured) == 1
    session_id, text, ctx = captured[0]
    assert session_id == "123"
    assert text == msg.text
    assert '[Replying to: "' not in text
    assert ctx.reply_to_text == "what assumptions do you have"


def test_build_context_extracts_mentions_from_caption() -> None:
    adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
    msg = _FakeMessage()
    msg.photo = None
    msg.caption = "@data_bot please check this"
    msg.caption_entities = [SimpleNamespace(type="mention", offset=0, length=9)]

    ctx = adapter._build_context(msg)
    assert ctx.mentions == ["data_bot"]


def test_build_context_captures_sender_chat_metadata() -> None:
    adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
    msg = _FakeMessage()
    msg.photo = None
    msg.text = "hello"
    msg.from_user = None
    msg.sender_chat = SimpleNamespace(id=-1001, username="dan_chief_bot")

    ctx = adapter._build_context(msg)

    assert ctx.from_user_id is None
    assert ctx.from_user_username is None
    assert ctx.sender_chat_id == -1001
    assert ctx.sender_chat_username == "dan_chief_bot"


def test_build_context_generates_same_ingress_dedup_key_for_group_copies() -> None:
    adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
    msg_a = _FakeMessage()
    msg_a.photo = None
    msg_a.text = "Hello"
    msg_a.caption = None
    msg_a.message_id = 18
    msg_a.date = datetime(2026, 3, 10, 15, 38, 17, tzinfo=timezone.utc)
    msg_a.chat = SimpleNamespace(type="group")

    msg_b = _FakeMessage()
    msg_b.photo = None
    msg_b.text = "Hello"
    msg_b.caption = None
    msg_b.message_id = 48
    msg_b.date = datetime(2026, 3, 10, 15, 38, 17, tzinfo=timezone.utc)
    msg_b.chat = SimpleNamespace(type="group")

    ctx_a = adapter._build_context(msg_a)
    ctx_b = adapter._build_context(msg_b)

    assert ctx_a.ingress_dedup_key is not None
    assert ctx_a.ingress_dedup_key == ctx_b.ingress_dedup_key


def test_verify_webapp_init_data_returns_false_without_hash() -> None:
    assert verify_webapp_init_data("query_id=123&user=test", "bot-token") is False


def test_merge_command_pairs_preserves_defaults() -> None:
    merged = _merge_command_pairs(
        [("help", "Show available commands"), ("find", "Find a file")],
        [("search", "Search literature")],
    )
    commands = [cmd for cmd, _desc in merged]
    assert "help" in commands
    assert "find" in commands
    assert "search" in commands


def test_telegram_commands_filter_invalid_names() -> None:
    registry = CommandRegistry()
    registry.register(CommandDescriptor(
        name="/help",
        kind="chat",
        surfaces=["telegram"],
        help_text="Help",
    ))
    registry.register(CommandDescriptor(
        name="/goal-status",
        kind="chat",
        surfaces=["telegram"],
        help_text="Goal status",
    ))
    registry.register(CommandDescriptor(
        name="/follow_ups",
        kind="chat",
        surfaces=["telegram"],
        help_text="Follow ups",
    ))

    commands = registry.telegram_commands()

    assert ("help", "Help") in commands
    assert ("follow_ups", "Follow ups") in commands
    assert all(name != "goal-status" for name, _desc in commands)


@pytest.mark.asyncio
async def test_send_file_uses_photo_for_small_images(tmp_path: Path) -> None:
    image = tmp_path / "plot.png"
    image.write_bytes(b"png-bytes")

    adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
    fake_bot = _FakeBot()
    adapter._application = SimpleNamespace(bot=fake_bot)

    message_id = await adapter._send_file_to_chat(123, str(image))
    assert message_id == 101
    assert len(fake_bot.sent_photo) == 1
    assert adapter._last_bot_message_id[123] == 101


@pytest.mark.asyncio
async def test_media_message_downloads_photo_and_forwards_attachment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ta, "_MEDIA_DIR", tmp_path / "telegram-media")

    adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
    captured: list[tuple[str, str, object]] = []

    async def _callback(session_id: str, text: str, ctx) -> None:
        captured.append((session_id, text, ctx))

    adapter.set_message_callback(_callback, with_context=True)

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        message=_FakeMessage(),
    )

    await adapter._on_media_message(update, None)

    assert len(captured) == 1
    session_id, text, ctx = captured[0]
    assert session_id == "123"
    assert text.startswith("[Attachment: ")
    assert "\nlook at this" in text
    assert ctx.chat_id == 123


@pytest.mark.asyncio
async def test_media_message_keeps_reply_metadata_without_prefixing_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ta, "_MEDIA_DIR", tmp_path / "telegram-media")

    adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
    captured: list[tuple[str, str, object]] = []

    async def _callback(session_id: str, text: str, ctx) -> None:
        captured.append((session_id, text, ctx))

    adapter.set_message_callback(_callback, with_context=True)

    msg = _FakeMessage()
    msg.reply_to_message = SimpleNamespace(
        text="historical unrelated question",
        caption=None,
        reply_to_message=None,
        message_id=6,
    )
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        message=msg,
    )

    await adapter._on_media_message(update, None)

    assert len(captured) == 1
    _session_id, text, ctx = captured[0]
    assert text.startswith("[Attachment: ")
    assert '[Replying to: "' not in text
    assert ctx.reply_to_text == "historical unrelated question"


@pytest.mark.asyncio
async def test_send_text_retries_without_reply_to_when_parent_message_missing() -> None:
    class _ReplyFailBot(_FakeBot):
        async def send_message(self, **kwargs):
            self.sent_message.append(kwargs)
            if kwargs.get("reply_to_message_id") == 7:
                raise Exception("Message to be replied not found")
            return SimpleNamespace(message_id=404)

    adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
    fake_bot = _ReplyFailBot()
    adapter._application = SimpleNamespace(bot=fake_bot)

    message_id = await adapter._send_text(123, "hello", reply_to=7)

    assert message_id == 404
    assert len(fake_bot.sent_message) == 2
    assert fake_bot.sent_message[0]["reply_to_message_id"] == 7
    assert "reply_to_message_id" not in fake_bot.sent_message[1]


@pytest.mark.asyncio
async def test_set_reaction_disables_future_attempts_after_bad_request() -> None:
    class BadRequest(Exception):
        pass

    class _ReactionFailBot(_FakeBot):
        async def set_message_reaction(self, **kwargs):
            self.reaction_calls.append(kwargs)
            raise BadRequest("reactions are not supported here")

    adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
    fake_bot = _ReactionFailBot()
    adapter._application = SimpleNamespace(bot=fake_bot)

    await adapter.set_reaction(123, 7, "✅")
    await adapter.set_reaction(123, 8, "❌")

    assert len(fake_bot.reaction_calls) == 1


@pytest.mark.asyncio
async def test_send_or_edit_retries_without_reply_to_when_parent_message_missing() -> None:
    class _ReplyFailBot(_FakeBot):
        async def send_message(self, **kwargs):
            self.sent_message.append(kwargs)
            if kwargs.get("reply_to_message_id") == 7:
                raise Exception("Message to be replied not found")
            return SimpleNamespace(message_id=505)

    adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
    fake_bot = _ReplyFailBot()
    adapter._application = SimpleNamespace(bot=fake_bot)

    message_id = await adapter.send_or_edit(123, "hello", reply_to=7)

    assert message_id == 505
    assert len(fake_bot.sent_message) == 2
    assert fake_bot.sent_message[0]["reply_to_message_id"] == 7
    assert "reply_to_message_id" not in fake_bot.sent_message[1]
