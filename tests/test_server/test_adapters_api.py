from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from starlette.requests import Request

import dan.adapters as adapters_pkg
import dan.cli.bot as bot_cli
from dan.adapters.base import AdapterSessionStore
from dan.adapters.wechat_official_account_adapter import (
    build_encrypted_callback_reply,
    decrypt_encrypted_callback_xml,
)
from dan.server.routers import adapters as adapters_router


class FakeTelegramAdapter:
    def __init__(self, config) -> None:
        self.config = config
        self._running = False
        self._on_message = None
        self._on_event = None
        self._snapshot_state: str | None = None
        self._snapshot_error: str | None = None

    def set_message_callback(self, callback) -> None:
        self._on_message = callback

    def set_event_callback(self, callback) -> None:
        self._on_event = callback

    async def get_connection_snapshot(self) -> dict[str, object]:
        return {
            "connection_state": self._snapshot_state or (
                "connected" if self._running else "disconnected"
            ),
            "last_error": self._snapshot_error,
            "paired": None,
        }

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False


class FakeWhatsAppWebAdapter(FakeTelegramAdapter):
    def get_connection_snapshot(self) -> dict[str, object]:
        return {
            "connection_state": "pairing" if self._running else "disconnected",
            "last_error": None,
            "paired": False,
        }

    async def start(self) -> None:
        self._running = True
        if self._on_event is not None:
            self._on_event(
                {
                    "type": "status",
                    "connection_state": "pairing",
                    "paired": False,
                },
            )
            self._on_event({"type": "qr", "qr_data": "qr-test-value"})

    async def stop(self) -> None:
        self._running = False
        if self._on_event is not None:
            self._on_event({"type": "pair_status", "status": "disconnected"})


class FakeWeChatOfficialAccountAdapterConfig:
    def __init__(self, **config) -> None:
        self.__dict__.update(config)


class FakeWeChatOfficialAccountAdapter(FakeTelegramAdapter):
    async def get_connection_snapshot(self) -> dict[str, object]:
        return {
            "connection_state": "connected" if self._running else "disconnected",
            "last_error": self._snapshot_error,
            "paired": None,
        }


