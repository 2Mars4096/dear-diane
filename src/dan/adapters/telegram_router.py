"""Message routing for Telegram multi-bot groups.

The ``MessageRouter`` decides which bot in a fleet should respond to a given
group message.  Routing rules are evaluated in priority order: @mention >
forum topic > project keyword > default bot.  DMs bypass project routing
entirely — the receiving bot always handles them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RoutableBot:
    """Lightweight view of a bot for routing decisions."""

    name: str
    username: str
    projects: list[str] = field(default_factory=list)
    is_default: bool = False


class MessageRouter:
    """Decides which bot responds to a group message.

    Always returns exactly one bot or ``None`` (ignore the message).  Combined
    with the fleet's dedup set this guarantees at most one response per message.
    """

    def __init__(
        self,
        topic_map: dict[int, str] | None = None,
        multi_group_topic_maps: dict[int, dict[int, str]] | None = None,
    ) -> None:
        self._topic_map = topic_map or {}
        self._multi_group_topic_maps = multi_group_topic_maps or {}

    def set_topic_map(
        self, topic_map: dict[int, str], chat_id: int | None = None,
    ) -> None:
        if chat_id is not None:
            self._multi_group_topic_maps[chat_id] = topic_map
        else:
            self._topic_map = topic_map

    def route(
        self,
        text: str,
        mentions: list[str],
        chat_type: str,
        chat_id: int,
        thread_id: int | None,
        from_user_is_bot: bool,
        from_user_id: int | None,
        bots: list[RoutableBot],
        receiving_bot_username: str | None = None,
    ) -> RoutableBot | None:
        """Return the single bot that should handle this message, or None."""
        if from_user_is_bot:
            return None

        if not bots:
            return None

        if chat_type == "private":
            if receiving_bot_username:
                for bot in bots:
                    if bot.username == receiving_bot_username:
                        return bot
            return _find_default(bots) or bots[0]

        # --- group routing, in priority order ---

        # 1. @mention
        if mentions:
            for bot in bots:
                if bot.username in mentions:
                    return bot

        # 2. Forum topic mapping
        if thread_id is not None:
            topic_maps = self._multi_group_topic_maps.get(
                chat_id, self._topic_map,
            )
            project = topic_maps.get(thread_id)
            if project:
                for bot in bots:
                    if project in bot.projects:
                        return bot

        # 3. Project keyword scoring
        best_bot, best_score = None, 0
        lower_text = text.lower()
        for bot in bots:
            score = sum(
                1 for kw in bot.projects if kw.lower() in lower_text
            )
            if score > best_score:
                best_score = score
                best_bot = bot
        if best_bot is not None and best_score > 0:
            return best_bot

        # 4. Default bot fallback
        return _find_default(bots)


def _find_default(bots: list[RoutableBot]) -> RoutableBot | None:
    for bot in bots:
        if bot.is_default:
            return bot
    return None
