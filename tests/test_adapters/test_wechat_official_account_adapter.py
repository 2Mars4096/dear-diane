from __future__ import annotations

import asyncio
import base64
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch
from xml.etree import ElementTree as ET

import pytest

from dan.adapters.wechat_official_account_adapter import (
    WeChatOfficialAccountAdapter,
    WeChatOfficialAccountAdapterConfig,
    build_encrypted_callback_reply,
    build_passive_text_reply,
    decrypt_encrypted_callback_echostr,
    decrypt_encrypted_callback_xml,
    encrypt_callback_payload,
    parse_incoming_xml,
    validate_callback_encrypt_type,
    verify_message_signature,
    verify_signature,
)


def _signature(token: str, timestamp: str, nonce: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode("utf-8")).hexdigest()


_ENCODING_AES_KEY = base64.b64encode(
    b"0123456789abcdef0123456789abcdef",
).decode("utf-8").rstrip("=")
_APP_ID = "wx1234567890"


def _message_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    return hashlib.sha1(
        "".join(sorted([token, timestamp, nonce, encrypted])).encode("utf-8"),
    ).hexdigest()


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


def _event_xml(event: str, *, event_key: str = "menu-key") -> str:
    return (
        "<xml>"
        "<ToUserName><![CDATA[gh_public]]></ToUserName>"
        "<FromUserName><![CDATA[openid-123]]></FromUserName>"
        "<CreateTime>1710000001</CreateTime>"
        "<MsgType><![CDATA[event]]></MsgType>"
        f"<Event><![CDATA[{event}]]></Event>"
        f"<EventKey><![CDATA[{event_key}]]></EventKey>"
        "</xml>"
    )


def _mock_httpx_client(*, get_side_effect=None, post_side_effect=None):
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=get_side_effect)
    mock_client.post = AsyncMock(side_effect=post_side_effect)
    return mock_client


def test_verify_signature_accepts_standard_wechat_digest() -> None:
    token = "wechat-token"
    timestamp = "1710000000"
    nonce = "998877"
    signature = _signature(token, timestamp, nonce)

    assert verify_signature(token, signature, timestamp, nonce) is True


def test_verify_signature_rejects_invalid_inputs() -> None:
    assert verify_signature("wechat-token", "bad", "1710000000", "998877") is False
    assert verify_signature("", "bad", "1710000000", "998877") is False


def test_validate_callback_encrypt_type_allows_plaintext_modes() -> None:
    validate_callback_encrypt_type("")
    validate_callback_encrypt_type("raw")
    validate_callback_encrypt_type("plaintext")


def test_validate_callback_encrypt_type_rejects_encrypted_modes_when_disabled() -> None:
    with pytest.raises(ValueError):
        validate_callback_encrypt_type("aes")


def test_validate_callback_encrypt_type_allows_aes_when_enabled() -> None:
    validate_callback_encrypt_type("aes", encrypted_callbacks_enabled=True)


def test_parse_incoming_text_xml() -> None:
    parsed = parse_incoming_xml(_text_xml("hello from wechat"))

    assert parsed["msg_type"] == "text"
    assert parsed["kind"] == "text"
    assert parsed["from_user_name"] == "openid-123"
    assert parsed["to_user_name"] == "gh_public"
    assert parsed["content"] == "hello from wechat"
    assert parsed["text"] == "hello from wechat"


@pytest.mark.parametrize(
    ("event", "expected_text"),
    [
        ("subscribe", "subscribe"),
        ("unsubscribe", "unsubscribe"),
        ("CLICK", "menu-key"),
    ],
)
def test_parse_incoming_event_xml(event: str, expected_text: str) -> None:
    parsed = parse_incoming_xml(_event_xml(event))

    assert parsed["msg_type"] == "event"
    assert parsed["event"] == event.lower()
    assert parsed["kind"] == event.lower()
    assert parsed["text"] == expected_text
    assert parsed["event_key"] == "menu-key"