@pytest.fixture(autouse=True)
def reset_adapter_router_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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
        adapters_router,
        "_DEFAULT_WHATSAPP_WEB_CONFIG_PATH",
        whatsapp_config_path,
    )
    monkeypatch.setattr(
        adapters_router,
        "_DEFAULT_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH",
        wechat_config_path,
    )
    monkeypatch.setattr(adapters_router, "_update_env_file", lambda *_args, **_kwargs: None)
    def _verify_bot_token(token: str):
        asyncio.run(asyncio.sleep(0))
        if not token:
            return None
        return {
            "username": "dan_test_bot",
            "first_name": "DAN",
            "can_join_groups": True,
            "can_read_all_group_messages": False,
        }

    monkeypatch.setattr(bot_cli, "verify_bot_token", _verify_bot_token)
    monkeypatch.setattr(adapters_pkg, "TelegramAdapter", FakeTelegramAdapter)
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
        adapters_router.importlib_util,
        "find_spec",
        lambda name: object() if name in {"telegram", "cryptography"} else None,
    )
    monkeypatch.setattr(
        adapters_router,
        "_build_qr_svg_data_uri",
        lambda qr: f"data:image/svg+xml,{qr}",
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


async def _read_sse_event(iterator) -> dict[str, object]:
    chunk = await anext(iterator)
    text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
    assert text.startswith("data: ")
    return json.loads(text.removeprefix("data: ").strip())


def _signature(token: str, timestamp: str, nonce: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode("utf-8")).hexdigest()


_WECHAT_ENCODING_AES_KEY = base64.b64encode(
    b"0123456789abcdef0123456789abcdef",
).decode("utf-8").rstrip("=")
_WECHAT_APP_ID = "wx1234567890"


def _parse_encrypted_envelope(xml_text: str) -> dict[str, str]:
    root = ET.fromstring(xml_text)
    return {
        "encrypt": str(root.findtext("Encrypt") or ""),
        "msg_signature": str(root.findtext("MsgSignature") or ""),
        "timestamp": str(root.findtext("TimeStamp") or ""),
        "nonce": str(root.findtext("Nonce") or ""),
    }


def _text_xml(content: str = "hello") -> str:
    return (
        "<xml>"
        "<ToUserName><![CDATA[gh_public]]></ToUserName>"
        "<FromUserName><![CDATA[openid-123]]></FromUserName>"
        "<CreateTime>1710000000</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        "<MsgId>42</MsgId>"
        "</xml>"
    )


def _make_request(body: str) -> Request:
    raw = body.encode("utf-8")

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/adapters/wechat/callback",
            "headers": [],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_telegram_config_summary_persists_masked_token_and_username() -> None:
    summary = await adapters_router.save_adapter_config(
        "telegram",
        {
            "bot_token": "123456789:FAKE_TOKEN",
            "allowed_chat_ids": [111, 222],
        },
    )

    assert summary["configured"] is True
    assert summary["bot_username"] == "dan_test_bot"
    assert summary["allowed_chat_ids"] == [111, 222]
    assert summary["allowed_chat_count"] == 2
    assert summary["dependency_installed"] is True
    assert summary["install_hint"] == "pip install 'dan[messaging]'"
    assert summary["masked_token"] == "1234...OKEN"

    reloaded = await adapters_router.get_adapter_config("telegram")
    assert reloaded["bot_username"] == "dan_test_bot"
    assert reloaded["allowed_chat_ids"] == [111, 222]
    assert reloaded["masked_token"] == "1234...OKEN"


@pytest.mark.asyncio
async def test_wechat_config_summary_persists_masked_secrets_and_start_status() -> None:
    summary = await adapters_router.save_adapter_config(
        "wechat",
        {
            "app_id": "wx1234567890",
            "app_secret": "wechat-secret-value",
            "token": "wechat-token-value",
            "encoding_aes_key": "aes-key-value",
            "webhook_url": "https://example.com/callback",
            "callback_path": "openclaw/callback",
            "account_name": "Claw Bot",
            "app_name": "OpenClaw WeChat",
            "welcome_message": "Hi from WeChat",
            "support_encrypted_callbacks": False,
            "passive_reply_budget_seconds": 2.5,
            "passive_reply_fallback_text": "Please wait...",
            "api_base_url": "https://wechat.example.test",
            "access_token_refresh_margin_seconds": 45,
            "server_url": "https://example.com/wechat",
            "auto_start": True,
        },
    )

    assert summary["configured"] is True
    assert summary["app_id"] == "wx1234567890"
    assert summary["webhook_url"] == "https://example.com/callback"
    assert summary["callback_path"] == "openclaw/callback"
    assert summary["account_name"] == "Claw Bot"
    assert summary["app_name"] == "OpenClaw WeChat"
    assert summary["welcome_message"] == "Hi from WeChat"
    assert summary["support_encrypted_callbacks"] is False
    assert summary["passive_reply_budget_seconds"] == 2.5
    assert summary["passive_reply_fallback_text"] == "Please wait..."
    assert summary["api_base_url"] == "https://wechat.example.test"
    assert summary["access_token_refresh_margin_seconds"] == 45.0
    assert summary["masked_app_secret"] == "wech...alue"
    assert summary["masked_token"] == "wech...alue"
    assert summary["masked_encoding_aes_key"] == "aes-...alue"
    assert summary["server_url"] == "https://example.com/wechat"
    assert summary["auto_start"] is True
    assert summary["dependency_installed"] is True
    assert summary["install_hint"] == "pip install 'dan[wechat]'"

    reloaded = await adapters_router.get_adapter_config("wechat")
    assert reloaded["app_id"] == "wx1234567890"
    assert reloaded["masked_token"] == "wech...alue"
    assert reloaded["welcome_message"] == "Hi from WeChat"
    assert reloaded["passive_reply_budget_seconds"] == 2.5
    assert reloaded["passive_reply_fallback_text"] == "Please wait..."

    started = await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(type="wechat", config={}),
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert started["status"] == "started"
    adapter = adapters_router._active_adapters[started["adapter_id"]][0]
    assert adapter.config.webhook_url == "https://example.com/callback"
    assert adapter.config.callback_path == "openclaw/callback"
    assert adapter.config.account_name == "Claw Bot"
    assert adapter.config.app_name == "OpenClaw WeChat"
    assert adapter.config.welcome_message == "Hi from WeChat"
    assert adapter.config.passive_reply_budget_seconds == 2.5
    assert adapter.config.passive_reply_fallback_text == "Please wait..."
    assert adapter.config.api_base_url == "https://wechat.example.test"
    assert adapter.config.access_token_refresh_margin_seconds == 45.0
    statuses = await adapters_router.adapter_status()
    assert len(statuses) == 1
    assert statuses[0]["adapter_id"] == started["adapter_id"]
    assert statuses[0]["type"] == "wechat"
    assert statuses[0]["running"] is True
    assert statuses[0]["connection_state"] == "connected"


@pytest.mark.asyncio
async def test_wechat_reset_adapter_config_clears_saved_settings_and_stops_adapter() -> None:
    await adapters_router.save_adapter_config(
        "wechat",
        {
            "app_id": "wx1234567890",
            "token": "wechat-token-value",
            "welcome_message": "Reset me",
            "auto_start": True,
        },
    )
    started = await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(type="wechat", config={}),
    )
    await asyncio.sleep(0)

    reset = await adapters_router.reset_adapter_config("wechat")

    assert reset["configured"] is False
    assert reset["app_id"] is None
    assert reset["masked_token"] is None
    assert reset["welcome_message"] == "Welcome! Send a message to start a workflow."
    assert reset["auto_start"] is False
    assert await adapters_router.adapter_status() == []
    with pytest.raises(adapters_router.HTTPException) as exc_info:
        await adapters_router.adapter_events(started["adapter_id"])
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_wechat_config_preserves_explicit_blank_welcome_message() -> None:
    await adapters_router.save_adapter_config(
        "wechat",
        {
            "app_id": "wx1234567890",
            "token": "wechat-token-value",
            "welcome_message": "",
        },
    )

    reloaded = await adapters_router.get_adapter_config("wechat")
    assert reloaded["welcome_message"] == ""

    started = await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(type="wechat", config={}),
    )
    await asyncio.sleep(0)

    adapter = adapters_router._active_adapters[started["adapter_id"]][0]
    assert adapter.config.welcome_message == ""


