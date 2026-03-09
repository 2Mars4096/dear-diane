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
from typing import Any

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
from dan.server.concierge.identity import format_bare_prefix, get_bot_name

logger = logging.getLogger(__name__)


@dataclass
class BotInstance:
    """A single bot within the fleet."""

    name: str
    token: str
    projects: list[str] = field(default_factory=list)
    personality: str = ""
    is_default: bool = False
    adapter: TelegramAdapter | None = None
    bot_username: str = ""
    allowed_users: list[int] = field(default_factory=list)


class BotFleet:
    """Manages multiple Telegram bots in a single event loop.

    Each bot has its own ``Application`` instance from ``python-telegram-bot``
    using the lower-level ``initialize() -> start() -> start_polling()``
    pattern so they share one event loop.
    """

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
        self._seen_ttl = 60.0
        self._http: Any = None
        self._stop_event = asyncio.Event()
        self._bg_tasks: set[asyncio.Task[None]] = set()
        self._conversation_history: dict[str, list[dict[str, str]]] = {}
        self._conversation_workflows: dict[str, str] = {}
        self._history_locks: dict[str, asyncio.Lock] = {}

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

        await self._ensure_topics()
        self._start_cleanup_task()

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

    # -- dedup --------------------------------------------------------------

    def _check_dedup(self, chat_id: int, message_id: int) -> bool:
        """Return True if this message was already seen (skip it)."""
        key = (chat_id, message_id)
        now = time.monotonic()
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def _evict_seen(self) -> None:
        cutoff = time.monotonic() - self._seen_ttl
        expired = [k for k, t in self._seen.items() if t < cutoff]
        for k in expired:
            del self._seen[k]

    def _start_cleanup_task(self) -> None:
        async def _cleanup_loop() -> None:
            while not self._stop_event.is_set():
                await asyncio.sleep(30)
                self._evict_seen()

        task = asyncio.create_task(_cleanup_loop())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # -- message handling ---------------------------------------------------

    def _make_callback(self, bot: BotInstance):
        async def _on_message(
            ext_id: str, text: str, ctx: MessageContext,
        ) -> None:
            if self._check_dedup(ctx.chat_id, ctx.message_id):
                return

            if ctx.from_user_is_bot:
                return

            routable_bots = self._routable_bots()
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
            )

            if winner is None:
                return

            selected = self._bots.get(winner.name)
            if selected is None:
                return
            if selected.allowed_users and ctx.from_user_id not in selected.allowed_users:
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

        thread_key = _conversation_thread_key(ctx, bot.name)
        surface = f"telegram:{bot.name}"

        try:
            history_lock = self._history_locks.setdefault(thread_key, asyncio.Lock())
            async with history_lock:
                wf_id = await self._ensure_scratch(thread_key)

                history = self._conversation_history.get(thread_key, [])
                att_path = _extract_attachment(text)
                voice_path = _extract_voice_note(text)
                msg_text = text

                if voice_path:
                    transcribed = await _transcribe_voice_note(voice_path)
                    if transcribed:
                        msg_text = _merge_voice_context(text, transcribed)
                    else:
                        await bot.adapter._send_text(
                            ctx.chat_id,
                            "I received your voice message but couldn't transcribe it. "
                            "Please send as text.",
                            reply_to=ctx.message_id,
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

                history.append({"role": "user", "content": msg_text})
                if len(history) > 40:
                    history = history[-40:]
                self._conversation_history[thread_key] = history

                _bare = format_bare_prefix(bot_name=bot.name.upper())
                identity_block = (
                    f"You are {bot.name}, a {bot.personality or 'helpful'} assistant. "
                    f"Short replies (1-3 sentences). Answer directly — no {_bare} prefix."
                )
                if bot.projects:
                    identity_block += (
                        f" You focus on these projects: {', '.join(bot.projects)}."
                    )
                other_names = [
                    f"{b.name} (@{b.bot_username})"
                    for b in self._bots.values()
                    if b.name != bot.name
                ]
                if other_names:
                    identity_block += (
                        f" Other bots in this group: {', '.join(other_names)}."
                    )

                history_with_context = [
                    {"role": "system", "content": identity_block},
                    *history,
                ]
                body: dict[str, Any] = {
                    "workflow_id": wf_id,
                    "message": msg_text,
                    "history": history_with_context,
                    "thread_id": thread_key,
                    "mode": "auto",
                    "surface": surface,
                }
                if att_path:
                    body["attachment_path"] = att_path

                resp = await self._http.post("/api/chat/message", json=body)
                if resp.status_code != 200:
                    if history and history[-1].get("role") == "user":
                        history.pop()
                    if settings.use_reactions:
                        await bot.adapter.set_reaction(
                            ctx.chat_id, ctx.message_id, "❌",
                        )
                    return

                payload = resp.json()
                channel_id = payload.get("stream_channel_id")

                if not channel_id:
                    content = payload.get("content", "")
                    if content:
                        await self._send_reply(bot, ctx, content)
                        history.append({"role": "assistant", "content": content})
                    if settings.use_reactions:
                        await bot.adapter.set_reaction(
                            ctx.chat_id, ctx.message_id, "✅",
                        )
                    return

                if settings.streaming_edits:
                    full_reply = await self._stream_with_edits(
                        bot, ctx, channel_id,
                    )
                else:
                    full_reply = await self._stream_collect(
                        bot, ctx, channel_id,
                    )

                if full_reply:
                    history.append({"role": "assistant", "content": full_reply})

                if settings.use_reactions:
                    await bot.adapter.set_reaction(
                        ctx.chat_id, ctx.message_id, "✅",
                    )

        except Exception as exc:
            logger.exception("Fleet dispatch failed for %s: %s", bot.name, exc)
            history = self._conversation_history.get(thread_key, [])
            if history and history[-1].get("role") == "user":
                history.pop()
            if settings.use_reactions:
                await bot.adapter.set_reaction(
                    ctx.chat_id, ctx.message_id, "❌",
                )

    async def _stream_with_edits(
        self,
        bot: BotInstance,
        ctx: MessageContext,
        channel_id: str,
    ) -> str:
        """Process WS events incrementally, editing the message in place."""
        import websockets

        assert bot.adapter is not None
        ws_url = self._server_url.replace("http://", "ws://").replace(
            "https://", "wss://",
        )
        url = f"{ws_url}/api/chat/{channel_id}/events"

        collected_tokens: list[str] = []
        current_msg_id: int | None = None
        sent_prefix_len = 0
        last_edit_time = 0.0
        edit_interval = 0.5
        file_paths: list[str] = []
        error_message = ""
        complete_content = ""

        try:
            async with websockets.connect(
                url, ping_interval=None, ping_timeout=None,
            ) as ws:
                async for ws_msg in ws:
                    event = json.loads(ws_msg)
                    if not isinstance(event, dict):
                        continue
                    evt_type = event.get("type", "")

                    if evt_type == "chat_token":
                        collected_tokens.append(event.get("token", ""))
                        now = time.monotonic()
                        if now - last_edit_time >= edit_interval:
                            text = _strip_prefix_and_html(
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
                                    )
                                last_edit_time = now

                    elif evt_type == "chat_file_attachment":
                        path = event.get("path", "")
                        if path:
                            file_paths.append(path)

                    elif evt_type in (
                        "chat_complete",
                        "chat_interrupted",
                    ):
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

        full = "".join(collected_tokens).strip()
        from dan.cli.adapter import _strip_function_call_xml

        full = _strip_function_call_xml(full)
        if complete_content:
            complete_content = _strip_function_call_xml(complete_content)
            if not full:
                full = complete_content
            elif complete_content not in full:
                full = f"{full}\n\n{complete_content}".strip()
        elif error_message:
            full = (
                f"{full}\n\nI hit an error: {error_message}".strip()
                if full
                else f"I hit an error: {error_message}"
            )

        if full:
            clean = _strip_prefix_and_html(full)
            remaining = clean[sent_prefix_len:]
            if current_msg_id is not None and remaining:
                await bot.adapter.send_or_edit(
                    ctx.chat_id, remaining[:4096], current_msg_id,
                )
                sent_prefix_len += min(len(remaining), 4096)
                remaining = clean[sent_prefix_len:]
            if remaining:
                for chunk in _split_message(remaining):
                    await bot.adapter.send_or_edit(ctx.chat_id, chunk)
            elif current_msg_id is None:
                await self._send_reply(bot, ctx, clean)

        for fp in file_paths:
            if bot.adapter:
                try:
                    await bot.adapter._send_file_to_chat(ctx.chat_id, fp)
                    if self._config.settings.auto_pin_deliverables:
                        last_id = bot.adapter._last_bot_message_id.get(ctx.chat_id)
                        if last_id:
                            await bot.adapter.pin_message(ctx.chat_id, last_id)
                except Exception as exc:
                    logger.warning("Failed to send file %s: %s", fp, exc)

        return full

    async def _stream_collect(
        self,
        bot: BotInstance,
        ctx: MessageContext,
        channel_id: str,
    ) -> str:
        """Collect all events then send final reply (non-streaming mode)."""
        import websockets

        assert bot.adapter is not None
        ws_url = self._server_url.replace("http://", "ws://").replace(
            "https://", "wss://",
        )
        url = f"{ws_url}/api/chat/{channel_id}/events"

        stream_events: list[dict[str, Any]] = []
        try:
            async with websockets.connect(
                url, ping_interval=None, ping_timeout=None,
            ) as ws:
                async for ws_msg in ws:
                    event = json.loads(ws_msg)
                    if isinstance(event, dict):
                        stream_events.append(event)
                    if isinstance(event, dict) and event.get("type") in (
                        "chat_complete",
                        "chat_interrupted",
                        "chat_error",
                    ):
                        break
        except Exception as exc:
            logger.error("WS stream error: %s", exc)

        from dan.cli.adapter import _consume_chat_stream_events

        full_reply, _mutation, file_paths = _consume_chat_stream_events(
            stream_events,
        )

        if full_reply:
            clean = _strip_prefix_and_html(full_reply)
            await self._send_reply(bot, ctx, clean)

        for fp in file_paths:
            if bot.adapter:
                try:
                    await bot.adapter._send_file_to_chat(ctx.chat_id, fp)
                    if self._config.settings.auto_pin_deliverables:
                        last_id = bot.adapter._last_bot_message_id.get(ctx.chat_id)
                        if last_id:
                            await bot.adapter.pin_message(ctx.chat_id, last_id)
                except Exception as exc:
                    logger.warning("Failed to send file %s: %s", fp, exc)

        return full_reply

    async def _send_reply(
        self, bot: BotInstance, ctx: MessageContext, text: str,
    ) -> None:
        assert bot.adapter is not None
        from dan.server.concierge.actions import (
            split_message_for_surface,
            strip_html_for_messaging,
        )

        clean = strip_html_for_messaging(text)
        parts = split_message_for_surface(clean, "telegram")
        for i, part in enumerate(parts):
            reply_to = ctx.message_id if i == 0 else None
            await bot.adapter._send_text(ctx.chat_id, part, reply_to=reply_to)
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


def _strip_prefix_and_html(text: str) -> str:
    from dan.server.concierge.identity import strip_prefix
    from dan.server.concierge.actions import strip_html_for_messaging

    text = strip_prefix(text)
    text = strip_html_for_messaging(text)
    return text


# -- standalone runner ------------------------------------------------------


async def run_fleet(
    config_path: str | Path | None = None,
    server_url: str | None = None,
) -> None:
    """Start the fleet and block until SIGINT/SIGTERM."""
    fleet = BotFleet.from_config(config_path, server_url)
    await fleet.start()

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
    await fleet.stop()


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
