"""Tests for Telegram multi-bot message routing."""

from __future__ import annotations

from dan.adapters.telegram_router import (
    DefaultBotSignal,
    MentionSignal,
    MessageRouter,
    ProjectKeywordSignal,
    ReplyOwnershipSignal,
    RoutableBot,
    RoutingContext,
    TopicMappingSignal,
)


def _bots() -> list[RoutableBot]:
    return [
        RoutableBot(
            name="research-bot",
            username="research_bot",
            projects=["literature", "paper"],
            is_default=False,
        ),
        RoutableBot(
            name="data-bot",
            username="data_bot",
            projects=["equity", "analysis", "data"],
            is_default=False,
        ),
        RoutableBot(
            name="dan",
            username="dan_bot",
            projects=[],
            is_default=True,
        ),
    ]


# ---------------------------------------------------------------------------
# Existing behavior preserved
# ---------------------------------------------------------------------------


def test_route_prefers_explicit_mention() -> None:
    router = MessageRouter()
    winner = router.route(
        text="Hey @data_bot analyze this CSV",
        mentions=["data_bot"],
        chat_type="group",
        chat_id=-1001,
        thread_id=None,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
    )
    assert winner is not None
    assert winner.name == "data-bot"


def test_route_uses_forum_topic_mapping() -> None:
    router = MessageRouter(multi_group_topic_maps={-1001: {42: "literature"}})
    winner = router.route(
        text="What should I read next?",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=42,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
    )
    assert winner is not None
    assert winner.name == "research-bot"


def test_route_uses_keyword_overlap_before_default() -> None:
    router = MessageRouter()
    winner = router.route(
        text="Please do an equity analysis on this portfolio",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=None,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
    )
    assert winner is not None
    assert winner.name == "data-bot"


def test_route_falls_back_to_default_bot() -> None:
    router = MessageRouter()
    winner = router.route(
        text="What should I work on next?",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=None,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
    )
    assert winner is not None
    assert winner.name == "dan"


def test_route_uses_receiving_bot_in_private_chat() -> None:
    router = MessageRouter()
    winner = router.route(
        text="Help me with anything",
        mentions=[],
        chat_type="private",
        chat_id=555,
        thread_id=None,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
        receiving_bot_username="research_bot",
    )
    assert winner is not None
    assert winner.name == "research-bot"


def test_route_ignores_messages_from_bots() -> None:
    router = MessageRouter()
    winner = router.route(
        text="hello",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=None,
        from_user_is_bot=True,
        from_user_id=123,
        bots=_bots(),
    )
    assert winner is None


# ---------------------------------------------------------------------------
# Reply-sticky ownership
# ---------------------------------------------------------------------------


def test_reply_to_bot_routes_to_that_bot() -> None:
    """Replying to research-bot's message should route to research-bot."""
    router = MessageRouter()
    winner = router.route(
        text="Can you elaborate?",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=None,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
        reply_to_bot="research-bot",
    )
    assert winner is not None
    assert winner.name == "research-bot"


def test_reply_to_bot_by_username() -> None:
    """Reply-sticky also works when reply_to_bot is a username."""
    router = MessageRouter()
    winner = router.route(
        text="Thanks, more details?",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=None,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
        reply_to_bot="data_bot",
    )
    assert winner is not None
    assert winner.name == "data-bot"


def test_mention_overrides_reply_sticky() -> None:
    """Explicit @mention should beat reply-sticky ownership."""
    router = MessageRouter()
    winner = router.route(
        text="@dan_bot help me instead",
        mentions=["dan_bot"],
        chat_type="group",
        chat_id=-1001,
        thread_id=None,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
        reply_to_bot="research-bot",
    )
    assert winner is not None
    assert winner.name == "dan"


def test_reply_sticky_beats_keyword() -> None:
    """Reply to research-bot should win even when text contains data-bot keywords."""
    router = MessageRouter()
    winner = router.route(
        text="What about the equity analysis?",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=None,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
        reply_to_bot="research-bot",
    )
    assert winner is not None
    assert winner.name == "research-bot"


def test_reply_to_unknown_bot_falls_through() -> None:
    """Reply to an unknown bot name should fall through to other signals."""
    router = MessageRouter()
    winner = router.route(
        text="Hello",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=None,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
        reply_to_bot="nonexistent-bot",
    )
    assert winner is not None
    assert winner.name == "dan"


# ---------------------------------------------------------------------------
# Signal architecture
# ---------------------------------------------------------------------------


