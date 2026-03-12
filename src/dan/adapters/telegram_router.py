"""Message routing for Telegram multi-bot groups.

The ``MessageRouter`` decides which bot in a fleet should respond to a given
group message.  Routing is driven by pluggable ``RoutingSignal`` instances
evaluated in priority order.  The first signal to return a match wins.

Default signal chain: @mention > reply-sticky ownership > forum topic >
project keyword > default bot.  DMs bypass signal routing entirely — the
receiving bot always handles them.

Callers can reorder, add, or remove signals via ``set_signals()``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class RoutableBot:
    """Lightweight view of a bot for routing decisions."""

    name: str
    username: str
    projects: list[str] = field(default_factory=list)
    is_default: bool = False


@dataclass
class RoutingContext:
    """Everything a routing signal needs to make a decision."""

    text: str
    mentions: list[str]
    chat_type: str
    chat_id: int
    thread_id: int | None
    from_user_is_bot: bool
    from_user_id: int | None
    bots: list[RoutableBot]
    receiving_bot_username: str | None = None
    reply_to_bot: str | None = None


@dataclass
class RoutingResult:
    """A routing decision from a signal."""

    bot: RoutableBot
    signal: str
    confidence: float = 1.0
    reason: str = ""


@runtime_checkable
class RoutingSignal(Protocol):
    """A single routing signal that can vote on which bot handles a message."""

    name: str

    def evaluate(self, ctx: RoutingContext) -> RoutingResult | None: ...


# ---------------------------------------------------------------------------
# Built-in signals
# ---------------------------------------------------------------------------


class MentionSignal:
    """Explicit @mention — highest priority."""

    name = "mention"

    def evaluate(self, ctx: RoutingContext) -> RoutingResult | None:
        if not ctx.mentions:
            return None
        for bot in ctx.bots:
            if bot.username in ctx.mentions:
                return RoutingResult(
                    bot=bot,
                    signal=self.name,
                    confidence=1.0,
                    reason=f"@{bot.username} mentioned",
                )
        return None


class ReplyOwnershipSignal:
    """Replying to a bot's message routes to that bot.

    When a user replies to a message from bot X, this signal keeps the
    conversation with bot X instead of falling through to keyword/default
    routing — preventing the "wrong bot takeover" problem in groups.
    """

    name = "reply_ownership"

    def evaluate(self, ctx: RoutingContext) -> RoutingResult | None:
        if not ctx.reply_to_bot:
            return None
        for bot in ctx.bots:
            if bot.name == ctx.reply_to_bot or bot.username == ctx.reply_to_bot:
                return RoutingResult(
                    bot=bot,
                    signal=self.name,
                    confidence=0.9,
                    reason=f"reply to {bot.name}'s message",
                )
        return None


class TopicMappingSignal:
    """Forum topic → project → bot assignment."""

    name = "topic_mapping"

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

    def evaluate(self, ctx: RoutingContext) -> RoutingResult | None:
        if ctx.thread_id is None:
            return None
        topic_maps = self._multi_group_topic_maps.get(
            ctx.chat_id, self._topic_map,
        )
        project = topic_maps.get(ctx.thread_id)
        if not project:
            return None
        for bot in ctx.bots:
            if project in bot.projects:
                return RoutingResult(
                    bot=bot,
                    signal=self.name,
                    confidence=0.85,
                    reason=f"topic {ctx.thread_id} → project '{project}'",
                )
        return None


class ProjectKeywordSignal:
    """Project keyword scoring — match project names in message text."""

    name = "project_keyword"

    def evaluate(self, ctx: RoutingContext) -> RoutingResult | None:
        best_bot, best_score = None, 0
        lower_text = ctx.text.lower()
        for bot in ctx.bots:
            score = sum(
                1 for kw in bot.projects if kw.lower() in lower_text
            )
            if score > best_score:
                best_score = score
                best_bot = bot
        if best_bot is not None and best_score > 0:
            return RoutingResult(
                bot=best_bot,
                signal=self.name,
                confidence=0.7,
                reason=f"{best_score} keyword match(es)",
            )
        return None


class DefaultBotSignal:
    """Fallback to the default bot when no other signal matches."""

    name = "default_bot"

    def evaluate(self, ctx: RoutingContext) -> RoutingResult | None:
        default = _find_default(ctx.bots)
        if default is not None:
            return RoutingResult(
                bot=default,
                signal=self.name,
                confidence=0.1,
                reason="default bot fallback",
            )
        return None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def _default_signals(
    topic_map: dict[int, str] | None = None,
    multi_group_topic_maps: dict[int, dict[int, str]] | None = None,
) -> list[RoutingSignal]:
    """Build the standard signal chain."""
    return [
        MentionSignal(),
        ReplyOwnershipSignal(),
        TopicMappingSignal(
            topic_map=topic_map,
            multi_group_topic_maps=multi_group_topic_maps,
        ),
        ProjectKeywordSignal(),
        DefaultBotSignal(),
    ]


class MessageRouter:
    """Decides which bot responds to a group message.

    Always returns exactly one bot or ``None`` (ignore the message).  Combined
    with the fleet's dedup set this guarantees at most one response per message.

    The router evaluates a configurable chain of ``RoutingSignal`` instances.
    Signals are tried in order; the first non-None result wins.
    """

    def __init__(
        self,
        topic_map: dict[int, str] | None = None,
        multi_group_topic_maps: dict[int, dict[int, str]] | None = None,
        signals: list[RoutingSignal] | None = None,
    ) -> None:
        if signals is not None:
            self._signals = list(signals)
        else:
            self._signals = _default_signals(topic_map, multi_group_topic_maps)

    @property
    def signals(self) -> list[RoutingSignal]:
        return list(self._signals)

    def set_signals(self, signals: list[RoutingSignal]) -> None:
        """Replace the signal chain."""
        self._signals = list(signals)

    def add_signal(self, signal: RoutingSignal, *, index: int | None = None) -> None:
        """Insert a signal at a specific position (default: append)."""
        if index is not None:
            self._signals.insert(index, signal)
        else:
            self._signals.append(signal)

    def remove_signal(self, name: str) -> bool:
        """Remove a signal by name. Returns True if found."""
        before = len(self._signals)
        self._signals = [s for s in self._signals if s.name != name]
        return len(self._signals) < before

    def set_topic_map(
        self, topic_map: dict[int, str], chat_id: int | None = None,
    ) -> None:
        """Delegate to the TopicMappingSignal if present."""
        for sig in self._signals:
            if isinstance(sig, TopicMappingSignal):
                sig.set_topic_map(topic_map, chat_id)
                return

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
        reply_to_bot: str | None = None,
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

        ctx = RoutingContext(
            text=text,
            mentions=mentions,
            chat_type=chat_type,
            chat_id=chat_id,
            thread_id=thread_id,
            from_user_is_bot=from_user_is_bot,
            from_user_id=from_user_id,
            bots=bots,
            receiving_bot_username=receiving_bot_username,
            reply_to_bot=reply_to_bot,
        )

        for signal in self._signals:
            try:
                result = signal.evaluate(ctx)
            except Exception:
                logger.warning(
                    "Routing signal %s raised; skipping",
                    getattr(signal, "name", signal),
                    exc_info=True,
                )
                continue
            if result is not None:
                logger.debug(
                    "Route: signal=%s bot=%s confidence=%.2f reason=%s",
                    result.signal,
                    result.bot.name,
                    result.confidence,
                    result.reason,
                )
                return result.bot

        return None


def _find_default(bots: list[RoutableBot]) -> RoutableBot | None:
    for bot in bots:
        if bot.is_default:
            return bot
    return None
