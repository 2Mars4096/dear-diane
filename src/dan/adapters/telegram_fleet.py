"""Multi-bot fleet coordinator for Telegram group chats.

``BotFleet`` manages multiple ``TelegramAdapter`` instances sharing one event
loop.  A per-message deduplication set ensures only one bot processes each
incoming message, and the ``MessageRouter`` decides which bot that is.

The fleet is a **standalone process** that communicates with ``dan-serve`` via
HTTP — it does not embed the concierge.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from dan.adapters.telegram_adapter import (
    MessageContext,
    TelegramAdapter,
    TelegramAdapterConfig,
    _split_message,
)
from dan.adapters.telegram_config import (
    TelegramFleetConfig,
    TelegramGroupConfig,
    load_fleet_config,
    save_fleet_config,
)
from dan.adapters.telegram_router import MessageRouter, RoutableBot

logger = logging.getLogger(__name__)
_FLEET_LOCK_FILE = Path.home() / ".dan" / "telegram" / "fleet.lock"


class FleetAlreadyRunningError(RuntimeError):
    """Raised when another Telegram fleet process already owns the poller."""


@dataclass
class BotInstance:
    """A single bot within the fleet."""

    name: str
    token: str
    projects: list[str] = field(default_factory=list)
    personality: str = ""
    is_default: bool = False
    adapter: TelegramAdapter | None = None
    bot_id: int | None = None
    bot_username: str = ""
    allowed_users: list[int | str] = field(default_factory=list)


class BotFleet:
    """Manages multiple Telegram bots in a single event loop.

    Each bot has its own ``Application`` instance from ``python-telegram-bot``
    using the lower-level ``initialize() -> start() -> start_polling()``
    pattern so they share one event loop.
    """

    _QUEUE_HINT_INITIAL_DELAY: float = float(
        os.environ.get("DAN_TELEGRAM_QUEUE_HINT_DELAY", "10")
    )
    _QUEUE_HINT_REPEAT_INTERVAL: float = float(
        os.environ.get("DAN_TELEGRAM_QUEUE_HINT_INTERVAL", "20")
    )
    _PROGRESS_INITIAL_DELAY: float = float(
        os.environ.get("DAN_TELEGRAM_PROGRESS_DELAY", "10")
    )
    _PROGRESS_REPEAT_INTERVAL: float = float(
        os.environ.get("DAN_TELEGRAM_PROGRESS_INTERVAL", "20")
    )
    _PROGRESS_MAX_INTERVAL: float = float(
        os.environ.get("DAN_TELEGRAM_PROGRESS_MAX_INTERVAL", "300")
    )
    _PROGRESS_BACKOFF_FACTOR: float = float(
        os.environ.get("DAN_TELEGRAM_PROGRESS_BACKOFF", "1.5")
    )

    def __init__(
        self,
        config: TelegramFleetConfig,
        config_path: str | Path | None = None,
        server_url: str | None = None,
    ) -> None:
        self._config = config
        self._config_path = Path(config_path) if config_path is not None else None
        self._server_url = (
            server_url
            or os.environ.get("DAN_SERVER_URL")
            or "http://127.0.0.1:8000"
        ).rstrip("/")
        self._bots: dict[str, BotInstance] = {}
        self._router = MessageRouter()
        self._seen: dict[tuple[int, int], float] = {}
        self._seen_ingress: dict[str, float] = {}
        self._seen_ttl = 60.0
        self._http: Any = None
        self._stop_event = asyncio.Event()
        self._bg_tasks: set[asyncio.Task[None]] = set()
        self._conversation_history: dict[str, list[dict[str, str]]] = {}
        self._conversation_workflows: dict[str, str] = {}
        self._history_locks: dict[str, asyncio.Lock] = {}
        self._message_lanes: dict[tuple[int, int], tuple[str, float]] = {}
        self._last_outbound: dict[int, tuple[int, float]] = {}

    def _latest_outbound_id(self, chat_id: int) -> int | None:
        """Most recent outbound message id we sent in *chat_id*."""
        entry = self._last_outbound.get(chat_id)
        return entry[0] if entry else None

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        import httpx

        self._http = httpx.AsyncClient(
            base_url=self._server_url, timeout=120.0,
        )

        topic_maps: dict[int, dict[int, str]] = {}
        for gid_str, gcfg in self._config.groups.items():
            gid = int(gid_str)
            topic_maps[gid] = {int(k): v for k, v in gcfg.topic_map.items()}
        self._router = MessageRouter(multi_group_topic_maps=topic_maps)

        for name, bot_cfg in self._config.bots.items():
            adapter_config = TelegramAdapterConfig(
                bot_token=bot_cfg.token,
                progress_throttle=self._config.settings.progress_throttle,
                max_inbound_media_mb=self._config.settings.max_inbound_media_mb,
            )
            adapter = TelegramAdapter(adapter_config)

            bot = BotInstance(
                name=name,
                token=bot_cfg.token,
                projects=list(bot_cfg.projects),
                personality=bot_cfg.personality,
                is_default=bot_cfg.default,
                adapter=adapter,
                allowed_users=list(bot_cfg.allowed_users),
            )
            self._bots[name] = bot

            adapter.set_message_callback(
                self._make_callback(bot), with_context=True,
            )
            adapter.set_topic_created_callback(
                lambda chat_id, thread_id, topic_name, bot_name=name: (
                    self._on_topic_created(bot_name, chat_id, thread_id, topic_name)
                ),
            )
            await adapter.start()
            bot.bot_id = adapter.bot_user_id
            bot.bot_username = adapter.bot_username
            await adapter.register_custom_commands(
                self._infer_project_commands(bot),
            )

            if self._config.settings.use_reactions:
                pass

            logger.info(
                "Fleet: started %s (@%s) — projects=%s default=%s",
                name, bot.bot_username, bot.projects, bot.is_default,
            )

        if self._config.settings.mini_app_url:
            for bot in self._bots.values():
                if bot.adapter:
                    try:
                        await bot.adapter.set_menu_button(
                            self._config.settings.mini_app_url,
                        )
                    except Exception:
                        logger.debug(
                            "Mini App menu button setup skipped for %s",
                            bot.name,
                        )

        await self._ensure_topics()
        self._start_cleanup_task()
        self._start_control_watcher()

    async def stop(self) -> None:
        self._stop_event.set()
        for task in list(self._bg_tasks):
            task.cancel()

        for name in reversed(list(self._bots)):
            bot = self._bots[name]
            if bot.adapter:
                await bot.adapter.stop()
            logger.info("Fleet: stopped %s", name)

        if self._http:
            await self._http.aclose()

    async def stop_bot(self, name: str) -> bool:
        """Gracefully stop a single bot within the fleet."""
        bot = self._bots.get(name)
        if bot is None:
            return False
        if bot.adapter:
            await bot.adapter.stop()
        del self._bots[name]
        self._refresh_router()
        logger.info("Fleet: stopped individual bot %s", name)
        if not self._bots:
            self._stop_event.set()
        return True

    # -- dedup --------------------------------------------------------------

    def _check_dedup(
        self,
        chat_id: int,
        message_id: int,
        ingress_dedup_key: str | None = None,
    ) -> bool:
        """Return True if this message was already seen (skip it)."""
        key = (chat_id, message_id)
        now = time.monotonic()
        if key in self._seen:
            return True
        if ingress_dedup_key and ingress_dedup_key in self._seen_ingress:
            return True
        self._seen[key] = now
        if ingress_dedup_key:
            self._seen_ingress[ingress_dedup_key] = now
        return False

    def _evict_seen(self) -> None:
        cutoff = time.monotonic() - self._seen_ttl
        expired = [k for k, t in self._seen.items() if t < cutoff]
        for k in expired:
            del self._seen[k]
        expired_ingress = [
            k for k, t in self._seen_ingress.items() if t < cutoff
        ]
        for k in expired_ingress:
            del self._seen_ingress[k]
        expired_lane_keys = [
            k for k, (_lane_key, ts) in self._message_lanes.items() if ts < cutoff
        ]
        for k in expired_lane_keys:
            del self._message_lanes[k]
    def _remember_message_lane(
        self,
        chat_id: int,
        message_id: int | None,
        lane_key: str | None,
    ) -> None:
        if message_id is None or not lane_key:
            return
        self._message_lanes[(chat_id, message_id)] = (lane_key, time.monotonic())

    def _lookup_message_lane(self, chat_id: int, message_id: int | None) -> str | None:
        if message_id is None:
            return None
        entry = self._message_lanes.get((chat_id, message_id))
        if entry is None:
            return None
        lane_key, _timestamp = entry
        return lane_key

    def _lookup_reply_to_bot(self, chat_id: int, message_id: int | None) -> str | None:
        """Extract the bot name from a previously tracked outbound message.

        Lane keys follow the format ``{chat_id}:{thread}:{bot_name}`` (or
        ``{chat_id}:{thread}:{bot_name}:m{msg_id}`` in private chats).
        The third segment is always the bot name.
        """
        lane_key = self._lookup_message_lane(chat_id, message_id)
        if lane_key is None:
            return None
        parts = lane_key.split(":")
        if len(parts) >= 3:
            return parts[2]
        return None

    def _build_surface_context(self, bot: BotInstance) -> dict[str, Any]:
        peers = [
            {
                "name": peer.name,
                "username": peer.bot_username,
            }
            for peer in self._bots.values()
            if peer.name != bot.name and peer.bot_username
        ]
        return {
            "identity": {
                "name": bot.name,
                "role": "messaging_assistant",
                "username": bot.bot_username,
                "personality": bot.personality,
                "project_focus": list(bot.projects),
            },
            "peers": peers,
        }

    def _remember_outbound_message(
        self,
        chat_id: int,
        message_id: int | None,
        *,
        lane_key: str | None = None,
    ) -> None:
        """Pre-seed dedup for bot messages that will later arrive via getUpdates."""
        if message_id is None:
            return
        now = time.monotonic()
        self._seen[(chat_id, message_id)] = now
        self._last_outbound[chat_id] = (message_id, now)
        if lane_key:
            self._message_lanes[(chat_id, message_id)] = (lane_key, now)

    @staticmethod
    def _format_elapsed_seconds(elapsed_seconds: float) -> str:
        seconds = max(1, int(round(elapsed_seconds)))
        if seconds < 60:
            return f"{seconds}s elapsed"
        minutes, seconds = divmod(seconds, 60)
        if seconds == 0:
            return f"{minutes}m elapsed"
        return f"{minutes}m {seconds}s elapsed"

    def _format_queue_hint(
        self,
        queue_position: int,
        elapsed_seconds: float,
        *,
        hint_count: int,
    ) -> str:
        pos = max(int(queue_position or 0), 1)
        if hint_count == 0:
            return f"Queued (position {pos}) — I'll reply when ready."
        return (
            f"Queued (position {pos}) — I'll reply when ready. "
            f"({self._format_elapsed_seconds(elapsed_seconds)})"
        )

    @staticmethod
    def _format_non_text_delivery_status(
        delivered_file_count: int,
        delivered_poll_count: int,
        failed_file_count: int = 0,
        failed_poll_count: int = 0,
    ) -> str | None:
        def _noun(count: int, singular: str, plural: str) -> str:
            return singular if count == 1 else plural

        def _join(parts: list[str]) -> str:
            if not parts:
                return ""
            if len(parts) == 1:
                return parts[0]
            if len(parts) == 2:
                return f"{parts[0]} and {parts[1]}"
            return f"{', '.join(parts[:-1])}, and {parts[-1]}"

        success_parts: list[str] = []
        failure_parts: list[str] = []
        if delivered_file_count > 0:
            success_parts.append(
                f"delivered the requested "
                f"{_noun(delivered_file_count, 'file', 'files')}",
            )
        if delivered_poll_count > 0:
            success_parts.append(
                f"sent the requested "
                f"{_noun(delivered_poll_count, 'poll', 'polls')}",
            )
        if failed_file_count > 0:
            failure_parts.append(
                f"couldn't deliver the requested "
                f"{_noun(failed_file_count, 'file', 'files')}",
            )
        if failed_poll_count > 0:
            failure_parts.append(
                f"couldn't send the requested "
                f"{_noun(failed_poll_count, 'poll', 'polls')}",
            )
        if not success_parts and not failure_parts:
            return None
        if success_parts and not failure_parts:
            sentence = _join(success_parts)
            return f"{sentence[0].upper()}{sentence[1:]}."
        if failure_parts and not success_parts:
            return f"I {_join(failure_parts)}."
        return f"I {_join(success_parts)}, but I {_join(failure_parts)}."

    async def _append_history_turn(
        self,
        lane_key: str,
        turn: dict[str, str],
    ) -> list[dict[str, str]]:
        history_lock = self._history_locks.setdefault(lane_key, asyncio.Lock())
        async with history_lock:
            history = list(self._conversation_history.get(lane_key, []))
            history.append(turn)
            if len(history) > 40:
                history = history[-40:]
            self._conversation_history[lane_key] = history
            return list(history)

    async def _remove_history_turn(
        self,
        lane_key: str,
        turn: dict[str, str],
    ) -> None:
        history_lock = self._history_locks.setdefault(lane_key, asyncio.Lock())
        async with history_lock:
            history = list(self._conversation_history.get(lane_key, []))
            for idx in range(len(history) - 1, -1, -1):
                if history[idx] is turn:
                    del history[idx]
                    break
            self._conversation_history[lane_key] = history

    async def _append_assistant_turn(self, lane_key: str, content: str) -> None:
        if not content:
            return
        await self._append_history_turn(
            lane_key,
            {"role": "assistant", "content": content},
        )

    def _start_cleanup_task(self) -> None:
        async def _cleanup_loop() -> None:
            while not self._stop_event.is_set():
                await asyncio.sleep(30)
                self._evict_seen()

        task = asyncio.create_task(_cleanup_loop())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _start_control_watcher(self) -> None:
        ctl_file = Path.home() / ".dan" / "telegram" / "fleet.ctl"

        async def _watch_loop() -> None:
            while not self._stop_event.is_set():
                await asyncio.sleep(2)
                if not ctl_file.exists():
                    continue
                try:
                    cmd = ctl_file.read_text().strip()
                    ctl_file.unlink(missing_ok=True)
                    if cmd.startswith("stop:"):
                        bot_name = cmd[5:].strip()
                        stopped = await self.stop_bot(bot_name)
                        if stopped:
                            logger.info("Control: stopped bot %s", bot_name)
                        else:
                            logger.warning(
                                "Control: bot '%s' not found", bot_name,
                            )
                except Exception as exc:
                    logger.warning("Control file error: %s", exc)

        task = asyncio.create_task(_watch_loop())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # -- message handling ---------------------------------------------------

    def _make_callback(self, bot: BotInstance):
        async def _on_message(
            ext_id: str, text: str, ctx: MessageContext,
        ) -> None:
            if self._check_dedup(
                ctx.chat_id, ctx.message_id, ctx.ingress_dedup_key,
            ):
                return

            if _is_bot_authored_message(ctx, self._bots):
                return

            routable_bots = self._routable_bots()
            reply_to_bot = self._lookup_reply_to_bot(
                ctx.chat_id, ctx.reply_to_message_id,
            )
            winner = self._router.route(
                text=text,
                mentions=ctx.mentions,
                chat_type=ctx.chat_type,
                chat_id=ctx.chat_id,
                thread_id=ctx.thread_id,
                from_user_is_bot=ctx.from_user_is_bot,
                from_user_id=ctx.from_user_id,
                bots=routable_bots,
                receiving_bot_username=bot.bot_username,
                reply_to_bot=reply_to_bot,
            )

            if winner is None:
                return

            selected = self._bots.get(winner.name)
            if selected is None:
                return
            if selected.allowed_users and not _user_allowed(
                selected.allowed_users, ctx.from_user_id, ctx.from_user_username,
            ):
                return

            task = asyncio.create_task(
                self._dispatch(selected, ext_id, text, ctx),
            )
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)

        return _on_message

    def _routable_bots(self) -> list[RoutableBot]:
        return [
            RoutableBot(
                name=b.name,
                username=b.bot_username,
                projects=b.projects,
                is_default=b.is_default,
            )
            for b in self._bots.values()
        ]

    # -- server dispatch ----------------------------------------------------

    async def _dispatch(
        self,
        bot: BotInstance,
        ext_id: str,
        text: str,
        ctx: MessageContext,
    ) -> None:
        if bot.adapter is None or self._http is None:
            return

        settings = self._config.settings

        if settings.use_reactions:
            await bot.adapter.set_reaction(ctx.chat_id, ctx.message_id, "⏳")

        conversation_key = _conversation_thread_key(ctx, bot.name)
        lane_key = _conversation_lane_key(
            ctx,
            bot.name,
            reply_lane_key=self._lookup_message_lane(
                ctx.chat_id,
                ctx.reply_to_message_id,
            ),
        )
        self._remember_message_lane(ctx.chat_id, ctx.message_id, lane_key)
        surface = f"telegram:{bot.name}"
        user_turn: dict[str, str] | None = None

        try:
            wf_id = await self._ensure_scratch(conversation_key)

            att_path = _extract_attachment(text)
            voice_path = _extract_voice_note(text)
            msg_text = text

            if voice_path:
                transcribed = await _transcribe_voice_note(voice_path)
                if transcribed:
                    msg_text = _merge_voice_context(text, transcribed)
                else:
                    sent_message_id = await bot.adapter._send_text(
                        ctx.chat_id,
                        "I received your voice message but couldn't transcribe it. "
                        "Please send as text.",
                        reply_to=ctx.message_id,
                        thread_id=ctx.thread_id,
                    )
                    self._remember_outbound_message(
                        ctx.chat_id,
                        sent_message_id,
                        lane_key=lane_key,
                    )
                    if settings.use_reactions:
                        await bot.adapter.set_reaction(
                            ctx.chat_id,
                            ctx.message_id,
                            "❌",
                        )
                    return
            elif att_path and att_path.lower().endswith(".pdf"):
                msg_text = (
                    f"Please review this PDF: {att_path}\n{_strip_media_marker(text)}"
                    if _strip_media_marker(text)
                    else f"Please review this PDF: {att_path}"
                )

            user_turn = {"role": "user", "content": msg_text}
            history = await self._append_history_turn(lane_key, user_turn)
            body: dict[str, Any] = {
                "workflow_id": wf_id,
                "message": msg_text,
                "history": history,
                "thread_id": conversation_key,
                "mode": "auto",
                "surface": surface,
                "surface_context": self._build_surface_context(bot),
            }
            if att_path:
                body["attachment_path"] = att_path

            resp = await self._http.post("/api/chat/message", json=body)
            if resp.status_code != 200:
                await self._remove_history_turn(lane_key, user_turn)
                if settings.use_reactions:
                    await bot.adapter.set_reaction(
                        ctx.chat_id, ctx.message_id, "❌",
                    )
                try:
                    await bot.adapter._send_text(
                        ctx.chat_id,
                        "Sorry, I couldn't process that — please try again.",
                        reply_to=ctx.message_id,
                        thread_id=ctx.thread_id,
                    )
                except Exception:
                    pass
                return

            payload = resp.json()
            channel_id = payload.get("stream_channel_id")

            if not channel_id:
                content = payload.get("content", "")
                if content:
                    content = _format_for_telegram(content)
                    await self._send_reply(
                        bot,
                        ctx,
                        content,
                        lane_key=lane_key,
                        already_cleaned=True,
                        thread_id=ctx.thread_id,
                    )
                    await self._append_assistant_turn(lane_key, content)
                if settings.use_reactions:
                    await bot.adapter.set_reaction(
                        ctx.chat_id, ctx.message_id, "✅",
                    )
                return

            if settings.streaming_edits:
                full_reply = await self._stream_with_edits(
                    bot,
                    ctx,
                    channel_id,
                    lane_key=lane_key,
                )
            else:
                full_reply = await self._stream_collect(
                    bot,
                    ctx,
                    channel_id,
                    lane_key=lane_key,
                )

            if full_reply:
                await self._append_assistant_turn(lane_key, full_reply)

            if settings.use_reactions:
                await bot.adapter.set_reaction(
                    ctx.chat_id, ctx.message_id, "✅",
                )

        except Exception as exc:
            logger.exception("Fleet dispatch failed for %s: %s", bot.name, exc)
            if user_turn is not None:
                await self._remove_history_turn(lane_key, user_turn)
            try:
                await bot.adapter._send_text(
                    ctx.chat_id,
                    "Sorry, something went wrong — please try again.",
                    reply_to=ctx.message_id,
                    thread_id=ctx.thread_id,
                )
            except Exception:
                pass
            if settings.use_reactions:
                await bot.adapter.set_reaction(
                    ctx.chat_id, ctx.message_id, "❌",
                )

    async def _stream_with_edits(
        self,
        bot: BotInstance,
        ctx: MessageContext,
        channel_id: str,
        *,
        lane_key: str | None = None,
    ) -> str:
        """Process WS events incrementally, editing the message in place.

        The progress timer lives here (not in the concierge) so that
        messaging surfaces own their own UX:
        - No progress bubble for fast replies
        - After ``_PROGRESS_INITIAL_DELAY`` seconds of silence, show a
          compact status that is edited in-place every
          ``_PROGRESS_REPEAT_INTERVAL`` seconds with elapsed time.
        """
        assert bot.adapter is not None

        collected_tokens: list[str] = []
        current_msg_id: int | None = None
        sent_prefix_len = 0
        last_edit_time = 0.0
        edit_interval = 0.5
        file_paths: list[str] = []
        error_message = ""
        complete_content = ""
        delivered_poll_count = 0
        failed_poll_count = 0

        stream_start = time.monotonic()
        progress_count = 0
        got_content = False
        progress_msg_id: int | None = None
        current_repeat_interval = self._PROGRESS_REPEAT_INTERVAL
        last_progress_time = 0.0

        async def _show_progress() -> None:
            nonlocal current_msg_id, progress_count, progress_msg_id
            nonlocal current_repeat_interval, last_progress_time
            elapsed = time.monotonic() - stream_start
            prefix = "Working on it" if progress_count == 0 else "Still working"
            text = (
                f"{prefix} — preparing response "
                f"({self._format_elapsed_seconds(elapsed)})"
            )

            latest = self._latest_outbound_id(ctx.chat_id)
            chat_has_moved_on = (
                progress_msg_id is not None
                and latest is not None
                and latest != progress_msg_id
            )

            if chat_has_moved_on:
                progress_msg_id = None
                current_msg_id = None

            edit_target = progress_msg_id if progress_msg_id is not None else current_msg_id
            new_id = await bot.adapter.send_or_edit(
                ctx.chat_id,
                text,
                edit_target,
                reply_to=(
                    ctx.message_id if edit_target is None else None
                ),
                thread_id=ctx.thread_id,
            )
            if new_id is not None:
                progress_msg_id = new_id
                current_msg_id = new_id
            self._remember_outbound_message(
                ctx.chat_id, progress_msg_id, lane_key=lane_key,
            )
            progress_count += 1
            last_progress_time = time.monotonic()

            current_repeat_interval = min(
                current_repeat_interval * self._PROGRESS_BACKOFF_FACTOR,
                self._PROGRESS_MAX_INTERVAL,
            )

        _sentinel = object()
        event_queue: asyncio.Queue[Any] = asyncio.Queue()

        async def _drain_stream() -> None:
            try:
                async for ev in self._iter_chat_stream_events(channel_id):
                    await event_queue.put(ev)
            except Exception as exc:
                await event_queue.put(exc)
            finally:
                await event_queue.put(_sentinel)

        drain_task = asyncio.create_task(_drain_stream())

        try:
            while True:
                if not got_content and progress_count == 0:
                    timeout: float | None = self._PROGRESS_INITIAL_DELAY
                elif not got_content:
                    timeout = current_repeat_interval
                else:
                    timeout = None

                try:
                    if timeout is not None:
                        item = await asyncio.wait_for(
                            event_queue.get(), timeout=timeout,
                        )
                    else:
                        item = await event_queue.get()
                except asyncio.TimeoutError:
                    await _show_progress()
                    continue

                if item is _sentinel:
                    break
                if isinstance(item, Exception):
                    raise item
                event = item
                evt_type = event.get("type", "")

                if evt_type == "chat_token":
                    got_content = True
                    collected_tokens.append(event.get("delta", event.get("token", "")))
                    now = time.monotonic()
                    if now - last_edit_time >= edit_interval:
                        text = _format_for_telegram(
                            "".join(collected_tokens).strip(),
                        )
                        chunk_text = text[sent_prefix_len:]
                        if chunk_text:
                            if len(chunk_text) > 4000 and current_msg_id is not None:
                                finalized = chunk_text[:4096]
                                await bot.adapter.send_or_edit(
                                    ctx.chat_id,
                                    finalized,
                                    current_msg_id,
                                    thread_id=ctx.thread_id,
                                )
                                sent_prefix_len += len(finalized)
                                current_msg_id = None
                                chunk_text = text[sent_prefix_len:]
                            if chunk_text:
                                current_msg_id = await bot.adapter.send_or_edit(
                                    ctx.chat_id,
                                    chunk_text,
                                    current_msg_id,
                                    reply_to=(
                                        ctx.message_id
                                        if current_msg_id is None
                                        else None
                                    ),
                                    thread_id=ctx.thread_id,
                                )
                                self._remember_outbound_message(
                                    ctx.chat_id,
                                    current_msg_id,
                                    lane_key=lane_key,
                                )
                            last_edit_time = now

                elif evt_type == "chat_file_attachment":
                    path = event.get("path", "")
                    if path:
                        file_paths.append(path)

                elif evt_type == "chat_poll_request":
                    q = event.get("question", "")
                    opts = event.get("options", [])
                    if q and opts:
                        poll_id = await bot.adapter.send_poll(
                            ctx.chat_id, q, opts,
                            is_anonymous=event.get("is_anonymous", False),
                            allows_multiple=event.get("allows_multiple", False),
                            reply_to=ctx.message_id,
                            thread_id=ctx.thread_id,
                        )
                        if poll_id:
                            delivered_poll_count += 1
                        else:
                            failed_poll_count += 1

                elif evt_type in (
                    "chat_complete",
                    "chat_interrupted",
                ):
                    if event.get("detected_mode") == "progress_ack":
                        phase_label = (event.get("phase_label") or "").strip()
                        text = phase_label or (event.get("content", "") or "").strip()
                        if text:
                            current_msg_id = await bot.adapter.send_or_edit(
                                ctx.chat_id,
                                text,
                                current_msg_id,
                                reply_to=(
                                    ctx.message_id
                                    if current_msg_id is None
                                    else None
                                ),
                                thread_id=ctx.thread_id,
                            )
                            self._remember_outbound_message(
                                ctx.chat_id,
                                current_msg_id,
                                lane_key=lane_key,
                            )
                        continue
                    complete_content = event.get("content", "") or ""
                    break

                elif evt_type == "chat_error":
                    error_message = str(
                        event.get("error", "") or "",
                    ).strip()
                    break

        except Exception as exc:
            logger.error("WS stream error: %s", exc)
            error_message = str(exc)
        finally:
            if not drain_task.done():
                drain_task.cancel()
                try:
                    await drain_task
                except (asyncio.CancelledError, Exception):
                    pass

        streamed_full = "".join(collected_tokens).strip()
        from dan.cli.adapter import _strip_function_call_xml

        streamed_full = _strip_function_call_xml(streamed_full)
        used_complete = False
        if complete_content:
            complete_content = _strip_function_call_xml(complete_content)
            if not streamed_full:
                full = complete_content
                used_complete = True
            elif complete_content and len(complete_content) > 20:
                full = complete_content
                used_complete = True
            else:
                full = streamed_full
        elif error_message:
            friendly_error = _format_telegram_stream_error(error_message)
            full = (
                f"{streamed_full}\n\n{friendly_error}".strip()
                if streamed_full
                else friendly_error
            )
        else:
            full = streamed_full

        if full:
            clean = _format_for_telegram(full)
            if used_complete and current_msg_id is not None:
                current_msg_id = await bot.adapter.send_or_edit(
                    ctx.chat_id, clean[:4096], current_msg_id,
                    thread_id=ctx.thread_id,
                )
                self._remember_outbound_message(
                    ctx.chat_id,
                    current_msg_id,
                    lane_key=lane_key,
                )
                remaining = clean[4096:]
                for chunk in _split_message(remaining):
                    sent_message_id = await bot.adapter.send_or_edit(
                        ctx.chat_id, chunk, thread_id=ctx.thread_id,
                    )
                    self._remember_outbound_message(
                        ctx.chat_id,
                        sent_message_id,
                        lane_key=lane_key,
                    )
            elif used_complete:
                await self._send_reply(
                    bot, ctx, clean, lane_key=lane_key,
                    already_cleaned=True, thread_id=ctx.thread_id,
                )
            else:
                remaining = clean[sent_prefix_len:]
                if current_msg_id is not None and remaining:
                    current_msg_id = await bot.adapter.send_or_edit(
                        ctx.chat_id, remaining[:4096], current_msg_id,
                        thread_id=ctx.thread_id,
                    )
                    self._remember_outbound_message(
                        ctx.chat_id,
                        current_msg_id,
                        lane_key=lane_key,
                    )
                    sent_prefix_len += min(len(remaining), 4096)
                    remaining = clean[sent_prefix_len:]
                if remaining:
                    for chunk in _split_message(remaining):
                        sent_message_id = await bot.adapter.send_or_edit(
                            ctx.chat_id, chunk, thread_id=ctx.thread_id,
                        )
                        self._remember_outbound_message(
                            ctx.chat_id,
                            sent_message_id,
                            lane_key=lane_key,
                        )
                elif current_msg_id is None:
                    await self._send_reply(
                        bot, ctx, clean, lane_key=lane_key,
                        already_cleaned=True, thread_id=ctx.thread_id,
                    )

        delivered_file_count = 0
        failed_file_count = 0
        for fp in file_paths:
            if bot.adapter:
                try:
                    sent_message_id = await bot.adapter._send_file_to_chat(
                        ctx.chat_id,
                        fp,
                        thread_id=ctx.thread_id,
                    )
                    self._remember_outbound_message(
                        ctx.chat_id,
                        sent_message_id,
                        lane_key=lane_key,
                    )
                    if self._config.settings.auto_pin_deliverables:
                        last_id = bot.adapter._last_bot_message_id.get(ctx.chat_id)
                        if last_id:
                            await bot.adapter.pin_message(ctx.chat_id, last_id)
                    if sent_message_id is not None:
                        delivered_file_count += 1
                    else:
                        failed_file_count += 1
                except Exception as exc:
                    failed_file_count += 1
                    logger.warning("Failed to send file %s: %s", fp, exc)

        if not full:
            delivery_status = self._format_non_text_delivery_status(
                delivered_file_count,
                delivered_poll_count,
                failed_file_count,
                failed_poll_count,
            )
            should_surface_status = (
                delivery_status is not None
                and (
                    current_msg_id is not None
                    or failed_file_count > 0
                    or failed_poll_count > 0
                )
            )
            if should_surface_status:
                if current_msg_id is not None:
                    current_msg_id = await bot.adapter.send_or_edit(
                        ctx.chat_id,
                        delivery_status,
                        current_msg_id,
                        thread_id=ctx.thread_id,
                    )
                    self._remember_outbound_message(
                        ctx.chat_id,
                        current_msg_id,
                        lane_key=lane_key,
                    )
                else:
                    await self._send_reply(
                        bot,
                        ctx,
                        delivery_status,
                        lane_key=lane_key,
                        thread_id=ctx.thread_id,
                    )
                full = delivery_status

        return full

    async def _stream_collect(
        self,
        bot: BotInstance,
        ctx: MessageContext,
        channel_id: str,
        *,
        lane_key: str | None = None,
    ) -> str:
        """Collect all events then send final reply (non-streaming mode)."""
        assert bot.adapter is not None

        stream_events: list[dict[str, Any]] = []
        try:
            from dan.cli.adapter import _is_progress_ack_event
            async for event in self._iter_chat_stream_events(channel_id):
                if isinstance(event, dict):
                    stream_events.append(event)
                if (
                    isinstance(event, dict)
                    and event.get("type") == "chat_complete"
                    and _is_progress_ack_event(event)
                ):
                    continue
                if isinstance(event, dict) and event.get("type") in (
                    "chat_complete",
                    "chat_interrupted",
                    "chat_error",
                ):
                    break
        except Exception as exc:
            logger.error("WS stream error: %s", exc)

        from dan.cli.adapter import _consume_chat_stream_events

        full_reply, _mutation, file_paths, poll_requests = _consume_chat_stream_events(
            stream_events,
        )

        if full_reply:
            clean = _format_for_telegram(full_reply)
            await self._send_reply(
                bot, ctx, clean, lane_key=lane_key,
                already_cleaned=True, thread_id=ctx.thread_id,
            )

        for fp in file_paths:
            if bot.adapter:
                try:
                    sent_message_id = await bot.adapter._send_file_to_chat(
                        ctx.chat_id,
                        fp,
                        thread_id=ctx.thread_id,
                    )
                    self._remember_outbound_message(
                        ctx.chat_id,
                        sent_message_id,
                        lane_key=lane_key,
                    )
                    if self._config.settings.auto_pin_deliverables:
                        last_id = bot.adapter._last_bot_message_id.get(ctx.chat_id)
                        if last_id:
                            await bot.adapter.pin_message(ctx.chat_id, last_id)
                except Exception as exc:
                    logger.warning("Failed to send file %s: %s", fp, exc)

        for pr in poll_requests:
            if bot.adapter:
                try:
                    await bot.adapter.send_poll(
                        ctx.chat_id,
                        pr.get("question", ""),
                        pr.get("options", []),
                        is_anonymous=pr.get("is_anonymous", False),
                        allows_multiple=pr.get("allows_multiple", False),
                        reply_to=ctx.message_id,
                        thread_id=ctx.thread_id,
                    )
                except Exception as exc:
                    logger.warning("Failed to send poll: %s", exc)

        return full_reply

    async def _iter_chat_stream_events(
        self,
        channel_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield chat events, following queued-channel redirects when present."""
        import websockets

        ws_url = self._server_url.replace("http://", "ws://").replace(
            "https://", "wss://",
        )
        next_channel = channel_id
        seen_channels: set[str] = set()
        queue_position: int | None = None
        queued_started_at: float | None = None
        queue_hint_count = 0

        def _handle_queue_redirect(event: dict[str, Any]) -> str:
            nonlocal queue_position, queued_started_at, queue_hint_count
            queue_position = max(int(event.get("queue_position", 0) or 0), 1)
            if queued_started_at is None:
                queued_started_at = time.monotonic()
            queue_hint_count = 0
            return str(event.get("stream_channel_id") or "").strip()

        while next_channel:
            current_channel = next_channel
            next_channel = ""
            if current_channel in seen_channels:
                logger.warning(
                    "Skipping repeated queued channel redirect for %s",
                    current_channel,
                )
                break
            seen_channels.add(current_channel)
            url = f"{ws_url}/api/chat/{current_channel}/events"
            async with websockets.connect(
                url, ping_interval=None, ping_timeout=None,
            ) as ws:
                if not hasattr(ws, "recv"):
                    async for ws_msg in ws:
                        event = json.loads(ws_msg)
                        if not isinstance(event, dict):
                            continue
                        if event.get("type") == "chat_queued":
                            redirected = _handle_queue_redirect(event)
                            if redirected and redirected not in seen_channels:
                                next_channel = redirected
                                break
                            continue
                        queue_position = None
                        queued_started_at = None
                        queue_hint_count = 0
                        yield event
                    continue

                while True:
                    timeout: float | None = None
                    if queue_position is not None and queued_started_at is not None:
                        timeout = (
                            self._QUEUE_HINT_INITIAL_DELAY
                            if queue_hint_count == 0
                            else self._QUEUE_HINT_REPEAT_INTERVAL
                        )
                    try:
                        if timeout is None:
                            ws_msg = await ws.recv()
                        else:
                            ws_msg = await asyncio.wait_for(ws.recv(), timeout)
                    except asyncio.TimeoutError:
                        if queue_position is None or queued_started_at is None:
                            continue
                        yield {
                            "type": "chat_complete",
                            "content": self._format_queue_hint(
                                queue_position,
                                time.monotonic() - queued_started_at,
                                hint_count=queue_hint_count,
                            ),
                            "detected_mode": "progress_ack",
                        }
                        queue_hint_count += 1
                        continue
                    except Exception as exc:
                        if exc.__class__.__name__.startswith("ConnectionClosed"):
                            break
                        raise
                    event = json.loads(ws_msg)
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "chat_queued":
                        redirected = _handle_queue_redirect(event)
                        if redirected and redirected not in seen_channels:
                            next_channel = redirected
                            break
                        continue
                    queue_position = None
                    queued_started_at = None
                    queue_hint_count = 0
                    yield event

    async def _send_reply(
        self,
        bot: BotInstance,
        ctx: MessageContext,
        text: str,
        *,
        lane_key: str | None = None,
        already_cleaned: bool = False,
        thread_id: int | None = None,
    ) -> None:
        assert bot.adapter is not None
        from dan.server.concierge.actions import (
            split_message_for_surface,
            strip_html_for_messaging,
        )

        clean = text if already_cleaned else strip_html_for_messaging(text)
        parts = split_message_for_surface(clean, "telegram")
        for i, part in enumerate(parts):
            reply_to = ctx.message_id if i == 0 else None
            sent_message_id = await bot.adapter._send_text(
                ctx.chat_id, part, reply_to=reply_to,
                thread_id=thread_id,
            )
            self._remember_outbound_message(
                ctx.chat_id,
                sent_message_id,
                lane_key=lane_key,
            )
            if len(parts) > 1:
                await asyncio.sleep(0.3)

    async def _ensure_scratch(self, thread_key: str) -> str:
        wf_id = self._conversation_workflows.get(thread_key)
        if wf_id:
            return wf_id

        sanitized = re.sub(r"[^A-Za-z0-9]", "", thread_key)[-16:] or "fleet"
        wf_id = f"_fleet_{sanitized}"
        try:
            resp = await self._http.get(f"/api/graphs/{wf_id}")
            if resp.status_code == 404:
                await self._http.post(
                    "/api/graphs",
                    json={
                        "graph_id": wf_id,
                        "data": {"nodes": [], "edges": []},
                    },
                )
        except Exception:
            pass
        self._conversation_workflows[thread_key] = wf_id
        return wf_id

    @classmethod
    def from_config(
        cls,
        config_path: str | Path | None = None,
        server_url: str | None = None,
    ) -> BotFleet:
        config = load_fleet_config(config_path)
        return cls(config, config_path=config_path, server_url=server_url)

    @classmethod
    def load_from_config(
        cls,
        config_path: str | Path | None = None,
        server_url: str | None = None,
    ) -> BotFleet:
        return cls.from_config(config_path, server_url=server_url)

    async def _ensure_topics(self) -> None:
        creator = self._default_or_first_bot()
        if creator is None or creator.adapter is None or creator.adapter._application is None:
            return

        changed = False
        all_projects = sorted(
            {project for bot in self._bots.values() for project in bot.projects},
        )

        for gid_str, gcfg in self._config.groups.items():
            if not gcfg.forum_topics:
                continue
            chat_id = int(gid_str)
            try:
                chat = await creator.adapter._application.bot.get_chat(chat_id)
            except Exception:
                continue
            if not getattr(chat, "is_forum", False):
                continue

            desired_projects = ["general", *all_projects]
            existing = set(gcfg.topic_map.values())
            for project in desired_projects:
                if project in existing:
                    continue
                try:
                    topic = await creator.adapter._application.bot.create_forum_topic(
                        chat_id=chat_id,
                        name=project,
                    )
                    gcfg.topic_map[str(topic.message_thread_id)] = project
                    changed = True
                except Exception:
                    logger.debug(
                        "Unable to create topic '%s' in %s",
                        project,
                        chat_id,
                    )

        if changed:
            self._refresh_router()
            save_fleet_config(self._config, self._config_path)

    def _default_or_first_bot(self) -> BotInstance | None:
        for bot in self._bots.values():
            if bot.is_default:
                return bot
        return next(iter(self._bots.values()), None)

    def _infer_project_commands(self, bot: BotInstance) -> list[tuple[str, str]]:
        commands: list[tuple[str, str]] = [
            ("help", "Show available commands"),
            ("status", "Check current task status"),
            ("cancel", "Cancel current task"),
        ]
        project_text = " ".join(bot.projects).lower()
        if "research" in project_text or "literature" in project_text:
            commands.append(("search", "Search literature"))
        if "data" in project_text or "equity" in project_text or "analysis" in project_text:
            commands.append(("analyze", "Analyze a dataset or report"))
        return commands[:10]

    async def _on_topic_created(
        self,
        bot_name: str,
        chat_id: int,
        thread_id: int,
        topic_name: str,
    ) -> None:
        group = self._config.groups.setdefault(str(chat_id), TelegramGroupConfig())
        group.forum_topics = True
        group.topic_map[str(thread_id)] = topic_name
        bot = self._config.bots.get(bot_name)
        if bot is not None and topic_name not in bot.projects:
            bot.projects.append(topic_name)
        live_bot = self._bots.get(bot_name)
        if live_bot is not None and topic_name not in live_bot.projects:
            live_bot.projects.append(topic_name)
        self._refresh_router()
        save_fleet_config(self._config, self._config_path)

    def _refresh_router(self) -> None:
        topic_maps: dict[int, dict[int, str]] = {}
        for gid_str, gcfg in self._config.groups.items():
            topic_maps[int(gid_str)] = {
                int(k): v for k, v in gcfg.topic_map.items()
            }
        self._router = MessageRouter(multi_group_topic_maps=topic_maps)


