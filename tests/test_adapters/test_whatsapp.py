"""Tests for the WhatsApp Business API adapter."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.adapters.whatsapp_adapter import (
    WhatsAppAdapter,
    WhatsAppAdapterConfig,
    _process_webhook_payload,
    _split_message,
    _verify_signature,
)


@pytest.fixture
def wa_config() -> WhatsAppAdapterConfig:
    return WhatsAppAdapterConfig(
        workflow_path="/tmp/wf.md",
        access_token="FAKE_TOKEN",
        phone_number_id="123456",
        verify_token="test-verify",
        app_secret="test-secret",
    )


@pytest.fixture
def wa_adapter(wa_config: WhatsAppAdapterConfig) -> WhatsAppAdapter:
    return WhatsAppAdapter(wa_config)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestWhatsAppAdapterConfig:
    def test_defaults(self) -> None:
        cfg = WhatsAppAdapterConfig()
        assert cfg.access_token == ""
        assert cfg.verify_token == "dan-verify"

    def test_custom(self) -> None:
        cfg = WhatsAppAdapterConfig(access_token="tok", phone_number_id="pid")
        assert cfg.access_token == "tok"
        assert cfg.phone_number_id == "pid"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self, wa_adapter: WhatsAppAdapter) -> None:
        await wa_adapter.start()
        assert wa_adapter._running is True
        await wa_adapter.stop()
        assert wa_adapter._running is False


# ---------------------------------------------------------------------------
# Session mapping
# ---------------------------------------------------------------------------

class TestSessionMapping:
    def test_register_lookup(self, wa_adapter: WhatsAppAdapter) -> None:
        wa_adapter.register_session("sess-1", "+1234567890")
        assert wa_adapter._phone_from_session("sess-1") == "+1234567890"

    def test_unregister(self, wa_adapter: WhatsAppAdapter) -> None:
        wa_adapter.register_session("sess-2", "+0987654321")
        wa_adapter.unregister_session("sess-2")
        assert wa_adapter._phone_from_session("sess-2") is None


# ---------------------------------------------------------------------------
# Message splitting
# ---------------------------------------------------------------------------

class TestSplitMessage:
    def test_short_message(self) -> None:
        assert _split_message("hello") == ["hello"]

    def test_long_message(self) -> None:
        text = "word " * 1000
        chunks = _split_message(text, max_len=100)
        assert all(len(c) <= 100 for c in chunks)


# ---------------------------------------------------------------------------
# Webhook payload processing
# ---------------------------------------------------------------------------

class TestWebhookPayload:
    def test_text_message(self, wa_adapter: WhatsAppAdapter) -> None:
        wa_adapter.register_session("sess-wh", "+1111")
        loop = asyncio.new_event_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        wa_adapter._pending["sess-wh"] = fut

        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "+1111",
                            "type": "text",
                            "text": {"body": "hello world"},
                        }],
                    },
                }],
            }],
        }
        _process_webhook_payload(wa_adapter, payload)

        assert fut.done()
        assert fut.result()["response"] == "hello world"
        loop.close()

    def test_button_reply(self, wa_adapter: WhatsAppAdapter) -> None:
        wa_adapter.register_session("sess-btn", "+2222")
        loop = asyncio.new_event_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        wa_adapter._pending["sess-btn"] = fut

        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "+2222",
                            "type": "interactive",
                            "interactive": {
                                "type": "button_reply",
                                "button_reply": {"id": "approve_yes", "title": "Yes"},
                            },
                        }],
                    },
                }],
            }],
        }
        _process_webhook_payload(wa_adapter, payload)

        assert fut.done()
        assert fut.result()["approved"] is True
        loop.close()

    def test_list_reply(self, wa_adapter: WhatsAppAdapter) -> None:
        wa_adapter.register_session("sess-list", "+3333")
        loop = asyncio.new_event_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        wa_adapter._pending["sess-list"] = fut

        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "+3333",
                            "type": "interactive",
                            "interactive": {
                                "type": "list_reply",
                                "list_reply": {"id": "option_1", "title": "Option A"},
                            },
                        }],
                    },
                }],
            }],
        }
        _process_webhook_payload(wa_adapter, payload)

        assert fut.done()
        assert fut.result()["title"] == "Option A"
        loop.close()

    def test_empty_payload(self, wa_adapter: WhatsAppAdapter) -> None:
        _process_webhook_payload(wa_adapter, {})
        _process_webhook_payload(wa_adapter, {"entry": []})


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

class TestSignatureVerification:
    def test_valid_signature(self) -> None:
        secret = "my-secret"
        payload = b'{"test": true}'
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert _verify_signature(payload, secret, f"sha256={expected}") is True

    def test_invalid_signature(self) -> None:
        assert _verify_signature(b"data", "secret", "sha256=invalid") is False

    def test_no_prefix(self) -> None:
        assert _verify_signature(b"data", "secret", "bad-header") is False


# ---------------------------------------------------------------------------
# Wait for response
# ---------------------------------------------------------------------------

class TestWaitForResponse:
    @pytest.mark.asyncio
    async def test_response_resolved(self, wa_adapter: WhatsAppAdapter) -> None:
        async def _feed() -> None:
            await asyncio.sleep(0.05)
            fut = wa_adapter._pending.get("sess-wr")
            if fut and not fut.done():
                fut.set_result({"response": "user reply"})

        asyncio.create_task(_feed())
        result = await wa_adapter.wait_for_response("sess-wr", timeout=2.0)
        assert result["response"] == "user reply"

    @pytest.mark.asyncio
    async def test_response_timeout(self, wa_adapter: WhatsAppAdapter) -> None:
        with pytest.raises(asyncio.TimeoutError):
            await wa_adapter.wait_for_response("sess-to", timeout=0.1)


# ---------------------------------------------------------------------------
# Send methods (mock httpx)
# ---------------------------------------------------------------------------

class TestSendMethods:
    @pytest.mark.asyncio
    async def test_send_text_message(self, wa_adapter: WhatsAppAdapter) -> None:
        wa_adapter.register_session("sess-send", "+4444")

        captured_payload: dict[str, Any] = {}

        async def mock_api_post(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
            captured_payload.update(payload)
            return {"messages": [{"id": "wamid.123"}]}

        wa_adapter._api_post = mock_api_post  # type: ignore[assignment]
        await wa_adapter._send_text_message("+4444", "Hello!")
        assert captured_payload["to"] == "+4444"
        assert captured_payload["text"]["body"] == "Hello!"

    @pytest.mark.asyncio
    async def test_send_result(self, wa_adapter: WhatsAppAdapter) -> None:
        wa_adapter.register_session("sess-result", "+5555")

        calls: list[dict[str, Any]] = []

        async def mock_api_post(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append(payload)
            return {}

        wa_adapter._api_post = mock_api_post  # type: ignore[assignment]
        await wa_adapter.send_result("sess-result", {"score": 100})
        assert len(calls) >= 1
        assert "score" in calls[0]["text"]["body"].lower() or "100" in calls[0]["text"]["body"]
