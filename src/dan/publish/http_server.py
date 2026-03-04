"""HTTP REST server for published DAN workflows.

Provides a FastAPI router that can be mounted on ``dan-serve`` or run
standalone.  Routes are relative; mount with prefix="/api/published" to get:

    GET  /                              — list published workflows
    GET  /{workflow_id}/schema          — WorkflowInterface JSON
    POST /{workflow_id}/run             — sync execution
    POST /{workflow_id}/run-async       — async (returns session_id)
    GET  /{workflow_id}/runs/{sid}      — poll session status
    POST /{workflow_id}/runs/{sid}/submit-input
    GET  /{workflow_id}/events          — SSE event stream
    WS   /{workflow_id}/ws              — bidirectional WebSocket
    GET  /health                        — health check (under same prefix)
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from dan.engine.executor import EngineConfig
from dan.models.graph import Graph
from dan.publish.schema import slugify
from dan.publish.session import (
    PublishSessionStore,
    PublishedHumanRenderer,
    SessionStatus,
    submit_human_input,
)
from dan.utils.workflow_interface import WorkflowInterface, derive_workflow_interface

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate limiter (sliding window)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple in-memory sliding-window rate limiter."""

    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: int = 60,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._windows: dict[str, collections.deque[float]] = {}

    def check(self, workflow_id: str) -> bool:
        """Return ``True`` if the request is allowed (under limit)."""
        now = time.monotonic()
        dq = self._windows.setdefault(workflow_id, collections.deque())
        cutoff = now - self.window_seconds
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= self.max_requests:
            return False
        dq.append(now)
        return True


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    inputs: dict[str, Any] = {}


class RunAsyncResponse(BaseModel):
    session_id: str
    status: str


class SubmitInputRequest(BaseModel):
    data: dict[str, Any] = {}


class WorkflowSummary(BaseModel):
    workflow_id: str
    name: str
    description: str
    has_human_nodes: bool


class HealthResponse(BaseModel):
    status: str = "ok"
    workflow_count: int = 0
    uptime_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Published workflow registry
# ---------------------------------------------------------------------------

class _PublishedWorkflow:
    __slots__ = ("graph", "interface", "slug", "api_key", "rate_limit")

    def __init__(
        self,
        graph: Graph,
        interface: WorkflowInterface,
        slug: str,
        api_key: str | None = None,
        rate_limit: int | None = None,
    ) -> None:
        self.graph = graph
        self.interface = interface
        self.slug = slug
        self.api_key = api_key
        self.rate_limit = rate_limit


class PublishRegistry:
    """Holds the set of published workflows and shared session store."""

    def __init__(self, engine_config: EngineConfig | None = None) -> None:
        self.workflows: dict[str, _PublishedWorkflow] = {}
        self.session_store = PublishSessionStore()
        self.engine_config = engine_config or EngineConfig()
        self.human_timeout = 300.0
        self.rate_limiter = RateLimiter()
        self._per_workflow_limiters: dict[str, RateLimiter] = {}
        self._start_time = time.time()

    def register(
        self,
        graph: Graph,
        *,
        name_override: str | None = None,
        api_key: str | None = None,
        rate_limit: int | None = None,
    ) -> str:
        iface = derive_workflow_interface(graph)
        if name_override:
            iface.name = name_override
        slug = slugify(iface.name)
        self.workflows[slug] = _PublishedWorkflow(
            graph, iface, slug, api_key, rate_limit,
        )
        logger.info("Registered published workflow: %s", slug)
        return slug

    def unregister(self, workflow_id: str) -> bool:
        if self.workflows.pop(workflow_id, None) is not None:
            self._per_workflow_limiters.pop(workflow_id, None)
            return True
        return False

    def is_published(self, workflow_id: str) -> bool:
        return workflow_id in self.workflows

    def get(self, workflow_id: str) -> _PublishedWorkflow | None:
        return self.workflows.get(workflow_id)

    def list_all(self) -> list[WorkflowSummary]:
        return [
            WorkflowSummary(
                workflow_id=w.slug,
                name=w.interface.name,
                description=w.interface.description,
                has_human_nodes=w.interface.has_human_nodes,
            )
            for w in self.workflows.values()
        ]

    @property
    def uptime(self) -> float:
        return time.time() - self._start_time


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def _check_auth(
    registry: PublishRegistry,
    workflow_id: str,
    request: Request,
    api_key: str | None = None,
) -> None:
    wf = registry.get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not published")
    if wf.api_key:
        provided = (
            api_key
            or request.headers.get("x-api-key")
            or request.query_params.get("api_key")
        )
        if provided != wf.api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def _check_rate_limit(registry: PublishRegistry, workflow_id: str) -> None:
    """Enforce per-workflow rate limiting if configured."""
    wf = registry.get(workflow_id)
    if wf is not None and wf.rate_limit is not None:
        if workflow_id not in registry._per_workflow_limiters:
            registry._per_workflow_limiters[workflow_id] = RateLimiter(
                max_requests=wf.rate_limit, window_seconds=60,
            )
        if not registry._per_workflow_limiters[workflow_id].check(workflow_id):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
    elif not registry.rate_limiter.check(workflow_id):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


