"""Pytest tests for MessageRouter."""

import pytest

from dan.adapters.telegram_router import MessageRouter, RoutableBot


# --- Fixtures ---


@pytest.fixture
def bot_a() -> RoutableBot:
    return RoutableBot(
        name="BotA",
        username="bot_a_bot",
        projects=["alpha", "beta"],
        is_default=False,
    )


@pytest.fixture
def bot_b() -> RoutableBot:
    return RoutableBot(
        name="BotB",
        username="bot_b_bot",
        projects=["gamma", "delta"],
        is_default=False,
    )


@pytest.fixture
def bot_default() -> RoutableBot:
    return RoutableBot(
        name="DefaultBot",
        username="default_bot",
        projects=[],
        is_default=True,
    )


def _route(
    router: MessageRouter,
    text: str = "",
    mentions: list[str] | None = None,
    chat_type: str = "supergroup",
    chat_id: int = 100,
    thread_id: int | None = None,
    from_user_is_bot: bool = False,
    from_user_id: int | None = 123,
    bots: list[RoutableBot] | None = None,
    receiving_bot_username: str | None = None,
) -> RoutableBot | None:
    """Helper to call route with sensible defaults."""
    return router.route(
        text=text,
        mentions=mentions or [],
        chat_type=chat_type,
        chat_id=chat_id,
        thread_id=thread_id,
        from_user_is_bot=from_user_is_bot,
        from_user_id=from_user_id,
        bots=bots or [],
        receiving_bot_username=receiving_bot_username,
    )


# --- 1. @mention routing ---


def test_mention_routes_to_matching_bot(bot_a: RoutableBot, bot_b: RoutableBot) -> None:
    router = MessageRouter()
    result = _route(
        router,
        text="Hey @bot_b_bot can you help?",
        mentions=["bot_b_bot"],
        bots=[bot_a, bot_b],
    )
    assert result is bot_b


def test_mention_ignores_other_bots(bot_a: RoutableBot, bot_b: RoutableBot) -> None:
    router = MessageRouter()
    result = _route(
        router,
        text="Hey @bot_b_bot",
        mentions=["bot_b_bot"],
        bots=[bot_a, bot_b],
    )
    assert result is bot_b


# --- 2. Forum topic routing ---


def test_forum_topic_routes_to_bot_with_project(
    bot_a: RoutableBot, bot_b: RoutableBot
) -> None:
    router = MessageRouter(topic_map={5: "gamma"})
    result = _route(
        router,
        text="random message",
        thread_id=5,
        bots=[bot_a, bot_b],
    )
    assert result is bot_b


def test_forum_topic_ignores_unmapped_thread(
    bot_a: RoutableBot, bot_b: RoutableBot
) -> None:
    router = MessageRouter(topic_map={5: "gamma"})
    result = _route(
        router,
        text="random message",
        thread_id=99,
        bots=[bot_a, bot_b],
    )
    assert result is None


# --- 3. Project keyword scoring ---


def test_keyword_routes_to_matching_bot(
    bot_a: RoutableBot, bot_b: RoutableBot
) -> None:
    router = MessageRouter()
    result = _route(
        router,
        text="Let's discuss gamma and delta",
        bots=[bot_a, bot_b],
    )
    assert result is bot_b


def test_keyword_case_insensitive(bot_a: RoutableBot, bot_b: RoutableBot) -> None:
    router = MessageRouter()
    result = _route(
        router,
        text="GAMMA and DELTA",
        bots=[bot_a, bot_b],
    )
    assert result is bot_b


# --- 4. Default bot fallback ---


def test_default_bot_fallback_when_no_match(
    bot_a: RoutableBot, bot_default: RoutableBot
) -> None:
    router = MessageRouter()
    result = _route(
        router,
        text="unrelated message",
        bots=[bot_a, bot_default],
    )
    assert result is bot_default


# --- 5. DM (private chat) ---


def test_dm_routes_to_receiving_bot(
    bot_a: RoutableBot, bot_b: RoutableBot
) -> None:
    router = MessageRouter()
    result = _route(
        router,
        text="hello",
        chat_type="private",
        bots=[bot_a, bot_b],
        receiving_bot_username="bot_a_bot",
    )
    assert result is bot_a


def test_dm_ignores_project_keywords(
    bot_a: RoutableBot, bot_b: RoutableBot
) -> None:
    """DM always routes to receiving bot, not by project keywords."""
    router = MessageRouter()
    result = _route(
        router,
        text="gamma delta project",
        chat_type="private",
        bots=[bot_a, bot_b],
        receiving_bot_username="bot_a_bot",
    )
    assert result is bot_a


