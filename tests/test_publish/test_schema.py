"""Tests for publish schema derivation helpers."""

from __future__ import annotations

import pytest

from dan.publish.schema import (
    slugify,
    workflow_to_mcp_tools,
    workflow_to_openapi_paths,
    workflow_to_openapi_spec,
)
from dan.utils.workflow_interface import WorkflowInterface


class TestSlugify:
    def test_simple(self):
        assert slugify("My Workflow") == "my_workflow"

    def test_special_chars(self):
        assert slugify("paper-writing (v2)") == "paper_writing_v2"

    def test_empty(self):
        assert slugify("") == "workflow"

    def test_numbers(self):
        assert slugify("Test 123") == "test_123"

    def test_already_slug(self):
        assert slugify("simple_chain") == "simple_chain"


class TestWorkflowToMcpTools:
    def test_simple_workflow_one_tool(self, simple_interface: WorkflowInterface):
        tools = workflow_to_mcp_tools(simple_interface)
        assert len(tools) == 1
        assert tools[0]["name"] == "simple_pipe_run"
        assert "inputSchema" in tools[0]
        assert tools[0]["inputSchema"]["properties"]["topic"]["type"] == "string"

    def test_human_workflow_three_tools(self, human_interface: WorkflowInterface):
        tools = workflow_to_mcp_tools(human_interface)
        assert len(tools) == 3
        names = {t["name"] for t in tools}
        assert "human_review_run" in names
        assert "human_review_status" in names
        assert "human_review_submit_input" in names

    def test_status_tool_schema(self, human_interface: WorkflowInterface):
        tools = workflow_to_mcp_tools(human_interface)
        status = next(t for t in tools if t["name"].endswith("_status"))
        assert "session_id" in status["inputSchema"]["properties"]
        assert "session_id" in status["inputSchema"]["required"]

    def test_submit_tool_schema(self, human_interface: WorkflowInterface):
        tools = workflow_to_mcp_tools(human_interface)
        submit = next(t for t in tools if t["name"].endswith("_submit_input"))
        assert "session_id" in submit["inputSchema"]["properties"]
        assert "data" in submit["inputSchema"]["properties"]


class TestWorkflowToOpenApiPaths:
    def test_simple_workflow_paths(self, simple_interface: WorkflowInterface):
        paths = workflow_to_openapi_paths(simple_interface)
        slug = "simple_pipe"
        assert f"/api/published/{slug}/run" in paths
        assert f"/api/published/{slug}/run-async" in paths
        assert f"/api/published/{slug}/schema" in paths
        assert f"/api/published/{slug}/runs/{{session_id}}" in paths
        # no submit-input for non-human workflow
        assert f"/api/published/{slug}/runs/{{session_id}}/submit-input" not in paths

    def test_human_workflow_includes_submit(self, human_interface: WorkflowInterface):
        paths = workflow_to_openapi_paths(human_interface)
        slug = "human_review"
        assert f"/api/published/{slug}/runs/{{session_id}}/submit-input" in paths


class TestWorkflowToOpenApiSpec:
    def test_spec_structure(self, simple_interface: WorkflowInterface):
        spec = workflow_to_openapi_spec([simple_interface])
        assert spec["openapi"] == "3.1.0"
        assert "paths" in spec
        assert "/health" in spec["paths"]
        assert "/api/published/" in spec["paths"]

    def test_multi_workflow_spec(
        self,
        simple_interface: WorkflowInterface,
        human_interface: WorkflowInterface,
    ):
        spec = workflow_to_openapi_spec([simple_interface, human_interface])
        paths = spec["paths"]
        assert "/api/published/simple_pipe/run" in paths
        assert "/api/published/human_review/run" in paths