def create_publish_router(registry: PublishRegistry) -> APIRouter:
    """Create a FastAPI router for the publish endpoints."""
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    async def health_check():
        return HealthResponse(
            status="ok",
            workflow_count=len(registry.workflows),
            uptime_seconds=registry.uptime,
        )

    @router.get("/", response_model=list[WorkflowSummary])
    async def list_workflows():
        return registry.list_all()

    @router.get("/{workflow_id}/schema")
    async def get_schema(workflow_id: str, request: Request):
        _check_auth(registry, workflow_id, request)
        wf = registry.get(workflow_id)
        assert wf is not None
        return {
            "name": wf.interface.name,
            "description": wf.interface.description,
            "input_schema": wf.interface.input_schema,
            "output_schema": wf.interface.output_schema,
            "has_human_nodes": wf.interface.has_human_nodes,
        }

    @router.post("/{workflow_id}/run")
    async def run_sync(workflow_id: str, body: RunRequest, request: Request):
        """Synchronous execution — blocks until complete."""
        _check_auth(registry, workflow_id, request)
        _check_rate_limit(registry, workflow_id)
        wf = registry.get(workflow_id)
        assert wf is not None

        from dan.engine import Engine, AutoRenderer

        renderer = AutoRenderer()
        engine = Engine(config=registry.engine_config, human_renderer=renderer)

        try:
            result = await engine.run(wf.graph, inputs=body.inputs or None)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        return {
            "success": result.success,
            "outputs": result.outputs,
            "errors": result.errors,
            "node_statuses": result.node_statuses,
        }

    @router.post("/{workflow_id}/run-async", response_model=RunAsyncResponse)
    async def run_async(workflow_id: str, body: RunRequest, request: Request):
        """Start async execution, return session_id for polling."""
        _check_auth(registry, workflow_id, request)
        _check_rate_limit(registry, workflow_id)
        wf = registry.get(workflow_id)
        assert wf is not None

        session = await registry.session_store.create(workflow_id)
        renderer = PublishedHumanRenderer(
            registry.session_store, timeout=registry.human_timeout,
        )
        renderer.active_session_id = session.session_id

        from dan.engine import Engine

        engine = Engine(config=registry.engine_config, human_renderer=renderer)
        asyncio.create_task(
            _background_run(engine, wf.graph, body.inputs, registry.session_store, session.session_id)
        )

        return RunAsyncResponse(
            session_id=session.session_id,
            status=SessionStatus.RUNNING.value,
        )

    @router.get("/{workflow_id}/runs/{session_id}")
    async def get_session(workflow_id: str, session_id: str, request: Request):
        _check_auth(registry, workflow_id, request)
        session = await registry.session_store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.workflow_id != workflow_id:
            raise HTTPException(status_code=404, detail="Session not found for this workflow")
        return session.to_dict()

    @router.post("/{workflow_id}/runs/{session_id}/submit-input")
    async def submit_input(
        workflow_id: str,
        session_id: str,
        body: SubmitInputRequest,
        request: Request,
    ):
        _check_auth(registry, workflow_id, request)
        session = await submit_human_input(registry.session_store, session_id, body.data)
        if session is None:
            raise HTTPException(
                status_code=400,
                detail="Session not found or not awaiting input",
            )
        return session.to_dict()

    # -- SSE event stream ---------------------------------------------------

    @router.get("/{workflow_id}/events")
    async def sse_events(
        workflow_id: str,
        session_id: str = Query(...),
        request: Request = None,
    ):
        """Server-Sent Events stream for a published workflow session."""
        _check_auth(registry, workflow_id, request)
        session = await registry.session_store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.workflow_id != workflow_id:
            raise HTTPException(status_code=404, detail="Session not found for this workflow")

        async def _event_generator():
            async for event in registry.session_store.subscribe_events(session_id):
                payload = json.dumps(event)
                yield f"data: {payload}\n\n"

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # -- WebSocket bidirectional --------------------------------------------

    @router.websocket("/{workflow_id}/ws")
    async def published_ws(websocket: WebSocket, workflow_id: str):
        """Bidirectional WebSocket for published workflow interaction."""
        # Auth check before accepting
        wf = registry.get(workflow_id)
        if wf is None:
            await websocket.close(code=4004, reason="Workflow not found")
            return
        if wf.api_key:
            provided = websocket.query_params.get("api_key") or websocket.headers.get("x-api-key")
            if provided != wf.api_key:
                await websocket.close(code=4001, reason="Unauthorized")
                return
        await websocket.accept()

        session_id: str | None = None
        try:
            while True:
                raw = await websocket.receive_json()
                msg_type = raw.get("type", "")

                if msg_type == "start":
                    wf = registry.get(workflow_id)
                    if wf is None:
                        await websocket.send_json({"type": "error", "detail": "Workflow not found"})
                        continue

                    try:
                        _check_rate_limit(registry, workflow_id)
                    except HTTPException:
                        await websocket.send_json({"type": "error", "detail": "Rate limit exceeded"})
                        continue

                    session = await registry.session_store.create(workflow_id)
                    session_id = session.session_id
                    renderer = PublishedHumanRenderer(
                        registry.session_store, timeout=registry.human_timeout,
                    )
                    renderer.active_session_id = session_id

                    from dan.engine import Engine

                    engine = Engine(config=registry.engine_config, human_renderer=renderer)
                    inputs = raw.get("inputs", {})

                    async def _run_and_stream(sid: str):
                        try:
                            result = await engine.run(wf.graph, inputs=inputs or None)
                            await registry.session_store.set_result(sid, result.outputs or {})
                        except Exception as exc:
                            logger.exception("Published WS workflow failed: %s", exc)
                            await registry.session_store.set_error(sid, str(exc))

                    run_task = asyncio.create_task(_run_and_stream(session_id))

                    async def _forward_events(sid: str):
                        try:
                            async for event in registry.session_store.subscribe_events(sid):
                                await websocket.send_json({"type": "event", **event})
                        except Exception:
                            pass

                    forward_task = asyncio.create_task(_forward_events(session_id))

                    await websocket.send_json({
                        "type": "session_started",
                        "session_id": session_id,
                    })

                elif msg_type == "submit_input":
                    sid = raw.get("session_id") or session_id
                    if sid is None:
                        await websocket.send_json({"type": "error", "detail": "No active session"})
                        continue
                    data = raw.get("data", {})
                    result = await submit_human_input(registry.session_store, sid, data)
                    if result is None:
                        await websocket.send_json({"type": "error", "detail": "Not awaiting input"})
                    else:
                        await websocket.send_json({"type": "input_accepted", "session_id": sid})

                else:
                    await websocket.send_json({"type": "error", "detail": f"Unknown message type: {msg_type}"})

        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("Published WebSocket error", exc_info=True)

    return router


