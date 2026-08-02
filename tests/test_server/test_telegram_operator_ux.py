"""Tests for Telegram operator UX polish (Plan 31-32, Task 3)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import dan.adapters as adapters_pkg
import dan.cli.bot as bot_cli
from dan.adapters.telegram_adapter import TelegramAdapter, TelegramAdapterConfig
from dan.server.routers import adapters as adapters_router


class FakeTelegramAdapter:
    def __init__(self, config: Any) -> None:
        self.config = config
        self._running = False
        self._on_message: Any = None
        self._on_event: Any = None
        self.register_custom_commands = AsyncMock()
        self.set_menu_button = AsyncMock()

    def set_message_callback(self, callback: Any) -> None:
        self._on_message = callback

    def set_event_callback(self, callback: Any) -> None:
        self._on_event = callback

    def get_connection_snapshot(self) -> dict[str, object]:
        return {
            "connection_state": "connected" if self._running else "disconnected",
            "last_error": None,
            "paired": None,
        }

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False


@pytest.fixture(autouse=True)
def reset_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    telegram_config_path = tmp_path / "telegram-config.json"
    monkeypatch.setenv("DAN_TELEGRAM_CONFIG", str(telegram_config_path))
    monkeypatch.delenv("DAN_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(adapters_router, "_update_env_file", lambda *_a, **_kw: None)

    def _verify_bot_token(token: str):
        asyncio.run(asyncio.sleep(0))
        if not token:
            return None
        return {"username": "dan_test_bot", "first_name": "DAN"}

    monkeypatch.setattr(bot_cli, "verify_bot_token", _verify_bot_token)
    monkeypatch.setattr(adapters_pkg, "TelegramAdapter", FakeTelegramAdapter)
    monkeypatch.setattr(
        adapters_router.importlib_util,
        "find_spec",
        lambda name: object() if name == "telegram" else None,
    )

    adapters_router._active_adapters.clear()
    adapters_router._adapter_session_stores.clear()
    adapters_router._adapter_start_times.clear()
    adapters_router._adapter_renderers.clear()
    adapters_router._adapter_surface_types.clear()
    adapters_router._adapter_status_snapshots.clear()
    adapters_router._adapter_event_subscribers.clear()
    adapters_router._adapter_event_snapshots.clear()

    yield

    for _adapter, task in list(adapters_router._active_adapters.values()):
        if not task.done():
            task.cancel()
    adapters_router._active_adapters.clear()
    adapters_router._adapter_session_stores.clear()
    adapters_router._adapter_start_times.clear()
    adapters_router._adapter_renderers.clear()
    adapters_router._adapter_surface_types.clear()
    adapters_router._adapter_status_snapshots.clear()
    adapters_router._adapter_event_subscribers.clear()
    adapters_router._adapter_event_snapshots.clear()


# ---------------------------------------------------------------------------
# 3-1: commands config round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commands_config_round_trip() -> None:
    commands = [
        {"command": "deploy", "description": "Deploy to production"},
        {"command": "rollback", "description": "Rollback last deploy"},
    ]
    saved = await adapters_router.save_adapter_config(
        "telegram",
        {"bot_token": "123456789:FAKE", "commands": commands},
    )
    assert saved["commands"] == commands

    reloaded = await adapters_router.get_adapter_config("telegram")
    assert reloaded["commands"] == commands


@pytest.mark.asyncio
async def test_commands_config_defaults_empty() -> None:
    saved = await adapters_router.save_adapter_config(
        "telegram",
        {"bot_token": "123456789:FAKE"},
    )
    assert saved["commands"] == []

    reloaded = await adapters_router.get_adapter_config("telegram")
    assert reloaded["commands"] == []


@pytest.mark.asyncio
async def test_commands_config_preserves_on_partial_update() -> None:
    await adapters_router.save_adapter_config(
        "telegram",
        {
            "bot_token": "123456789:FAKE",
            "commands": [{"command": "deploy", "description": "Deploy"}],
        },
    )
    updated = await adapters_router.save_adapter_config(
        "telegram",
        {"allowed_chat_ids": [111]},
    )
    assert updated["commands"] == [{"command": "deploy", "description": "Deploy"}]


# ---------------------------------------------------------------------------
# 3-2: mini_app_url config round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mini_app_url_config_round_trip() -> None:
    saved = await adapters_router.save_adapter_config(
        "telegram",
        {"bot_token": "123456789:FAKE", "mini_app_url": "https://example.com/app"},
    )
    assert saved["mini_app_url"] == "https://example.com/app"

    reloaded = await adapters_router.get_adapter_config("telegram")
    assert reloaded["mini_app_url"] == "https://example.com/app"


@pytest.mark.asyncio
async def test_mini_app_url_defaults_none() -> None:
    reloaded = await adapters_router.get_adapter_config("telegram")
    assert reloaded["mini_app_url"] is None


@pytest.mark.asyncio
async def test_mini_app_url_clear() -> None:
    await adapters_router.save_adapter_config(
        "telegram",
        {"bot_token": "123456789:FAKE", "mini_app_url": "https://example.com/app"},
    )
    cleared = await adapters_router.save_adapter_config(
        "telegram",
        {"mini_app_url": None},
    )
    assert cleared["mini_app_url"] is None

    reloaded = await adapters_router.get_adapter_config("telegram")
    assert reloaded["mini_app_url"] is None


# ---------------------------------------------------------------------------
# 3-1: apply-commands calls register_custom_commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_commands_calls_register_custom_commands() -> None:
    started = await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(
            type="telegram",
            config={"bot_token": "123456789:FAKE"},
        ),
    )
    await asyncio.sleep(0)

    adapter_id = started["adapter_id"]
    adapter, _task = adapters_router._active_adapters[adapter_id]

    result = await adapters_router.apply_commands(
        adapter_id,
        {"commands": [{"command": "deploy", "description": "Deploy to prod"}]},
    )
    assert result == {"status": "applied"}
    adapter.register_custom_commands.assert_awaited_once_with(
        [("deploy", "Deploy to prod")],
    )


@pytest.mark.asyncio
async def test_apply_commands_404_for_missing_adapter() -> None:
    with pytest.raises(adapters_router.HTTPException) as exc_info:
        await adapters_router.apply_commands("nonexistent", {"commands": []})
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_apply_commands_400_for_non_telegram_adapter() -> None:
    adapter_id = "fake-wa"
    adapters_router._active_adapters[adapter_id] = (MagicMock(), MagicMock())
    adapters_router._adapter_surface_types[adapter_id] = "whatsapp-web"

    with pytest.raises(adapters_router.HTTPException) as exc_info:
        await adapters_router.apply_commands(adapter_id, {"commands": []})
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# 3-2: apply-menu calls set_menu_button
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_menu_sets_url() -> None:
    started = await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(
            type="telegram",
            config={"bot_token": "123456789:FAKE"},
        ),
    )
    await asyncio.sleep(0)

    adapter_id = started["adapter_id"]
    adapter, _task = adapters_router._active_adapters[adapter_id]

    result = await adapters_router.apply_menu(
        adapter_id,
        {"mini_app_url": "https://app.example.com"},
    )
    assert result == {"status": "applied"}
    adapter.set_menu_button.assert_awaited_once_with("https://app.example.com")


@pytest.mark.asyncio
async def test_apply_menu_resets_on_empty() -> None:
    started = await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(
            type="telegram",
            config={"bot_token": "123456789:FAKE"},
        ),
    )
    await asyncio.sleep(0)

    adapter_id = started["adapter_id"]
    adapter, _task = adapters_router._active_adapters[adapter_id]

    await adapters_router.apply_menu(adapter_id, {"mini_app_url": ""})
    adapter.set_menu_button.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_apply_menu_resets_on_null() -> None:
    started = await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(
            type="telegram",
            config={"bot_token": "123456789:FAKE"},
        ),
    )
    await asyncio.sleep(0)

    adapter_id = started["adapter_id"]
    adapter, _task = adapters_router._active_adapters[adapter_id]

    await adapters_router.apply_menu(adapter_id, {"mini_app_url": None})
    adapter.set_menu_button.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_apply_menu_404_for_missing_adapter() -> None:
    with pytest.raises(adapters_router.HTTPException) as exc_info:
        await adapters_router.apply_menu("nonexistent", {"url": "https://x.com"})
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 3-3: welcome message with/without project_description
# ---------------------------------------------------------------------------


class TestWelcomeMessageProjectDescription:
    def _make_update(self, chat_id: int, text: str) -> MagicMock:
        update = MagicMock()
        update.effective_chat.id = chat_id
        update.message.text = text
        update.message.caption = None
        update.message.chat.type = "private"
        update.message.reply_text = AsyncMock()
        return update

    @pytest.mark.asyncio
    async def test_start_without_project_description(self) -> None:
        config = TelegramAdapterConfig(bot_token="tok")
        adapter = TelegramAdapter(config)
        update = self._make_update(123, "/start")

        await adapter._cmd_start(update, None)

        reply_text = update.message.reply_text.call_args[0][0]
        assert reply_text == config.welcome_message
        assert "What this bot does" not in reply_text

    @pytest.mark.asyncio
    async def test_start_with_project_description(self) -> None:
        config = TelegramAdapterConfig(
            bot_token="tok",
            project_description="Manage your deployments via chat.",
        )
        adapter = TelegramAdapter(config)
        update = self._make_update(123, "/start")

        await adapter._cmd_start(update, None)

        reply_text = update.message.reply_text.call_args[0][0]
        assert "What this bot does: Manage your deployments via chat." in reply_text

    @pytest.mark.asyncio
    async def test_help_without_project_description(self) -> None:
        config = TelegramAdapterConfig(bot_token="tok")
        adapter = TelegramAdapter(config)
        update = self._make_update(123, "/help")

        await adapter._cmd_help(update, None)

        reply_text = update.message.reply_text.call_args[0][0]
        assert "What this bot does" not in reply_text
        assert "Or just type naturally!" in reply_text

    @pytest.mark.asyncio
    async def test_help_with_project_description(self) -> None:
        config = TelegramAdapterConfig(
            bot_token="tok",
            project_description="Manage your deployments via chat.",
        )
        adapter = TelegramAdapter(config)
        update = self._make_update(123, "/help")

        await adapter._cmd_help(update, None)

        reply_text = update.message.reply_text.call_args[0][0]
        assert "What this bot does: Manage your deployments via chat." in reply_text
        assert "Or just type naturally!" in reply_text
