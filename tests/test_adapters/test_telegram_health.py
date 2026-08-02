"""Tests for TelegramAdapter.get_connection_snapshot() health reporting."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.adapters.telegram_adapter import TelegramAdapter, TelegramAdapterConfig


@pytest.fixture
def tg_config() -> TelegramAdapterConfig:
    return TelegramAdapterConfig(
        workflow_path="/tmp/wf.md",
        bot_token="123456:FAKE_TOKEN",
    )


@pytest.fixture
def tg_adapter(tg_config: TelegramAdapterConfig) -> TelegramAdapter:
    return TelegramAdapter(tg_config)


@pytest.mark.asyncio
async def test_snapshot_disconnected_when_not_started(
    tg_adapter: TelegramAdapter,
) -> None:
    snapshot = await tg_adapter.get_connection_snapshot()
    assert snapshot == {"connection_state": "disconnected"}


@pytest.mark.asyncio
async def test_snapshot_disconnected_when_application_none(
    tg_adapter: TelegramAdapter,
) -> None:
    tg_adapter._running = True
    tg_adapter._application = None
    snapshot = await tg_adapter.get_connection_snapshot()
    assert snapshot == {"connection_state": "disconnected"}


@pytest.mark.asyncio
async def test_snapshot_connected_when_bot_api_succeeds(
    tg_adapter: TelegramAdapter,
) -> None:
    mock_bot = MagicMock()
    mock_bot.get_me = AsyncMock(return_value=MagicMock(id=12345))
    mock_app = MagicMock()
    mock_app.bot = mock_bot

    tg_adapter._application = mock_app
    tg_adapter._running = True
    tg_adapter._connection_state = "connected"
    tg_adapter.bot_username = "test_bot"

    snapshot = await tg_adapter.get_connection_snapshot()
    assert snapshot["connection_state"] == "connected"
    assert snapshot["bot_username"] == "test_bot"
    assert snapshot["last_error"] is None
    assert snapshot["session_count"] == 0


@pytest.mark.asyncio
async def test_snapshot_uses_connection_state_attr(
    tg_adapter: TelegramAdapter,
) -> None:
    mock_bot = MagicMock()
    mock_bot.get_me = AsyncMock(return_value=MagicMock(id=12345))
    mock_app = MagicMock()
    mock_app.bot = mock_bot

    tg_adapter._application = mock_app
    tg_adapter._running = True
    tg_adapter._connection_state = "reconnecting"

    snapshot = await tg_adapter.get_connection_snapshot()
    assert snapshot["connection_state"] == "reconnecting"


@pytest.mark.asyncio
async def test_snapshot_counts_seen_chat_ids(
    tg_adapter: TelegramAdapter,
) -> None:
    mock_bot = MagicMock()
    mock_bot.get_me = AsyncMock(return_value=MagicMock(id=12345))
    mock_app = MagicMock()
    mock_app.bot = mock_bot

    tg_adapter._application = mock_app
    tg_adapter._running = True
    tg_adapter._seen_chat_ids = {111, 222, 333}

    snapshot = await tg_adapter.get_connection_snapshot()
    assert snapshot["session_count"] == 3


@pytest.mark.asyncio
async def test_snapshot_error_when_bot_api_fails(
    tg_adapter: TelegramAdapter,
) -> None:
    mock_bot = MagicMock()
    mock_bot.get_me = AsyncMock(side_effect=RuntimeError("Network timeout"))
    mock_app = MagicMock()
    mock_app.bot = mock_bot

    tg_adapter._application = mock_app
    tg_adapter._running = True
    tg_adapter.bot_username = "test_bot"

    snapshot = await tg_adapter.get_connection_snapshot()
    assert snapshot["connection_state"] == "error"
    assert "Network timeout" in snapshot["last_error"]
    assert snapshot["bot_username"] == "test_bot"