async def _background_run(
    engine: Any,
    graph: Graph,
    inputs: dict[str, Any],
    store: PublishSessionStore,
    session_id: str,
) -> None:
    try:
        result = await engine.run(graph, inputs=inputs or None)
        await store.set_result(session_id, result.outputs or {})
    except Exception as exc:
        logger.exception("Published workflow execution failed: %s", exc)
        await store.set_error(session_id, str(exc))


# ---------------------------------------------------------------------------
# Standalone app factory
# ---------------------------------------------------------------------------

def create_publish_app(
    workflows: list[tuple[Graph, str | None]],
    *,
    engine_config: EngineConfig | None = None,
    global_api_key: str | None = None,
    human_timeout: float = 300.0,
) -> FastAPI:
    """Create a standalone FastAPI app for published workflows."""
    from fastapi.middleware.cors import CORSMiddleware

    registry = PublishRegistry(engine_config)
    registry.human_timeout = human_timeout

    for graph, name_override in workflows:
        registry.register(graph, name_override=name_override, api_key=global_api_key)

    app = FastAPI(title="DAN Published Workflows", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    router = create_publish_router(registry)
    app.include_router(router, prefix="/api/published")

    @app.get("/health", response_model=HealthResponse)
    async def root_health():
        return HealthResponse(
            status="ok",
            workflow_count=len(registry.workflows),
            uptime_seconds=registry.uptime,
        )

    return app
