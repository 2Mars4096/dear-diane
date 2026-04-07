"""Block package CRUD + export endpoints."""

from __future__ import annotations

import os
import shutil
import tempfile
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from dan.server.paths import resolve_workspace_root
from dan.server.routers.dependencies import get_graph_store, get_block_registry

logger = logging.getLogger(__name__)

router = APIRouter()


def _cleanup_export_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


@router.get("/api/blocks")
async def list_blocks(request: Request):
    registry = get_block_registry(request)
    registry.scan()
    blocks = registry.list_blocks()
    return [
        {
            "name": b.name,
            "version": b.version,
            "block_type": b.block_type,
            "description": b.metadata.description if b.metadata else "",
            "tags": b.metadata.tags if b.metadata else [],
            "input_schema": b.metadata.input_schema if b.metadata else {},
            "output_schema": b.metadata.output_schema if b.metadata else {},
            "install_path": str(b.install_path),
        }
        for b in blocks
    ]


@router.get("/api/blocks/{name}")
async def get_block_info(name: str, request: Request):
    registry = get_block_registry(request)
    block = registry.get_block(name)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block '{name}' not found")

    result: dict[str, Any] = {
        "name": block.name,
        "version": block.version,
        "block_type": block.block_type,
        "install_path": str(block.install_path),
        "metadata": block.metadata.model_dump() if block.metadata else {},
    }

    readme_path = block.install_path / "README.md"
    if readme_path.exists():
        result["readme"] = readme_path.read_text(encoding="utf-8")

    return result


@router.post("/api/blocks/import")
async def import_block_endpoint(request: Request, body: dict[str, Any]):
    from dan.blocks import import_block

    workspace_root = resolve_workspace_root()
    source = body.get("path")
    if not source:
        raise HTTPException(status_code=400, detail="Provide {\"path\": \"...\"} pointing to a tarball, directory, or URL")

    try:
        installed = import_block(source, workspace=Path(workspace_root))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    registry = get_block_registry(request)
    registry.scan()

    return {
        "status": "imported",
        "name": installed.name,
        "version": installed.version,
        "block_type": installed.block_type,
        "install_path": str(installed.install_path),
    }


@router.post("/api/blocks/export/{graph_id}")
async def export_block_endpoint(
    graph_id: str,
    request: Request,
    name: str = "",
    version: str = "0.1.0",
):
    from dan.models.graph import Graph
    from dan.blocks import export_workflow_block, pack_block

    gs = get_graph_store(request)
    graph_data = gs.get_graph(graph_id)
    if graph_data is None:
        raise HTTPException(status_code=404, detail="Graph not found")

    try:
        graph = Graph.model_validate(graph_data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid graph: {exc}")

    output_dir = Path(tempfile.mkdtemp(prefix="dan-block-export-"))
    try:
        block_dir = export_workflow_block(graph, output_dir, name=name, version=version)
        tarball = pack_block(block_dir)
    except Exception as exc:
        shutil.rmtree(str(output_dir), ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    from starlette.background import BackgroundTask
    from starlette.responses import FileResponse

    return FileResponse(
        str(tarball),
        media_type="application/gzip",
        filename=tarball.name,
        background=BackgroundTask(_cleanup_export_dir, str(output_dir)),
    )


@router.post("/api/blocks/export/{graph_id}/{node_id}")
async def export_composite_block_endpoint(
    graph_id: str,
    node_id: str,
    request: Request,
    name: str = "",
    version: str = "0.1.0",
):
    from dan.models.graph import Graph
    from dan.blocks import export_composite_block, pack_block

    gs = get_graph_store(request)
    graph_data = gs.get_graph(graph_id)
    if graph_data is None:
        raise HTTPException(status_code=404, detail="Graph not found")

    try:
        graph = Graph.model_validate(graph_data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid graph: {exc}")

    output_dir = Path(tempfile.mkdtemp(prefix="dan-block-export-"))
    try:
        block_dir = export_composite_block(graph, node_id, output_dir, name=name, version=version)
        tarball = pack_block(block_dir)
    except Exception as exc:
        shutil.rmtree(str(output_dir), ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    from starlette.background import BackgroundTask
    from starlette.responses import FileResponse

    return FileResponse(
        str(tarball),
        media_type="application/gzip",
        filename=tarball.name,
        background=BackgroundTask(_cleanup_export_dir, str(output_dir)),
    )


@router.delete("/api/blocks/{name}/{version}")
async def remove_block(name: str, version: str, request: Request):
    registry = get_block_registry(request)
    removed = registry.remove_block(name, version)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Block '{name}@{version}' not found")
    return {"status": "removed", "name": name, "version": version}
