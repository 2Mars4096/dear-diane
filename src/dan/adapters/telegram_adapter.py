"""Telegram messaging adapter — ``python-telegram-bot`` backend.

The adapter runs in long-polling *or* webhook mode.  Incoming messages
trigger workflow runs; HumanNode prompts are rendered as Telegram messages
with inline keyboards for selection / approval render modes.

Requires the ``python-telegram-bot`` optional dependency
(``pip install 'dan[messaging]'``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from pydantic import Field

from dan.adapters.base import AdapterConfig

logger = logging.getLogger(__name__)

_TELEGRAM_MAX_MESSAGE_LENGTH = 4096
_PROGRESS_THROTTLE_SECONDS = 5.0


class TelegramAdapterConfig(AdapterConfig):
    """Configuration for the Telegram adapter."""

    bot_token: str = ""
    allowed_chat_ids: list[int] = Field(default_factory=list)
    webhook_url: str | None = None
    progress_throttle: float = _PROGRESS_THROTTLE_SECONDS


class TelegramAdapter:
    """Renders HumanNode I/O through a Telegram bot.

    Features
    --------
    * Long-polling (default) or webhook mode.
    * Inline keyboards for ``selection`` and ``approval`` render modes.
    * Messages longer than 4 096 characters are automatically split.
    * ``/start``, ``/status``, ``/cancel`` commands.
    * One active workflow per ``chat_id``; concurrent runs are queued.
    * Progress updates throttled to ≤ 1 every *progress_throttle* seconds.
    """

    def __init__(self, config: TelegramAdapterConfig) -> None:
        self.config = config
        self._application: Any = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._last_progress: dict[str, float] = {}
        self._on_new_message: Callable[[str, str], Any] | None = None
        self._running = False
        self._session_map: dict[int, str] = {}
        self._chat_map: dict[str, int] = {}

    def set_message_callback(self, callback: Callable[[str, str], Any] | None) -> None:
        self._on_new_message = callback

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        try:
            from telegram.ext import (
                Application,
                CallbackQueryHandler,
                CommandHandler,
                MessageHandler,
                filters,
            )
        except ImportError as exc:
            raise RuntimeError(
                "python-telegram-bot is required for the Telegram adapter. "
                "Install with: pip install 'dan[messaging]'"
            ) from exc

        builder = Application.builder().token(self.config.bot_token)
        self._application = builder.build()

        self._application.add_handler(CommandHandler("start", self._cmd_start))
        self._application.add_handler(CommandHandler("status", self._cmd_status))
        self._application.add_handler(CommandHandler("cancel", self._cmd_cancel))
        self._application.add_handler(CallbackQueryHandler(self._on_callback_query))
        self._application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text_message),
        )

        await self._application.initialize()
        if self.config.webhook_url:
            await self._application.bot.set_webhook(self.config.webhook_url)
        await self._application.start()
        if not self.config.webhook_url:
            await self._application.updater.start_polling()

        self._running = True
        logger.info("Telegram adapter started (bot token: %s…)", self.config.bot_token[:8])

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
        logger.info("Telegram adapter stopped")

    # -- send ---------------------------------------------------------------

    async def send_prompt(
        self, session_id: str, prompt: str, schema: dict[str, Any] | None = None,
    ) -> None:
        chat_id = self._chat_id_from_session(session_id)
        if chat_id is None:
            logger.warning("send_prompt: unknown session %s", session_id)
            return
        await self._send_text(chat_id, prompt)

    async def send_prompt_with_keyboard(
        self,
        session_id: str,
        prompt: str,
        options: list[str],
        *,
        mode: str = "selection",
    ) -> None:
        """Send prompt with inline keyboard buttons."""
        chat_id = self._chat_id_from_session(session_id)
        if chat_id is None:
            return

        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        except ImportError:
            await self._send_text(chat_id, prompt)
            return

        if mode == "approval":
            buttons = [
                [
                    InlineKeyboardButton("Yes", callback_data="__approve_yes"),
                    InlineKeyboardButton("No", callback_data="__approve_no"),
                ]
            ]
        else:
            buttons = [
                [InlineKeyboardButton(opt, callback_data=f"__sel_{i}")]
                for i, opt in enumerate(options)
            ]

        markup = InlineKeyboardMarkup(buttons)
        await self._application.bot.send_message(
            chat_id=chat_id, text=prompt, reply_markup=markup,
        )

    async def send_result(self, session_id: str, result: dict[str, Any]) -> None:
        chat_id = self._chat_id_from_session(session_id)
        if chat_id is None:
            return
        lines = ["*Workflow Result*\n"]
        for key, value in result.items():
            lines.append(f"*{key}:* {value}")
        text = "\n".join(lines)
        await self._send_text(chat_id, text)

    async def send_progress(self, session_id: str, message: str) -> None:
        """Send a throttled progress update."""
        now = time.monotonic()
        last = self._last_progress.get(session_id, 0.0)
        if now - last < self.config.progress_throttle:
            return
        self._last_progress[session_id] = now
        chat_id = self._chat_id_from_session(session_id)
        if chat_id is not None:
            await self._send_text(chat_id, f"⏳ {message}")

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
            await update.message.reply_text("A workflow is running and waiting for your input.")
        else:
            await update.message.reply_text("No active workflow. Send a message to start one.")

    async def _cmd_cancel(self, update: Any, context: Any) -> None:
        if not self._is_allowed(update.effective_chat.id):
            return
        chat_id = update.effective_chat.id
        sid = self._session_id_from_chat(chat_id)
        if sid:
            fut = self._pending.pop(sid, None)
            if fut and not fut.done():
                fut.cancel()
            await update.message.reply_text("Workflow cancelled.")
        else:
            await update.message.reply_text("No active workflow to cancel.")

    async def _on_text_message(self, update: Any, context: Any) -> None:
        if not self._is_allowed(update.effective_chat.id):
            return
        chat_id = update.effective_chat.id
        text = update.message.text or ""
        sid = self._session_id_from_chat(chat_id)

        if sid and sid in self._pending:
            fut = self._pending.get(sid)
            if fut and not fut.done():
                fut.set_result({"response": text})
                return

        if self._on_new_message is not None:
            await self._on_new_message(str(chat_id), text)

    async def _on_callback_query(self, update: Any, context: Any) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()

        chat_id = query.message.chat_id
        data = query.data or ""

        sid = self._session_id_from_chat(chat_id)
        if sid and sid in self._pending:
            fut = self._pending.get(sid)
            if fut and not fut.done():
                if data == "__approve_yes":
                    fut.set_result({"approved": True, "response": "yes"})
                elif data == "__approve_no":
                    fut.set_result({"approved": False, "response": "no"})
                elif data.startswith("__sel_"):
                    try:
                        idx = int(data.split("_")[-1])
                        fut.set_result({"selected_index": idx, "response": data})
                    except ValueError:
                        fut.set_result({"response": data})
                else:
                    fut.set_result({"response": data})

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

    async def _send_text(self, chat_id: int, text: str) -> None:
        if self._application is None:
            return
        for chunk in _split_message(text):
            await self._application.bot.send_message(chat_id=chat_id, text=chunk)


def _split_message(text: str, max_len: int = _TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
    """Split text into chunks that fit Telegram's message length limit."""
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