def test_build_passive_text_reply_round_trips() -> None:
    reply = build_passive_text_reply("openid-123", "gh_public", "Hello <world>")

    assert "<MsgType><![CDATA[text]]></MsgType>" in reply
    assert "<ToUserName><![CDATA[openid-123]]></ToUserName>" in reply
    assert "<FromUserName><![CDATA[gh_public]]></FromUserName>" in reply
    assert "<Content><![CDATA[Hello <world>]]></Content>" in reply


def test_verify_message_signature_accepts_encrypted_digest() -> None:
    encrypted = encrypt_callback_payload(
        _text_xml("secret"),
        encoding_aes_key=_ENCODING_AES_KEY,
        app_id=_APP_ID,
        random_prefix=b"0123456789abcdef",
    )
    signature = _message_signature("wechat-token", "1710000000", "998877", encrypted)

    assert verify_message_signature(
        "wechat-token",
        signature,
        "1710000000",
        "998877",
        encrypted,
    ) is True


def test_encrypted_callback_echostr_round_trips() -> None:
    encrypted = encrypt_callback_payload(
        "echo-me",
        encoding_aes_key=_ENCODING_AES_KEY,
        app_id=_APP_ID,
        random_prefix=b"0123456789abcdef",
    )
    msg_signature = _message_signature("wechat-token", "1710000000", "998877", encrypted)

    assert decrypt_encrypted_callback_echostr(
        encrypted,
        token="wechat-token",
        msg_signature=msg_signature,
        timestamp="1710000000",
        nonce="998877",
        encoding_aes_key=_ENCODING_AES_KEY,
        app_id=_APP_ID,
    ) == "echo-me"


def test_encrypted_callback_xml_round_trips() -> None:
    encrypted = encrypt_callback_payload(
        _text_xml("secret"),
        encoding_aes_key=_ENCODING_AES_KEY,
        app_id=_APP_ID,
        random_prefix=b"0123456789abcdef",
    )
    msg_signature = _message_signature("wechat-token", "1710000000", "998877", encrypted)
    body = (
        "<xml>"
        f"<ToUserName><![CDATA[gh_public]]></ToUserName>"
        f"<Encrypt><![CDATA[{encrypted}]]></Encrypt>"
        "</xml>"
    )

    assert decrypt_encrypted_callback_xml(
        body,
        token="wechat-token",
        msg_signature=msg_signature,
        timestamp="1710000000",
        nonce="998877",
        encoding_aes_key=_ENCODING_AES_KEY,
        app_id=_APP_ID,
    ) == _text_xml("secret")


def test_build_encrypted_callback_reply_round_trips() -> None:
    encrypted_reply = build_encrypted_callback_reply(
        _text_xml("reply"),
        token="wechat-token",
        encoding_aes_key=_ENCODING_AES_KEY,
        app_id=_APP_ID,
        timestamp="1710000000",
        nonce="998877",
    )
    envelope = _parse_encrypted_envelope(encrypted_reply)

    assert decrypt_encrypted_callback_xml(
        encrypted_reply,
        token="wechat-token",
        msg_signature=envelope["msg_signature"],
        timestamp=envelope["timestamp"],
        nonce=envelope["nonce"],
        encoding_aes_key=_ENCODING_AES_KEY,
        app_id=_APP_ID,
    ) == _text_xml("reply")


@pytest.mark.asyncio
async def test_adapter_lifecycle_and_snapshot() -> None:
    adapter = WeChatOfficialAccountAdapter(
        WeChatOfficialAccountAdapterConfig(
            app_id="app-id",
            app_secret="secret",
            token="wechat-token",
            account_name="clawbot",
        ),
    )

    assert await adapter.get_connection_snapshot() == {"connection_state": "disconnected"}

    await adapter.start()
    snapshot = await adapter.get_connection_snapshot()
    assert snapshot["connection_state"] == "connected"
    assert snapshot["configured"] is True
    assert snapshot["session_count"] == 0
    assert snapshot["account_name"] == "clawbot"

    await adapter.stop()
    assert await adapter.get_connection_snapshot() == {"connection_state": "disconnected"}