# -- helpers ----------------------------------------------------------------


def _extract_attachment(text: str) -> str | None:
    clean = _strip_reply_prefix_lines(text)
    if clean.startswith("[Attachment: "):
        end = clean.find("]\n")
        if end > 0:
            return clean[len("[Attachment: ") : end].strip()
        if clean.endswith("]"):
            return clean[len("[Attachment: ") : -1].strip()
    return None


def _extract_voice_note(text: str) -> str | None:
    clean = _strip_reply_prefix_lines(text)
    if clean.startswith("[Voice note: "):
        end = clean.find("]")
        if end > 0:
            return clean[len("[Voice note: ") : end].strip()
    return None


async def _transcribe_voice_note(path: str) -> str | None:
    try:
        from dan.adapters.whatsapp_web_adapter import transcribe_audio

        return await transcribe_audio(path)
    except Exception:
        return None


def _strip_reply_prefix_lines(text: str) -> str:
    lines = text.splitlines()
    while lines and lines[0].startswith('[Replying to: "'):
        lines.pop(0)
    return "\n".join(lines).strip()


def _split_reply_prefix_lines(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    prefix_lines: list[str] = []
    while lines and lines[0].startswith('[Replying to: "'):
        prefix_lines.append(lines.pop(0))
    return "\n".join(prefix_lines).strip(), "\n".join(lines).strip()


def _strip_media_marker(text: str) -> str:
    reply_prefix, body = _split_reply_prefix_lines(text)
    if body.startswith("[Attachment: "):
        end = body.find("]\n")
        if end > 0:
            body = body[end + 2 :].strip()
        elif body.endswith("]"):
            body = ""
    elif body.startswith("[Voice note: "):
        end = body.find("]")
        body = body[end + 1 :].strip() if end > 0 else ""
    return "\n".join(part for part in (reply_prefix, body) if part).strip()


def _merge_voice_context(text: str, transcription: str) -> str:
    reply_prefix, body = _split_reply_prefix_lines(text)
    remainder = body
    if body.startswith("[Voice note: "):
        end = body.find("]")
        remainder = body[end + 1 :].strip() if end > 0 else ""
    return "\n".join(
        part for part in (reply_prefix, transcription, remainder) if part
    ).strip()


def _conversation_thread_key(ctx: MessageContext, bot_name: str) -> str:
    thread_part = str(ctx.thread_id) if ctx.thread_id is not None else "main"
    return f"{ctx.chat_id}:{thread_part}:{bot_name}"


def _conversation_lane_key(
    ctx: MessageContext,
    bot_name: str,
    reply_lane_key: str | None = None,
) -> str:
    conversation_key = _conversation_thread_key(ctx, bot_name)
    if ctx.thread_id is not None or ctx.chat_type != "private":
        return conversation_key
    if reply_lane_key:
        return reply_lane_key
    return f"{conversation_key}:m{ctx.message_id}"


def _user_allowed(
    allowed: list[int | str],
    user_id: int | None,
    username: str | None,
) -> bool:
    """Check if a user matches the allowed list (int IDs or str usernames)."""
    for entry in allowed:
        if isinstance(entry, int) and user_id is not None and entry == user_id:
            return True
        if isinstance(entry, str) and username is not None:
            clean = entry.lstrip("@")
            if clean.lower() == username.lower():
                return True
    return False


def _is_bot_authored_message(
    ctx: MessageContext,
    bots: dict[str, BotInstance],
) -> bool:
    if ctx.from_user_is_bot:
        return True
    bot_ids = {bot.bot_id for bot in bots.values() if bot.bot_id is not None}
    if ctx.from_user_id is not None and ctx.from_user_id in bot_ids:
        return True
    bot_usernames = {
        bot.bot_username.lstrip("@").lower()
        for bot in bots.values()
        if bot.bot_username
    }
    for username in (ctx.from_user_username, ctx.sender_chat_username):
        if username and username.lstrip("@").lower() in bot_usernames:
            return True
    return False


def _format_for_telegram(text: str) -> str:
    """Convert concierge content for Telegram display.

    Transforms ``[DAN - Project] body`` → ``[Project]\\nbody`` so the user
    sees a compact project header while ``reply_to_message_id`` gives the
    conversational context.  HTML is also stripped.
    """
    from dan.server.concierge.identity import extract_label_from_prefix
    from dan.server.concierge.actions import strip_html_for_messaging

    label, remaining = extract_label_from_prefix(text)
    remaining = strip_html_for_messaging(remaining)
    if label:
        return f"[{label}]\n{remaining}"
    return remaining


def _format_telegram_stream_error(error_message: str) -> str:
    """Collapse raw backend/provider failures into Telegram-friendly copy."""
    message = str(error_message or "").strip()
    lower = message.lower()
    overload_markers = (
        "429",
        "rate limit",
        "quota",
        "upstream_error",
        "overload",
        "overloaded",
        "saturated",
        "请稍后再试",
        "负载已饱和",
    )
    if any(marker in lower for marker in overload_markers):
        return "The model provider is temporarily overloaded. Please try again in a moment."
    if not message:
        return "I hit an error while processing your message. Please try again."
    return f"I hit an error: {message}"


def _strip_prefix_and_html(text: str) -> str:
    """Legacy alias kept for any callers that still import this name."""
    return _format_for_telegram(text)


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


_FLEET_LOCK_ACQUIRE_MAX_ATTEMPTS = 5
_FLEET_LOCK_PENDING_GRACE_SECONDS = 0.2


def _acquire_fleet_lock(lock_path: Path | None = None) -> None:
    lock_path = lock_path or _FLEET_LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    attempts = 0

    while attempts < _FLEET_LOCK_ACQUIRE_MAX_ATTEMPTS:
        attempts += 1
        try:
            fd = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError:
            try:
                existing_pid = int(lock_path.read_text().strip())
            except (OSError, ValueError):
                existing_pid = None

            if existing_pid is not None and _pid_is_running(existing_pid):
                raise FleetAlreadyRunningError(
                    f"Telegram fleet already running (PID {existing_pid}).",
                )

            if existing_pid is None:
                try:
                    age_seconds = max(0.0, time.time() - lock_path.stat().st_mtime)
                except OSError:
                    age_seconds = _FLEET_LOCK_PENDING_GRACE_SECONDS
                if age_seconds < _FLEET_LOCK_PENDING_GRACE_SECONDS:
                    time.sleep(min(0.05, _FLEET_LOCK_PENDING_GRACE_SECONDS))
                    continue

            lock_path.unlink(missing_ok=True)
            continue

        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        return

    raise FleetAlreadyRunningError(
        "Failed to acquire fleet lock after "
        f"{_FLEET_LOCK_ACQUIRE_MAX_ATTEMPTS} attempts.",
    )


def _release_fleet_lock(lock_path: Path | None = None) -> None:
    lock_path = lock_path or _FLEET_LOCK_FILE
    try:
        owner_pid = int(lock_path.read_text().strip())
    except (OSError, ValueError):
        owner_pid = None

    if owner_pid is not None and owner_pid != os.getpid():
        return

    lock_path.unlink(missing_ok=True)


# -- standalone runner ------------------------------------------------------


async def run_fleet(
    config_path: str | Path | None = None,
    server_url: str | None = None,
) -> None:
    """Start the fleet and block until SIGINT/SIGTERM."""
    _acquire_fleet_lock()
    fleet: BotFleet | None = None
    started = False
    try:
        fleet = BotFleet.from_config(config_path, server_url)
        await fleet.start()
        started = True

        loop = asyncio.get_running_loop()
        _force = False

        def _handle_signal() -> None:
            nonlocal _force
            if _force:
                print("\nForce exit.", file=sys.stderr)
                os._exit(1)
            _force = True
            fleet._stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)

        _print_fleet_status(fleet)
        await fleet._stop_event.wait()
    finally:
        try:
            if fleet is not None and started:
                await fleet.stop()
        finally:
            _release_fleet_lock()


def _print_fleet_status(fleet: BotFleet) -> None:
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()
        table = Table(show_header=True, header_style="bold")
        table.add_column("Bot")
        table.add_column("Username")
        table.add_column("Projects")
        table.add_column("Default")

        for bot in fleet._bots.values():
            table.add_row(
                bot.name,
                f"@{bot.bot_username}",
                ", ".join(bot.projects) or "—",
                "✓" if bot.is_default else "",
            )

        console.print(Panel(
            table,
            title="DAN Bot Fleet",
            subtitle=f"Server: {fleet._server_url}",
        ))
        console.print("Press Ctrl+C to stop.")
    except ImportError:
        print(f"Fleet running ({len(fleet._bots)} bots)")
        for bot in fleet._bots.values():
            print(f"  {bot.name} (@{bot.bot_username})")
