"""MCP server generation for published DAN workflows.

Generates and runs an MCP server that exposes each workflow as a set of
MCP tools.  Requires the ``mcp`` optional dependency (``pip install dan[mcp]``).

Each workflow gets:
  - ``{name}_run``          — execute the workflow (blocks until done or awaiting input)
  - ``{name}_status``       — get session status  (only if has_human_nodes)
  - ``{name}_submit_input`` — submit HumanNode input (only if has_human_nodes)

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
from dan.publish.schema import slugify
from dan.publish.session import (
    PublishSession,
    PublishSessionStore,
    PublishedHumanRenderer,
    SessionStatus,
    submit_human_input,
)
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

    Returns
    -------
    A ``FastMCP`` instance ready to ``.run()``.
    """
    McpClass = _require_mcp()
    mcp = McpClass(server_name)

    config = engine_config or EngineConfig()
    session_store = PublishSessionStore()

    entries: list[_WorkflowEntry] = []
    for graph, name_override in workflows:
        iface = derive_workflow_interface(graph)
        if name_override:
            iface.name = name_override
        slug = slugify(iface.name)
        entries.append(_WorkflowEntry(graph, iface, slug))

    for entry in entries:
        _register_workflow_tools(mcp, entry, session_store, config, human_timeout)
        _register_workflow_resource(mcp, entry)

    return mcp


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def _register_workflow_tools(
    mcp: Any,
    entry: _WorkflowEntry,
    store: PublishSessionStore,
    config: EngineConfig,
    human_timeout: float,
) -> None:
    """Register MCP tools for a single workflow."""
    slug = entry.slug
    iface = entry.interface
    graph = entry.graph

    run_desc = iface.description or f"Run the {iface.name} workflow"

    @mcp.tool(name=f"{slug}_run", description=run_desc)
    async def run_tool(**kwargs: Any) -> dict[str, Any]:
        return await _execute_workflow(
            graph=graph,
            inputs=kwargs,
            store=store,
            config=config,
            has_human=iface.has_human_nodes,
            human_timeout=human_timeout,
            workflow_id=slug,
        )

    if iface.has_human_nodes:

        @mcp.tool(
            name=f"{slug}_status",
            description=f"Get execution status for {iface.name}",
        )
        async def status_tool(session_id: str) -> dict[str, Any]:
            session = await store.get(session_id)
            if session is None:
                return {"error": f"Session '{session_id}' not found"}
            return session.to_dict()

        @mcp.tool(
            name=f"{slug}_submit_input",
            description=f"Submit human input for a pending prompt in {iface.name}",
        )
        async def submit_tool(session_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
            if data is None:
                data = {}
            session = await submit_human_input(store, session_id, data)
            if session is None:
                return {"error": f"Session '{session_id}' not found or not awaiting input"}
            return session.to_dict()


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
# Workflow execution
# ---------------------------------------------------------------------------

async def _execute_workflow(
    *,
    graph: Graph,
    inputs: dict[str, Any],
    store: PublishSessionStore,
    config: EngineConfig,
    has_human: bool,
    human_timeout: float,
    workflow_id: str,
) -> dict[str, Any]:
    """Run a workflow graph via the DAN engine.

    For workflows without HumanNodes, blocks until complete and returns
    the result directly.

    For workflows with HumanNodes, starts execution in the background
    and returns immediately with a session_id.  The caller polls
    ``status`` and uses ``submit_input`` for HumanNode interaction.
    """
    from dan.engine import Engine

    session = await store.create(workflow_id)
    renderer = PublishedHumanRenderer(store, timeout=human_timeout)
    renderer.active_session_id = session.session_id

    engine = Engine(config=config, human_renderer=renderer)

    if has_human:
        asyncio.create_task(
            _run_in_background(engine, graph, inputs, store, session.session_id)
        )
        return {
            "session_id": session.session_id,
            "status": SessionStatus.RUNNING.value,
            "message": "Execution started. Use status/submit_input tools for interaction.",
        }

    try:
        result = await engine.run(graph, inputs=inputs or None)
        outputs = result.outputs or {}
        await store.set_result(session.session_id, outputs)
        return {
            "session_id": session.session_id,
            "status": SessionStatus.COMPLETED.value,
            "output": outputs,
            "success": result.success,
        }
    except Exception as exc:
        await store.set_error(session.session_id, str(exc))
        return {
            "session_id": session.session_id,
            "status": SessionStatus.FAILED.value,
            "error": str(exc),
        }


async def _run_in_background(
    engine: Any,
    graph: Graph,
    inputs: dict[str, Any],
    store: PublishSessionStore,
    session_id: str,
) -> None:
    """Run engine in background task, updating session on completion/failure."""
    try:
        result = await engine.run(graph, inputs=inputs or None)
        outputs = result.outputs or {}
        await store.set_result(session_id, outputs)
    except Exception as exc:
        logger.exception("Background workflow execution failed: %s", exc)
        await store.set_error(session_id, str(exc))


# ---------------------------------------------------------------------------
# Convenience runners
# ---------------------------------------------------------------------------

def run_mcp_stdio(
    workflows: list[tuple[Graph, str | None]],
    *,
    engine_config: EngineConfig | None = None,
    server_name: str = "dan-publish",
    human_timeout: float = 300.0,
) -> None:
    """Build and run an MCP server over stdio transport (blocking)."""
    mcp = build_mcp_server(
        workflows,
        engine_config=engine_config,
        server_name=server_name,
        human_timeout=human_timeout,
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
) -> None:
    """Build and run an MCP server over streamable HTTP transport (blocking)."""
    mcp = build_mcp_server(
        workflows,
        engine_config=engine_config,
        server_name=server_name,
        human_timeout=human_timeout,
    )
    mcp.run(transport="streamable-http", host=host, port=port)


# ---------------------------------------------------------------------------
# Graph loading helpers
# ---------------------------------------------------------------------------

def load_workflows_from_path(path: str | Path) -> list[tuple[Graph, str | None]]:
    """Load workflow graph(s) from a file or directory.

    Supports ``.json`` files and directories containing ``.json`` files.
    Returns a list of ``(Graph, name_override)`` tuples.
    """
    p = Path(path)
    results: list[tuple[Graph, str | None]] = []

    if p.is_dir():
        for f in sorted(p.glob("*.json")):
            try:
                g = _load_single(f)
                results.append((g, None))
            except Exception:
                logger.warning("Skipping invalid graph file: %s", f)
    elif p.suffix == ".json":
        results.append((_load_single(p), None))
    elif p.suffix in (".md",):
        from dan.loader import load
        results.append((load(p), None))
    elif p.suffix == ".py":
        from dan.cli.run import load_graph_from_python
        results.append((load_graph_from_python(p), None))
    else:
        raise ValueError(f"Unsupported workflow source: {p}")

    return results


def _load_single(path: Path) -> Graph:
    with open(path) as f:
        data = json.load(f)
    return Graph.model_validate(data)