def test_dm_fallback_to_default_when_no_receiving_bot(
    bot_a: RoutableBot, bot_default: RoutableBot
) -> None:
    router = MessageRouter()
    result = _route(
        router,
        text="hello",
        chat_type="private",
        bots=[bot_a, bot_default],
        receiving_bot_username=None,
    )
    assert result is bot_default


# --- 6. Bot message filtering ---


def test_bot_message_returns_none(bot_a: RoutableBot) -> None:
    router = MessageRouter()
    result = _route(
        router,
        text="Hey @bot_a_bot",
        mentions=["bot_a_bot"],
        from_user_is_bot=True,
        bots=[bot_a],
    )
    assert result is None


# --- 7. Empty bots list ---


def test_empty_bots_returns_none() -> None:
    router = MessageRouter()
    result = _route(
        router,
        text="hello",
        bots=[],
    )
    assert result is None


# --- 8. Multi-group topic maps ---


def test_multi_group_topic_maps_different_groups(
    bot_a: RoutableBot, bot_b: RoutableBot
) -> None:
    """Different groups have different topic→project mappings."""
    router = MessageRouter(
        topic_map={1: "alpha"},
        multi_group_topic_maps={
            100: {10: "alpha"},
            200: {10: "gamma"},
        },
    )
    # Group 100, thread 10 → alpha → bot_a
    r1 = _route(router, text="x", chat_id=100, thread_id=10, bots=[bot_a, bot_b])
    assert r1 is bot_a
    # Group 200, thread 10 → gamma → bot_b
    r2 = _route(router, text="x", chat_id=200, thread_id=10, bots=[bot_a, bot_b])
    assert r2 is bot_b


def test_multi_group_fallback_to_global_topic_map(
    bot_a: RoutableBot, bot_b: RoutableBot
) -> None:
    """Group not in multi_group_topic_maps uses global topic_map."""
    router = MessageRouter(
        topic_map={5: "gamma"},
        multi_group_topic_maps={100: {10: "alpha"}},
    )
    result = _route(
        router,
        text="x",
        chat_id=999,
        thread_id=5,
        bots=[bot_a, bot_b],
    )
    assert result is bot_b


# --- 9. No default bot ---


def test_no_default_bot_returns_none_when_no_match(
    bot_a: RoutableBot, bot_b: RoutableBot
) -> None:
    router = MessageRouter()
    result = _route(
        router,
        text="unrelated message",
        bots=[bot_a, bot_b],
    )
    assert result is None


# --- 10. Multiple keyword matches ---


def test_multiple_keyword_matches_routes_to_highest_overlap(
    bot_a: RoutableBot, bot_b: RoutableBot
) -> None:
    """Routes to bot with highest keyword overlap."""
    bot_c = RoutableBot(
        name="BotC",
        username="bot_c_bot",
        projects=["alpha", "beta", "gamma"],
        is_default=False,
    )
    router = MessageRouter()
    # "alpha beta gamma" — bot_c has 3 matches, bot_a has 2, bot_b has 1
    result = _route(
        router,
        text="alpha beta gamma",
        bots=[bot_a, bot_b, bot_c],
    )
    assert result is bot_c


def test_keyword_tie_returns_first_bot_with_highest_score(
    bot_a: RoutableBot, bot_b: RoutableBot
) -> None:
    """When two bots have same score, first in list wins."""
    bot_c = RoutableBot(
        name="BotC",
        username="bot_c_bot",
        projects=["alpha", "gamma"],
        is_default=False,
    )
    router = MessageRouter()
    # "alpha gamma" — bot_a has 1 (alpha), bot_b has 1 (gamma), bot_c has 2
    result = _route(
        router,
        text="alpha gamma",
        bots=[bot_a, bot_b, bot_c],
    )
    assert result is bot_c


# --- Priority order: mention > topic > keyword > default ---


def test_mention_overrides_topic(
    bot_a: RoutableBot, bot_b: RoutableBot
) -> None:
    router = MessageRouter(topic_map={5: "gamma"})
    result = _route(
        router,
        text="Hey @bot_a_bot",
        mentions=["bot_a_bot"],
        thread_id=5,
        bots=[bot_a, bot_b],
    )
    assert result is bot_a


def test_topic_overrides_keyword(
    bot_a: RoutableBot, bot_b: RoutableBot
) -> None:
    router = MessageRouter(topic_map={5: "alpha"})
    result = _route(
        router,
        text="gamma delta",
        thread_id=5,
        bots=[bot_a, bot_b],
    )
    assert result is bot_a


def test_keyword_overrides_default(
    bot_a: RoutableBot, bot_default: RoutableBot
) -> None:
    router = MessageRouter()
    result = _route(
        router,
        text="alpha",
        bots=[bot_a, bot_default],
    )
    assert result is bot_a
