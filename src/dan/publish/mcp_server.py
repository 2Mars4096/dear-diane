"""MCP server generation for published DAN workflows.

Generates and runs an MCP server that exposes each workflow as a set of
MCP tools.  Requires the ``mcp`` optional dependency (``pip install dan[mcp]``).

Each workflow gets:
  - ``{name}_run``          — execute the workflow (blocks until done or awaiting input)
  - ``{name}_status``       — get session status  (only if has_human_nodes)
  - ``{name}_submit_input`` — submit HumanNode input (only if has_human_nodes)
  - ``{name}_cancel``       — cancel a running workflow (only if has_human_nodes)

Workflow metadata is also exposed as an MCP resource for discovery.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from dan.engine.executor import EngineConfig
from dan.models.graph import Graph
from dan.publish.runtime import PublishRuntime
from dan.publish.schema import slugify
from dan.utils.workflow_interface import WorkflowInterface, derive_workflow_interface

logger = logging.getLogger(__name__)

try:
    from mcp.server.fastmcp import FastMCP

    _HAS_MCP = True
except ImportError:  # pragma: no cover
    _HAS_MCP = False
    FastMCP = None  # type: ignore[assignment,misc]


def _require_mcp() -> type:
    if not _HAS_MCP:
        raise ImportError(
            "The 'mcp' package is required for MCP server support. "
            "Install it with: pip install dan[mcp]"
        )
    return FastMCP  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Lazy runtime resolver (per-server, not global)
# ---------------------------------------------------------------------------

class _RuntimeHolder:
    """Lazily resolves to gateway or local runtime on first async call.

    Instantiated once per ``build_mcp_server`` invocation and captured by
    tool closures.  The first tool call triggers server detection; all
    subsequent calls reuse the resolved runtime.
    """

    def __init__(
        self,
        *,
        server_url: str | None,
        force_local: bool,
        engine_config: EngineConfig | None,
        human_timeout: float,
        runtime: PublishRuntime | None,
    ) -> None:
        self._server_url = server_url
        self._force_local = force_local
        self._engine_config = engine_config
        self._human_timeout = human_timeout
        self._resolved: PublishRuntime | None = runtime
        self._lock = asyncio.Lock()

    async def get(self) -> PublishRuntime:
        if self._resolved is not None:
            return self._resolved

        async with self._lock:
            if self._resolved is not None:
                return self._resolved

            from dan.publish.runtime import create_publish_runtime

            self._resolved = await create_publish_runtime(
                server_url=self._server_url,
                force_local=self._force_local,
                engine_config=self._engine_config,
                human_timeout=self._human_timeout,
            )
            return self._resolved


# ---------------------------------------------------------------------------
# Workflow registry (holds loaded graphs + interfaces)
# ---------------------------------------------------------------------------

class _WorkflowEntry:
    __slots__ = ("graph", "interface", "slug")

    def __init__(self, graph: Graph, interface: WorkflowInterface, slug: str) -> None:
        self.graph = graph
        self.interface = interface
        self.slug = slug


# ---------------------------------------------------------------------------
# MCP server builder
# ---------------------------------------------------------------------------

def build_mcp_server(
    workflows: list[tuple[Graph, str | None]],
    *,
    engine_config: EngineConfig | None = None,
    server_name: str = "dan-publish",
    human_timeout: float = 300.0,
    server_url: str | None = None,
    force_local: bool = False,
    runtime: PublishRuntime | None = None,
) -> Any:
    """Build a FastMCP server instance from one or more workflow graphs.

    Parameters
    ----------
    workflows:
        List of ``(graph, name_override)`` tuples.  If *name_override* is
        ``None``, the graph metadata name is used.
    engine_config:
        Engine configuration (LLM keys, model, etc.).
    server_name:
        Display name for the MCP server.
    human_timeout:
        Default timeout (seconds) for HumanNode prompts.
    server_url:
        Optional dan-serve URL override.  When set (and ``force_local`` is
        ``False``), workflow execution routes through the gateway API.
    force_local:
        Force direct engine execution regardless of server availability.
    runtime:
        Pre-created :class:`PublishRuntime`.  When provided, *server_url*,
        *force_local*, *engine_config*, and *human_timeout* are ignored
        and the runtime is used directly (no lazy detection).
    """
    McpClass = _require_mcp()
    mcp = McpClass(server_name)

    holder = _RuntimeHolder(
        server_url=server_url,
        force_local=force_local,
        engine_config=engine_config,
        human_timeout=human_timeout,
        runtime=runtime,
    )

    entries: list[_WorkflowEntry] = []
    for graph, name_override in workflows:
        iface = derive_workflow_interface(graph)
        if name_override:
            iface.name = name_override
        slug = slugify(iface.name)
        entries.append(_WorkflowEntry(graph, iface, slug))

    for entry in entries:
        _register_workflow_tools(mcp, entry, holder)
        _register_workflow_resource(mcp, entry)

    return mcp


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def _register_workflow_tools(
    mcp: Any,
    entry: _WorkflowEntry,
    holder: _RuntimeHolder,
) -> None:
    """Register MCP tools for a single workflow."""
    slug = entry.slug
    iface = entry.interface
    graph = entry.graph

    run_desc = iface.description or f"Run the {iface.name} workflow"

    @mcp.tool(name=f"{slug}_run", description=run_desc)
    async def run_tool(**kwargs: Any) -> dict[str, Any]:
        rt = await holder.get()
        if iface.has_human_nodes:
            session_id = await rt.run_async(slug, graph, kwargs)
            return {
                "session_id": session_id,
                "status": "running",
                "message": "Execution started. Use status/submit_input tools for interaction.",
            }
        return await rt.run_sync(slug, graph, kwargs)

    if iface.has_human_nodes:

        @mcp.tool(
            name=f"{slug}_status",
            description=f"Get execution status for {iface.name}",
        )
        async def status_tool(session_id: str) -> dict[str, Any]:
            rt = await holder.get()
            result = await rt.get_status(session_id)
            if result is None:
                return {"error": f"Session '{session_id}' not found"}
            return result

        @mcp.tool(
            name=f"{slug}_submit_input",
            description=f"Submit human input for a pending prompt in {iface.name}",
        )
        async def submit_tool(session_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
            if data is None:
                data = {}
            rt = await holder.get()
            result = await rt.submit_input(session_id, data)
            if result is None:
                return {"error": f"Session '{session_id}' not found or not awaiting input"}
            return result

        @mcp.tool(
            name=f"{slug}_cancel",
            description=f"Cancel a running execution of {iface.name}",
        )
        async def cancel_tool(run_id: str) -> dict[str, Any]:
            rt = await holder.get()
            cancelled = await rt.cancel(run_id)
            return {"run_id": run_id, "cancelled": cancelled}


def _register_workflow_resource(mcp: Any, entry: _WorkflowEntry) -> None:
    """Expose workflow metadata as an MCP resource for discovery."""
    slug = entry.slug
    iface = entry.interface

    @mcp.resource(f"workflow://{slug}/metadata")
    def metadata_resource() -> str:
        return json.dumps({
            "name": iface.name,
            "description": iface.description,
            "input_schema": iface.input_schema,
            "output_schema": iface.output_schema,
            "has_human_nodes": iface.has_human_nodes,
            "estimated_duration": iface.estimated_duration,
        }, indent=2)


# ---------------------------------------------------------------------------
# Convenience runners
# ---------------------------------------------------------------------------

def run_mcp_stdio(
    workflows: list[tuple[Graph, str | None]],
    *,
    engine_config: EngineConfig | None = None,
    server_name: str = "dan-publish",
    human_timeout: float = 300.0,
    server_url: str | None = None,
    force_local: bool = False,
) -> None:
    """Build and run an MCP server over stdio transport (blocking)."""
    mcp = build_mcp_server(
        workflows,
        engine_config=engine_config,
        server_name=server_name,
        human_timeout=human_timeout,
        server_url=server_url,
        force_local=force_local,
    )
    mcp.run(transport="stdio")


def run_mcp_http(
    workflows: list[tuple[Graph, str | None]],
    *,
    engine_config: EngineConfig | None = None,
    server_name: str = "dan-publish",
    host: str = "0.0.0.0",
    port: int = 8001,
    human_timeout: float = 300.0,
    server_url: str | None = None,
    force_local: bool = False,
) -> None:
    """Build and run an MCP server over streamable HTTP transport (blocking)."""
    mcp = build_mcp_server(
        workflows,
        engine_config=engine_config,
        server_name=server_name,
        human_timeout=human_timeout,
        server_url=server_url,
        force_local=force_local,
    )
    mcp.run(transport="streamable-http", host=host, port=port)


# ---------------------------------------------------------------------------
# Graph loading helpers
# ---------------------------------------------------------------------------

def load_workflows_from_path(path: str | Path) -> list[tuple[Graph, str | None]]:
    """Load workflow graph(s) from a file or directory.

    Supports ``.json``, ``.md``, and ``.py`` files, and directories
    containing workflow files.
    Returns a list of ``(Graph, name_override)`` tuples.
    """
    from dan.utils.workflow_loader import (
        load_graph,
        load_workflows_from_directory,
        WorkflowLoadError,
    )

    p = Path(path)

    if p.is_dir():
        return load_workflows_from_directory(p)

    try:
        graph = load_graph(p)
        return [(graph, None)]
    except WorkflowLoadError as exc:
        raise ValueError(str(exc)) from exc
