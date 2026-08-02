"""Tests for publish/share/export capability handlers (25-3)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from dan.models.graph import Graph, GraphMetadata
from dan.server.capability_registry import CapabilityContext
from dan.server.capability_handlers import (
    handle_export_workflow,
    handle_get_publish_status,
    handle_import_block,
    handle_list_blocks,
    handle_list_published,
    handle_publish_workflow,
    handle_share_workflow,
    handle_unpublish_workflow,
    register_publish_capabilities,
    WRITE_MODES,
)
from dan.server.capability_registry import ALL_MODES, ChatCapabilityRegistry


def _minimal_graph(graph_id: str = "test-wf") -> Graph:
    return Graph(
        metadata=GraphMetadata(name="Test Workflow", description="A test"),
        nodes=[],
        edges=[],
    )


def _graph_data(graph: Graph) -> dict:
    return json.loads(graph.model_dump_json())


# ── publish_workflow ─────────────────────────────────────────────────

class TestPublishWorkflow:

    @pytest.mark.asyncio
    async def test_no_registry(self):
        ctx = CapabilityContext(workflow_id="wf1")
        result = await handle_publish_workflow({}, ctx)
        assert result.success is False
        assert "not available" in result.message

    @pytest.mark.asyncio
    async def test_graph_not_found(self):
        mock_store = MagicMock()
        mock_store.get_graph.return_value = None
        mock_registry = MagicMock()
        ctx = CapabilityContext(
            workflow_id="",
            graph_store=mock_store,
            publish_registry=mock_registry,
        )
        result = await handle_publish_workflow({"graph_id": "missing"}, ctx)
        assert result.success is False
        assert "not found" in result.message

    @pytest.mark.asyncio
    async def test_publish_success(self, tmp_path):
        graph = _minimal_graph("wf1")
        mock_store = MagicMock()
        mock_store.get_graph.return_value = _graph_data(graph)
        mock_registry = MagicMock()
        mock_registry.register.return_value = "test_workflow"
        ctx = CapabilityContext(
            workflow_id="wf1",
            graph_store=mock_store,
            publish_registry=mock_registry,
            graphs_dir=str(tmp_path),
        )
        result = await handle_publish_workflow({}, ctx)
        assert result.success is True
        assert "test_workflow" in result.message
        mock_registry.register.assert_called_once()
        pub_file = tmp_path / "wf1.publish.json"
        assert pub_file.exists()
        config = json.loads(pub_file.read_text())
        assert config.get("enabled") is True


# ── unpublish_workflow ───────────────────────────────────────────────

class TestUnpublishWorkflow:

    @pytest.mark.asyncio
    async def test_no_registry(self):
        ctx = CapabilityContext(workflow_id="wf1")
        result = await handle_unpublish_workflow({}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_unpublish_success(self, tmp_path):
        graph = _minimal_graph("wf1")
        mock_store = MagicMock()
        mock_store.get_graph.return_value = _graph_data(graph)
        mock_registry = MagicMock()
        pub_file = tmp_path / "wf1.publish.json"
        pub_file.write_text(json.dumps({"enabled": True}))
        ctx = CapabilityContext(
            workflow_id="wf1",
            graph_store=mock_store,
            publish_registry=mock_registry,
            graphs_dir=str(tmp_path),
        )
        result = await handle_unpublish_workflow({}, ctx)
        assert result.success is True
        mock_registry.unregister.assert_called_once()
        assert not pub_file.exists()


# ── export_workflow ───────────────────────────────────────────────────

class TestExportWorkflow:

    @pytest.mark.asyncio
    async def test_graph_not_found(self):
        mock_store = MagicMock()
        mock_store.get_graph.return_value = None
        ctx = CapabilityContext(workflow_id="", graph_store=mock_store)
        result = await handle_export_workflow({"format": "block"}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_export_block(self):
        graph = _minimal_graph("wf1")
        mock_store = MagicMock()
        mock_store.get_graph.return_value = _graph_data(graph)
        ctx = CapabilityContext(workflow_id="wf1", graph_store=mock_store)
        result = await handle_export_workflow({"format": "block"}, ctx)
        assert result.success is True
        assert "path" in result.data
        assert result.data["format"] == "block"

    @pytest.mark.asyncio
    async def test_export_markdown(self):
        graph = _minimal_graph("wf1")
        mock_store = MagicMock()
        mock_store.get_graph.return_value = _graph_data(graph)
        ctx = CapabilityContext(workflow_id="wf1", graph_store=mock_store)
        result = await handle_export_workflow({"format": "markdown"}, ctx)
        assert result.success is True
        assert "paths" in result.data
        assert result.data["format"] == "markdown"

    @pytest.mark.asyncio
    async def test_export_python(self):
        graph = _minimal_graph("wf1")
        mock_store = MagicMock()
        mock_store.get_graph.return_value = _graph_data(graph)
        ctx = CapabilityContext(workflow_id="wf1", graph_store=mock_store)
        result = await handle_export_workflow({"format": "python"}, ctx)
        assert result.success is True
        assert "path" in result.data
        assert result.data["format"] == "python"


# ── share_workflow ────────────────────────────────────────────────────

class TestShareWorkflow:

    @pytest.mark.asyncio
    async def test_graph_not_found(self):
        mock_store = MagicMock()
        mock_store.get_graph.return_value = None
        ctx = CapabilityContext(workflow_id="", graph_store=mock_store)
        result = await handle_share_workflow({"format": "mcp_config"}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_share_mcp_config(self):
        graph = _minimal_graph("wf1")
        mock_store = MagicMock()
        mock_store.get_graph.return_value = _graph_data(graph)
        ctx = CapabilityContext(workflow_id="wf1", graph_store=mock_store, graphs_dir="/tmp/graphs")
        result = await handle_share_workflow({"format": "mcp_config"}, ctx)
        assert result.success is True
        assert "mcpServers" in result.data
        assert "mcp_config" in result.message.lower() or "mcp" in result.message.lower()

    @pytest.mark.asyncio
    async def test_share_api_docs(self):
        graph = _minimal_graph("wf1")
        mock_store = MagicMock()
        mock_store.get_graph.return_value = _graph_data(graph)
        ctx = CapabilityContext(workflow_id="wf1", graph_store=mock_store)
        result = await handle_share_workflow({"format": "api_docs"}, ctx)
        assert result.success is True
        assert "Published Workflow API" in (result.data.get("markdown") or result.message)

    @pytest.mark.asyncio
    async def test_share_openapi(self):
        graph = _minimal_graph("wf1")
        mock_store = MagicMock()
        mock_store.get_graph.return_value = _graph_data(graph)
        ctx = CapabilityContext(workflow_id="wf1", graph_store=mock_store)
        result = await handle_share_workflow({"format": "openapi"}, ctx)
        assert result.success is True
        assert "openapi" in str(result.data).lower() or "paths" in result.data


# ── list_published ────────────────────────────────────────────────────

class TestListPublished:

    @pytest.mark.asyncio
    async def test_no_registry(self):
        ctx = CapabilityContext(workflow_id="wf1")
        result = await handle_list_published({}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_empty_list(self):
        mock_registry = MagicMock()
        mock_registry.list_all.return_value = []
        ctx = CapabilityContext(workflow_id="wf1", publish_registry=mock_registry)
        result = await handle_list_published({}, ctx)
        assert result.success is True
        assert "No published" in result.message

    @pytest.mark.asyncio
    async def test_with_items(self):
        mock_registry = MagicMock()
        mock_registry.list_all.return_value = [
            MagicMock(workflow_id="slug1", name="WF1", description="Desc", has_human_nodes=False),
        ]
        ctx = CapabilityContext(workflow_id="wf1", publish_registry=mock_registry)
        result = await handle_list_published({}, ctx)
        assert result.success is True
        assert "slug1" in result.message
        assert "WF1" in result.message


# ── get_publish_status ───────────────────────────────────────────────

class TestGetPublishStatus:

    @pytest.mark.asyncio
    async def test_no_registry(self):
        ctx = CapabilityContext(workflow_id="wf1")
        result = await handle_get_publish_status({}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_graph_not_found(self):
        mock_store = MagicMock()
        mock_store.get_graph.return_value = None
        mock_registry = MagicMock()
        ctx = CapabilityContext(
            workflow_id="",
            graph_store=mock_store,
            publish_registry=mock_registry,
        )
        result = await handle_get_publish_status({"graph_id": "missing"}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_published(self, tmp_path):
        graph = _minimal_graph("wf1")
        mock_store = MagicMock()
        mock_store.get_graph.return_value = _graph_data(graph)
        mock_registry = MagicMock()
        mock_registry.is_published.return_value = True
        pub_file = tmp_path / "wf1.publish.json"
        pub_file.write_text(json.dumps({"enabled": True, "api_key": None}))
        ctx = CapabilityContext(
            workflow_id="wf1",
            graph_store=mock_store,
            publish_registry=mock_registry,
            graphs_dir=str(tmp_path),
        )
        result = await handle_get_publish_status({}, ctx)
        assert result.success is True
        assert result.data["published"] is True
        assert "config" in result.data


# ── import_block ──────────────────────────────────────────────────────

class TestImportBlock:

    @pytest.mark.asyncio
    async def test_no_registry(self):
        ctx = CapabilityContext(workflow_id="wf1")
        result = await handle_import_block({"source": "/tmp/block"}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_missing_source(self):
        mock_registry = MagicMock()
        ctx = CapabilityContext(workflow_id="wf1", block_registry=mock_registry)
        result = await handle_import_block({}, ctx)
        assert result.success is False
        assert "required" in result.message.lower() or "source" in result.message.lower()


# ── list_blocks ───────────────────────────────────────────────────────

class TestListBlocks:

    @pytest.mark.asyncio
    async def test_no_registry(self):
        ctx = CapabilityContext(workflow_id="wf1")
        result = await handle_list_blocks({}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_empty_list(self):
        mock_registry = MagicMock()
        mock_registry.list_blocks.return_value = []
        ctx = CapabilityContext(workflow_id="wf1", block_registry=mock_registry)
        result = await handle_list_blocks({}, ctx)
        assert result.success is True
        assert "No blocks" in result.message


# ── register_publish_capabilities ────────────────────────────────────

class TestRegisterPublishCapabilities:

    def test_tools_registered(self):
        reg = ChatCapabilityRegistry()
        register_publish_capabilities(reg)
        names = reg.list_tool_names()
        assert "publish_workflow" in names
        assert "unpublish_workflow" in names
        assert "export_workflow" in names
        assert "share_workflow" in names
        assert "list_published" in names
        assert "get_publish_status" in names
        assert "import_block" in names
        assert "list_blocks" in names

    def test_write_tools_only_in_write_modes(self):
        reg = ChatCapabilityRegistry()
        register_publish_capabilities(reg)
        for mode in WRITE_MODES:
            assert reg.is_available("publish_workflow", mode)
        for mode in ("ask", "plan"):
            assert not reg.is_available("publish_workflow", mode)

    def test_read_tools_in_all_modes(self):
        reg = ChatCapabilityRegistry()
        register_publish_capabilities(reg)
        for mode in ALL_MODES:
            assert reg.is_available("list_published", mode)
            assert reg.is_available("share_workflow", mode)