@pytest.mark.asyncio
async def test_wechat_start_uses_default_api_base_url_when_saved_override_is_blank() -> None:
    await adapters_router.save_adapter_config(
        "wechat",
        {
            "token": "wechat-token-value",
            "api_base_url": "",
        },
    )

    prepared = adapters_router._prepare_wechat_official_account_start_config(
        {"api_base_url": ""},
    )
    assert "api_base_url" not in prepared


@pytest.mark.asyncio
async def test_wechat_callback_verify_returns_echostr() -> None:
    await adapters_router.save_adapter_config(
        "wechat",
        {
            "app_id": "wx1234567890",
            "token": "wechat-token-value",
        },
    )
    await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(type="wechat", config={}),
    )
    await asyncio.sleep(0)

    timestamp = "1710000000"
    nonce = "998877"
    signature = _signature("wechat-token-value", timestamp, nonce)
    response = await adapters_router.wechat_callback_verify(
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        echostr="echo-me",
    )

    assert response.body == b"echo-me"


@pytest.mark.asyncio
async def test_wechat_callback_rejects_encrypted_mode_when_not_enabled() -> None:
    await adapters_router.save_adapter_config(
        "wechat",
        {
            "app_id": "wx1234567890",
            "token": "wechat-token-value",
        },
    )
    await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(type="wechat", config={}),
    )
    await asyncio.sleep(0)

    timestamp = "1710000000"
    nonce = "998877"
    signature = _signature("wechat-token-value", timestamp, nonce)
    with pytest.raises(adapters_router.HTTPException) as exc_info:
        await adapters_router.wechat_callback_verify(
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            echostr="echo-me",
            encrypt_type="aes",
        )

    assert exc_info.value.status_code == 400
    assert "Encrypted WeChat callbacks are not enabled" in exc_info.value.detail


@pytest.mark.asyncio
async def test_wechat_callback_rejects_encrypted_mode_without_required_config() -> None:
    await adapters_router.save_adapter_config(
        "wechat",
        {
            "app_id": "",
            "token": "wechat-token-value",
            "support_encrypted_callbacks": True,
        },
    )
    await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(type="wechat", config={}),
    )
    await asyncio.sleep(0)

    timestamp = "1710000000"
    nonce = "998877"
    signature = _signature("wechat-token-value", timestamp, nonce)
    with pytest.raises(adapters_router.HTTPException) as exc_info:
        await adapters_router.wechat_callback_verify(
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            echostr="echo-me",
            encrypt_type="aes",
            msg_signature="unused",
        )

    assert exc_info.value.status_code == 400
    assert "app_id is required" in exc_info.value.detail


