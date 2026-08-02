"""Tests for deferred server integration endpoints — publish, blocks, adapters."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import AsyncGenerator
import hashlib

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

_tmp_graphs = tempfile.mkdtemp()
_tmp_checkpoints = tempfile.mkdtemp()
os.environ["DAN_GRAPHS_DIR"] = _tmp_graphs
os.environ["DAN_CHECKPOINT_DIR"] = _tmp_checkpoints

from dan.server.app import app, lifespan  # noqa: E402
from dan.server.routers import adapters as adapters_router  # noqa: E402


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def _wechat_signature(token: str, timestamp: str, nonce: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode("utf-8")).hexdigest()


def _wechat_text_xml(content: str = "hello") -> str:
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


# ------------------------------------------------------------------
# Publish endpoints
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_unpublish_status(client: AsyncClient):
    resp = await client.post("/api/graphs", json={"graph_id": "pub-test"})
    assert resp.status_code == 200

    resp = await client.get("/api/graphs/pub-test/publish-status")
    assert resp.status_code == 200
    assert resp.json()["published"] is False

    resp = await client.post("/api/graphs/pub-test/publish")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "published"
    assert data["graph_id"] == "pub-test"

    resp = await client.get("/api/graphs/pub-test/publish-status")
    assert resp.status_code == 200
    assert resp.json()["published"] is True

    resp = await client.post("/api/graphs/pub-test/unpublish")
    assert resp.status_code == 200
    assert resp.json()["status"] == "unpublished"

    resp = await client.get("/api/graphs/pub-test/publish-status")
    assert resp.status_code == 200
    assert resp.json()["published"] is False


@pytest.mark.asyncio
async def test_publish_nonexistent_graph(client: AsyncClient):
    resp = await client.post("/api/graphs/no-such-graph/publish")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_publish_with_api_key(client: AsyncClient):
    resp = await client.post("/api/graphs", json={"graph_id": "key-test"})
    assert resp.status_code == 200

    resp = await client.post(
        "/api/graphs/key-test/publish",
        json={"api_key": "secret-123"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_publish_status_nonexistent_graph(client: AsyncClient):
    resp = await client.get("/api/graphs/missing/publish-status")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_publish_persistence_roundtrip(client: AsyncClient):
    """Publish creates config, unpublish removes it, status reflects both."""
    resp = await client.post("/api/graphs", json={"graph_id": "persist-test"})
    assert resp.status_code == 200

    resp = await client.post("/api/graphs/persist-test/publish")
    assert resp.status_code == 200

    resp = await client.get("/api/graphs/persist-test/publish-status")
    assert resp.status_code == 200
    status = resp.json()
    assert status["published"] is True
    assert status.get("config", {}).get("enabled") is True

    resp = await client.post("/api/graphs/persist-test/unpublish")
    assert resp.status_code == 200

    resp = await client.get("/api/graphs/persist-test/publish-status")
    assert resp.status_code == 200
    assert resp.json()["published"] is False


# ------------------------------------------------------------------
# Block endpoints
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_blocks(client: AsyncClient):
    resp = await client.get("/api/blocks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_block_not_found(client: AsyncClient):
    resp = await client.get("/api/blocks/nonexistent-block")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_block(client: AsyncClient):
    resp = await client.post("/api/graphs", json={"graph_id": "block-export-test"})
    assert resp.status_code == 200

    resp = await client.post("/api/blocks/export/block-export-test?name=test-block&version=0.1.0")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("application/")


@pytest.mark.asyncio
async def test_export_block_graph_not_found(client: AsyncClient):
    resp = await client.post("/api/blocks/export/no-such-graph")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_import_block_missing_path(client: AsyncClient):
    resp = await client.post("/api/blocks/import", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_remove_block_not_found(client: AsyncClient):
    resp = await client.delete("/api/blocks/nothing/0.0.0")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# Adapter endpoints
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_status_empty(client: AsyncClient):
    resp = await client.get("/api/adapters/status")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_adapter_start_invalid_type(client: AsyncClient):
    resp = await client.post("/api/adapters/start", json={
        "type": "fax",
        "workflow_path": "test.json",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_adapter_start_and_stop_email(client: AsyncClient):
    resp = await client.post("/api/adapters/start", json={
        "type": "email",
        "workflow_path": "test.json",
        "config": {
            "imap_host": "localhost",
            "smtp_host": "localhost",
            "target_email": "test@example.com",
        },
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "started"
    adapter_id = data["adapter_id"]

    resp = await client.get("/api/adapters/status")
    assert resp.status_code == 200
    adapters = resp.json()
    assert len(adapters) >= 1
    found = [a for a in adapters if a["adapter_id"] == adapter_id]
    assert len(found) == 1
    assert found[0]["type"] == "email"

    resp = await client.post("/api/adapters/stop", json={"adapter_id": adapter_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"


@pytest.mark.asyncio
async def test_wechat_callback_endpoint_returns_passive_xml_reply(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
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
        assert account_id == "gh_public"
        assert session_id
        return "chat-wechat-app-1"

    async def _fake_drain_wechat_chat_stream(
        channel_id: str,
        *,
        server_url: str | None = None,
    ) -> str:
        assert channel_id == "chat-wechat-app-1"
        return "Hello from DAN"

    monkeypatch.setattr(adapters_router, "_start_wechat_chat_stream", _fake_start_wechat_chat_stream)
    monkeypatch.setattr(adapters_router, "_drain_wechat_chat_stream", _fake_drain_wechat_chat_stream)

    start_resp = await client.post(
        "/api/adapters/start",
        json={
            "type": "wechat",
            "config": {
                "app_id": "wx1234567890",
                "token": "wechat-token-value",
            },
        },
    )
    assert start_resp.status_code == 200
    adapter_id = start_resp.json()["adapter_id"]
    await asyncio.sleep(0)

    timestamp = "1710000000"
    nonce = "998877"
    signature = _wechat_signature("wechat-token-value", timestamp, nonce)

    try:
        response = await client.post(
            "/api/adapters/wechat/callback",
            params={
                "signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            content=_wechat_text_xml("hello from wechat"),
            headers={"content-type": "application/xml"},
        )
        assert response.status_code == 200
        assert "<MsgType><![CDATA[text]]></MsgType>" in response.text
        assert "<Content><![CDATA[Hello from DAN]]></Content>" in response.text
    finally:
        await client.post("/api/adapters/stop", json={"adapter_id": adapter_id})


@pytest.mark.asyncio
async def test_wechat_callback_endpoint_honors_configured_callback_path(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
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
        assert session_id
        return "chat-wechat-app-2"

    async def _fake_drain_wechat_chat_stream(
        channel_id: str,
        *,
        server_url: str | None = None,
    ) -> str:
        assert channel_id == "chat-wechat-app-2"
        return "Hello from path"

    monkeypatch.setattr(adapters_router, "_start_wechat_chat_stream", _fake_start_wechat_chat_stream)
    monkeypatch.setattr(adapters_router, "_drain_wechat_chat_stream", _fake_drain_wechat_chat_stream)

    start_resp = await client.post(
        "/api/adapters/start",
        json={
            "type": "wechat",
            "config": {
                "app_id": "wx1234567890",
                "token": "wechat-token-value",
                "callback_path": "openclaw/callback",
            },
        },
    )
    assert start_resp.status_code == 200
    adapter_id = start_resp.json()["adapter_id"]
    await asyncio.sleep(0)

    timestamp = "1710000000"
    nonce = "998877"
    signature = _wechat_signature("wechat-token-value", timestamp, nonce)

    try:
        wrong = await client.post(
            "/api/adapters/wechat/callback",
            params={
                "signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            content=_wechat_text_xml("hello from wechat"),
            headers={"content-type": "application/xml"},
        )
        assert wrong.status_code == 404

        response = await client.post(
            "/api/adapters/wechat/openclaw/callback",
            params={
                "signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            content=_wechat_text_xml("hello from wechat"),
            headers={"content-type": "application/xml"},
        )
        assert response.status_code == 200
        assert "<Content><![CDATA[Hello from path]]></Content>" in response.text
    finally:
        await client.post("/api/adapters/stop", json={"adapter_id": adapter_id})


@pytest.mark.asyncio
async def test_adapter_stop_not_found(client: AsyncClient):
    resp = await client.post("/api/adapters/stop", json={"adapter_id": "nope"})
    assert resp.status_code == 404