def test_default_signal_chain_has_five_signals() -> None:
    router = MessageRouter()
    names = [s.name for s in router.signals]
    assert names == [
        "mention",
        "reply_ownership",
        "topic_mapping",
        "project_keyword",
        "default_bot",
    ]


def test_custom_signal_chain() -> None:
    """Router with only mention and default — no reply-sticky or keyword."""
    router = MessageRouter(
        signals=[MentionSignal(), DefaultBotSignal()],
    )
    winner = router.route(
        text="What about equity?",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=None,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
    )
    assert winner is not None
    assert winner.name == "dan"


def test_remove_signal() -> None:
    router = MessageRouter()
    removed = router.remove_signal("project_keyword")
    assert removed is True
    names = [s.name for s in router.signals]
    assert "project_keyword" not in names
    assert len(names) == 4


def test_add_signal_at_index() -> None:
    router = MessageRouter()
    extra = ProjectKeywordSignal()
    extra.name = "custom_keyword"
    router.add_signal(extra, index=1)
    names = [s.name for s in router.signals]
    assert names[1] == "custom_keyword"
    assert len(names) == 6


def test_set_signals_replaces_chain() -> None:
    router = MessageRouter()
    router.set_signals([DefaultBotSignal()])
    assert len(router.signals) == 1
    assert router.signals[0].name == "default_bot"


def test_signal_exception_is_caught() -> None:
    """A broken signal should not crash the router — it's skipped."""

    class BrokenSignal:
        name = "broken"

        def evaluate(self, ctx: RoutingContext) -> None:
            raise RuntimeError("kaboom")

    router = MessageRouter(signals=[BrokenSignal(), DefaultBotSignal()])
    winner = router.route(
        text="hello",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=None,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
    )
    assert winner is not None
    assert winner.name == "dan"


def test_set_topic_map_delegates_to_signal() -> None:
    router = MessageRouter()
    router.set_topic_map({99: "paper"}, chat_id=-1001)
    winner = router.route(
        text="What's the status?",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=99,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
    )
    assert winner is not None
    assert winner.name == "research-bot"


# ---------------------------------------------------------------------------
# Individual signal unit tests
# ---------------------------------------------------------------------------


def _ctx(**overrides) -> RoutingContext:
    defaults = dict(
        text="hello",
        mentions=[],
        chat_type="group",
        chat_id=-1001,
        thread_id=None,
        from_user_is_bot=False,
        from_user_id=123,
        bots=_bots(),
        receiving_bot_username=None,
        reply_to_bot=None,
    )
    defaults.update(overrides)
    return RoutingContext(**defaults)


def test_mention_signal_returns_none_without_mention() -> None:
    assert MentionSignal().evaluate(_ctx()) is None


def test_mention_signal_matches() -> None:
    result = MentionSignal().evaluate(_ctx(mentions=["research_bot"]))
    assert result is not None
    assert result.bot.name == "research-bot"
    assert result.confidence == 1.0


def test_reply_ownership_signal_returns_none_without_reply() -> None:
    assert ReplyOwnershipSignal().evaluate(_ctx()) is None


def test_reply_ownership_signal_matches_by_name() -> None:
    result = ReplyOwnershipSignal().evaluate(_ctx(reply_to_bot="data-bot"))
    assert result is not None
    assert result.bot.name == "data-bot"
    assert result.confidence == 0.9


def test_reply_ownership_signal_matches_by_username() -> None:
    result = ReplyOwnershipSignal().evaluate(_ctx(reply_to_bot="research_bot"))
    assert result is not None
    assert result.bot.name == "research-bot"


def test_keyword_signal_returns_none_without_match() -> None:
    assert ProjectKeywordSignal().evaluate(_ctx(text="hello world")) is None


def test_keyword_signal_picks_highest_score() -> None:
    result = ProjectKeywordSignal().evaluate(
        _ctx(text="equity analysis and data review"),
    )
    assert result is not None
    assert result.bot.name == "data-bot"
    assert result.confidence == 0.7


def test_default_signal_returns_default_bot() -> None:
    result = DefaultBotSignal().evaluate(_ctx())
    assert result is not None
    assert result.bot.name == "dan"


def test_default_signal_returns_none_when_no_default() -> None:
    bots_no_default = [
        RoutableBot(name="a", username="a_bot"),
        RoutableBot(name="b", username="b_bot"),
    ]
    result = DefaultBotSignal().evaluate(_ctx(bots=bots_no_default))
    assert result is None