@pytest.mark.asyncio
async def test_wechat_callback_verify_decrypts_encrypted_echostr() -> None:
    await adapters_router.save_adapter_config(
        "wechat",
        {
            "app_id": _WECHAT_APP_ID,
            "token": "wechat-token-value",
            "encoding_aes_key": _WECHAT_ENCODING_AES_KEY,
            "support_encrypted_callbacks": True,
        },
    )
    await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(type="wechat", config={}),
    )
    await asyncio.sleep(0)

    timestamp = "1710000000"
    nonce = "998877"
    signature = _signature("wechat-token-value", timestamp, nonce)
    encrypted_echo = build_encrypted_callback_reply(
        "echo-me",
        token="wechat-token-value",
        encoding_aes_key=_WECHAT_ENCODING_AES_KEY,
        app_id=_WECHAT_APP_ID,
        timestamp=timestamp,
        nonce=nonce,
    )
    envelope = _parse_encrypted_envelope(encrypted_echo)

    response = await adapters_router.wechat_callback_verify(
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        echostr=envelope["encrypt"],
        encrypt_type="aes",
        msg_signature=envelope["msg_signature"],
    )

    assert response.body == b"echo-me"


@pytest.mark.asyncio
async def test_wechat_callback_verify_respects_configured_callback_path() -> None:
    await adapters_router.save_adapter_config(
        "wechat",
        {
            "app_id": "wx1234567890",
            "token": "wechat-token-value",
            "callback_path": "openclaw/callback",
        },
    )
    await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(type="wechat", config={}),
    )
    await asyncio.sleep(0)

    timestamp = "1710000000"
    nonce = "998877"
    signature = _signature("wechat-token-value", timestamp, nonce)

    with pytest.raises(adapters_router.HTTPException) as exc_info:
        await adapters_router.wechat_callback_verify(
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            echostr="echo-me",
        )
    assert exc_info.value.status_code == 404

    response = await adapters_router.wechat_callback_verify_at_path(
        "openclaw/callback",
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        echostr="echo-me",
    )
    assert response.body == b"echo-me"


@pytest.mark.asyncio
async def test_wechat_callback_message_returns_passive_xml_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    await adapters_router.save_adapter_config(
        "wechat",
        {
            "app_id": "wx1234567890",
            "token": "wechat-token-value",
            "server_url": "https://relay.example",
        },
    )
    await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(type="wechat", config={}),
    )
    await asyncio.sleep(0)

    started: list[tuple[str, str, str, str, str, str, str]] = []

    async def _fake_start_wechat_chat_stream(
        *,
        adapter_id: str,
        adapter,
        external_id: str,
        message_text: str,
        account_id: str,
        session_id: str | None = None,
        server_url: str | None = None,
    ) -> str:
        started.append(
            (
                adapter_id,
                adapter.config.app_id,
                external_id,
                message_text,
                account_id,
                session_id or "",
                server_url or "",
            ),
        )
        return "chat-wechat-1"

    async def _fake_drain_wechat_chat_stream(
        channel_id: str,
        *,
        server_url: str | None = None,
    ) -> str:
        assert channel_id == "chat-wechat-1"
        assert server_url == "https://relay.example"
        return "Hello from DAN"

    monkeypatch.setattr(adapters_router, "_start_wechat_chat_stream", _fake_start_wechat_chat_stream)
    monkeypatch.setattr(adapters_router, "_drain_wechat_chat_stream", _fake_drain_wechat_chat_stream)

    timestamp = "1710000000"
    nonce = "998877"
    signature = _signature("wechat-token-value", timestamp, nonce)
    response = await adapters_router.wechat_callback_message(
        request=_make_request(_text_xml("hello from wechat")),
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
    )

    body = response.body.decode("utf-8")
    assert "<MsgType><![CDATA[text]]></MsgType>" in body
    assert "<Content><![CDATA[Hello from DAN]]></Content>" in body
    assert started
    assert started[0][2:5] == ("openid-123", "hello from wechat", "gh_public")
    assert started[0][5]
    assert started[0][6] == "https://relay.example"


