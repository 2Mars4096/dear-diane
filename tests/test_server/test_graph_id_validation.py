"""Tests for graph ID path-traversal sanitization (22-3 Task 1)."""

from __future__ import annotations

import os
import tempfile
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from dan.server.graph_store import GraphStore, _validate_graph_id

os.environ.setdefault("DAN_GRAPHS_DIR", tempfile.mkdtemp())
os.environ.setdefault("DAN_CHECKPOINT_DIR", tempfile.mkdtemp())


# ── Unit tests: _validate_graph_id ────────────────────────────────────


class TestValidateGraphId:
    def test_valid_ids_accepted(self):
        for gid in ("my-workflow", "test_1", "a.b.c", "A123", "_scratch"):
            _validate_graph_id(gid)

    def test_path_traversal_rejected(self):
        for gid in ("../escape", "foo/bar", "foo\\bar"):
            with pytest.raises(ValueError):
                _validate_graph_id(gid)

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            _validate_graph_id("")

    def test_too_long_rejected(self):
        with pytest.raises(ValueError):
            _validate_graph_id("a" * 100)

    def test_special_chars_rejected(self):
        for gid in ("foo bar", "foo@bar", "foo#bar"):
            with pytest.raises(ValueError):
                _validate_graph_id(gid)

    def test_leading_dot_rejected(self):
        with pytest.raises(ValueError):
            _validate_graph_id(".hidden")

    def test_leading_dash_rejected(self):
        with pytest.raises(ValueError):
            _validate_graph_id("-flag")


# ── GraphStore integration ────────────────────────────────────────────


class TestGraphStoreRejectsBadId:
    def test_get_graph_rejects_traversal(self, tmp_path):
        store = GraphStore(str(tmp_path))
        with pytest.raises(ValueError):
            store.get_graph("../x")

    def test_save_graph_rejects_traversal(self, tmp_path):
        store = GraphStore(str(tmp_path))
        with pytest.raises(ValueError):
            store.save_graph("../x", {"nodes": []})

    def test_create_graph_rejects_traversal(self, tmp_path):
        store = GraphStore(str(tmp_path))
        with pytest.raises(ValueError):
            store.create_graph("foo/bar")

    def test_delete_graph_rejects_empty(self, tmp_path):
        store = GraphStore(str(tmp_path))
        with pytest.raises(ValueError):
            store.delete_graph("")

    def test_load_as_model_rejects_bad(self, tmp_path):
        store = GraphStore(str(tmp_path))
        with pytest.raises(ValueError):
            store.load_as_model("foo@bar")

    def test_set_last_opened_rejects_bad(self, tmp_path):
        store = GraphStore(str(tmp_path))
        with pytest.raises(ValueError):
            store.set_last_opened("foo bar")


# ── App route integration ─────────────────────────────────────────────


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    from dan.server.app import app, lifespan

    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_create_graph_rejects_traversal(client: AsyncClient):
    resp = await client.post("/api/graphs", json={"graph_id": "../escape"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_graph_rejects_special_chars(client: AsyncClient):
    resp = await client.get("/api/graphs/foo@bar")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_graph_rejects_special_chars(client: AsyncClient):
    resp = await client.put("/api/graphs/foo~bar", json={"nodes": []})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_graph_rejects_special_chars(client: AsyncClient):
    resp = await client.delete("/api/graphs/foo!bar")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_graph_rejects_too_long(client: AsyncClient):
    resp = await client.post("/api/graphs", json={"graph_id": "a" * 100})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_graph_accepts_valid(client: AsyncClient):
    resp = await client.post("/api/graphs", json={"graph_id": "valid-test-wf"})
    assert resp.status_code == 200
    assert resp.json()["graph_id"] == "valid-test-wf"
