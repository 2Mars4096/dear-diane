"""Telegram messaging adapter — ``python-telegram-bot`` backend.

Full-featured adapter supporting media handling, reactions, streaming edits,
forum topics, polls, inline keyboards, pinned messages, and bot commands.

Requires ``python-telegram-bot`` (``pip install 'dan[messaging]'``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import mimetypes
import os
import time
from urllib.parse import parse_qsl
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import Field

from dan.adapters.base import AdapterConfig

logger = logging.getLogger(__name__)

_TELEGRAM_MAX_MESSAGE_LENGTH = 4096
_PROGRESS_THROTTLE_SECONDS = 5.0
_MEDIA_DIR = Path.home() / ".dan" / "telegram" / "media"
_MEDIA_CLEANUP_SECONDS = 3600


@dataclass
class MessageContext:
    """Metadata from an incoming Telegram message for routing decisions."""

    chat_id: int
    message_id: int
    thread_id: int | None = None
    from_user_id: int | None = None
    from_user_is_bot: bool = False
    from_user_username: str | None = None
    mentions: list[str] = field(default_factory=list)
    reply_to_text: str | None = None
    reply_to_message_id: int | None = None
    chat_type: str = "private"


class TelegramAdapterConfig(AdapterConfig):
    """Configuration for the Telegram adapter."""

    bot_token: str = ""
    allowed_chat_ids: list[int] = Field(default_factory=list)
    webhook_url: str | None = None
    progress_throttle: float = _PROGRESS_THROTTLE_SECONDS
    max_inbound_media_mb: float = 20.0
    try_markdown: bool = True
    bot_name: str = ""
    personality: str = ""
    projects: list[str] = Field(default_factory=list)


class TelegramAdapter:
    """Full-featured Telegram bot adapter.

    Supports chat-mode routing, media I/O, reactions, streaming edits,
    forum topics, polls, inline keyboards, pinned messages, and bot commands.
    """

    def __init__(self, config: TelegramAdapterConfig) -> None:
        self.config = config
        self._application: Any = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._last_progress: dict[str, float] = {}
        self._on_new_message: Callable[..., Any] | None = None
        self._on_topic_created: Callable[[int, int, str], Any] | None = None
        self._callback_with_context: bool = False
        self._running = False
        self._session_map: dict[int, str] = {}
        self._chat_map: dict[str, int] = {}
        self._poll_futures: dict[str, asyncio.Future[list[int]]] = {}
        self._poll_sessions: dict[str, str] = {}
        self.bot_username: str = ""
        self._last_media_cleanup: float = 0.0
        self._last_bot_message_id: dict[int, int] = {}
        self._bg_tasks: set[asyncio.Task[Any]] = set()

    def _resolve_token(self) -> str:
        return self.config.bot_token or os.environ.get("DAN_TELEGRAM_BOT_TOKEN", "")

    def set_message_callback(
        self, callback: Callable[..., Any] | None, *, with_context: bool = False,
    ) -> None:
        self._on_new_message = callback
        self._callback_with_context = with_context

    def set_topic_created_callback(
        self, callback: Callable[[int, int, str], Any] | None,
    ) -> None:
        self._on_topic_created = callback

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        try:
            from telegram.ext import (
                Application,
                CallbackQueryHandler,
                CommandHandler,
                MessageHandler,
                PollAnswerHandler,
                filters,
            )
        except ImportError as exc:
            raise RuntimeError(
                "python-telegram-bot is required. "
                "Install with: pip install 'dan[messaging]'"
            ) from exc

        token = self._resolve_token()
        if not token:
            raise ValueError(
                "No bot token provided. Set --bot-token or DAN_TELEGRAM_BOT_TOKEN."
            )

        self._application = Application.builder().token(token).build()
        app = self._application

        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("cancel", self._cmd_cancel))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("pin", self._cmd_pin))
        app.add_handler(CommandHandler("topic", self._cmd_topic))
        app.add_handler(MessageHandler(filters.COMMAND, self._on_command_message))
        app.add_handler(CallbackQueryHandler(self._on_callback_query))
        app.add_handler(PollAnswerHandler(self._on_poll_answer))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text_message),
        )
        media_filter = (
            filters.PHOTO
            | filters.Document.ALL
            | filters.VOICE
            | filters.AUDIO
            | filters.VIDEO
            | filters.VIDEO_NOTE
            | filters.Sticker.ALL
            | filters.CONTACT
            | filters.LOCATION
        )
        app.add_handler(MessageHandler(media_filter, self._on_media_message))

        await app.initialize()

        me = await app.bot.get_me()
        self.bot_username = me.username or ""

        await self._register_default_commands()

        if self.config.webhook_url:
            await app.bot.set_webhook(self.config.webhook_url)
        await app.start()
        if not self.config.webhook_url:
            await app.updater.start_polling()

        self._running = True
        logger.info("Telegram adapter started (@%s)", self.bot_username)

    async def stop(self) -> None:
        self._running = False
        if self._application is not None:
            if self._application.updater and self._application.updater.running:
                await self._application.updater.stop()
            await self._application.stop()
            await self._application.shutdown()
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        for fut in self._poll_futures.values():
            if not fut.done():
                fut.cancel()
        for task in list(self._bg_tasks):
            task.cancel()
        logger.info("Telegram adapter stopped")

    # -- send ---------------------------------------------------------------

    async def send_message(self, session_id: str, text: str) -> None:
        await self.send_prompt(session_id, text, None)

    async def send_prompt(
        self,
        session_id: str,
        prompt: str,
        schema: dict[str, Any] | None = None,
    ) -> int | None:
        chat_id = self._chat_id_from_session(session_id)
        if chat_id is None:
            logger.warning("send_prompt: unknown session %s", session_id)
            return None
        msg_id = await self._send_text(chat_id, prompt)
        if msg_id is not None:
            self._last_bot_message_id[chat_id] = msg_id
        return msg_id

    async def send_prompt_with_keyboard(
        self,
        session_id: str,
        prompt: str,
        options: list[str],
        *,
        mode: str = "selection",
        callback_prefix: str = "",
    ) -> None:
        chat_id = self._chat_id_from_session(session_id)
        if chat_id is None:
            return

        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        except ImportError:
            await self._send_text(chat_id, prompt)
            return

        if mode == "selection" and 2 <= len(options) <= 10:
            await self.send_poll_for_session(
                session_id,
                prompt,
                options,
            )
            return

        pfx = callback_prefix
        if mode == "approval":
            buttons = [[
                InlineKeyboardButton("Yes", callback_data=f"{pfx}__approve_yes"),
                InlineKeyboardButton("No", callback_data=f"{pfx}__approve_no"),
            ]]
        else:
            buttons = [
                [InlineKeyboardButton(opt, callback_data=f"{pfx}__sel_{i}")]
                for i, opt in enumerate(options)
            ]

        markup = InlineKeyboardMarkup(buttons)
        msg = await self._application.bot.send_message(
            chat_id=chat_id, text=prompt, reply_markup=markup,
        )
        self._schedule_keyboard_expiry(chat_id, msg.message_id)

    async def send_result(self, session_id: str, result: dict[str, Any]) -> None:
        chat_id = self._chat_id_from_session(session_id)
        if chat_id is None:
            return
        lines = ["Workflow Result\n"]
        for key, value in result.items():
            lines.append(f"{key}: {value}")
        await self._send_text(chat_id, "\n".join(lines))

    async def send_progress(self, session_id: str, message: str) -> None:
        now = time.monotonic()
        last = self._last_progress.get(session_id, 0.0)
        if now - last < self.config.progress_throttle:
            return
        self._last_progress[session_id] = now
        chat_id = self._chat_id_from_session(session_id)
        if chat_id is not None:
            await self._send_text(chat_id, f"⏳ {message}")

    async def send_file(self, session_id: str, file_path: str) -> None:
        chat_id = self._chat_id_from_session(session_id)
        if chat_id is None:
            return
        await self._send_file_to_chat(chat_id, file_path)

    async def _send_file_to_chat(self, chat_id: int, file_path: str) -> int | None:
        path = Path(file_path)
        if not path.exists():
            await self._application.bot.send_message(
                chat_id=chat_id, text=f"File not found: {file_path}",
            )
            return None

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > 50:
            await self._application.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"File too large to send ({size_mb:.1f} MB). "
                    "Telegram limits bot uploads to 50 MB."
                ),
            )
            return None

        mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        caption = f"{path.name} ({size_mb:.1f} MB)" if size_mb >= 0.1 else path.name

        if mime_type.startswith("image/") and size_mb <= 10:
            with open(path, "rb") as f:
                msg = await self._application.bot.send_photo(
                    chat_id=chat_id, photo=f, caption=caption,
                )
                self._last_bot_message_id[chat_id] = msg.message_id
                return msg.message_id
        else:
            with open(path, "rb") as f:
                msg = await self._application.bot.send_document(
                    chat_id=chat_id, document=f, caption=caption, filename=path.name,
                )
                self._last_bot_message_id[chat_id] = msg.message_id
                return msg.message_id

    # -- reactions ----------------------------------------------------------

    async def set_reaction(
        self, chat_id: int, message_id: int, emoji: str,
    ) -> None:
        try:
            from telegram import ReactionTypeEmoji

            await self._application.bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
        except Exception:
            pass

    async def remove_reaction(self, chat_id: int, message_id: int) -> None:
        try:
            await self._application.bot.set_message_reaction(
                chat_id=chat_id, message_id=message_id, reaction=[],
            )
        except Exception:
            pass

    # -- streaming edits ----------------------------------------------------

    async def send_or_edit(
        self,
        chat_id: int,
        text: str,
        message_id: int | None = None,
        *,
        reply_to: int | None = None,
    ) -> int | None:
        """Send a new message or edit an existing one. Returns message_id."""
        if not text:
            return message_id
        if len(text) > _TELEGRAM_MAX_MESSAGE_LENGTH:
            text = text[: _TELEGRAM_MAX_MESSAGE_LENGTH - 20] + "\n\n…(truncated)"
        try:
            if message_id is not None:
                await self._application.bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=text,
                )
                return message_id
            else:
                kwargs: dict[str, Any] = {"chat_id": chat_id, "text": text}
                if reply_to:
                    kwargs["reply_to_message_id"] = reply_to
                msg = await self._application.bot.send_message(**kwargs)
                return msg.message_id
        except Exception as exc:
            exc_str = str(exc).lower()
            if message_id is not None:
                if "message is not modified" in exc_str:
                    return message_id
                logger.debug("Edit failed (%s), sending new message", exc)
                try:
                    msg = await self._application.bot.send_message(
                        chat_id=chat_id, text=text,
                    )
                    return msg.message_id
                except Exception:
                    pass
            return None

    # -- polls --------------------------------------------------------------

    async def send_poll(
        self,
        chat_id: int,
        question: str,
        options: list[str],
        *,
        is_anonymous: bool = False,
        allows_multiple: bool = False,
        reply_to: int | None = None,
    ) -> str | None:
        if len(options) < 2 or len(options) > 10:
            return None
        try:
            kwargs: dict[str, Any] = {
                "chat_id": chat_id,
                "question": question,
                "options": options,
                "is_anonymous": is_anonymous,
                "allows_multiple_answers": allows_multiple,
            }
            if reply_to:
                kwargs["reply_to_message_id"] = reply_to
            msg = await self._application.bot.send_poll(**kwargs)
            self._last_bot_message_id[chat_id] = msg.message_id
            return msg.poll.id if msg.poll else None
        except Exception as exc:
            logger.warning("Failed to send poll: %s", exc)
            return None

    async def send_poll_for_session(
        self,
        session_id: str,
        question: str,
        options: list[str],
        *,
        is_anonymous: bool = False,
        allows_multiple: bool = False,
    ) -> str | None:
        chat_id = self._chat_id_from_session(session_id)
        if chat_id is None:
            return None
        poll_id = await self.send_poll(
            chat_id,
            question,
            options,
            is_anonymous=is_anonymous,
            allows_multiple=allows_multiple,
        )
        if poll_id:
            self._poll_sessions[poll_id] = session_id
        return poll_id

    # -- pin ----------------------------------------------------------------

    async def pin_message(self, chat_id: int, message_id: int) -> bool:
        try:
            await self._application.bot.pin_chat_message(
                chat_id=chat_id, message_id=message_id,
            )
            return True
        except Exception as exc:
            logger.debug("Failed to pin message: %s", exc)
            return False

    # -- command registration -----------------------------------------------

    async def _register_default_commands(self) -> None:
        try:
            from telegram import (
                BotCommand,
                BotCommandScopeAllGroupChats,
                BotCommandScopeAllPrivateChats,
            )

            dm_commands = [
                BotCommand(cmd, desc)
                for cmd, desc in self._default_dm_commands()
            ]
            group_commands = [
                BotCommand(cmd, desc)
                for cmd, desc in self._default_group_commands()
            ]
            try:
                await self._application.bot.set_my_commands(
                    dm_commands, scope=BotCommandScopeAllPrivateChats(),
                )
                await self._application.bot.set_my_commands(
                    group_commands, scope=BotCommandScopeAllGroupChats(),
                )
            except Exception:
                await self._application.bot.set_my_commands(dm_commands)
        except Exception as exc:
            logger.debug("Failed to register commands: %s", exc)

    async def register_custom_commands(
        self, commands: list[tuple[str, str]],
    ) -> None:
        try:
            from telegram import (
                BotCommand,
                BotCommandScopeAllGroupChats,
                BotCommandScopeAllPrivateChats,
            )

            dm_pairs = _merge_command_pairs(self._default_dm_commands(), commands)
            group_pairs = _merge_command_pairs(
                self._default_group_commands(),
                commands,
            )
            await self._application.bot.set_my_commands(
                [BotCommand(cmd, desc) for cmd, desc in dm_pairs],
                scope=BotCommandScopeAllPrivateChats(),
            )
            await self._application.bot.set_my_commands(
                [BotCommand(cmd, desc) for cmd, desc in group_pairs],
                scope=BotCommandScopeAllGroupChats(),
            )
        except Exception as exc:
            logger.debug("Failed to register custom commands: %s", exc)

    # -- inline keyboards ---------------------------------------------------

    async def send_action_keyboard(
        self,
        chat_id: int,
        text: str,
        actions: list[tuple[str, str]],
        *,
        reply_to: int | None = None,
        callback_prefix: str = "",
    ) -> int | None:
        """Send message with action buttons. actions = [(label, callback_data), ...]"""
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            buttons = [[
                InlineKeyboardButton(label, callback_data=f"{callback_prefix}{cb}")
                for label, cb in actions
            ]]
            markup = InlineKeyboardMarkup(buttons)
            kwargs: dict[str, Any] = {
                "chat_id": chat_id, "text": text, "reply_markup": markup,
            }
            if reply_to:
                kwargs["reply_to_message_id"] = reply_to
            msg = await self._application.bot.send_message(**kwargs)
            self._schedule_keyboard_expiry(chat_id, msg.message_id)
            return msg.message_id
        except Exception as exc:
            logger.debug("Failed to send action keyboard: %s", exc)
            return None

    async def remove_keyboard(self, chat_id: int, message_id: int) -> None:
        try:
            await self._application.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=message_id, reply_markup=None,
            )
        except Exception:
            pass

    # -- mini app -----------------------------------------------------------

    async def set_menu_button(
        self, web_app_url: str, text: str = "Open Editor",
    ) -> None:
        try:
            from telegram import MenuButtonWebApp, WebAppInfo

            await self._application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text=text, web_app=WebAppInfo(url=web_app_url),
                ),
            )
        except Exception as exc:
            logger.debug("Failed to set menu button: %s", exc)

    # -- receive ------------------------------------------------------------

    async def wait_for_response(
        self, session_id: str, timeout: float,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[session_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(session_id, None)

    # -- telegram handlers --------------------------------------------------

    async def _cmd_start(self, update: Any, context: Any) -> None:
        if not self._is_allowed(update.effective_chat.id):
            return
        await update.message.reply_text(self.config.welcome_message)

    async def _cmd_status(self, update: Any, context: Any) -> None:
        if not self._is_allowed(update.effective_chat.id):
            return
        chat_id = update.effective_chat.id
        sid = self._session_id_from_chat(chat_id)
        if sid and sid in self._pending:
            await update.message.reply_text(
                "A task is running and waiting for your input.",
            )
        else:
            await update.message.reply_text(
                "No active task. Send a message to start one.",
            )

    async def _cmd_cancel(self, update: Any, context: Any) -> None:
        if not self._is_allowed(update.effective_chat.id):
            return
        chat_id = update.effective_chat.id
        sid = self._session_id_from_chat(chat_id)
        if sid:
            fut = self._pending.pop(sid, None)
            if fut and not fut.done():
                fut.cancel()
            await update.message.reply_text("Task cancelled.")
        else:
            await update.message.reply_text("No active task to cancel.")

    async def _cmd_help(self, update: Any, context: Any) -> None:
        if not self._is_allowed(update.effective_chat.id):
            return
        await update.message.reply_text(
            "Available commands:\n"
            "/help — Show this help\n"
            "/find <query> — Find a file\n"
            "/send <path> — Send a file\n"
            "/status — Check task status\n"
            "/cancel — Cancel current task\n"
            "/show — Show current workflow\n"
            "/list — List workflows\n"
            "/mcp — List MCP tools\n"
            "/pin — Pin last bot message\n"
            "/topic create <name> — Create a topic\n\n"
            "Or just type naturally!",
        )

    async def _on_command_message(self, update: Any, context: Any) -> None:
        if not self._is_allowed(update.effective_chat.id):
            return
        chat_id = update.effective_chat.id
        msg = update.message
        text = msg.text or ""
        sid = self._session_id_from_chat(chat_id)

        if sid and sid in self._pending:
            fut = self._pending.get(sid)
            if fut and not fut.done():
                fut.set_result({"response": text})
                return

        if self._on_new_message is not None:
            ctx = self._build_context(msg)
            reply_ctx = self._build_reply_prefix(msg)
            full_text = f"{reply_ctx}{text}" if reply_ctx else text
            await self._fire_callback(str(chat_id), full_text, ctx)

    async def _cmd_pin(self, update: Any, context: Any) -> None:
        if not self._is_allowed(update.effective_chat.id):
            return
        chat_id = update.effective_chat.id
        if update.message.reply_to_message:
            ok = await self.pin_message(
                chat_id, update.message.reply_to_message.message_id,
            )
            if ok:
                await update.message.reply_text("Pinned!")
            else:
                await update.message.reply_text(
                    "Couldn't pin — I may need admin rights.",
                )
        else:
            last_id = self._last_bot_message_id.get(chat_id)
            if last_id:
                ok = await self.pin_message(chat_id, last_id)
                if ok:
                    await update.message.reply_text("Pinned!")
                else:
                    await update.message.reply_text(
                        "Couldn't pin — I may need admin rights.",
                    )
            else:
                await update.message.reply_text(
                    "Reply to a message with /pin to pin it.",
                )

    async def _cmd_topic(self, update: Any, context: Any) -> None:
        if not self._is_allowed(update.effective_chat.id):
            return
        text = update.message.text or ""
        parts = text.strip().split(maxsplit=2)

        if len(parts) >= 3 and parts[1].lower() == "create":
            topic_name = parts[2]
            try:
                result = await self._application.bot.create_forum_topic(
                    chat_id=update.effective_chat.id, name=topic_name,
                )
                if self._on_topic_created is not None:
                    maybe_awaitable = self._on_topic_created(
                        update.effective_chat.id,
                        result.message_thread_id,
                        topic_name,
                    )
                    if asyncio.iscoroutine(maybe_awaitable):
                        await maybe_awaitable
                await update.message.reply_text(
                    f"Topic '{topic_name}' created "
                    f"(ID: {result.message_thread_id}).",
                )
            except Exception as exc:
                await update.message.reply_text(f"Failed to create topic: {exc}")
        else:
            await update.message.reply_text("Usage: /topic create <name>")

    async def _on_text_message(self, update: Any, context: Any) -> None:
        if not self._is_allowed(update.effective_chat.id):
            return
        chat_id = update.effective_chat.id
        msg = update.message
        text = msg.text or ""
        sid = self._session_id_from_chat(chat_id)

        if sid and sid in self._pending:
            fut = self._pending.get(sid)
            if fut and not fut.done():
                fut.set_result({"response": text})
                return

        if self._on_new_message is not None:
            ctx = self._build_context(msg)
            reply_ctx = self._build_reply_prefix(msg)
            full_text = f"{reply_ctx}{text}" if reply_ctx else text
            await self._fire_callback(str(chat_id), full_text, ctx)

    async def _on_media_message(self, update: Any, context: Any) -> None:
        if not self._is_allowed(update.effective_chat.id):
            return
        chat_id = update.effective_chat.id
        msg = update.message

        if msg.sticker:
            if self._on_new_message:
                ctx = self._build_context(msg)
                await self._fire_callback(
                    str(chat_id), "[User sent a sticker]", ctx,
                )
            return

        if msg.contact:
            name = (
                f"{msg.contact.first_name or ''} "
                f"{msg.contact.last_name or ''}"
            ).strip()
            if self._on_new_message:
                ctx = self._build_context(msg)
                await self._fire_callback(
                    str(chat_id), f"[User shared contact: {name}]", ctx,
                )
            return

        if msg.location:
            if self._on_new_message:
                ctx = self._build_context(msg)
                await self._fire_callback(
                    str(chat_id),
                    f"[User shared location: {msg.location.latitude}, "
                    f"{msg.location.longitude}]",
                    ctx,
                )
            return

        file_obj = None
        media_type = ""

        if msg.photo:
            file_obj = await msg.photo[-1].get_file()
            media_type = "photo"
        elif msg.document:
            file_obj = await msg.document.get_file()
            media_type = "document"
        elif msg.voice:
            file_obj = await msg.voice.get_file()
            media_type = "voice"
        elif msg.audio:
            file_obj = await msg.audio.get_file()
            media_type = "audio"
        elif msg.video:
            file_obj = await msg.video.get_file()
            media_type = "video"
        elif msg.video_note:
            file_obj = await msg.video_note.get_file()
            media_type = "video_note"

        if file_obj is None:
            return

        size_mb = (file_obj.file_size or 0) / (1024 * 1024)
        if size_mb > self.config.max_inbound_media_mb:
            await msg.reply_text(
                f"File too large ({size_mb:.1f} MB). "
                f"Telegram limits bot downloads to "
                f"{self.config.max_inbound_media_mb:.0f} MB.",
            )
            return

        local_path = await self._download_media(file_obj, media_type)
        if not local_path:
            await msg.reply_text(
                "Couldn't download the file. Please try again.",
            )
            return

        caption = msg.caption or ""
        reply_ctx = self._build_reply_prefix(msg)

        if media_type in ("voice", "audio"):
            text = f"[Voice note: {local_path}]"
            if caption:
                text = f"{text}\n{caption}"
        else:
            text = f"[Attachment: {local_path}]"
            if caption:
                text = f"{text}\n{caption}"

        if reply_ctx:
            text = f"{reply_ctx}{text}"

        if self._on_new_message:
            ctx = self._build_context(msg)
            await self._fire_callback(str(chat_id), text, ctx)

    async def _on_callback_query(self, update: Any, context: Any) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()

        chat_id = query.message.chat_id
        data = query.data or ""

        clean_data = data
        if ":" in data and not data.startswith("__"):
            clean_data = data.split(":", 1)[1]

        sid = self._session_id_from_chat(chat_id)
        if sid and sid in self._pending:
            fut = self._pending.get(sid)
            if fut and not fut.done():
                if clean_data == "__approve_yes":
                    fut.set_result({"approved": True, "response": "yes"})
                elif clean_data == "__approve_no":
                    fut.set_result({"approved": False, "response": "no"})
                elif clean_data.startswith("__sel_"):
                    try:
                        idx = int(clean_data.split("_")[-1])
                        fut.set_result(
                            {"selected_index": idx, "response": data},
                        )
                    except ValueError:
                        fut.set_result({"response": data})
                else:
                    fut.set_result({"response": data})

    async def _on_poll_answer(self, update: Any, context: Any) -> None:
        answer = update.poll_answer
        if answer is None:
            return
        poll_id = answer.poll_id
        session_id = self._poll_sessions.pop(str(poll_id), None)
        if session_id and session_id in self._pending:
            fut = self._pending.get(session_id)
            if fut and not fut.done():
                fut.set_result(
                    {
                        "selected_index": answer.option_ids[0]
                        if answer.option_ids
                        else None,
                        "selected_indices": answer.option_ids,
                        "response": ",".join(str(i) for i in answer.option_ids),
                    },
                )
        fut = self._poll_futures.pop(str(poll_id), None)
        if fut and not fut.done():
            fut.set_result(answer.option_ids)

    # -- internal helpers ---------------------------------------------------

    def _is_allowed(self, chat_id: int) -> bool:
        if not self.config.allowed_chat_ids:
            return True
        return chat_id in self.config.allowed_chat_ids

    def register_session(self, session_id: str, chat_id: int) -> None:
        self._session_map[chat_id] = session_id
        self._chat_map[session_id] = chat_id

    def unregister_session(self, session_id: str) -> None:
        chat_id = self._chat_map.pop(session_id, None)
        if chat_id is not None:
            self._session_map.pop(chat_id, None)

    def _session_id_from_chat(self, chat_id: int) -> str | None:
        return self._session_map.get(chat_id)

    def _chat_id_from_session(self, session_id: str) -> int | None:
        return self._chat_map.get(session_id)

    async def _send_text(
        self, chat_id: int, text: str, reply_to: int | None = None,
    ) -> int | None:
        if self._application is None:
            return None
        last_msg_id = None
        for chunk in _split_message(text):
            last_msg_id = await self._try_send_markdown(
                chat_id, chunk, reply_to=reply_to,
            )
            reply_to = None
        return last_msg_id

    async def _try_send_markdown(
        self, chat_id: int, text: str, *, reply_to: int | None = None,
    ) -> int | None:
        kwargs: dict[str, Any] = {"chat_id": chat_id}
        if reply_to:
            kwargs["reply_to_message_id"] = reply_to

        if self.config.try_markdown and "```" in text:
            try:
                formatted = _escape_markdown_v2(text)
                msg = await self._application.bot.send_message(
                    **kwargs, text=formatted, parse_mode="MarkdownV2",
                )
                return msg.message_id
            except Exception:
                pass

        msg = await self._application.bot.send_message(**kwargs, text=text)
        return msg.message_id

    def _build_context(self, msg: Any) -> MessageContext:
        mentions: list[str] = []
        for source_text, entities in (
            (msg.text or "", msg.entities or []),
            (msg.caption or "", msg.caption_entities or []),
        ):
            for entity in entities:
                if entity.type == "mention":
                    mention_text = source_text[
                        entity.offset : entity.offset + entity.length
                    ]
                    if mention_text.startswith("@"):
                        mentions.append(mention_text[1:])
                elif entity.type == "text_mention" and entity.user:
                    mentions.append(entity.user.username or str(entity.user.id))

        reply_text = None
        reply_msg_id = None
        if msg.reply_to_message:
            reply_text = (
                msg.reply_to_message.text
                or msg.reply_to_message.caption
                or ""
            )
            reply_msg_id = msg.reply_to_message.message_id

        return MessageContext(
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            thread_id=getattr(msg, "message_thread_id", None),
            from_user_id=msg.from_user.id if msg.from_user else None,
            from_user_is_bot=msg.from_user.is_bot if msg.from_user else False,
            from_user_username=(
                msg.from_user.username if msg.from_user else None
            ),
            mentions=mentions,
            reply_to_text=reply_text,
            reply_to_message_id=reply_msg_id,
            chat_type=msg.chat.type if msg.chat else "private",
        )

    def _build_reply_prefix(self, msg: Any) -> str:
        chain: list[str] = []
        current = msg.reply_to_message
        depth = 0
        while current is not None and depth < 5:
            reply_text = current.text or current.caption or ""
            if reply_text:
                if len(reply_text) > 200:
                    reply_text = reply_text[:200] + "…"
                chain.append(f'[Replying to: "{reply_text}"]')
            current = getattr(current, "reply_to_message", None)
            depth += 1
        if not chain:
            return ""
        return "\n".join(reversed(chain)) + "\n"

    async def _fire_callback(
        self, session_id: str, text: str, ctx: MessageContext,
    ) -> None:
        if self._callback_with_context:
            await self._on_new_message(session_id, text, ctx)
        else:
            await self._on_new_message(session_id, text)

    async def _download_media(
        self, file_obj: Any, media_type: str,
    ) -> str | None:
        _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        await self._cleanup_old_media()

        ext = Path(file_obj.file_path or "").suffix or _default_ext(media_type)
        local_name = f"{int(time.time())}_{file_obj.file_unique_id}{ext}"
        local_path = _MEDIA_DIR / local_name
        try:
            await file_obj.download_to_drive(str(local_path))
            return str(local_path)
        except Exception as exc:
            logger.warning("Media download failed: %s", exc)
            return None

    async def _cleanup_old_media(self) -> None:
        now = time.time()
        if now - self._last_media_cleanup < 300:
            return
        self._last_media_cleanup = now
        if not _MEDIA_DIR.exists():
            return
        cutoff = now - _MEDIA_CLEANUP_SECONDS
        for f in _MEDIA_DIR.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                pass

    def _default_dm_commands(self) -> list[tuple[str, str]]:
        return [
            ("help", "Show available commands"),
            ("status", "Check current task status"),
            ("cancel", "Cancel current task"),
            ("find", "Find a file on your computer"),
            ("send", "Send you a file"),
            ("list", "List saved workflows"),
            ("show", "Show current workflow"),
            ("mcp", "List available MCP tools"),
        ]

    def _default_group_commands(self) -> list[tuple[str, str]]:
        return self._default_dm_commands() + [
            ("pin", "Pin the last bot message"),
            ("topic", "Manage forum topics"),
        ]

    def _schedule_keyboard_expiry(
        self, chat_id: int, message_id: int, delay: float = 300.0,
    ) -> None:
        async def _expire() -> None:
            await asyncio.sleep(delay)
            await self.remove_keyboard(chat_id, message_id)

        task = asyncio.create_task(_expire())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)


# -- module-level helpers ---------------------------------------------------


def _default_ext(media_type: str) -> str:
    return {
        "photo": ".jpg",
        "voice": ".ogg",
        "audio": ".mp3",
        "video": ".mp4",
        "video_note": ".mp4",
        "document": "",
    }.get(media_type, "")


def _split_message(
    text: str, max_len: int = _TELEGRAM_MAX_MESSAGE_LENGTH,
) -> list[str]:
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


_MARKDOWN_V2_SPECIAL = set(r"_*[]()~`>#+-=|{}.!")


def _escape_markdown_v2(text: str) -> str:
    """Escape text for MarkdownV2, preserving triple-backtick code blocks."""
    parts = text.split("```")
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            escaped = ""
            for ch in part:
                if ch in _MARKDOWN_V2_SPECIAL:
                    escaped += "\\" + ch
                else:
                    escaped += ch
            result.append(escaped)
        else:
            result.append(part)
    return "```".join(result)


def verify_webapp_init_data(init_data: str, bot_token: str) -> bool:
    """Verify Telegram WebApp initData payloads.

    This supports Mini App authentication by validating the HMAC signature
    described in https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    their_hash = pairs.pop("hash", "")
    if not their_hash:
        return False

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(pairs.items())
    )
    secret = hmac.new(
        b"WebAppData",
        bot_token.encode(),
        hashlib.sha256,
    ).digest()
    our_hash = hmac.new(
        secret,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(our_hash, their_hash)


def _merge_command_pairs(
    base: list[tuple[str, str]],
    extra: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    merged: dict[str, str] = {cmd: desc for cmd, desc in base}
    for cmd, desc in extra:
        merged[cmd] = desc
    return list(merged.items())