@pytest.mark.asyncio
async def test_wechat_callback_message_uses_shared_adapter_session_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await adapters_router.save_adapter_config(
        "wechat",
        {
            "app_id": "wx1234567890",
            "token": "wechat-token-value",
        },
    )
    started = await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(type="wechat", config={}),
    )
    await asyncio.sleep(0)

    adapter_id = started["adapter_id"]
    store = adapters_router._adapter_session_stores[adapter_id]
    assert isinstance(store, AdapterSessionStore)

    seen_session_ids: list[str] = []

    async def _fake_start_wechat_chat_stream(
        *,
        adapter_id: str,
        adapter,
        external_id: str,
        message_text: str,
        account_id: str,
        session_id: str | None = None,
        server_url: str | None = None,
    ) -> str:
        assert external_id == "openid-123"
        assert message_text == "hello from wechat"
        seen_session_ids.append(str(session_id or ""))
        return "chat-wechat-shared-session"

    async def _fake_drain_wechat_chat_stream(
        channel_id: str,
        *,
        server_url: str | None = None,
    ) -> str:
        assert channel_id == "chat-wechat-shared-session"
        return "Hello from DAN"

    monkeypatch.setattr(adapters_router, "_start_wechat_chat_stream", _fake_start_wechat_chat_stream)
    monkeypatch.setattr(adapters_router, "_drain_wechat_chat_stream", _fake_drain_wechat_chat_stream)

    timestamp = "1710000000"
    nonce = "998877"
    signature = _signature("wechat-token-value", timestamp, nonce)
    response_one = await adapters_router.wechat_callback_message(
        request=_make_request(_text_xml("hello from wechat")),
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
    )
    response_two = await adapters_router.wechat_callback_message(
        request=_make_request(_text_xml("hello from wechat")),
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
    )

    assert response_one.status_code == 200
    assert response_two.status_code == 200
    assert len(seen_session_ids) == 2
    assert seen_session_ids[0] == seen_session_ids[1]
    assert seen_session_ids[0] != "openid-123"

    shared_session = await store.get_by_external("openid-123")
    assert shared_session is not None
    assert shared_session.session_id == seen_session_ids[0]


@pytest.mark.asyncio
async def test_wechat_callback_message_returns_encrypted_xml_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await adapters_router.save_adapter_config(
        "wechat",
        {
            "app_id": _WECHAT_APP_ID,
            "token": "wechat-token-value",
            "encoding_aes_key": _WECHAT_ENCODING_AES_KEY,
            "support_encrypted_callbacks": True,
        },
    )
    await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(type="wechat", config={}),
    )
    await asyncio.sleep(0)

    async def _fake_start_wechat_chat_stream(
        *,
        adapter_id: str,
        adapter,
        external_id: str,
        message_text: str,
        account_id: str,
        session_id: str | None = None,
        server_url: str | None = None,
    ) -> str:
        return "chat-wechat-encrypted"

    async def _fake_drain_wechat_chat_stream(
        channel_id: str,
        *,
        server_url: str | None = None,
    ) -> str:
        assert channel_id == "chat-wechat-encrypted"
        return "Encrypted hello"

    monkeypatch.setattr(adapters_router, "_start_wechat_chat_stream", _fake_start_wechat_chat_stream)
    monkeypatch.setattr(adapters_router, "_drain_wechat_chat_stream", _fake_drain_wechat_chat_stream)

    timestamp = "1710000000"
    nonce = "998877"
    signature = _signature("wechat-token-value", timestamp, nonce)
    encrypted_body = build_encrypted_callback_reply(
        _text_xml("hello from wechat"),
        token="wechat-token-value",
        encoding_aes_key=_WECHAT_ENCODING_AES_KEY,
        app_id=_WECHAT_APP_ID,
        timestamp=timestamp,
        nonce=nonce,
    )
    inbound_envelope = _parse_encrypted_envelope(encrypted_body)

    response = await adapters_router.wechat_callback_message(
        request=_make_request(encrypted_body),
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        encrypt_type="aes",
        msg_signature=inbound_envelope["msg_signature"],
    )

    envelope = _parse_encrypted_envelope(response.body.decode("utf-8"))
    decrypted = decrypt_encrypted_callback_xml(
        response.body.decode("utf-8"),
        token="wechat-token-value",
        msg_signature=envelope["msg_signature"],
        timestamp=envelope["timestamp"],
        nonce=envelope["nonce"],
        encoding_aes_key=_WECHAT_ENCODING_AES_KEY,
        app_id=_WECHAT_APP_ID,
    )

    assert "<Content><![CDATA[Encrypted hello]]></Content>" in decrypted


