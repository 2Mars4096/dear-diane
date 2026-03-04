"""Schema derivation helpers for published workflows.

Converts a ``WorkflowInterface`` into MCP tool schemas and OpenAPI 3.1
operation objects.  Reuses ``derive_workflow_interface()`` from
``dan.utils.workflow_interface``.
"""

from __future__ import annotations

import re
from typing import Any

from dan.utils.workflow_interface import WorkflowInterface

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Lowercase, replace non-alnum with underscore, strip edges."""
    return _SLUG_RE.sub("_", name.lower()).strip("_") or "workflow"


def workflow_to_mcp_tools(interface: WorkflowInterface) -> list[dict[str, Any]]:
    """Build a list of MCP tool descriptors for a workflow.

    Always produces a ``{name}_run`` tool.  If the workflow has HumanNodes,
    also produces ``{name}_status`` and ``{name}_submit_input`` tools.
    """
    slug = slugify(interface.name)
    desc = interface.description or f"Run the {interface.name} workflow"

    tools: list[dict[str, Any]] = [
        {
            "name": f"{slug}_run",
            "description": desc,
            "inputSchema": interface.input_schema,
        },
    ]

    if interface.has_human_nodes:
        tools.append({
            "name": f"{slug}_status",
            "description": f"Get execution status for {interface.name}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session ID returned by run"},
                },
                "required": ["session_id"],
            },
        })
        tools.append({
            "name": f"{slug}_submit_input",
            "description": f"Submit human input for a pending prompt in {interface.name}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session ID"},
                    "data": {"type": "object", "description": "Response data matching the pending prompt's output schema"},
                },
                "required": ["session_id", "data"],
            },
        })

    return tools


def workflow_to_openapi_paths(
    interface: WorkflowInterface,
    *,
    base_path: str = "/api/published",
) -> dict[str, Any]:
    """Generate OpenAPI 3.1 path items for a workflow's HTTP endpoints."""
    slug = slugify(interface.name)
    wf_base = f"{base_path}/{slug}"

    paths: dict[str, Any] = {}

    paths[f"{wf_base}/run"] = {
        "post": {
            "summary": f"Run {interface.name} (synchronous)",
            "operationId": f"{slug}_run_sync",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": interface.input_schema}},
            },
            "responses": {
                "200": {
                    "description": "Execution result",
                    "content": {"application/json": {"schema": interface.output_schema}},
                },
            },
        },
    }

    paths[f"{wf_base}/run-async"] = {
        "post": {
            "summary": f"Run {interface.name} (async, returns session_id)",
            "operationId": f"{slug}_run_async",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": interface.input_schema}},
            },
            "responses": {
                "200": {
                    "description": "Session created",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "session_id": {"type": "string"},
                                    "status": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    }

    paths[f"{wf_base}/runs/{{session_id}}"] = {
        "get": {
            "summary": f"Get session status for {interface.name}",
            "operationId": f"{slug}_get_status",
            "parameters": [
                {"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}},
            ],
            "responses": {
                "200": {"description": "Session status"},
            },
        },
    }

    if interface.has_human_nodes:
        paths[f"{wf_base}/runs/{{session_id}}/submit-input"] = {
            "post": {
                "summary": f"Submit human input for {interface.name}",
                "operationId": f"{slug}_submit_input",
                "parameters": [
                    {"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"type": "object", "description": "Human input data"},
                        },
                    },
                },
                "responses": {"200": {"description": "Input accepted"}},
            },
        }

    paths[f"{wf_base}/schema"] = {
        "get": {
            "summary": f"Get schema for {interface.name}",
            "operationId": f"{slug}_schema",
            "responses": {
                "200": {
                    "description": "Workflow interface schema",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
            },
        },
    }

    return paths


def workflow_to_openapi_spec(
    interfaces: list[WorkflowInterface],
    *,
    title: str = "DAN Published Workflows",
    version: str = "1.0.0",
    base_path: str = "/api/published",
) -> dict[str, Any]:
    """Assemble a full OpenAPI 3.1 spec from one or more workflow interfaces."""
    paths: dict[str, Any] = {}
    for iface in interfaces:
        paths.update(workflow_to_openapi_paths(iface, base_path=base_path))

    paths["/health"] = {
        "get": {
            "summary": "Health check",
            "operationId": "health_check",
            "responses": {
                "200": {
                    "description": "Server status",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "status": {"type": "string"},
                                    "workflow_count": {"type": "integer"},
                                },
                            },
                        },
                    },
                },
            },
        },
    }

    paths[f"{base_path}/"] = {
        "get": {
            "summary": "List all published workflows",
            "operationId": "list_workflows",
            "responses": {
                "200": {
                    "description": "Published workflows",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "description": {"type": "string"},
                                        "has_human_nodes": {"type": "boolean"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }

    return {
        "openapi": "3.1.0",
        "info": {"title": title, "version": version},
        "paths": paths,
    }
