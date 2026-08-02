"""Tests for the HTTP REST publish server."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from dan.models.graph import Graph
from dan.publish.http_server import (
    PublishRegistry,
    create_publish_app,
    create_publish_router,
)
@pytest.fixture()
def registry(simple_workflow: Graph, human_workflow: Graph) -> PublishRegistry:
    reg = PublishRegistry()
    reg.register(simple_workflow)
    reg.register(human_workflow)
    return reg


@pytest.fixture()
def simple_app(simple_workflow: Graph):
    return create_publish_app([(simple_workflow, None)], force_local=True)


@pytest.fixture()
def multi_app(simple_workflow: Graph, human_workflow: Graph):
    return create_publish_app(
        [(simple_workflow, None), (human_workflow, None)],
        force_local=True,
    )


class TestPublishRegistry:
    def test_register(self, simple_workflow: Graph):
        reg = PublishRegistry()
        slug = reg.register(simple_workflow)
        assert slug == "simple_pipe"
        assert reg.get("simple_pipe") is not None

    def test_register_with_name_override(self, simple_workflow: Graph):
        reg = PublishRegistry()
        slug = reg.register(simple_workflow, name_override="custom")
        assert slug == "custom"

    def test_list_all(self, registry: PublishRegistry):
        summaries = registry.list_all()
        assert len(summaries) == 2
        names = {s.workflow_id for s in summaries}
        assert "simple_pipe" in names
        assert "human_review" in names

    def test_get_nonexistent(self, registry: PublishRegistry):
        assert registry.get("nope") is None


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health(self, simple_app):
        transport = ASGITransport(app=simple_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/health")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "ok"
            assert data["workflow_count"] == 1


class TestListWorkflows:
    @pytest.mark.asyncio
    async def test_list(self, multi_app):
        transport = ASGITransport(app=multi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/published/")
            assert r.status_code == 200
            data = r.json()
            assert len(data) == 2
            names = {d["workflow_id"] for d in data}
            assert "simple_pipe" in names


class TestSchemaEndpoint:
    @pytest.mark.asyncio
    async def test_get_schema(self, simple_app):
        transport = ASGITransport(app=simple_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/published/simple_pipe/schema")
            assert r.status_code == 200
            data = r.json()
            assert "input_schema" in data
            assert "output_schema" in data
            assert data["name"] == "simple-pipe"

    @pytest.mark.asyncio
    async def test_schema_not_found(self, simple_app):
        transport = ASGITransport(app=simple_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/published/nonexistent/schema")
            assert r.status_code == 404


class TestRunAsyncEndpoint:
    @pytest.mark.asyncio
    async def test_run_async_creates_session(self, multi_app):
        transport = ASGITransport(app=multi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/published/human_review/run-async",
                json={"inputs": {"topic": "AI"}},
            )
            assert r.status_code == 200
            data = r.json()
            assert "session_id" in data
            assert data["status"] == "running"


class TestGetSession:
    @pytest.mark.asyncio
    async def test_session_not_found(self, simple_app):
        transport = ASGITransport(app=simple_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/published/simple_pipe/runs/bad-id")
            assert r.status_code == 404


class TestAuthEndpoint:
    @pytest.mark.asyncio
    async def test_api_key_required(self, simple_workflow: Graph):
        app = create_publish_app(
            [(simple_workflow, None)],
            global_api_key="secret123",
            force_local=True,
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/published/simple_pipe/schema")
            assert r.status_code == 401

            r = await client.get(
                "/api/published/simple_pipe/schema",
                headers={"x-api-key": "secret123"},
            )
            assert r.status_code == 200

            r = await client.get(
                "/api/published/simple_pipe/schema?api_key=secret123",
            )
            assert r.status_code == 200