@pytest.mark.asyncio
async def test_wechat_callback_message_uses_timeout_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(
            type="wechat",
            config={
                "app_id": "wx1234567890",
                "token": "wechat-token-value",
                "passive_reply_budget_seconds": 0.01,
                "passive_reply_fallback_text": "Working on it...",
            },
        ),
    )
    await asyncio.sleep(0)

    async def _fake_start_wechat_chat_stream(
        *,
        adapter_id: str,
        adapter,
        external_id: str,
        message_text: str,
        account_id: str,
        session_id: str | None = None,
        server_url: str | None = None,
    ) -> str:
        return "chat-wechat-2"

    async def _slow_drain_wechat_chat_stream(
        channel_id: str,
        *,
        server_url: str | None = None,
    ) -> str:
        assert channel_id == "chat-wechat-2"
        await asyncio.sleep(0.2)
        return "Late reply"

    delivered: list[str] = []
    delivered_event = asyncio.Event()

    async def _fake_followup_delivery(
        *,
        adapter_id: str,
        adapter,
        external_id: str,
        result_task,
    ) -> None:
        delivered.append(await result_task)
        delivered_event.set()

    monkeypatch.setattr(adapters_router, "_start_wechat_chat_stream", _fake_start_wechat_chat_stream)
    monkeypatch.setattr(adapters_router, "_drain_wechat_chat_stream", _slow_drain_wechat_chat_stream)
    monkeypatch.setattr(adapters_router, "_await_wechat_followup_delivery", _fake_followup_delivery)

    timestamp = "1710000000"
    nonce = "998877"
    signature = _signature("wechat-token-value", timestamp, nonce)
    response = await adapters_router.wechat_callback_message(
        request=_make_request(_text_xml("hello from wechat")),
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
    )

    body = response.body.decode("utf-8")
    assert "<Content><![CDATA[Working on it...]]></Content>" in body
    await asyncio.wait_for(delivered_event.wait(), timeout=1.0)
    assert delivered == ["Late reply"]


@pytest.mark.asyncio
async def test_start_wechat_chat_stream_uses_configured_server_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        text = '{"stream_channel_id":"chat-remote-1"}'

        @staticmethod
        def json() -> dict[str, str]:
            return {"stream_channel_id": "chat-remote-1"}

    class _FakeAsyncClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            captured["base_url"] = base_url
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, path: str, json: dict[str, object]) -> _FakeResponse:
            captured["path"] = path
            captured["json"] = json
            return _FakeResponse()

    monkeypatch.setattr(adapters_router.httpx, "AsyncClient", _FakeAsyncClient)

    adapter = type(
        "_Adapter",
        (),
        {"config": type("_Config", (), {"app_id": "wx1234567890"})()},
    )()

    channel_id = await adapters_router._start_wechat_chat_stream(
        adapter_id="wechat-app-1",
        adapter=adapter,
        external_id="openid-123",
        message_text="hello from wechat",
        account_id="gh_public",
        session_id="session-123",
        server_url="https://relay.example/base/",
    )

    assert channel_id == "chat-remote-1"
    assert captured["base_url"] == "https://relay.example/base"
    assert captured["path"] == "/api/chat/message"
    assert captured["timeout"] == 120.0
    assert captured["json"] == {
        "workflow_id": "_scratch",
        "message": "hello from wechat",
        "history": [],
        "thread_id": "session-123",
        "session_id": "session-123",
        "mode": "auto",
        "surface": "wechat:wx1234567890",
        "surface_type": "wechat",
        "surface_id": "wx1234567890",
        "surface_context": {
            "identity": {
                "platform": "wechat",
                "channel": "official_account",
                "app_id": "wx1234567890",
                "account_id": "gh_public",
                "openid": "openid-123",
            },
            "adapter_instructions": (
                "You are replying in a WeChat Official Account conversation. "
                "Keep the response concise, messaging-friendly, and clear."
            ),
        },
    }


