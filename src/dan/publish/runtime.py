"""Unified execution backend for published workflows.

Two implementations:
- ``GatewayRuntime`` — routes execution through dan-serve gateway (primary).
- ``LocalRuntime``   — direct Engine execution with in-process session store (fallback).

Use ``create_publish_runtime()`` to auto-detect and return the appropriate one.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import time
import uuid
from typing import Any, AsyncGenerator

from dan.models.graph import Graph

logger = logging.getLogger(__name__)


class PublishRuntime(abc.ABC):
    """Unified execution backend for published workflows.

    Both MCP tools and HTTP endpoints delegate to this interface,
    eliminating branching on ``is_server_mode`` at every call site.

    Return-dict contract for ``run_sync``:

    - ``session_id: str`` — identifier for status polling / input submission
    - ``status: str`` — ``"completed"`` or ``"failed"``
    - ``output: dict`` — workflow outputs (present when completed)
    - ``success: bool`` — whether the run succeeded (present when completed)
    - ``error: str`` — error message (present when failed)

    Note: ``graph`` is used by ``LocalRuntime`` for direct execution;
    ``GatewayRuntime`` dispatches by ``workflow_id`` and ignores ``graph``
    (the server holds the authoritative graph).
    """

    @property
    @abc.abstractmethod
    def mode(self) -> str:
        """Return ``"gateway"`` or ``"local"``."""

    @abc.abstractmethod
    async def run_sync(
        self,
        workflow_id: str,
        graph: Graph,
        inputs: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Execute *graph* and block until complete. Returns result dict."""

    @abc.abstractmethod
    async def run_async(
        self,
        workflow_id: str,
        graph: Graph,
        inputs: dict[str, Any] | None,
    ) -> str:
        """Start execution and return a session/run ID immediately."""

    @abc.abstractmethod
    async def get_status(self, session_id: str) -> dict[str, Any] | None:
        """Return session status dict, or ``None`` if not found."""

    @abc.abstractmethod
    async def submit_input(
        self, session_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Submit human input. Returns updated session dict or ``None``."""

    @abc.abstractmethod
    async def cancel(self, run_id: str) -> bool:
        """Cancel a running workflow. Returns whether cancellation succeeded."""

    @abc.abstractmethod
    async def subscribe_events(
        self, session_id: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield session-level events for *session_id*."""
        yield  # type: ignore[misc]

    @abc.abstractmethod
    async def close(self) -> None:
        """Release resources."""


# ---------------------------------------------------------------------------
# GatewayRuntime
# ---------------------------------------------------------------------------

class GatewayRuntime(PublishRuntime):
    """Routes through dan-serve gateway via :class:`DanClient`.

    ``graph`` is not used — the server holds the authoritative graph
    and dispatch is by ``workflow_id`` only.
    """

    def __init__(
        self,
        server_url: str | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self._server_url = server_url
        self._client: Any | None = client
        self._surface_id: str = f"publish-{uuid.uuid4().hex[:8]}"
        self._init_lock = asyncio.Lock()

    @property
    def mode(self) -> str:
        return "gateway"

    @property
    def surface_id(self) -> str:
        """Stable surface identifier for this runtime instance."""
        return self._surface_id

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._init_lock:
            if self._client is None:
                from dan.client import DanClient

                self._client = DanClient(base_url=self._server_url)
                try:
                    await self._client.register_surface(
                        surface_id=self._surface_id,
                        surface_type="mcp",
                    )
                except Exception:
                    logger.debug("Surface registration failed (non-fatal)", exc_info=True)
            return self._client

    # -- dispatch -----------------------------------------------------------

    async def run_sync(
        self,
        workflow_id: str,
        graph: Graph,
        inputs: dict[str, Any] | None,
    ) -> dict[str, Any]:
        client = await self._ensure_client()
        result = await client.dispatch(
            workflow_id=workflow_id,
            inputs=inputs,
            surface_id=self._surface_id,
        )
        run_id = result.run_id

        final_data: dict[str, Any] = {}
        completed = False
        async for event in client.subscribe_run(run_id):
            etype = event.get("event_type", "")
            if etype == "run_completed":
                final_data = event.get("data", {})
                completed = True
                break
            elif etype == "run_failed":
                error = (event.get("data") or {}).get("error", "Run failed")
                raise RuntimeError(f"Workflow failed: {error}")
            elif etype == "run_cancelled":
                raise RuntimeError("Workflow was cancelled")

        if not completed:
            raise RuntimeError(
                f"Event stream for run {run_id} ended without a terminal event"
            )

        return {
            "session_id": run_id,
            "status": "completed",
            "output": final_data,
            "success": True,
        }

    async def run_async(
        self,
        workflow_id: str,
        graph: Graph,
        inputs: dict[str, Any] | None,
    ) -> str:
        client = await self._ensure_client()
        result = await client.dispatch(
            workflow_id=workflow_id,
            inputs=inputs,
            surface_id=self._surface_id,
        )
        return result.run_id

    # -- session operations -------------------------------------------------

    async def get_status(self, session_id: str) -> dict[str, Any] | None:
        client = await self._ensure_client()
        status = await client.get_run_status(session_id)
        pending = await client.get_pending_inputs()
        run_pending = [p for p in pending if p.run_id == session_id]

        result: dict[str, Any] = {
            "session_id": session_id,
            "status": status.get("status", "unknown"),
        }
        if run_pending:
            first = run_pending[0]
            result["pending_prompt"] = {
                "request_id": first.request_id,
                "node_id": first.node_id,
                "prompt": first.prompt,
            }
            if hasattr(first, "input_schema"):
                result["pending_prompt"]["input_schema"] = first.input_schema
        return result

    async def submit_input(
        self, session_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        client = await self._ensure_client()
        pending = await client.get_pending_inputs()
        run_pending = [p for p in pending if p.run_id == session_id]
        if not run_pending:
            return None
        request_id = run_pending[0].request_id
        ok = await client.submit_human_input(session_id, request_id, data)
        if not ok:
            return None
        return {"session_id": session_id, "status": "input_submitted"}

    async def cancel(self, run_id: str) -> bool:
        client = await self._ensure_client()
        return await client.cancel_run(run_id)

    async def subscribe_events(
        self, session_id: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        client = await self._ensure_client()
        async for event in client.subscribe_run(session_id):
            etype = event.get("event_type", "")
            mapped = _map_engine_event(etype, event)
            if mapped is not None:
                yield mapped

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


# ---------------------------------------------------------------------------
# LocalRuntime
# ---------------------------------------------------------------------------

class LocalRuntime(PublishRuntime):
    """Direct Engine execution with in-process session management."""

    def __init__(
        self,
        engine_config: Any | None = None,
        human_timeout: float = 300.0,
        model_gateway: Any | None = None,
    ) -> None:
        from dan.engine.executor import EngineConfig
        from dan.publish.session import PublishSessionStore

        self._config = engine_config or EngineConfig()
        self._human_timeout = human_timeout
        self._model_gateway = model_gateway
        self._store = PublishSessionStore()
        self._tasks: dict[str, asyncio.Task] = {}

    @property
    def mode(self) -> str:
        return "local"

    @property
    def session_store(self) -> Any:
        """Expose session store for SSE/WebSocket endpoints that need direct access."""
        return self._store

    def _make_engine(self, *, human_renderer: Any | None = None) -> Any:
        from dan.engine import Engine

        return Engine(
            config=self._config,
            human_renderer=human_renderer,
            model_gateway=self._model_gateway,
        )

    # -- dispatch -----------------------------------------------------------

    async def run_sync(
        self,
        workflow_id: str,
        graph: Graph,
        inputs: dict[str, Any] | None,
    ) -> dict[str, Any]:
        session = await self._store.create(workflow_id)
        engine = self._make_engine()

        try:
            result = await engine.run(graph, inputs=inputs or None)
            outputs = result.outputs or {}
            await self._store.set_result(session.session_id, outputs)
            return {
                "session_id": session.session_id,
                "status": "completed",
                "output": outputs,
                "success": result.success,
            }
        except Exception as exc:
            await self._store.set_error(session.session_id, str(exc))
            return {
                "session_id": session.session_id,
                "status": "failed",
                "error": str(exc),
            }

    async def run_async(
        self,
        workflow_id: str,
        graph: Graph,
        inputs: dict[str, Any] | None,
    ) -> str:
        from dan.publish.session import PublishedHumanRenderer

        session = await self._store.create(workflow_id)
        renderer = PublishedHumanRenderer(self._store, timeout=self._human_timeout)
        renderer.active_session_id = session.session_id

        engine = self._make_engine(human_renderer=renderer)

        task = asyncio.create_task(
            self._background_run(engine, graph, inputs, session.session_id)
        )
        self._tasks[session.session_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(session.session_id, None))

        return session.session_id

    async def _background_run(
        self, engine: Any, graph: Graph, inputs: dict[str, Any] | None, session_id: str
    ) -> None:
        try:
            result = await engine.run(graph, inputs=inputs or None)
            outputs = result.outputs or {}
            await self._store.set_result(session_id, outputs)
        except Exception as exc:
            logger.exception("Background workflow execution failed: %s", exc)
            await self._store.set_error(session_id, str(exc))

    # -- session operations -------------------------------------------------

    async def get_status(self, session_id: str) -> dict[str, Any] | None:
        session = await self._store.get(session_id)
        if session is None:
            return None
        return session.to_dict()

    async def submit_input(
        self, session_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        from dan.publish.session import submit_human_input

        session = await submit_human_input(self._store, session_id, data)
        if session is None:
            return None
        return session.to_dict()

    async def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        await self._store.set_error(run_id, "Cancelled by user")
        return True

    async def subscribe_events(
        self, session_id: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self._store.subscribe_events(session_id):
            yield event

    async def close(self) -> None:
        tasks = [t for t in self._tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

async def create_publish_runtime(
    *,
    server_url: str | None = None,
    force_local: bool = False,
    engine_config: Any | None = None,
    human_timeout: float = 300.0,
    model_gateway: Any | None = None,
) -> PublishRuntime:
    """Detect dan-serve availability and return the appropriate runtime.

    Tries the gateway first (unless *force_local*).  Falls back to local
    engine execution if the server is unreachable.
    """
    if not force_local:
        from dan.client import DanClient

        client = DanClient(base_url=server_url)
        try:
            if await client.is_server_available():
                runtime = GatewayRuntime(server_url=server_url, client=client)
                try:
                    await client.register_surface(
                        surface_id=runtime.surface_id,
                        surface_type="mcp",
                    )
                except Exception:
                    pass
                logger.info("Publish connected to dan-serve (gateway mode)")
                return runtime
        except Exception:
            pass
        await client.close()

    logger.info("Publish running in local mode")
    return LocalRuntime(
        engine_config=engine_config,
        human_timeout=human_timeout,
        model_gateway=model_gateway,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _map_engine_event(etype: str, event: dict[str, Any]) -> dict[str, Any] | None:
    """Map engine event types to session-level events for SSE/WebSocket consumers."""
    data = event.get("data", {})
    ts = event.get("timestamp", time.time())

    if etype == "run_completed":
        return {"type": "session_completed", "result": data, "timestamp": ts}
    elif etype == "run_failed":
        return {"type": "session_failed", "error": data.get("error", "unknown"), "timestamp": ts}
    elif etype == "run_cancelled":
        return {"type": "session_failed", "error": "cancelled", "timestamp": ts}
    elif etype == "human_input_needed":
        return {"type": "status_changed", "status": "awaiting_input", "timestamp": ts, **data}
    elif etype == "human_input_resolved":
        return {"type": "status_changed", "status": "running", "timestamp": ts}
    elif etype in ("node_started", "node_completed", "node_failed", "node_skipped"):
        return {"type": "progress", "event_type": etype, "timestamp": ts, **data}
    elif etype == "run_started":
        return {"type": "status_changed", "status": "running", "timestamp": ts}
    return None
