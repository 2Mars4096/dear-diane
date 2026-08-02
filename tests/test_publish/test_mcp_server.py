"""Tests for MCP server generation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from dan.models.graph import Graph
from dan.publish.mcp_server import load_workflows_from_path


class TestLoadWorkflowsFromPath:
    def test_load_single_json(self, workflow_json_file: Path):
        wfs = load_workflows_from_path(workflow_json_file)
        assert len(wfs) == 1
        graph, name = wfs[0]
        assert isinstance(graph, Graph)
        assert name is None

    def test_load_directory(self, workflow_dir: Path):
        wfs = load_workflows_from_path(workflow_dir)
        assert len(wfs) == 2

    def test_load_nonexistent_raises(self, tmp_path: Path):
        bad = tmp_path / "nope.json"
        with pytest.raises(Exception):
            load_workflows_from_path(bad)


class TestBuildMcpServer:
    """Test MCP server building (requires mcp package or mocks)."""

    def test_build_server_without_mcp_raises(self, simple_workflow: Graph):
        """If mcp is not installed, should raise ImportError."""
        with patch("dan.publish.mcp_server._HAS_MCP", False):
            with patch("dan.publish.mcp_server.FastMCP", None):
                from dan.publish.mcp_server import build_mcp_server
                with pytest.raises(ImportError, match="mcp"):
                    build_mcp_server([(simple_workflow, None)])

    def test_build_server_with_mock_mcp(self, simple_workflow: Graph):
        """Build server with a mocked FastMCP class."""
        mock_mcp_instance = MagicMock()
        mock_mcp_class = MagicMock(return_value=mock_mcp_instance)

        with patch("dan.publish.mcp_server._HAS_MCP", True):
            with patch("dan.publish.mcp_server.FastMCP", mock_mcp_class):
                from dan.publish.mcp_server import build_mcp_server
                server = build_mcp_server([(simple_workflow, None)])
                assert server is mock_mcp_instance
                mock_mcp_class.assert_called_once_with("dan-publish")

                tool_calls = [
                    c for c in mock_mcp_instance.tool.call_args_list
                ]
                assert len(tool_calls) >= 1
                tool_name = tool_calls[0][1].get("name", "")
                assert "run" in tool_name

    def test_build_server_human_workflow_has_three_tools(self, human_workflow: Graph):
        """Human workflow should register run + status + submit_input + cancel tools."""
        mock_mcp_instance = MagicMock()
        mock_mcp_class = MagicMock(return_value=mock_mcp_instance)

        mock_mcp_instance.tool.return_value = lambda f: f
        mock_mcp_instance.resource.return_value = lambda f: f

        with patch("dan.publish.mcp_server._HAS_MCP", True):
            with patch("dan.publish.mcp_server.FastMCP", mock_mcp_class):
                from dan.publish.mcp_server import build_mcp_server
                build_mcp_server([(human_workflow, None)])

                tool_calls = mock_mcp_instance.tool.call_args_list
                assert len(tool_calls) == 4
                tool_names = [c[1].get("name", "") for c in tool_calls]
                assert any("run" in n for n in tool_names)
                assert any("status" in n for n in tool_names)
                assert any("submit_input" in n for n in tool_names)
                assert any("cancel" in n for n in tool_names)

    def test_build_server_with_name_override(self, simple_workflow: Graph):
        mock_mcp_instance = MagicMock()
        mock_mcp_class = MagicMock(return_value=mock_mcp_instance)

        mock_mcp_instance.tool.return_value = lambda f: f
        mock_mcp_instance.resource.return_value = lambda f: f

        with patch("dan.publish.mcp_server._HAS_MCP", True):
            with patch("dan.publish.mcp_server.FastMCP", mock_mcp_class):
                from dan.publish.mcp_server import build_mcp_server
                build_mcp_server([(simple_workflow, "my_custom_name")])

                tool_calls = mock_mcp_instance.tool.call_args_list
                tool_name = tool_calls[0][1].get("name", "")
                assert "my_custom_name" in tool_name

    def test_multi_workflow_server(self, simple_workflow: Graph, human_workflow: Graph):
        mock_mcp_instance = MagicMock()
        mock_mcp_class = MagicMock(return_value=mock_mcp_instance)

        mock_mcp_instance.tool.return_value = lambda f: f
        mock_mcp_instance.resource.return_value = lambda f: f

        with patch("dan.publish.mcp_server._HAS_MCP", True):
            with patch("dan.publish.mcp_server.FastMCP", mock_mcp_class):
                from dan.publish.mcp_server import build_mcp_server
                build_mcp_server([
                    (simple_workflow, None),
                    (human_workflow, None),
                ])

                # simple: 1 tool, human: 4 tools (run+status+submit+cancel) = 5 total
                tool_calls = mock_mcp_instance.tool.call_args_list
                assert len(tool_calls) == 5

                resource_calls = mock_mcp_instance.resource.call_args_list
                assert len(resource_calls) == 2
