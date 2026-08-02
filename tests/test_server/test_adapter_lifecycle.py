"""Tests for adapter lifecycle: auto_start config, duplicate guard, state tracking, shutdown persistence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import dan.adapters as adapters_pkg
import dan.cli.bot as bot_cli
from dan.server.routers import adapters as adapters_router
from dan.adapters.telegram_adapter import TelegramAdapter, TelegramAdapterConfig


class FakeAdapter:
    def __init__(self, config: Any) -> None:
        self.config = config
        self._running = False
        self._on_message = None
        self._on_event = None

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


class FakeWhatsAppWebAdapter(FakeAdapter):
    pass


class FakeWeChatOfficialAccountAdapterConfig:
    def __init__(self, **config: Any) -> None:
        self.__dict__.update(config)


class FakeWeChatOfficialAccountAdapter(FakeAdapter):
    pass


@pytest.fixture(autouse=True)
def reset_adapter_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    telegram_config_path = tmp_path / "telegram-config.json"
    whatsapp_dir = tmp_path / "whatsapp-web"
    whatsapp_dir.mkdir(parents=True, exist_ok=True)
    whatsapp_db_path = whatsapp_dir / "session.sqlite3"
    whatsapp_config_path = whatsapp_dir / "config.json"
    wechat_dir = tmp_path / "wechat-official-account"
    wechat_dir.mkdir(parents=True, exist_ok=True)
    wechat_config_path = wechat_dir / "config.json"

    monkeypatch.setenv("DAN_TELEGRAM_CONFIG", str(telegram_config_path))
    monkeypatch.delenv("DAN_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(adapters_router, "_DEFAULT_WHATSAPP_WEB_DB_PATH", whatsapp_db_path)
    monkeypatch.setattr(
        adapters_router, "_DEFAULT_WHATSAPP_WEB_CONFIG_PATH", whatsapp_config_path,
    )
    monkeypatch.setattr(
        adapters_router,
        "_DEFAULT_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH",
        wechat_config_path,
    )
    monkeypatch.setattr(adapters_router, "_update_env_file", lambda *_a, **_kw: None)

    def _verify_bot_token(token: str) -> dict[str, Any] | None:
        if not token:
            return None
        return {"username": "dan_test_bot", "first_name": "DAN"}

    monkeypatch.setattr(bot_cli, "verify_bot_token", _verify_bot_token)
    monkeypatch.setattr(adapters_pkg, "TelegramAdapter", FakeAdapter)
    monkeypatch.setattr(adapters_pkg, "WhatsAppWebAdapter", FakeWhatsAppWebAdapter)
    monkeypatch.setattr(
        adapters_pkg,
        "WeChatOfficialAccountAdapter",
        FakeWeChatOfficialAccountAdapter,
        raising=False,
    )
    monkeypatch.setattr(
        adapters_pkg,
        "WeChatOfficialAccountAdapterConfig",
        FakeWeChatOfficialAccountAdapterConfig,
        raising=False,
    )
    monkeypatch.setattr(
        adapters_router.importlib_util, "find_spec",
        lambda name: object() if name in ("telegram", "neonize", "wechatpy") else None,
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
# 0-1: auto_start config save and read round-trip
# ---------------------------------------------------------------------------


class TestAutoStartConfig:
    @pytest.mark.asyncio
    async def test_telegram_auto_start_round_trip(self) -> None:
        saved = await adapters_router.save_adapter_config(
            "telegram",
            {"bot_token": "123456789:FAKE", "auto_start": True},
        )
        assert saved["auto_start"] is True

        reloaded = await adapters_router.get_adapter_config("telegram")
        assert reloaded["auto_start"] is True

    @pytest.mark.asyncio
    async def test_telegram_auto_start_defaults_false(self) -> None:
        saved = await adapters_router.save_adapter_config(
            "telegram",
            {"bot_token": "123456789:FAKE"},
        )
        assert saved["auto_start"] is False

    @pytest.mark.asyncio
    async def test_whatsapp_auto_start_round_trip(self) -> None:
        saved = await adapters_router.save_adapter_config(
            "whatsapp-web",
            {"allowed_jids": ["test@s.whatsapp.net"], "auto_start": True},
        )
        assert saved["auto_start"] is True

        reloaded = await adapters_router.get_adapter_config("whatsapp-web")
        assert reloaded["auto_start"] is True

    @pytest.mark.asyncio
    async def test_whatsapp_auto_start_defaults_false(self) -> None:
        saved = await adapters_router.save_adapter_config(
            "whatsapp-web",
            {"allowed_jids": ["test@s.whatsapp.net"]},
        )
        assert saved["auto_start"] is False

    @pytest.mark.asyncio
    async def test_wechat_auto_start_round_trip(self) -> None:
        saved = await adapters_router.save_adapter_config(
            "wechat",
            {"app_id": "wx123", "token": "tok", "auto_start": True},
        )
        assert saved["auto_start"] is True

        reloaded = await adapters_router.get_adapter_config("wechat")
        assert reloaded["auto_start"] is True

    @pytest.mark.asyncio
    async def test_wechat_auto_start_defaults_false(self) -> None:
        saved = await adapters_router.save_adapter_config(
            "wechat",
            {"app_id": "wx123", "token": "tok"},
        )
        assert saved["auto_start"] is False


# ---------------------------------------------------------------------------
# 0-4: Duplicate start returns 409
# ---------------------------------------------------------------------------


class TestDuplicateStartGuard:
    @pytest.mark.asyncio
    async def test_duplicate_telegram_start_returns_409(self) -> None:
        first = await adapters_router.start_adapter(
            adapters_router.AdapterStartRequest(
                type="telegram", config={"bot_token": "123:TOK"},
            ),
        )
        assert first["status"] == "started"
        await asyncio.sleep(0)

        with pytest.raises(adapters_router.HTTPException) as exc_info:
            await adapters_router.start_adapter(
                adapters_router.AdapterStartRequest(
                    type="telegram", config={"bot_token": "123:TOK"},
                ),
            )
        assert exc_info.value.status_code == 409
        assert "already running" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_different_adapter_types_can_coexist(self) -> None:
        tg = await adapters_router.start_adapter(
            adapters_router.AdapterStartRequest(
                type="telegram", config={"bot_token": "123:TOK"},
            ),
        )
        assert tg["status"] == "started"
        await asyncio.sleep(0)

        wa_db = adapters_router._DEFAULT_WHATSAPP_WEB_DB_PATH
        wa_db.parent.mkdir(parents=True, exist_ok=True)
        wa_db.write_text("session")

        wa = await adapters_router.start_adapter(
            adapters_router.AdapterStartRequest(type="whatsapp-web", config={}),
        )
        assert wa["status"] == "started"

        wechat = await adapters_router.start_adapter(
            adapters_router.AdapterStartRequest(
                type="wechat", config={"app_id": "wx123", "token": "tok"},
            ),
        )
        assert wechat["status"] == "started"

    @pytest.mark.asyncio
    async def test_can_restart_after_stop(self) -> None:
        first = await adapters_router.start_adapter(
            adapters_router.AdapterStartRequest(
                type="telegram", config={"bot_token": "123:TOK"},
            ),
        )
        await asyncio.sleep(0)

        await adapters_router.stop_adapter(
            adapters_router.AdapterStopRequest(adapter_id=first["adapter_id"]),
        )

        second = await adapters_router.start_adapter(
            adapters_router.AdapterStartRequest(
                type="telegram", config={"bot_token": "123:TOK"},
            ),
        )
        assert second["status"] == "started"


# ---------------------------------------------------------------------------
# 0-3: Telegram _connection_state transitions
# ---------------------------------------------------------------------------


class TestTelegramConnectionState:
    def test_initial_state_is_disconnected(self) -> None:
        adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
        assert adapter._connection_state == "disconnected"
        assert adapter._last_error is None
        assert adapter._seen_chat_ids == set()

    @pytest.mark.asyncio
    async def test_start_sets_starting_then_error_on_import_failure(self) -> None:
        adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
        with patch.dict("sys.modules", {"telegram": None, "telegram.ext": None}):
            with pytest.raises(RuntimeError):
                await adapter.start()
        assert adapter._connection_state == "error"
        assert adapter._last_error is not None

    def test_stop_sets_disconnected(self) -> None:
        adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))
        adapter._connection_state = "connected"
        adapter._running = True
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.stop())
        finally:
            loop.close()
        assert adapter._connection_state == "disconnected"
        assert adapter._running is False

    @pytest.mark.asyncio
    async def test_retry_loop_transitions(self) -> None:
        adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))

        mock_app = AsyncMock()
        call_count = 0

        async def _start_polling_fail_then_succeed(**kwargs: Any) -> None:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Network error")

        mock_app.updater.start_polling = _start_polling_fail_then_succeed

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await adapter._start_polling_with_retry(mock_app)

        assert adapter._connection_state != "error"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_loop_exhausted_sets_error(self) -> None:
        adapter = TelegramAdapter(TelegramAdapterConfig(bot_token="x"))

        mock_app = AsyncMock()
        mock_app.updater.start_polling = AsyncMock(
            side_effect=ConnectionError("persistent failure"),
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ConnectionError):
                await adapter._start_polling_with_retry(mock_app)

        assert adapter._connection_state == "error"
        assert "persistent failure" in (adapter._last_error or "")


# ---------------------------------------------------------------------------
# 0-5: Shutdown persists state
# ---------------------------------------------------------------------------


class TestShutdownPersistsState:
    def test_persist_adapter_state_writes_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from dan.server import startup as startup_mod

        state_path = tmp_path / "adapters-state.json"
        monkeypatch.setattr(startup_mod, "_ADAPTERS_STATE_PATH", state_path)

        fake_task = MagicMock()
        fake_task.done.return_value = False
        fake_adapter = FakeAdapter(None)

        adapters_router._active_adapters["test-id"] = (fake_adapter, fake_task)
        adapters_router._adapter_surface_types["test-id"] = "telegram"

        startup_mod._persist_adapter_state()

        assert state_path.exists()
        data = json.loads(state_path.read_text())
        assert len(data) == 1
        assert data[0]["type"] == "telegram"
        assert data[0]["adapter_id"] == "test-id"

    def test_persist_skips_done_tasks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from dan.server import startup as startup_mod

        state_path = tmp_path / "adapters-state.json"
        monkeypatch.setattr(startup_mod, "_ADAPTERS_STATE_PATH", state_path)

        done_task = MagicMock()
        done_task.done.return_value = True
        adapters_router._active_adapters["done-id"] = (FakeAdapter(None), done_task)
        adapters_router._adapter_surface_types["done-id"] = "telegram"

        startup_mod._persist_adapter_state()

        data = json.loads(state_path.read_text())
        assert len(data) == 0


# ---------------------------------------------------------------------------
# 0-2: init_adapters restores adapters with auto_start=True
# ---------------------------------------------------------------------------


class TestInitAdapters:
    @pytest.mark.asyncio
    async def test_init_adapters_starts_telegram_with_auto_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from dan.server import startup as startup_mod

        tg_config_path = tmp_path / "telegram" / "config.json"
        tg_config_path.parent.mkdir(parents=True, exist_ok=True)
        tg_config_path.write_text(json.dumps({
            "bots": {
                "desktop-ui": {
                    "token": "123:FAKE",
                    "auto_start": True,
                }
            }
        }))

        monkeypatch.setattr(startup_mod, "_TELEGRAM_CONFIG_PATH", tg_config_path)
        monkeypatch.setattr(
            startup_mod, "_WHATSAPP_WEB_CONFIG_PATH", tmp_path / "wa-config.json",
        )
        monkeypatch.setattr(
            startup_mod,
            "_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH",
            tmp_path / "wechat-official-account" / "config.json",
        )
        monkeypatch.setattr(
            startup_mod, "_ADAPTERS_STATE_PATH", tmp_path / "adapters-state.json",
        )

        started_types: list[str] = []

        async def mock_start_adapter(req: Any) -> dict[str, str]:
            started_types.append(req.type)
            return {"adapter_id": "mock-id", "type": req.type, "status": "started"}

        monkeypatch.setattr(adapters_router, "start_adapter", mock_start_adapter)

        mock_app = AsyncMock()
        await startup_mod.init_adapters(mock_app)
        await asyncio.sleep(0.1)

        assert "telegram" in started_types

    @pytest.mark.asyncio
    async def test_init_adapters_starts_wechat_with_auto_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from dan.server import startup as startup_mod

        wechat_config_path = tmp_path / "wechat-official-account" / "config.json"
        wechat_config_path.parent.mkdir(parents=True, exist_ok=True)
        wechat_config_path.write_text(json.dumps({
            "app_id": "wx123",
            "token": "wechat-token",
            "callback_path": "openclaw/callback",
            "webhook_url": "https://example.com/callback",
            "welcome_message": "Hello from startup",
            "passive_reply_budget_seconds": 2.5,
            "passive_reply_fallback_text": "Please wait...",
            "auto_start": True,
        }))

        monkeypatch.setattr(startup_mod, "_TELEGRAM_CONFIG_PATH", tmp_path / "telegram" / "config.json")
        monkeypatch.setattr(
            startup_mod, "_WHATSAPP_WEB_CONFIG_PATH", tmp_path / "wa-config.json",
        )
        monkeypatch.setattr(
            startup_mod,
            "_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH",
            wechat_config_path,
        )
        monkeypatch.setattr(
            startup_mod, "_ADAPTERS_STATE_PATH", tmp_path / "adapters-state.json",
        )

        started: list[dict[str, Any]] = []

        async def mock_start_adapter(req: Any) -> dict[str, str]:
            started.append({"type": req.type, "config": dict(req.config)})
            return {"adapter_id": "mock-id", "type": req.type, "status": "started"}

        monkeypatch.setattr(adapters_router, "start_adapter", mock_start_adapter)

        mock_app = AsyncMock()
        await startup_mod.init_adapters(mock_app)
        await asyncio.sleep(0.1)

        assert any(entry["type"] == "wechat" for entry in started)
        wechat_entry = next(entry for entry in started if entry["type"] == "wechat")
        assert wechat_entry["config"]["callback_path"] == "openclaw/callback"
        assert wechat_entry["config"]["webhook_url"] == "https://example.com/callback"
        assert wechat_entry["config"]["welcome_message"] == "Hello from startup"
        assert wechat_entry["config"]["passive_reply_budget_seconds"] == 2.5
        assert wechat_entry["config"]["passive_reply_fallback_text"] == "Please wait..."

    @pytest.mark.asyncio
    async def test_init_adapters_skips_wechat_when_only_default_callback_path_is_saved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from dan.server import startup as startup_mod

        wechat_config_path = tmp_path / "wechat-official-account" / "config.json"
        wechat_config_path.parent.mkdir(parents=True, exist_ok=True)
        wechat_config_path.write_text(json.dumps({
            "callback_path": "callback",
            "auto_start": True,
        }))

        monkeypatch.setattr(startup_mod, "_TELEGRAM_CONFIG_PATH", tmp_path / "telegram" / "config.json")
        monkeypatch.setattr(
            startup_mod, "_WHATSAPP_WEB_CONFIG_PATH", tmp_path / "wa-config.json",
        )
        monkeypatch.setattr(
            startup_mod,
            "_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH",
            wechat_config_path,
        )
        monkeypatch.setattr(
            startup_mod, "_ADAPTERS_STATE_PATH", tmp_path / "adapters-state.json",
        )

        started_types: list[str] = []

        async def mock_start_adapter(req: Any) -> dict[str, str]:
            started_types.append(req.type)
            return {"adapter_id": "mock-id", "type": req.type, "status": "started"}

        monkeypatch.setattr(adapters_router, "start_adapter", mock_start_adapter)

        mock_app = AsyncMock()
        await startup_mod.init_adapters(mock_app)
        await asyncio.sleep(0.1)

        assert "wechat" not in started_types

    @pytest.mark.asyncio
    async def test_init_adapters_skips_wechat_when_only_reference_urls_are_saved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from dan.server import startup as startup_mod

        wechat_config_path = tmp_path / "wechat-official-account" / "config.json"
        wechat_config_path.parent.mkdir(parents=True, exist_ok=True)
        wechat_config_path.write_text(json.dumps({
            "webhook_url": "https://example.com/callback",
            "server_url": "https://relay.example",
            "auto_start": True,
        }))

        monkeypatch.setattr(startup_mod, "_TELEGRAM_CONFIG_PATH", tmp_path / "telegram" / "config.json")
        monkeypatch.setattr(
            startup_mod, "_WHATSAPP_WEB_CONFIG_PATH", tmp_path / "wa-config.json",
        )
        monkeypatch.setattr(
            startup_mod,
            "_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH",
            wechat_config_path,
        )
        monkeypatch.setattr(
            startup_mod, "_ADAPTERS_STATE_PATH", tmp_path / "adapters-state.json",
        )

        started_types: list[str] = []

        async def mock_start_adapter(req: Any) -> dict[str, str]:
            started_types.append(req.type)
            return {"adapter_id": "mock-id", "type": req.type, "status": "started"}

        monkeypatch.setattr(adapters_router, "start_adapter", mock_start_adapter)

        mock_app = AsyncMock()
        await startup_mod.init_adapters(mock_app)
        await asyncio.sleep(0.1)

        assert "wechat" not in started_types

    @pytest.mark.asyncio
    async def test_init_adapters_skips_without_auto_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from dan.server import startup as startup_mod

        tg_config_path = tmp_path / "telegram" / "config.json"
        tg_config_path.parent.mkdir(parents=True, exist_ok=True)
        tg_config_path.write_text(json.dumps({
            "bots": {
                "desktop-ui": {
                    "token": "123:FAKE",
                    "auto_start": False,
                }
            }
        }))

        monkeypatch.setattr(startup_mod, "_TELEGRAM_CONFIG_PATH", tg_config_path)
        monkeypatch.setattr(
            startup_mod, "_WHATSAPP_WEB_CONFIG_PATH", tmp_path / "wa-config.json",
        )
        monkeypatch.setattr(
            startup_mod,
            "_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH",
            tmp_path / "wechat-official-account" / "config.json",
        )
        monkeypatch.setattr(
            startup_mod, "_ADAPTERS_STATE_PATH", tmp_path / "adapters-state.json",
        )

        started_types: list[str] = []

        async def mock_start_adapter(req: Any) -> dict[str, str]:
            started_types.append(req.type)
            return {"adapter_id": "mock-id", "type": req.type, "status": "started"}

        monkeypatch.setattr(adapters_router, "start_adapter", mock_start_adapter)

        mock_app = AsyncMock()
        await startup_mod.init_adapters(mock_app)
        await asyncio.sleep(0.1)

        assert started_types == []

    @pytest.mark.asyncio
    async def test_init_adapters_uses_fallback_from_state_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from dan.server import startup as startup_mod

        tg_config_path = tmp_path / "telegram" / "config.json"
        tg_config_path.parent.mkdir(parents=True, exist_ok=True)
        tg_config_path.write_text(json.dumps({
            "bots": {
                "desktop-ui": {
                    "token": "123:FAKE",
                    "auto_start": False,
                }
            }
        }))

        state_path = tmp_path / "adapters-state.json"
        state_path.write_text(json.dumps([{"type": "telegram", "adapter_id": "old-id"}]))

        monkeypatch.setattr(startup_mod, "_TELEGRAM_CONFIG_PATH", tg_config_path)
        monkeypatch.setattr(
            startup_mod, "_WHATSAPP_WEB_CONFIG_PATH", tmp_path / "wa-config.json",
        )
        monkeypatch.setattr(
            startup_mod,
            "_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH",
            tmp_path / "wechat-official-account" / "config.json",
        )
        monkeypatch.setattr(startup_mod, "_ADAPTERS_STATE_PATH", state_path)

        started_types: list[str] = []

        async def mock_start_adapter(req: Any) -> dict[str, str]:
            started_types.append(req.type)
            return {"adapter_id": "mock-id", "type": req.type, "status": "started"}

        monkeypatch.setattr(adapters_router, "start_adapter", mock_start_adapter)

        mock_app = AsyncMock()
        await startup_mod.init_adapters(mock_app)
        await asyncio.sleep(0.1)

        assert "telegram" in started_types


# ---------------------------------------------------------------------------
# 1-2: Heartbeat updates snapshot / skips adapters without snapshot
# ---------------------------------------------------------------------------


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_updates_snapshot(self) -> None:
        mock_adapter = MagicMock()
        mock_adapter.get_connection_snapshot = AsyncMock(
            return_value={"connection_state": "connected", "bot_username": "hb_bot"},
        )
        mock_task = MagicMock()
        mock_task.done.return_value = False

        adapters_router._active_adapters["hb-1"] = (mock_adapter, mock_task)

        with patch.object(adapters_router, "_heartbeat_interval", 0):
            task = asyncio.create_task(adapters_router._adapter_heartbeat_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        snapshot = adapters_router._adapter_status_snapshots.get("hb-1", {})
        assert snapshot.get("connection_state") == "connected"
        assert snapshot.get("bot_username") == "hb_bot"
        mock_adapter.get_connection_snapshot.assert_awaited()

    @pytest.mark.asyncio
    async def test_heartbeat_skips_adapters_without_snapshot(self) -> None:
        mock_adapter = MagicMock(spec=[])
        mock_task = MagicMock()
        mock_task.done.return_value = False

        adapters_router._active_adapters["no-snap"] = (mock_adapter, mock_task)

        with patch.object(adapters_router, "_heartbeat_interval", 0):
            task = asyncio.create_task(adapters_router._adapter_heartbeat_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert "no-snap" not in adapters_router._adapter_status_snapshots