@pytest.mark.asyncio
async def test_wait_for_response_resolves_from_incoming_text() -> None:
    adapter = WeChatOfficialAccountAdapter(
        WeChatOfficialAccountAdapterConfig(token="wechat-token"),
    )
    adapter.register_session("session-1", "openid-123")

    waiter = asyncio.create_task(adapter.wait_for_response("session-1", timeout=1.0))
    await asyncio.sleep(0)
    adapter.handle_incoming_xml(_text_xml("yes please"))
    response = await waiter

    assert response["response"] == "yes please"
    assert response["message_type"] == "text"
    assert response["from_user_name"] == "openid-123"


@pytest.mark.asyncio
async def test_callback_receives_normalized_event_text_when_no_pending_session() -> None:
    adapter = WeChatOfficialAccountAdapter(
        WeChatOfficialAccountAdapterConfig(token="wechat-token"),
    )
    seen: list[tuple[str, str]] = []

    def _callback(openid: str, text: str) -> None:
        seen.append((openid, text))

    adapter.set_message_callback(_callback)
    adapter.handle_incoming_xml(_event_xml("CLICK", event_key="launch-clawbot"))

    assert seen == [("openid-123", "launch-clawbot")]


@pytest.mark.asyncio
async def test_async_callback_is_scheduled() -> None:
    adapter = WeChatOfficialAccountAdapter(
        WeChatOfficialAccountAdapterConfig(token="wechat-token"),
    )
    seen: list[str] = []
    event = asyncio.Event()

    async def _callback(_openid: str, text: str) -> None:
        seen.append(text)
        event.set()

    adapter.set_message_callback(_callback)
    adapter.handle_incoming_xml(_text_xml("launch"))
    await asyncio.wait_for(event.wait(), timeout=1.0)

    assert seen == ["launch"]


@pytest.mark.asyncio
async def test_send_prompt_fetches_token_and_posts_customer_service_message() -> None:
    adapter = WeChatOfficialAccountAdapter(
        WeChatOfficialAccountAdapterConfig(
            app_id="wx-app",
            app_secret="wx-secret",
            token="wechat-token",
        ),
    )
    adapter.register_session("session-1", "openid-123")

    token_response = MagicMock()
    token_response.raise_for_status = MagicMock()
    token_response.json.return_value = {"access_token": "access-1", "expires_in": 7200}
    send_response = MagicMock()
    send_response.raise_for_status = MagicMock()
    send_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
    mock_client = _mock_httpx_client(
        get_side_effect=[token_response],
        post_side_effect=[send_response, send_response],
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        await adapter.send_prompt("session-1", "hello there")
        await adapter.send_prompt("session-1", "second message")

    assert mock_client.get.await_count == 1
    assert mock_client.post.await_count == 2
    assert adapter._last_prompt_by_session["session-1"] == "second message"


@pytest.mark.asyncio
async def test_send_prompt_refreshes_access_token_after_wechat_api_error() -> None:
    adapter = WeChatOfficialAccountAdapter(
        WeChatOfficialAccountAdapterConfig(
            app_id="wx-app",
            app_secret="wx-secret",
            token="wechat-token",
        ),
    )
    adapter.register_session("session-1", "openid-123")
    adapter._access_token = "stale-token"
    adapter._access_token_expires_at = 9999999999.0

    token_response = MagicMock()
    token_response.raise_for_status = MagicMock()
    token_response.json.return_value = {"access_token": "fresh-token", "expires_in": 7200}
    expired_send_response = MagicMock()
    expired_send_response.raise_for_status = MagicMock()
    expired_send_response.json.return_value = {"errcode": 40001, "errmsg": "expired"}
    ok_send_response = MagicMock()
    ok_send_response.raise_for_status = MagicMock()
    ok_send_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
    mock_client = _mock_httpx_client(
        get_side_effect=[token_response],
        post_side_effect=[expired_send_response, ok_send_response],
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        await adapter.send_prompt("session-1", "hello there")

    assert mock_client.get.await_count == 1
    assert mock_client.post.await_count == 2
    assert adapter._access_token == "fresh-token"
