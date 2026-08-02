"""Tests for portal generation — MCP config, API docs, OpenAPI spec."""

from __future__ import annotations

import json

import pytest

from dan.publish.portal import (
    generate_api_docs,
    generate_mcp_config,
    generate_openapi_spec,
)
from dan.utils.workflow_interface import WorkflowInterface


class TestGenerateMcpConfig:
    def test_basic_config(self, simple_interface: WorkflowInterface):
        config = generate_mcp_config(
            "workflow.json",
            interfaces=[simple_interface],
        )
        assert "mcpServers" in config
        servers = config["mcpServers"]
        assert len(servers) == 1
        key = list(servers.keys())[0]
        server = servers[key]
        assert server["command"].endswith("python") or "python" in server["command"]
        assert "--type" in server["args"]
        assert "mcp" in server["args"]

    def test_name_override(self, simple_interface: WorkflowInterface):
        config = generate_mcp_config(
            "workflow.json",
            name="my-server",
            interfaces=[simple_interface],
        )
        assert "my-server" in config["mcpServers"]

    def test_absolute_path(self, simple_interface: WorkflowInterface, tmp_path):
        wf = tmp_path / "wf.json"
        wf.touch()
        config = generate_mcp_config(
            str(wf),
            interfaces=[simple_interface],
        )
        server = list(config["mcpServers"].values())[0]
        assert str(wf.resolve()) in server["args"]


class TestGenerateApiDocs:
    def test_basic_docs(self, simple_interface: WorkflowInterface):
        docs = generate_api_docs([simple_interface])
        assert "# Published Workflow API" in docs
        assert "simple-pipe" in docs or "simple_pipe" in docs
        assert "MCP Tools" in docs
        assert "HTTP Endpoints" in docs
        assert "Input Schema" in docs
        assert "Health Check" in docs

    def test_human_workflow_docs(self, human_interface: WorkflowInterface):
        docs = generate_api_docs([human_interface])
        assert "HumanNode Interaction" in docs
        assert "submit-input" in docs
        assert "awaiting_input" in docs

    def test_multi_workflow_docs(
        self,
        simple_interface: WorkflowInterface,
        human_interface: WorkflowInterface,
    ):
        docs = generate_api_docs([simple_interface, human_interface])
        assert "simple" in docs.lower()
        assert "human" in docs.lower()


class TestGenerateOpenApiSpec:
    def test_spec_structure(self, simple_interface: WorkflowInterface):
        spec = generate_openapi_spec([simple_interface])
        assert spec["openapi"] == "3.1.0"
        assert "info" in spec
        assert "paths" in spec

    def test_custom_title(self, simple_interface: WorkflowInterface):
        spec = generate_openapi_spec(
            [simple_interface],
            title="My API",
            version="2.0.0",
        )
        assert spec["info"]["title"] == "My API"
        assert spec["info"]["version"] == "2.0.0"

    def test_paths_include_workflow(self, simple_interface: WorkflowInterface):
        spec = generate_openapi_spec([simple_interface])
        assert "/api/published/simple_pipe/run" in spec["paths"]
        assert "/health" in spec["paths"]

    def test_spec_is_valid_json(self, simple_interface: WorkflowInterface):
        spec = generate_openapi_spec([simple_interface])
        serialized = json.dumps(spec)
        parsed = json.loads(serialized)
        assert parsed == spec
