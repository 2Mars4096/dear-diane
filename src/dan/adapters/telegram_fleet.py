"""Multi-bot fleet coordinator for Telegram group chats.

``BotFleet`` manages multiple ``TelegramAdapter`` instances sharing one event
loop.  A per-message deduplication set ensures only one bot processes each
incoming message, and the ``MessageRouter`` decides which bot that is.

The fleet is a **standalone process** that communicates with ``dan-serve`` via
HTTP — it does not embed the concierge.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import shlex
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote

from dan.agent_runtime.super_tui_contract import (
    SUPER_TUI_AGENT_CAPABILITIES,
    SUPER_TUI_DEFAULT_BACKEND,
    SUPER_TUI_SURFACE_PROFILE,
    build_super_tui_agent_execute_payload,
    build_super_tui_surface_context,
)
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
from dan.server.chat_v2 import (
    format_token_usage,
    merge_token_usage,
    normalize_token_usage,
)
from dan.server.chat_v2_progress import AgentProgressStateMachine

logger = logging.getLogger(__name__)
_FLEET_LOCK_FILE = Path.home() / ".dan" / "telegram" / "fleet.lock"
_TELEGRAM_STREAM_MISSING_TERMINAL_FALLBACK = (
    "The response stream ended before a final answer was produced. "
    "Please ask me to continue from the latest progress."
)
_TELEGRAM_WORKSPACE_MENU_PAGE_SIZE = 8


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
    _V2_AGENT_PROGRESS_INTERVAL: float = float(
        os.environ.get("DAN_TELEGRAM_V2_PROGRESS_INTERVAL", "10")
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
        self._last_outbound_by_lane: dict[str, tuple[int, float]] = {}
        self._active_conversations: dict[str, int] = {}
        self._workspace_menu_state: dict[str, dict[str, Any]] = {}
        self._session_menu_state: dict[str, dict[str, Any]] = {}
        self._telegram_surface_state = _load_telegram_surface_state()

    def _latest_outbound_id(self, chat_id: int, lane_key: str | None = None) -> int | None:
        """Most recent outbound message id we sent in the active lane/chat."""
        if lane_key:
            entry = self._last_outbound_by_lane.get(lane_key)
            if entry is not None:
                return entry[0]
        entry = self._last_outbound.get(chat_id)
        return entry[0] if entry else None

    def _conversation_has_active_dispatch(self, conversation_key: str) -> bool:
        return self._active_conversations.get(conversation_key, 0) > 0

    def _mark_conversation_dispatch_started(self, conversation_key: str) -> None:
        self._active_conversations[conversation_key] = (
            self._active_conversations.get(conversation_key, 0) + 1
        )

    def _mark_conversation_dispatch_finished(self, conversation_key: str) -> None:
        remaining = self._active_conversations.get(conversation_key, 0) - 1
        if remaining > 0:
            self._active_conversations[conversation_key] = remaining
        else:
            self._active_conversations.pop(conversation_key, None)

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

        seen_tokens: set[str] = set()
        for name, bot_cfg in self._config.bots.items():
            token = str(bot_cfg.token or "").strip()
            if not token:
                logger.warning("Fleet: skipping %s because it has no Telegram token", name)
                continue
            if token in seen_tokens:
                logger.warning(
                    "Fleet: skipping %s because another configured bot already uses the same token",
                    name,
                )
                continue
            seen_tokens.add(token)
            adapter_config = TelegramAdapterConfig(
                bot_token=token,
                progress_throttle=self._config.settings.progress_throttle,
                max_inbound_media_mb=self._config.settings.max_inbound_media_mb,
            )
            adapter = TelegramAdapter(adapter_config)

            bot = BotInstance(
                name=name,
                token=token,
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
            adapter.set_callback_query_callback(
                self._make_menu_callback(bot), with_context=True,
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
        expired_outbound = [
            k for k, (_message_id, ts) in self._last_outbound.items() if ts < cutoff
        ]
        for k in expired_outbound:
            del self._last_outbound[k]
        expired_lane_outbound = [
            k
            for k, (_message_id, ts) in self._last_outbound_by_lane.items()
            if ts < cutoff
        ]
        for k in expired_lane_outbound:
            del self._last_outbound_by_lane[k]
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
        workspace = _telegram_workspace_context(bot.name)
        return {
            "identity": {
                "name": bot.name,
                "role": "messaging_assistant",
                "username": bot.bot_username,
                "personality": bot.personality,
                "project_focus": list(bot.projects),
            },
            "peers": peers,
            **workspace,
        }

    def _build_turn_surface_context(
        self,
        bot: BotInstance,
        ctx: MessageContext,
        *,
        conversation_key: str | None = None,
        lane_key: str | None = None,
        reply_lane_key: str | None = None,
        history: list[dict[str, str]] | None = None,
        include_selected_session: bool = True,
    ) -> dict[str, Any]:
        history_payload = [
            {"role": item["role"], "content": item["content"]}
            for item in list(history or [])[-12:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        workspace = self._workspace_context_for_turn(
            bot,
            conversation_key=conversation_key,
            lane_key=lane_key,
        )
        context = build_super_tui_surface_context(
            workspace_root=str(workspace.get("workspace_root") or "~"),
            workspace_source=str(workspace.get("workspace_source") or "telegram"),
            conversation_recent_turns=history_payload,
            extra=self._build_surface_context(bot),
        )
        if workspace.get("workspace_id"):
            context["workspace_id"] = workspace["workspace_id"]
        context.update(
            {
                "ui_surface": "telegram",
                "agent_profile": SUPER_TUI_SURFACE_PROFILE,
                "agent_backend": SUPER_TUI_DEFAULT_BACKEND,
                "gui_for": "dan super-tui",
                "capabilities": _unique_strings(
                    [
                        *SUPER_TUI_AGENT_CAPABILITIES,
                        "message_edit",
                        "threaded_replies",
                        "media_download",
                        "workspace_menu",
                        "session_menu",
                    ]
                ),
            }
        )
        context["telegram"] = {
            "chat_id": ctx.chat_id,
            "message_id": ctx.message_id,
            "message_thread_id": ctx.thread_id,
            "reply_to_message_id": ctx.reply_to_message_id,
            "reply_to_text": _compact_context_text(ctx.reply_to_text, limit=1200),
            "chat_type": ctx.chat_type,
            "from_user_id": ctx.from_user_id,
            "from_user_username": ctx.from_user_username,
            "sender_chat_id": ctx.sender_chat_id,
            "sender_chat_username": ctx.sender_chat_username,
        }
        context["conversation"] = {
            **dict(context.get("conversation") or {}),
            "conversation_key": conversation_key or "",
            "lane_key": lane_key or "",
            "reply_lane_key": reply_lane_key or "",
            "history_turn_count": len(history or []),
            "history_window": min(len(history or []), 40),
        }
        selected_session = (
            self._session_binding_for_turn(
                conversation_key=conversation_key,
                lane_key=lane_key,
            )
            if include_selected_session
            else {}
        )
        if selected_session:
            context["selected_session"] = dict(selected_session)
            task_id = str(selected_session.get("task_id") or "").strip()
            queue_action = str(selected_session.get("queue_action") or "").strip()
            if task_id:
                context["task_id"] = task_id
            if queue_action:
                context["queue_action"] = queue_action
        return context

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
            self._last_outbound_by_lane[lane_key] = (message_id, now)

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
        *,
        seed_history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        history_lock = self._history_locks.setdefault(lane_key, asyncio.Lock())
        async with history_lock:
            if lane_key in self._conversation_history:
                history = list(self._conversation_history.get(lane_key, []))
            else:
                history = list(seed_history or [])
            history.append(turn)
            if len(history) > 40:
                history = history[-40:]
            self._conversation_history[lane_key] = history
            return list(history)

    async def _history_snapshot(self, lane_key: str) -> list[dict[str, str]]:
        history_lock = self._history_locks.setdefault(lane_key, asyncio.Lock())
        async with history_lock:
            return list(self._conversation_history.get(lane_key, []))

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

    async def _clear_history_key(self, lane_key: str) -> None:
        if not lane_key:
            return
        history_lock = self._history_locks.setdefault(lane_key, asyncio.Lock())
        async with history_lock:
            self._conversation_history.pop(lane_key, None)

    async def _record_user_turn(
        self,
        conversation_key: str,
        lane_key: str,
        turn: dict[str, str],
    ) -> list[dict[str, str]]:
        if lane_key == conversation_key:
            return await self._append_history_turn(conversation_key, turn)

        seed_history = await self._history_snapshot(conversation_key)
        lane_history = await self._append_history_turn(
            lane_key,
            turn,
            seed_history=seed_history,
        )
        await self._append_history_turn(conversation_key, turn)
        return lane_history

    async def _rollback_user_turn(
        self,
        conversation_key: str,
        lane_key: str,
        turn: dict[str, str],
    ) -> None:
        await self._remove_history_turn(lane_key, turn)
        if lane_key != conversation_key:
            await self._remove_history_turn(conversation_key, turn)

    async def _append_assistant_turn(
        self,
        lane_key: str,
        content: str,
        *,
        conversation_key: str | None = None,
    ) -> None:
        if not content:
            return
        await self._append_history_turn(
            lane_key,
            {"role": "assistant", "content": content},
        )
        if conversation_key and conversation_key != lane_key:
            await self._append_history_turn(
                conversation_key,
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

            conversation_key = _conversation_thread_key(ctx, selected.name)
            reply_lane_key = self._lookup_message_lane(
                ctx.chat_id,
                ctx.reply_to_message_id,
            )
            lane_key = _conversation_lane_key(
                ctx,
                selected.name,
                reply_lane_key=reply_lane_key,
                fork_for_parallel=(
                    reply_lane_key is None
                    and self._conversation_has_active_dispatch(conversation_key)
                ),
            )
            self._remember_message_lane(ctx.chat_id, ctx.message_id, lane_key)
            self._mark_conversation_dispatch_started(conversation_key)

            dispatch_params = {}
            try:
                signature = inspect.signature(self._dispatch)
                accepts_kwargs = any(
                    param.kind == inspect.Parameter.VAR_KEYWORD
                    for param in signature.parameters.values()
                )
                if accepts_kwargs or "conversation_key" in signature.parameters:
                    dispatch_params["conversation_key"] = conversation_key
                if accepts_kwargs or "lane_key" in signature.parameters:
                    dispatch_params["lane_key"] = lane_key
                if accepts_kwargs or "reply_lane_key" in signature.parameters:
                    dispatch_params["reply_lane_key"] = reply_lane_key
            except (TypeError, ValueError):
                dispatch_params = {
                    "conversation_key": conversation_key,
                    "lane_key": lane_key,
                    "reply_lane_key": reply_lane_key,
                }

            task = asyncio.create_task(
                self._dispatch(
                    selected,
                    ext_id,
                    text,
                    ctx,
                    **dispatch_params,
                ),
            )
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)

        return _on_message

    def _make_menu_callback(self, bot: BotInstance):
        async def _on_callback(
            ext_id: str, data: str, ctx: MessageContext,
        ) -> None:
            selected = self._bots.get(bot.name)
            if selected is None:
                return
            if selected.allowed_users and not _user_allowed(
                selected.allowed_users, ctx.from_user_id, ctx.from_user_username,
            ):
                return
            if data.startswith("danws:"):
                await self._handle_workspace_callback(selected, data, ctx)
            elif data.startswith("dansn:"):
                await self._handle_session_callback(selected, data, ctx)

        return _on_callback

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

    async def _handle_telegram_menu_command(
        self,
        bot: BotInstance,
        ctx: MessageContext,
        text: str,
        *,
        conversation_key: str,
        lane_key: str,
    ) -> bool:
        command = _telegram_menu_command(text)
        if not command:
            return False
        if command == "workspace":
            workspace_arg = _telegram_command_argument(text)
            await self._show_workspace_menu(
                bot,
                ctx,
                conversation_key=conversation_key,
                lane_key=lane_key,
                path=workspace_arg or None,
                reply_to=ctx.message_id,
            )
            return True
        if command == "session":
            await self._show_session_menu(
                bot,
                ctx,
                conversation_key=conversation_key,
                lane_key=lane_key,
                reply_to=ctx.message_id,
            )
            return True
        if command == "reset":
            binding = self._session_binding_for_turn(
                conversation_key=conversation_key,
                lane_key=lane_key,
            )
            stopped = await self._force_reset_selected_session(binding)
            sessions = self._telegram_surface_state.setdefault("active_sessions", {})
            sessions.pop(conversation_key, None)
            sessions.pop(lane_key, None)
            _save_telegram_surface_state(self._telegram_surface_state)
            await self._clear_history_key(conversation_key)
            await self._clear_history_key(lane_key)
            text = (
                "Telegram state reset. Existing DAN Super task status was forced out of this lane; your next Agent request starts fresh."
                if stopped
                else "Telegram state reset. Your next Agent request starts fresh."
            )
            await self._send_reply(
                bot,
                ctx,
                text,
                lane_key=lane_key,
                thread_id=ctx.thread_id,
            )
            return True
        return False

    def _workspace_context_for_turn(
        self,
        bot: BotInstance,
        *,
        conversation_key: str | None,
        lane_key: str | None,
    ) -> dict[str, Any]:
        base = _telegram_workspace_context(bot.name)
        active = self._active_workspace_for_keys(
            bot.name,
            conversation_key=conversation_key,
            lane_key=lane_key,
        )
        if active:
            return {
                "workspace_root": active,
                "workspace_id": _workspace_id_from_path(active),
                "workspace_source": "telegram_menu",
            }
        if base:
            base = dict(base)
            base.setdefault("workspace_source", "telegram_env")
            return base
        return {
            "workspace_root": "~",
            "workspace_id": "~",
            "workspace_source": "default_home",
        }

    def _session_binding_for_turn(
        self,
        *,
        conversation_key: str | None,
        lane_key: str | None,
    ) -> dict[str, Any]:
        sessions = self._telegram_surface_state.setdefault("active_sessions", {})
        for key in (lane_key, conversation_key):
            if key and isinstance(sessions.get(key), dict):
                return dict(sessions[key])
        return {}

    def _active_workspace_for_keys(
        self,
        bot_name: str,
        *,
        conversation_key: str | None,
        lane_key: str | None,
    ) -> str:
        active = self._telegram_surface_state.setdefault("active_workspaces", {})
        for key in (lane_key, conversation_key, f"bot:{bot_name}"):
            value = str(active.get(key or "") or "").strip()
            if value:
                return value
        return ""

    async def _send_telegram_menu(
        self,
        bot: BotInstance,
        ctx: MessageContext,
        text: str,
        buttons: list[list[tuple[str, str]]],
        *,
        message_id: int | None = None,
        reply_to: int | None = None,
        lane_key: str | None = None,
    ) -> int | None:
        assert bot.adapter is not None
        if hasattr(bot.adapter, "send_menu"):
            sent_id = await bot.adapter.send_menu(
                ctx.chat_id,
                text,
                buttons,
                message_id=message_id,
                reply_to=reply_to,
                thread_id=ctx.thread_id,
            )
        else:
            sent_id = await bot.adapter._send_text(
                ctx.chat_id,
                text,
                reply_to=reply_to,
                thread_id=ctx.thread_id,
            )
        self._remember_outbound_message(ctx.chat_id, sent_id, lane_key=lane_key)
        return sent_id

    async def _show_workspace_menu(
        self,
        bot: BotInstance,
        ctx: MessageContext,
        *,
        conversation_key: str,
        lane_key: str,
        path: str | None = None,
        page: int = 0,
        message_id: int | None = None,
        reply_to: int | None = None,
        menu_id: str | None = None,
        note: str = "",
    ) -> None:
        current = self._active_workspace_for_keys(
            bot.name,
            conversation_key=conversation_key,
            lane_key=lane_key,
        )
        default_workspace = _telegram_workspace_context(bot.name).get("workspace_root") or Path.cwd()
        browse_root = _normalize_workspace_menu_path(
            path or current or default_workspace,
            base=current or default_workspace,
        )
        entries = _workspace_child_dirs(browse_root)
        max_page = max(0, (len(entries) - 1) // _TELEGRAM_WORKSPACE_MENU_PAGE_SIZE)
        page = max(0, min(page, max_page))
        visible = entries[
            page * _TELEGRAM_WORKSPACE_MENU_PAGE_SIZE:
            (page + 1) * _TELEGRAM_WORKSPACE_MENU_PAGE_SIZE
        ]
        menu_id = menu_id or uuid.uuid4().hex[:8]
        recents = self._recent_workspaces(bot.name)
        self._workspace_menu_state[menu_id] = {
            "bot": bot.name,
            "conversation_key": conversation_key,
            "lane_key": lane_key,
            "path": str(browse_root),
            "page": page,
            "entries": [str(item) for item in visible],
            "recents": list(recents),
        }

        lines = ["DAN Super workspace"]
        if current:
            lines.append(f"Selected: {current}")
        else:
            lines.append("Selected: default workspace")
        lines.append(f"Browsing: {browse_root}")
        if note:
            lines.append("")
            lines.append(note)
        lines.append("")
        lines.append("Tap Down to enter a folder, Parent to go up, then Select this folder.")
        if not visible:
            lines.append("No child folders are visible here.")

        buttons: list[list[tuple[str, str]]] = []
        for idx, root in enumerate(recents[:4]):
            buttons.append([(f"Saved: {_short_path_label(root)}", f"danws:{menu_id}:recent:{idx}")])
        for idx, child in enumerate(visible):
            buttons.append([(f"Down: {_short_path_label(str(child))}", f"danws:{menu_id}:open:{idx}")])
        nav: list[tuple[str, str]] = []
        if browse_root.parent != browse_root:
            nav.append(("Parent", f"danws:{menu_id}:up:0"))
        if page > 0:
            nav.append(("Prev", f"danws:{menu_id}:page:{page - 1}"))
        if page < max_page:
            nav.append(("Next", f"danws:{menu_id}:page:{page + 1}"))
        if nav:
            buttons.append(nav)
        buttons.append(
            [
                ("Select this folder", f"danws:{menu_id}:select:0"),
                ("Refresh", f"danws:{menu_id}:refresh:0"),
            ]
        )
        buttons.append([("Sessions", f"danws:{menu_id}:sessions:0")])
        await self._send_telegram_menu(
            bot,
            ctx,
            "\n".join(lines),
            buttons,
            message_id=message_id,
            reply_to=reply_to,
            lane_key=lane_key,
        )

    async def _handle_workspace_callback(
        self,
        bot: BotInstance,
        data: str,
        ctx: MessageContext,
    ) -> None:
        _prefix, menu_id, action, raw_value = _split_menu_callback(data)
        state = self._workspace_menu_state.get(menu_id)
        if not state:
            await self._send_telegram_menu(
                bot,
                ctx,
                "This workspace menu expired. Send /workspace to open a fresh one.",
                [],
                message_id=ctx.message_id,
            )
            return
        conversation_key = str(state.get("conversation_key") or "")
        lane_key = str(state.get("lane_key") or conversation_key)
        current_path = str(state.get("path") or Path.cwd())
        page = int(state.get("page") or 0)
        if action == "open":
            entries = list(state.get("entries") or [])
            idx = _safe_int(raw_value)
            if 0 <= idx < len(entries):
                current_path = entries[idx]
                page = 0
        elif action == "recent":
            recents = list(state.get("recents") or [])
            idx = _safe_int(raw_value)
            if 0 <= idx < len(recents):
                current_path = str(recents[idx])
                self._select_workspace(
                    bot.name,
                    conversation_key=conversation_key,
                    lane_key=lane_key,
                    root=current_path,
                )
                await self._show_workspace_menu(
                    bot,
                    ctx,
                    conversation_key=conversation_key,
                    lane_key=lane_key,
                    path=current_path,
                    message_id=ctx.message_id,
                    menu_id=menu_id,
                    note="Workspace selected.",
                )
                return
        elif action == "up":
            current_path = str(Path(current_path).expanduser().parent)
            page = 0
        elif action == "page":
            page = _safe_int(raw_value)
        elif action == "refresh":
            pass
        elif action == "sessions":
            await self._show_session_menu(
                bot,
                ctx,
                conversation_key=conversation_key,
                lane_key=lane_key,
                message_id=ctx.message_id,
            )
            return
        elif action == "select":
            self._select_workspace(
                bot.name,
                conversation_key=conversation_key,
                lane_key=lane_key,
                root=current_path,
            )
            await self._show_workspace_menu(
                bot,
                ctx,
                conversation_key=conversation_key,
                lane_key=lane_key,
                path=current_path,
                message_id=ctx.message_id,
                menu_id=menu_id,
                note="Workspace selected.",
            )
            return
        await self._show_workspace_menu(
            bot,
            ctx,
            conversation_key=conversation_key,
            lane_key=lane_key,
            path=current_path,
            page=page,
            message_id=ctx.message_id,
            menu_id=menu_id,
        )

    def _select_workspace(
        self,
        bot_name: str,
        *,
        conversation_key: str,
        lane_key: str,
        root: str,
    ) -> None:
        root = str(_normalize_workspace_menu_path(root))
        active = self._telegram_surface_state.setdefault("active_workspaces", {})
        active[conversation_key] = root
        active[lane_key] = root
        active[f"bot:{bot_name}"] = root
        recents = self._telegram_surface_state.setdefault("recent_workspaces", {})
        bot_recents = [item for item in list(recents.get(bot_name, [])) if item != root]
        recents[bot_name] = [root, *bot_recents][:12]
        _save_telegram_surface_state(self._telegram_surface_state)

    def _recent_workspaces(self, bot_name: str) -> list[str]:
        recents = self._telegram_surface_state.setdefault("recent_workspaces", {})
        values = recents.get(bot_name, [])
        if not isinstance(values, list):
            return []
        return [str(item) for item in values if str(item).strip()]

    async def _show_session_menu(
        self,
        bot: BotInstance,
        ctx: MessageContext,
        *,
        conversation_key: str,
        lane_key: str,
        message_id: int | None = None,
        reply_to: int | None = None,
        menu_id: str | None = None,
        note: str = "",
    ) -> None:
        tasks = await self._load_available_sessions(conversation_key)
        menu_id = menu_id or uuid.uuid4().hex[:8]
        self._session_menu_state[menu_id] = {
            "bot": bot.name,
            "conversation_key": conversation_key,
            "lane_key": lane_key,
            "tasks": tasks,
        }
        binding = self._session_binding_for_turn(
            conversation_key=conversation_key,
            lane_key=lane_key,
        )
        display_tasks = tasks[:12]
        lines = ["DAN Super sessions"]
        if binding.get("task_id"):
            resumed = _session_task_title(
                next((task for task in tasks if str(task.get("task_id") or "") == str(binding.get("task_id") or "")), {})
            )
            lines.append(f"Resumed: {resumed or 'selected session'}")
        if note:
            lines.append("")
            lines.append(note)
        if not tasks:
            lines.append("")
            lines.append("No Agent sessions are attached to this Telegram thread yet.")
        else:
            lines.append("")
            lines.append("Select a session to resume it. Active sessions accept your next message as steering.")
            display_idx = 1
            for group in _session_workspace_groups(display_tasks):
                lines.append(f"[{group['label']}]")
                for task in group["tasks"]:
                    status = _session_status_label(task)
                    title = _session_task_title(task)
                    latest = _compact_context_text(task.get("latest_progress"), limit=72)
                    suffix = f" - {latest}" if latest and latest != title else ""
                    lines.append(f"{display_idx}. {status} {title}{suffix}".rstrip())
                    display_idx += 1
            if len(tasks) > len(display_tasks):
                lines.append(f"Showing latest {len(display_tasks)} of {len(tasks)} sessions.")

        buttons: list[list[tuple[str, str]]] = []
        for idx, task in enumerate(display_tasks):
            status = str(task.get("status") or "").strip().lower()
            title = _session_task_button_label(task)
            if status in {"running", "queued", "waiting_dependency", "paused", "needs_input"}:
                buttons.append(
                    [
                        (f"Steer: {title}", f"dansn:{menu_id}:use:{idx}"),
                        ("Next", f"dansn:{menu_id}:continue:{idx}"),
                        ("Status", f"dansn:{menu_id}:status:{idx}"),
                    ]
                )
            else:
                buttons.append(
                    [
                        (f"Resume: {title}", f"dansn:{menu_id}:use:{idx}"),
                        ("Status", f"dansn:{menu_id}:status:{idx}"),
                    ]
                )
        buttons.append(
            [
                ("Refresh", f"dansn:{menu_id}:refresh:0"),
                ("Clear resume", f"dansn:{menu_id}:clear:0"),
            ]
        )
        buttons.append([("Workspace", f"dansn:{menu_id}:workspace:0")])
        await self._send_telegram_menu(
            bot,
            ctx,
            "\n".join(lines),
            buttons,
            message_id=message_id,
            reply_to=reply_to,
            lane_key=lane_key,
        )

    async def _handle_session_callback(
        self,
        bot: BotInstance,
        data: str,
        ctx: MessageContext,
    ) -> None:
        _prefix, menu_id, action, raw_value = _split_menu_callback(data)
        state = self._session_menu_state.get(menu_id)
        if not state:
            await self._send_telegram_menu(
                bot,
                ctx,
                "This session menu expired. Send /session to open a fresh one.",
                [],
                message_id=ctx.message_id,
            )
            return
        conversation_key = str(state.get("conversation_key") or "")
        lane_key = str(state.get("lane_key") or conversation_key)
        tasks = list(state.get("tasks") or [])
        idx = _safe_int(raw_value)
        if action == "refresh":
            await self._show_session_menu(
                bot,
                ctx,
                conversation_key=conversation_key,
                lane_key=lane_key,
                message_id=ctx.message_id,
                menu_id=menu_id,
            )
            return
        if action == "clear":
            sessions = self._telegram_surface_state.setdefault("active_sessions", {})
            sessions.pop(conversation_key, None)
            sessions.pop(lane_key, None)
            _save_telegram_surface_state(self._telegram_surface_state)
            await self._show_session_menu(
                bot,
                ctx,
                conversation_key=conversation_key,
                lane_key=lane_key,
                message_id=ctx.message_id,
                menu_id=menu_id,
                note="Session resume cleared.",
            )
            return
        if action == "workspace":
            await self._show_workspace_menu(
                bot,
                ctx,
                conversation_key=conversation_key,
                lane_key=lane_key,
                message_id=ctx.message_id,
            )
            return
        if not (0 <= idx < len(tasks)):
            return
        task = dict(tasks[idx])
        if action == "status":
            latest = _compact_context_text(task.get("latest_progress"), limit=1200)
            metadata = dict(task.get("metadata") or {})
            text = "\n".join(
                line
                for line in (
                    f"Session: {_session_task_title(task)}",
                    f"Status: {task.get('status', 'unknown')}",
                    f"Phase: {task.get('phase', '')}",
                    f"Workspace: {_session_workspace_label(metadata.get('workspace_root') or task.get('workspace_root') or '')}",
                    f"Latest: {latest}" if latest else "",
                    f"Task id: {task.get('task_id', '')}",
                )
                if line
            )
            await self._send_telegram_menu(
                bot,
                ctx,
                text,
                [[("Back to sessions", f"dansn:{menu_id}:refresh:0")]],
                message_id=ctx.message_id,
                lane_key=lane_key,
            )
            return
        if action in {"use", "append", "continue"}:
            queue_action = "append" if action != "continue" else "continue_after_current"
            if action == "use" and str(task.get("status") or "") in {
                "completed",
                "failed",
                "blocked",
                "stopped",
            }:
                queue_action = ""
            binding = {
                "task_id": str(task.get("task_id") or ""),
                "run_id": str(dict(task.get("metadata") or {}).get("active_run_id") or ""),
                "status": str(task.get("status") or ""),
                "queue_action": queue_action,
            }
            sessions = self._telegram_surface_state.setdefault("active_sessions", {})
            sessions[conversation_key] = binding
            sessions[lane_key] = binding
            _save_telegram_surface_state(self._telegram_surface_state)
            note = (
                "Session resumed. Next message will append to the active run."
                if queue_action == "append"
                else "Session resumed. Next Agent-like message will continue from this task."
            )
            await self._show_session_menu(
                bot,
                ctx,
                conversation_key=conversation_key,
                lane_key=lane_key,
                message_id=ctx.message_id,
                menu_id=menu_id,
                note=note,
            )

    async def _load_available_sessions(self, thread_id: str) -> list[dict[str, Any]]:
        if self._http is None:
            return []
        try:
            resp = await self._http.get(
                "/api/v2/tasks?limit=60"
            )
            if resp.status_code != 200:
                resp = await self._http.get(
                    f"/api/v2/threads/{quote(thread_id, safe='')}/tasks?limit=20"
                )
                if resp.status_code != 200:
                    return []
            payload = resp.json()
            tasks = payload.get("tasks") if isinstance(payload, dict) else []
            if not isinstance(tasks, list):
                return []
            return [dict(item) for item in tasks if isinstance(item, dict)]
        except Exception:
            logger.debug("Failed to load Telegram Agent sessions", exc_info=True)
            return []

    # -- server dispatch ----------------------------------------------------

    async def _dispatch(
        self,
        bot: BotInstance,
        ext_id: str,
        text: str,
        ctx: MessageContext,
        *,
        conversation_key: str | None = None,
        lane_key: str | None = None,
        reply_lane_key: str | None = None,
    ) -> None:
        if conversation_key is None:
            conversation_key = _conversation_thread_key(ctx, bot.name)
        if lane_key is None:
            lane_key = _conversation_lane_key(
                ctx,
                bot.name,
                fork_for_parallel=self._conversation_has_active_dispatch(conversation_key),
            )
        if reply_lane_key is None:
            reply_lane_key = self._lookup_message_lane(
                ctx.chat_id,
                ctx.reply_to_message_id,
            )
        if bot.adapter is None or self._http is None:
            self._mark_conversation_dispatch_finished(conversation_key)
            return

        settings = self._config.settings

        if settings.use_reactions:
            await bot.adapter.set_reaction(ctx.chat_id, ctx.message_id, "⏳")

        surface = f"telegram:{bot.name}"
        surface_type = "telegram"
        surface_id = bot.name
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
            raw_agent_command = _telegram_agent_command(msg_text)
            requested_mode, msg_text = _telegram_requested_mode(msg_text)
            control_plane = _telegram_control_plane_override(bot.name)
            effective_mode = _telegram_effective_requested_mode(
                control_plane,
                requested_mode,
                msg_text,
            )
            if (
                control_plane == "v2"
                and raw_agent_command == "new"
                and reply_lane_key is None
                and ctx.message_id is not None
            ):
                lane_key = f"{conversation_key}:m{ctx.message_id}"
                self._remember_message_lane(ctx.chat_id, ctx.message_id, lane_key)
            if await self._handle_telegram_menu_command(
                bot,
                ctx,
                msg_text,
                conversation_key=conversation_key,
                lane_key=lane_key,
            ):
                if settings.use_reactions:
                    await bot.adapter.set_reaction(
                        ctx.chat_id, ctx.message_id, "✅",
                    )
                return
            selected_session = self._session_binding_for_turn(
                conversation_key=conversation_key,
                lane_key=lane_key,
            )
            if (
                control_plane == "v2"
                and selected_session
                and effective_mode == "auto"
                and not _telegram_v2_control_command(msg_text)
            ):
                effective_mode = "agent"
            if effective_mode == "agent" and not msg_text.strip():
                await self._send_reply(
                    bot,
                    ctx,
                    "Usage: /agent describe the task to run.",
                    lane_key=lane_key,
                    thread_id=ctx.thread_id,
                )
                if settings.use_reactions:
                    await bot.adapter.set_reaction(
                        ctx.chat_id, ctx.message_id, "❌",
                    )
                return

            user_turn = {"role": "user", "content": msg_text}
            history = await self._record_user_turn(
                conversation_key,
                lane_key,
                user_turn,
            )
            body: dict[str, Any] = {
                "workflow_id": wf_id,
                "message": msg_text,
                "history": history,
                "thread_id": conversation_key,
                "session_id": lane_key,
                "mode": effective_mode,
                "surface": surface,
                "surface_type": surface_type,
                "surface_id": surface_id,
                "surface_context": self._build_turn_surface_context(
                    bot,
                    ctx,
                    conversation_key=conversation_key,
                    lane_key=lane_key,
                    reply_lane_key=reply_lane_key,
                    history=history,
                    include_selected_session=raw_agent_command != "new",
                ),
            }
            if control_plane:
                body["control_plane_mode"] = control_plane
            if att_path:
                body["attachment_path"] = att_path

            selected_session_command_allowed = raw_agent_command in {
                "",
                "append",
                "inject",
                "continue",
                "continue-after-current",
            }
            if (
                control_plane == "v2"
                and selected_session
                and str(selected_session.get("queue_action") or "").strip()
                and str(selected_session.get("run_id") or "").strip()
                and selected_session_command_allowed
            ):
                full_reply = await self._send_v2_agent_run_command(
                    bot,
                    ctx,
                    body,
                    selected_session,
                    lane_key=lane_key,
                )
                if full_reply:
                    await self._append_assistant_turn(
                        lane_key,
                        full_reply,
                        conversation_key=conversation_key,
                    )
                if settings.use_reactions:
                    await bot.adapter.set_reaction(
                        ctx.chat_id, ctx.message_id, "✅",
                    )
                return

            if control_plane == "v2" and effective_mode == "agent":
                full_reply = await self._run_v2_agent_turn(
                    bot,
                    ctx,
                    body,
                    lane_key=lane_key,
                )
                if full_reply:
                    await self._append_assistant_turn(
                        lane_key,
                        full_reply,
                        conversation_key=conversation_key,
                    )
                if settings.use_reactions:
                    await bot.adapter.set_reaction(
                        ctx.chat_id, ctx.message_id, "✅",
                    )
                return

            endpoint = "/api/v2/chat/message" if control_plane == "v2" else "/api/chat/message"
            resp = await self._http.post(endpoint, json=body)
            if resp.status_code != 200:
                await self._rollback_user_turn(conversation_key, lane_key, user_turn)
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
                    await self._append_assistant_turn(
                        lane_key,
                        content,
                        conversation_key=conversation_key,
                    )
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
                await self._append_assistant_turn(
                    lane_key,
                    full_reply,
                    conversation_key=conversation_key,
                )

            if settings.use_reactions:
                await bot.adapter.set_reaction(
                    ctx.chat_id, ctx.message_id, "✅",
                )

        except Exception as exc:
            logger.exception("Fleet dispatch failed for %s: %s", bot.name, exc)
            if user_turn is not None:
                await self._rollback_user_turn(conversation_key, lane_key, user_turn)
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
        finally:
            self._mark_conversation_dispatch_finished(conversation_key)

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
        saw_terminal_event = False

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

            latest = self._latest_outbound_id(ctx.chat_id, lane_key=lane_key)
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
                    saw_terminal_event = True
                    complete_content = event.get("content", "") or ""
                    break

                elif evt_type == "chat_error":
                    saw_terminal_event = True
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
        elif not saw_terminal_event:
            full = _format_interrupted_stream_reply(streamed_full)
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
        saw_terminal_event = False
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
                    saw_terminal_event = True
                    break
        except Exception as exc:
            logger.error("WS stream error: %s", exc)

        from dan.cli.adapter import _consume_chat_stream_events

        full_reply, _mutation, file_paths, poll_requests = _consume_chat_stream_events(
            stream_events,
        )
        if not saw_terminal_event:
            full_reply = _format_interrupted_stream_reply(full_reply)

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

        def _queue_hint_event(*, hint_count: int) -> dict[str, Any] | None:
            if queue_position is None or queued_started_at is None:
                return None
            return {
                "type": "chat_complete",
                "content": self._format_queue_hint(
                    queue_position,
                    time.monotonic() - queued_started_at,
                    hint_count=hint_count,
                ),
                "detected_mode": "progress_ack",
            }

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
                            hint_event = _queue_hint_event(hint_count=queue_hint_count)
                            if hint_event is not None:
                                yield hint_event
                                queue_hint_count += 1
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
                        hint_event = _queue_hint_event(hint_count=queue_hint_count)
                        if hint_event is not None:
                            yield hint_event
                            queue_hint_count += 1
                        if redirected and redirected not in seen_channels:
                            next_channel = redirected
                            break
                        continue
                    queue_position = None
                    queued_started_at = None
                    queue_hint_count = 0
                    yield event

    async def _run_v2_agent_turn(
        self,
        bot: BotInstance,
        ctx: MessageContext,
        body: dict[str, Any],
        *,
        lane_key: str | None = None,
    ) -> str:
        """Create, execute, and stream a V2 Agent run for one Telegram turn."""

        assert bot.adapter is not None
        if self._http is None:
            raise RuntimeError("Telegram fleet HTTP client is not available")

        create_resp = await self._http.post("/api/v2/agent-runs", json=body)
        if create_resp.status_code != 200:
            raise RuntimeError(
                f"V2 Agent run creation failed: HTTP {create_resp.status_code}",
            )
        created = create_resp.json()
        control = created.get("v2_control_plane") if isinstance(created, dict) else {}
        control = control if isinstance(control, dict) else {}
        run_id = str(control.get("run_id") or "").strip()
        if not run_id:
            event = created.get("event") if isinstance(created, dict) else {}
            event = event if isinstance(event, dict) else {}
            message = (
                str(event.get("summary") or "").strip()
                or "This needs an explicit Agent lane. Send `/agent your task` to run it."
            )
            await self._send_reply(
                bot,
                ctx,
                _format_for_telegram(message),
                lane_key=lane_key,
                already_cleaned=True,
                thread_id=ctx.thread_id,
            )
            return message

        backend = (
            os.environ.get("DAN_TELEGRAM_V2_AGENT_BACKEND")
            or os.environ.get("DAN_CHAT_V2_AGENT_BACKEND")
            or SUPER_TUI_DEFAULT_BACKEND
        ).strip()
        execute_body = build_super_tui_agent_execute_payload(
            backend=backend,
            background=True,
            surface=f"telegram:{bot.name}",
            metadata={
                "surface": f"telegram:{bot.name}",
                "requested_from": "telegram",
                "telegram_bot": bot.name,
                "gui_for": "dan super-tui",
            },
        )
        execute_resp = await self._http.post(
            f"/api/v2/agent-runs/{run_id}/execute",
            json=execute_body,
        )
        if execute_resp.status_code != 200:
            raise RuntimeError(
                f"V2 Agent run execution failed: HTTP {execute_resp.status_code}",
            )
        return await self._stream_v2_agent_run_events(
            bot,
            ctx,
            run_id,
            lane_key=lane_key,
        )

    async def _send_v2_agent_run_command(
        self,
        bot: BotInstance,
        ctx: MessageContext,
        body: dict[str, Any],
        selected_session: dict[str, Any],
        *,
        lane_key: str | None = None,
    ) -> str:
        """Steer or queue work on an already selected Telegram Agent session."""

        assert bot.adapter is not None
        if self._http is None:
            raise RuntimeError("Telegram fleet HTTP client is not available")

        message_text = str(body.get("message") or "")
        explicit_command = _telegram_agent_command(message_text)
        command_name = str(selected_session.get("queue_action") or "").strip()
        if explicit_command in {"append", "inject"}:
            command_name = "append"
        elif explicit_command in {"continue", "continue-after-current"}:
            command_name = "continue_after_current"
        if command_name == "append":
            command_name = "append_followup"
        if command_name not in {"append_followup", "continue_after_current"}:
            return ""
        run_id = str(selected_session.get("run_id") or "").strip()
        task_id = str(selected_session.get("task_id") or "").strip()
        if not run_id:
            return ""
        payload = {
            "command": command_name,
            "task_id": task_id or None,
            "run_id": run_id,
            "idempotency_key": uuid.uuid4().hex,
            "payload": {
                "text": _telegram_command_payload_text(message_text),
                "surface_context": dict(body.get("surface_context") or {}),
                "history": list(body.get("history") or []),
            },
        }
        resp = await self._http.post(
            f"/api/v2/agent-runs/{run_id}/commands",
            json=payload,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"V2 Agent run command failed: HTTP {resp.status_code}",
            )
        data = resp.json()
        event = data.get("event") if isinstance(data, dict) else {}
        event = event if isinstance(event, dict) else {}
        summary = str(event.get("summary") or "").strip()
        if not summary:
            summary = (
                "Steering note queued for the active DAN Super run."
                if command_name == "append_followup"
                else "Queued after the current DAN Super run."
            )
        await self._send_reply(
            bot,
            ctx,
            summary,
            lane_key=lane_key,
            thread_id=ctx.thread_id,
        )
        return summary

    async def _force_reset_selected_session(self, selected_session: dict[str, Any]) -> bool:
        """Force a selected Telegram Agent run out of the active lane."""

        if self._http is None:
            return False
        run_id = str(selected_session.get("run_id") or "").strip()
        if not run_id:
            return False
        task_id = str(selected_session.get("task_id") or "").strip() or None
        try:
            await self._http.post(
                f"/api/v2/agent-runs/{run_id}/commands",
                json={
                    "command": "stop",
                    "task_id": task_id,
                    "run_id": run_id,
                    "idempotency_key": f"telegram-reset-{uuid.uuid4().hex}",
                    "payload": {"reason": "telegram_reset"},
                },
            )
            resp = await self._http.post(
                f"/api/v2/agent-runs/{run_id}/events",
                json={
                    "type": "stopped",
                    "run_id": run_id,
                    "task_id": task_id,
                    "summary": "Telegram reset forced this run out of the active lane.",
                    "source_event_type": "telegram.reset.forced",
                    "payload": {"checkpoint": "telegram.reset"},
                },
            )
            return int(getattr(resp, "status_code", 0) or 0) == 200
        except Exception:
            logger.debug("Failed to force-reset Telegram selected session", exc_info=True)
            return False

    async def _stream_v2_agent_run_events(
        self,
        bot: BotInstance,
        ctx: MessageContext,
        run_id: str,
        *,
        lane_key: str | None = None,
    ) -> str:
        """Stream normalized Agent events into one Telegram progress message."""

        assert bot.adapter is not None
        import websockets

        ws_url = self._server_url.replace("http://", "ws://").replace(
            "https://", "wss://",
        )
        url = f"{ws_url}/api/v2/agent-runs/{run_id}/events"
        current_msg_id: int | None = None
        terminal_summary = ""
        progress = AgentProgressStateMachine(run_id=run_id)
        heartbeat_interval = max(0.0, float(self._V2_AGENT_PROGRESS_INTERVAL))

        async def _send_progress(text: str) -> None:
            nonlocal current_msg_id
            current_msg_id = await bot.adapter.send_or_edit(
                ctx.chat_id,
                _format_for_telegram(text),
                current_msg_id,
                reply_to=ctx.message_id if current_msg_id is None else None,
                thread_id=ctx.thread_id,
            )
            self._remember_outbound_message(
                ctx.chat_id,
                current_msg_id,
                lane_key=lane_key,
            )

        async with websockets.connect(
            url, ping_interval=None, ping_timeout=None,
        ) as ws:
            while True:
                try:
                    if heartbeat_interval > 0:
                        ws_msg = await asyncio.wait_for(
                            ws.recv(),
                            timeout=heartbeat_interval,
                        )
                    else:
                        ws_msg = await ws.recv()
                except asyncio.TimeoutError:
                    if not progress.terminal:
                        await _send_progress(progress.render_status(heartbeat=True))
                    continue
                except Exception as exc:
                    if exc.__class__.__name__.startswith("ConnectionClosed"):
                        break
                    raise
                event = json.loads(ws_msg)
                if not isinstance(event, dict):
                    continue
                snapshot = progress.observe(event)
                text = progress.render_status()
                await _send_progress(text)
                if snapshot.terminal:
                    terminal_summary = snapshot.latest_summary or snapshot.detail or text
                    break
        return terminal_summary or progress.snapshot().latest_summary

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
        if self._http is None:
            raise RuntimeError("Telegram fleet HTTP client is not available")

        resp = await self._http.get(f"/api/graphs/{wf_id}")
        if resp.status_code == 404:
            create_resp = await self._http.post(
                "/api/graphs",
                json={
                    "graph_id": wf_id,
                    "data": {"nodes": [], "edges": []},
                },
            )
            if create_resp.status_code not in (200, 409):
                raise RuntimeError(
                    f"Unable to initialize workflow '{wf_id}' for Telegram conversation",
                )
        elif resp.status_code != 200:
            raise RuntimeError(
                f"Unable to load workflow '{wf_id}' for Telegram conversation",
            )
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
        return [
            ("agent", "Run a DAN Super task"),
            ("workspace", "Choose DAN Super workspace"),
            ("session", "Resume DAN Super session"),
            ("tasks", "Show DAN Super sessions"),
            ("append", "Steer active DAN Super run"),
            ("continue", "Queue after current run"),
            ("new", "Start a separate DAN Super run"),
            ("status", "Show DAN Super status"),
            ("cancel", "Stop active DAN Super run"),
            ("help", "Show available commands"),
        ]

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


def _env_suffix(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip().upper()).strip("_")


def _telegram_allow_v1() -> bool:
    return str(os.environ.get("DAN_TELEGRAM_ALLOW_V1") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _telegram_control_plane_override(bot_name: str) -> str:
    allow_v1 = _telegram_allow_v1()
    suffix = _env_suffix(bot_name)
    candidates = [
        f"DAN_TELEGRAM_{suffix}_CONTROL_PLANE" if suffix else "",
        "DAN_TELEGRAM_CONTROL_PLANE",
        "DAN_ADAPTERS_CONTROL_PLANE",
    ]
    for key in candidates:
        if not key:
            continue
        value = str(os.environ.get(key) or "").strip().lower()
        if value in {"v1", "legacy"}:
            return "v1" if allow_v1 else "v2"
        if value in {"v2", "dan-v2", "dan_v2"}:
            return "v2"
    if str(os.environ.get("DAN_TELEGRAM_V2") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return "v2"
    return "v2"


def _telegram_workspace_context(bot_name: str) -> dict[str, Any]:
    suffix = _env_suffix(bot_name)
    root = (
        os.environ.get(f"DAN_TELEGRAM_{suffix}_WORKSPACE_ROOT") if suffix else None
    ) or os.environ.get("DAN_TELEGRAM_WORKSPACE_ROOT") or os.environ.get(
        "DAN_WORKSPACE_ROOT",
    )
    workspace_id = (
        os.environ.get(f"DAN_TELEGRAM_{suffix}_WORKSPACE_ID") if suffix else None
    ) or os.environ.get("DAN_TELEGRAM_WORKSPACE_ID")
    context: dict[str, Any] = {}
    if root:
        context["workspace_root"] = root
    if workspace_id:
        context["workspace_id"] = workspace_id
    return context


def _telegram_requested_mode(text: str) -> tuple[str, str]:
    stripped = str(text or "").strip()
    if not stripped:
        return "auto", stripped
    first, _, rest = stripped.partition(" ")
    command = first.split("@", 1)[0].lower()
    if command in {"/agent", "/run", "/build", "/new"}:
        return "agent", rest.strip()
    if command in {"/append", "/inject", "/continue", "/continue-after-current"}:
        return "agent", stripped
    if stripped.lower().startswith("agent:"):
        return "agent", stripped.split(":", 1)[1].strip()
    return "auto", stripped


def _telegram_agent_command(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return ""
    first = stripped.split(maxsplit=1)[0].split("@", 1)[0].lower()
    command = first.lstrip("/")
    if command in {
        "agent",
        "run",
        "build",
        "new",
        "append",
        "inject",
        "continue",
        "continue-after-current",
    }:
        return command
    return ""


def _telegram_command_payload_text(text: str) -> str:
    stripped = str(text or "").strip()
    command = _telegram_agent_command(stripped)
    if command in {"append", "inject", "continue", "continue-after-current"}:
        _first, _sep, rest = stripped.partition(" ")
        return rest.strip()
    return stripped


def _telegram_effective_requested_mode(
    control_plane: str | None,
    requested_mode: str,
    text: str,
) -> str:
    mode = str(requested_mode or "auto").strip().lower() or "auto"
    if (
        control_plane == "v2"
        and mode == "auto"
        and not _telegram_v2_control_command(text)
        and _telegram_v2_auto_text_should_run_agent(text)
    ):
        return "agent"
    return mode


def _telegram_v2_control_command(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return ""
    first = stripped.split(maxsplit=1)[0].split("@", 1)[0].lower()
    command = first.lstrip("/")
    if command in {
        "status",
        "cancel",
        "help",
        "start",
        "workspace",
        "session",
        "sessions",
        "tasks",
        "reset",
        "clear",
    }:
        return command
    return ""


def _telegram_v2_auto_text_should_run_agent(text: str) -> bool:
    lower = " ".join(str(text or "").lower().split())
    if not lower:
        return False
    if _extract_attachment(text) or _extract_voice_note(text):
        return any(
            cue in lower
            for cue in (
                "analyze",
                "extract",
                "build",
                "turn into",
                "compare",
                "summarize",
            )
        )
    has_path_context = bool(
        re.search(r"(^|\s)(/[^ ]+|~/[^ ]+|[a-z]:\\)", lower)
    ) or any(
        cue in lower
        for cue in (
            "this path",
            "path:",
            "repo:",
            "workspace:",
            "in /",
            "in ~/",
        )
    )
    if has_path_context and any(
        cue in lower
        for cue in (
            "help",
            "do this",
            "work on",
            "fix",
            "patch",
            "build",
            "implement",
            "edit",
            "modify",
            "run tests",
            "create",
        )
    ):
        return True
    return any(
        cue in lower
        for cue in (
            "implement",
            "build",
            "create file",
            "write to",
            "refactor",
            "run tests",
            "make a website",
            "patch",
            "edit the",
            "modify the",
            "fix the repo",
            "fix this repo",
            "long running",
        )
    )


def _compact_context_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _format_v2_agent_event_for_telegram(
    *,
    event_type: str,
    summary: str,
    run_id: str,
    artifact_refs: list[dict[str, Any]],
    token_usage: dict[str, int] | None = None,
    event_token_usage_delta: dict[str, int] | None = None,
    event_token_usage_total: dict[str, int] | None = None,
) -> str:
    status = {
        "accepted": "Accepted",
        "queued": "Queued",
        "planned": "Planning",
        "worker_started": "Working",
        "model_text_delta": "Thinking",
        "tool_used": "Using tools",
        "artifact_changed": "Updated artifacts",
        "validation_started": "Validating",
        "repair_started": "Repairing",
        "token_usage_recorded": "Tokens",
        "completed": "Done",
        "failed": "Failed",
        "blocked": "Blocked",
        "stopped": "Stopped",
    }.get(event_type, event_type.replace("_", " ").title() or "Agent")
    lines = [f"{status}: {summary or run_id}"]
    if event_type == "token_usage_recorded":
        delta_text = format_token_usage(event_token_usage_delta or {})
        total_text = format_token_usage(event_token_usage_total or token_usage or {})
        lines = [f"Tokens: {delta_text}"]
        if total_text != "unavailable":
            lines[0] += f" (run total: {total_text})"
    elif event_type in {"completed", "failed", "blocked", "stopped"}:
        usage_text = format_token_usage(token_usage or event_token_usage_total or {})
        if usage_text != "unavailable":
            lines.append("")
            lines.append(f"Tokens: {usage_text}")
    if event_type == "completed" and artifact_refs:
        paths = [str(item.get("path") or "") for item in artifact_refs if item.get("path")]
        if paths:
            lines.append("")
            lines.append("Artifacts:")
            lines.extend(f"- {path}" for path in paths[:8])
    return _format_for_telegram("\n".join(lines).strip())


def _v2_event_token_usage_total(
    event: dict[str, Any],
    *,
    current_total: dict[str, int],
) -> dict[str, int]:
    total = normalize_token_usage(event.get("token_usage_total"))
    if total:
        return total
    delta = normalize_token_usage(event.get("token_usage_delta"))
    if delta:
        return merge_token_usage(current_total, delta)
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    total = normalize_token_usage(payload.get("token_usage_total") or payload.get("usage_totals"))
    if total:
        return total
    delta = normalize_token_usage(payload.get("token_usage_delta") or payload.get("usage"))
    if delta:
        return merge_token_usage(current_total, delta)
    return dict(current_total)


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
    *,
    fork_for_parallel: bool = False,
) -> str:
    conversation_key = _conversation_thread_key(ctx, bot_name)
    if reply_lane_key:
        return reply_lane_key
    if not fork_for_parallel or ctx.message_id is None:
        return conversation_key
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


def _telegram_state_dir() -> Path:
    configured = str(os.environ.get("DAN_TELEGRAM_STATE_DIR") or "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".dan" / "telegram"


def _telegram_surface_state_path() -> Path:
    return _telegram_state_dir() / "surface-state.json"


def _load_telegram_surface_state() -> dict[str, Any]:
    path = _telegram_surface_state_path()
    if not path.exists():
        return {
            "active_workspaces": {},
            "recent_workspaces": {},
            "active_sessions": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload.setdefault("active_workspaces", {})
            payload.setdefault("recent_workspaces", {})
            payload.setdefault("active_sessions", {})
            return payload
    except Exception:
        logger.debug("Failed to load Telegram surface state", exc_info=True)
    return {
        "active_workspaces": {},
        "recent_workspaces": {},
        "active_sessions": {},
    }


def _save_telegram_surface_state(state: dict[str, Any]) -> None:
    path = _telegram_surface_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        logger.debug("Failed to save Telegram surface state", exc_info=True)


def _telegram_menu_command(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return ""
    command = stripped.split(maxsplit=1)[0].split("@", 1)[0].lower()
    if command in {"/workspace", "/workspaces", "/ws"}:
        return "workspace"
    if command in {"/session", "/sessions", "/tasks", "/status"}:
        return "session"
    if command in {"/reset", "/clear"}:
        return "reset"
    return ""


def _telegram_command_argument(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return ""
    try:
        parts = shlex.split(stripped)
    except ValueError:
        parts = stripped.split(maxsplit=1)
    if len(parts) <= 1:
        return ""
    return " ".join(parts[1:]).strip()


def _normalize_workspace_menu_path(value: Any, *, base: Any = None) -> Path:
    raw = str(value or "").strip() or str(Path.cwd())
    if raw.startswith("$HOME/") or raw == "$HOME":
        raw = str(Path.home()) + raw[len("$HOME") :]
    path = Path(raw).expanduser()
    if not path.is_absolute():
        base_path = Path(str(base or Path.cwd())).expanduser()
        path = base_path / path
    try:
        return path.resolve(strict=False)
    except Exception:
        return path


def _workspace_child_dirs(path: Path) -> list[Path]:
    try:
        if not path.exists() or not path.is_dir():
            return []
        dirs = [
            item
            for item in path.iterdir()
            if item.is_dir() and not item.name.startswith(".")
        ]
        dirs.sort(key=lambda item: item.name.lower())
        return dirs[:100]
    except Exception:
        return []


def _short_path_label(path: str, *, max_len: int = 32) -> str:
    text = str(path or "").strip()
    name = Path(text).name or text
    label = name if len(name) <= max_len else name[: max_len - 3].rstrip() + "..."
    if label:
        return label
    return text[-max_len:] if len(text) > max_len else text


def _workspace_id_from_path(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    return Path(text).name or text


def _session_workspace_groups(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for task in tasks:
        metadata = dict(task.get("metadata") or {})
        root = str(task.get("workspace_root") or metadata.get("workspace_root") or "").strip()
        workspace_id = str(task.get("workspace_id") or metadata.get("workspace_id") or "").strip()
        key = root or workspace_id or "default"
        group = index.get(key)
        if group is None:
            group = {
                "key": key,
                "label": _session_workspace_label(root or workspace_id or "default workspace"),
                "tasks": [],
            }
            index[key] = group
            groups.append(group)
        group["tasks"].append(task)
    return groups


def _session_workspace_label(path: str) -> str:
    text = str(path or "").strip()
    if not text or text == "~":
        return "~"
    return _short_path_label(text, max_len=40)


def _session_task_title(task: dict[str, Any]) -> str:
    metadata = dict(task.get("metadata") or {})
    for value in (
        task.get("title"),
        task.get("objective"),
        metadata.get("command_text"),
        metadata.get("objective"),
        metadata.get("title"),
        task.get("latest_progress"),
    ):
        title = _compact_context_text(value, limit=96)
        if title:
            return title
    return "Untitled DAN Super session"


def _session_task_button_label(task: dict[str, Any]) -> str:
    return _compact_context_text(_session_task_title(task), limit=28)


def _session_status_label(task: dict[str, Any]) -> str:
    status = str(task.get("status") or "unknown").strip().lower()
    if status == "running":
        return "running"
    if status in {"queued", "waiting_dependency"}:
        return "queued"
    if status in {"needs_input", "paused"}:
        return status.replace("_", " ")
    if status in {"completed", "failed", "blocked", "stopped"}:
        return status
    return status or "unknown"


def _split_menu_callback(data: str) -> tuple[str, str, str, str]:
    parts = str(data or "").split(":", 3)
    while len(parts) < 4:
        parts.append("")
    return parts[0], parts[1], parts[2], parts[3]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


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


def _format_interrupted_stream_reply(partial_reply: str) -> str:
    partial = str(partial_reply or "").strip()
    if not partial:
        return _TELEGRAM_STREAM_MISSING_TERMINAL_FALLBACK
    return (
        f"{partial}\n\n{_TELEGRAM_STREAM_MISSING_TERMINAL_FALLBACK}"
    )


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
