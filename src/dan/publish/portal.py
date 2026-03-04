"""Portal helpers — generate consumer-facing config and documentation.

The output of these functions is copy-pasteable: MCP client config for
Cursor / Claude Desktop, markdown API docs, and OpenAPI 3.1 specs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from dan.publish.schema import (
    slugify,
    workflow_to_mcp_tools,
    workflow_to_openapi_spec,
)
from dan.utils.workflow_interface import WorkflowInterface


# ---------------------------------------------------------------------------
# MCP client config generation
# ---------------------------------------------------------------------------

def generate_mcp_config(
    workflow_path: str,
    name: str | None = None,
    *,
    interfaces: list[WorkflowInterface] | None = None,
    python_path: str | None = None,
) -> dict[str, Any]:
    """Produce a ready-to-paste MCP client config (for Cursor or Claude Desktop).

    The resulting JSON can be dropped into ``.cursor/mcp.json`` (Cursor) or
    ``claude_desktop_config.json`` (Claude Desktop) with zero edits.
    """
    py = python_path or sys.executable
    abs_path = str(Path(workflow_path).resolve())

    if name:
        server_name = name
    elif interfaces and len(interfaces) == 1:
        server_name = slugify(interfaces[0].name) or "dan-workflow"
    else:
        server_name = "dan-workflow"

    config: dict[str, Any] = {
        "mcpServers": {
            server_name: {
                "command": py,
                "args": ["-m", "dan.cli.publish", abs_path, "--type", "mcp"],
            },
        },
    }
    return config


# ---------------------------------------------------------------------------
# Markdown API docs
# ---------------------------------------------------------------------------

def generate_api_docs(
    interfaces: list[WorkflowInterface],
    *,
    base_url: str = "http://localhost:8001",
) -> str:
    """Generate markdown documentation for published workflow APIs."""
    lines: list[str] = ["# Published Workflow API", ""]

    for iface in interfaces:
        slug = slugify(iface.name)
        lines.append(f"## {iface.name}")
        lines.append("")
        if iface.description:
            lines.append(iface.description)
            lines.append("")

        lines.append("### MCP Tools")
        lines.append("")
        tools = workflow_to_mcp_tools(iface)
        for tool in tools:
            lines.append(f"**`{tool['name']}`** — {tool['description']}")
            lines.append("")
            lines.append("Input schema:")
            lines.append("```json")
            lines.append(json.dumps(tool["inputSchema"], indent=2))
            lines.append("```")
            lines.append("")

        lines.append("### HTTP Endpoints")
        lines.append("")
        lines.append(f"| Method | Path | Description |")
        lines.append(f"|--------|------|-------------|")
        lines.append(f"| POST | `{base_url}/api/published/{slug}/run` | Synchronous execution |")
        lines.append(f"| POST | `{base_url}/api/published/{slug}/run-async` | Async execution |")
        lines.append(f"| GET | `{base_url}/api/published/{slug}/runs/{{session_id}}` | Poll status |")
        if iface.has_human_nodes:
            lines.append(
                f"| POST | `{base_url}/api/published/{slug}/runs/{{session_id}}/submit-input` "
                f"| Submit human input |"
            )
        lines.append(f"| GET | `{base_url}/api/published/{slug}/schema` | Get schema |")
        lines.append("")

        lines.append("### Input Schema")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(iface.input_schema, indent=2))
        lines.append("```")
        lines.append("")

        lines.append("### Output Schema")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(iface.output_schema, indent=2))
        lines.append("```")
        lines.append("")

        if iface.has_human_nodes:
            lines.append("### HumanNode Interaction")
            lines.append("")
            lines.append("This workflow contains human-in-the-loop nodes. Use the multi-call pattern:")
            lines.append("")
            lines.append("1. Call `run-async` to start execution")
            lines.append("2. Poll `runs/{session_id}` until status is `awaiting_input`")
            lines.append("3. Read `pending_prompt` for the HumanNode request")
            lines.append("4. Call `runs/{session_id}/submit-input` with response data")
            lines.append("5. Continue polling until `completed` or `failed`")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("### Health Check")
    lines.append("")
    lines.append(f"```")
    lines.append(f"GET {base_url}/health")
    lines.append(f"```")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# OpenAPI spec
# ---------------------------------------------------------------------------

def generate_openapi_spec(
    interfaces: list[WorkflowInterface],
    *,
    title: str = "DAN Published Workflows",
    version: str = "1.0.0",
) -> dict[str, Any]:
    """Generate OpenAPI 3.1 spec for the published workflows."""
    return workflow_to_openapi_spec(interfaces, title=title, version=version)