@pytest.mark.asyncio
async def test_drain_wechat_chat_stream_uses_remote_events_when_server_url_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_iter_wechat_remote_chat_stream_events(
        *,
        channel_id: str,
        server_url: str,
    ):
        assert channel_id == "chat-remote-1"
        assert server_url == "https://relay.example"
        yield {"type": "chat_token", "delta": "Hello from DAN"}
        yield {"type": "chat_complete", "content": "Hello from DAN"}

    monkeypatch.setattr(
        adapters_router,
        "_iter_wechat_remote_chat_stream_events",
        _fake_iter_wechat_remote_chat_stream_events,
    )

    reply = await adapters_router._drain_wechat_chat_stream(
        "chat-remote-1",
        server_url="https://relay.example",
    )

    assert reply == "Hello from DAN"


@pytest.mark.asyncio
async def test_whatsapp_web_start_events_and_reset_pairing() -> None:
    saved = await adapters_router.save_adapter_config(
        "whatsapp-web",
        {
            "allowed_jids": ["15551234567@s.whatsapp.net"],
        },
    )

    db_path = Path(str(saved["db_path"]))
    wal_path = db_path.with_name(f"{db_path.name}-wal")
    shm_path = db_path.with_name(f"{db_path.name}-shm")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_text("session")
    wal_path.write_text("wal")
    shm_path.write_text("shm")

    started = await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(type="whatsapp-web", config={}),
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    statuses = await adapters_router.adapter_status()
    assert len(statuses) == 1
    assert statuses[0]["adapter_id"] == started["adapter_id"]
    assert statuses[0]["type"] == "whatsapp-web"
    assert statuses[0]["running"] is True
    assert statuses[0]["connection_state"] == "pairing"
    assert statuses[0]["paired"] is False

    response = await adapters_router.adapter_events(started["adapter_id"])
    event_one = await _read_sse_event(response.body_iterator)
    event_two = await _read_sse_event(response.body_iterator)
    event_types = {str(event_one["type"]), str(event_two["type"])}
    assert event_types == {"status", "qr"}
    qr_event = event_one if event_one["type"] == "qr" else event_two
    assert qr_event["qr_data"] == "qr-test-value"
    assert qr_event["svg_data_uri"] == "data:image/svg+xml,qr-test-value"
    if hasattr(response.body_iterator, "aclose"):
        await response.body_iterator.aclose()

    adapters_router._publish_adapter_event(
        started["adapter_id"],
        {
            "type": "pair_status",
            "status": "paired",
            "id": object(),
        },
    )
    replay = await adapters_router.adapter_events(started["adapter_id"])
    replay_events = [
        await _read_sse_event(replay.body_iterator),
        await _read_sse_event(replay.body_iterator),
        await _read_sse_event(replay.body_iterator),
    ]
    pair_status_event = next(
        event for event in replay_events if event["type"] == "pair_status"
    )
    assert isinstance(pair_status_event["id"], str)
    if hasattr(replay.body_iterator, "aclose"):
        await replay.body_iterator.aclose()

    reset = await adapters_router.reset_adapter_config("whatsapp-web")
    assert reset["paired"] is False
    assert reset["session_db_exists"] is False
    assert reset["dependency_installed"] is False
    assert reset["install_hint"] == "pip install 'dan[whatsapp-web]'"
    assert not db_path.exists()
    assert not wal_path.exists()
    assert not shm_path.exists()
    assert await adapters_router.adapter_status() == []
    with pytest.raises(adapters_router.HTTPException) as exc_info:
        await adapters_router.adapter_events(started["adapter_id"])
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_telegram_status_and_config_await_async_connection_snapshot() -> None:
    await adapters_router.save_adapter_config(
        "telegram",
        {
            "bot_token": "123456789:FAKE_TOKEN",
            "allowed_chat_ids": [111],
        },
    )

    started = await adapters_router.start_adapter(
        adapters_router.AdapterStartRequest(type="telegram", config={}),
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    adapter, _task = adapters_router._active_adapters[started["adapter_id"]]
    adapter._snapshot_state = "error"
    adapter._snapshot_error = "live snapshot failed"

    statuses = await adapters_router.adapter_status()
    assert len(statuses) == 1
    assert statuses[0]["adapter_id"] == started["adapter_id"]
    assert statuses[0]["connection_state"] == "error"
    assert statuses[0]["last_error"] == "live snapshot failed"

    summary = await adapters_router.get_adapter_config("telegram")
    assert summary["connection_state"] == "error"
    assert summary["last_error"] == "live snapshot failed"
