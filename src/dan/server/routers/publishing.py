"""Publish/unpublish/MCP-config/status endpoints."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from dan.server.routers.dependencies import (
    get_graph_store,
    get_publish_registry,
    get_graphs_dir,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/graphs/{graph_id}/publish")
async def publish_graph(graph_id: str, body: dict[str, Any] | None = None):
    from dan.models.graph import Graph

    registry = get_publish_registry()
    gs = get_graph_store()
    graphs_dir = get_graphs_dir()

    graph_data = gs.get_graph(graph_id)
    if graph_data is None:
        raise HTTPException(status_code=404, detail="Graph not found")

    body = body or {}
    try:
        graph = Graph.model_validate(graph_data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid graph: {exc}")

    api_key = body.get("api_key")
    rate_limit = body.get("rate_limit")
    slug = registry.register(graph, api_key=api_key, rate_limit=rate_limit)

    pub_config = {
        "enabled": True,
        "api_key": api_key,
        "rate_limit": rate_limit,
    }
    pub_path = Path(graphs_dir) / f"{graph_id}.publish.json"
    pub_path.write_text(json.dumps(pub_config, indent=2), encoding="utf-8")

    return {"status": "published", "workflow_id": slug, "graph_id": graph_id}


@router.post("/api/graphs/{graph_id}/unpublish")
async def unpublish_graph(graph_id: str):
    from dan.models.graph import Graph

    registry = get_publish_registry()
    gs = get_graph_store()
    graphs_dir = get_graphs_dir()

    graph_data = gs.get_graph(graph_id)
    if graph_data is None:
        raise HTTPException(status_code=404, detail="Graph not found")

    try:
        graph = Graph.model_validate(graph_data)
        from dan.publish.schema import slugify as _slugify
        from dan.utils.workflow_interface import derive_workflow_interface as _derive
        iface = _derive(graph)
        slug = _slugify(iface.name)
    except Exception:
        slug = graph_id

    registry.unregister(slug)

    pub_path = Path(graphs_dir) / f"{graph_id}.publish.json"
    if pub_path.exists():
        pub_path.unlink()

    return {"status": "unpublished", "graph_id": graph_id}


@router.get("/api/graphs/{graph_id}/mcp-config")
async def get_mcp_config(graph_id: str):
    from dan.models.graph import Graph

    gs = get_graph_store()
    graphs_dir = get_graphs_dir()

    graph_data = gs.get_graph(graph_id)
    if graph_data is None:
        raise HTTPException(status_code=404, detail="Graph not found")

    try:
        graph = Graph.model_validate(graph_data)
        from dan.publish.portal import generate_mcp_config

        workflow_path = str(Path(graphs_dir) / f"{graph_id}.json")
        config = generate_mcp_config(
            workflow_path,
            name=graph.metadata.name if graph.metadata else None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"config": config}


@router.get("/api/graphs/{graph_id}/publish-status")
async def publish_status(graph_id: str):
    from dan.models.graph import Graph

    registry = get_publish_registry()
    gs = get_graph_store()
    graphs_dir = get_graphs_dir()

    graph_data = gs.get_graph(graph_id)
    if graph_data is None:
        raise HTTPException(status_code=404, detail="Graph not found")

    try:
        graph = Graph.model_validate(graph_data)
        from dan.publish.schema import slugify as _slugify
        from dan.utils.workflow_interface import derive_workflow_interface as _derive
        iface = _derive(graph)
        slug = _slugify(iface.name)
    except Exception:
        slug = graph_id

    published = registry.is_published(slug)
    result: dict[str, Any] = {"graph_id": graph_id, "published": published, "workflow_id": slug if published else None}

    pub_path = Path(graphs_dir) / f"{graph_id}.publish.json"
    if pub_path.exists():
        try:
            result["config"] = json.loads(pub_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    return result
