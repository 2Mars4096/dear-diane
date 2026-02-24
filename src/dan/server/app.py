"""FastAPI application — graph CRUD, run management, and WebSocket events."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dan.engine.executor import EngineConfig
from dan.models.graph import Graph
from dan.server.graph_store import GraphStore
from dan.server.run_manager import RunManager

logger = logging.getLogger(__name__)


def _require_run_manager() -> RunManager:
    if _run_manager is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    return _run_manager

_graphs_dir = os.environ.get("DAN_GRAPHS_DIR", "./graphs")
_graph_store = GraphStore(base_dir=_graphs_dir)
_run_manager: RunManager | None = None


def _get_engine_config() -> EngineConfig:
    return EngineConfig(
        llm_base_url=os.environ.get("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1"),
        llm_api_key=os.environ.get("DAN_LLM_API_KEY", os.environ.get("LLM_API_KEY", "")),
        llm_default_model=os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6"),
        checkpoint_dir=os.environ.get("DAN_CHECKPOINT_DIR", "./checkpoints"),
        checkpoint_enabled=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _run_manager
    _run_manager = RunManager(engine_config=_get_engine_config())
    yield


app = FastAPI(title="Deep Agent Network", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# Request / response schemas
# ------------------------------------------------------------------


class CreateGraphRequest(BaseModel):
    graph_id: str
    data: dict[str, Any] | None = None


class RunRequest(BaseModel):
    graph_id: str
    inputs: dict[str, Any] | None = None
    run_id: str | None = None


class ResumeRequest(BaseModel):
    graph_id: str


# ------------------------------------------------------------------
# Graph CRUD
# ------------------------------------------------------------------


@app.get("/api/graphs")
async def list_graphs():
    graphs = _graph_store.list_graphs()
    last_opened = _graph_store.get_last_opened()
    return {"graphs": graphs, "last_opened": last_opened}


@app.post("/api/graphs")
async def create_graph(req: CreateGraphRequest):
    if _graph_store.get_graph(req.graph_id) is not None:
        raise HTTPException(status_code=409, detail=f"Graph '{req.graph_id}' already exists")
    data = _graph_store.create_graph(req.graph_id, req.data)
    return {"graph_id": req.graph_id, "data": data}


@app.get("/api/graphs/{graph_id}")
async def get_graph(graph_id: str):
    data = _graph_store.get_graph(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    _graph_store.set_last_opened(graph_id)
    return {"graph_id": graph_id, "data": data}


@app.put("/api/graphs/{graph_id}")
async def update_graph(graph_id: str, body: dict[str, Any]):
    _graph_store.save_graph(graph_id, body)
    return {"graph_id": graph_id, "status": "saved"}


@app.delete("/api/graphs/{graph_id}")
async def delete_graph(graph_id: str):
    if not _graph_store.delete_graph(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    return {"graph_id": graph_id, "status": "deleted"}


# ------------------------------------------------------------------
# Run management
# ------------------------------------------------------------------


@app.post("/api/runs")
async def start_run(req: RunRequest):
    rm = _require_run_manager()
    graph = _graph_store.load_as_model(req.graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{req.graph_id}' not found")
    record = await rm.start_run(
        graph, graph_id=req.graph_id, inputs=req.inputs, run_id=req.run_id,
    )
    return {"run_id": record.run_id, "status": record.status.value}


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str, req: ResumeRequest):
    rm = _require_run_manager()
    graph = _graph_store.load_as_model(req.graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{req.graph_id}' not found")
    record = await rm.resume_run(graph, graph_id=req.graph_id, run_id=run_id)
    return {"run_id": record.run_id, "status": record.status.value}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    rm = _require_run_manager()
    record = rm.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return record.snapshot()


@app.get("/api/runs")
async def list_runs():
    rm = _require_run_manager()
    return {"runs": rm.list_runs()}


# ------------------------------------------------------------------
# WebSocket — live run events
# ------------------------------------------------------------------


@app.websocket("/api/runs/{run_id}/events")
async def run_events_ws(websocket: WebSocket, run_id: str):
    rm = _require_run_manager()
    await websocket.accept()

    queue = rm.subscribe(run_id)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket error for run %s", run_id, exc_info=True)
    finally:
        rm.unsubscribe(run_id, queue)


# ------------------------------------------------------------------
# Static file serving for the built editor (production)
# ------------------------------------------------------------------

_editor_dist = os.path.join(os.path.dirname(__file__), "..", "..", "..", "editor", "dist")
if os.path.isdir(_editor_dist):
    app.mount("/", StaticFiles(directory=_editor_dist, html=True), name="editor")
