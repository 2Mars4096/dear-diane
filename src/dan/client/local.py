"""DanClientOrLocal — transparent server/local execution wrapper.

When a dan-serve instance is reachable, routes all operations through
DanClient (server mode). When the server is unreachable, falls back
to direct engine execution (local mode).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from dan.client.client import DanClient
from dan.client.models import ActivitySnapshot, CancelResult, DispatchResult, PendingInput, RunSummary

logger = logging.getLogger(__name__)


class DanClientOrLocal:
    """Wrapper that transparently routes to server or local engine."""

    def __init__(
        self,
        server_url: str | None = None,
        force_local: bool = False,
        engine_config: Any | None = None,
        tool_registry: Any | None = None,
        human_renderer: Any | None = None,
    ) -> None:
        self._force_local = force_local
        self._engine_config = engine_config
        self._tool_registry = tool_registry
        self._human_renderer = human_renderer
        self._client: DanClient | None = None
        self._is_server_mode: bool | None = None

        if not force_local:
            self._client = DanClient(base_url=server_url)

        self._local_tasks: dict[str, asyncio.Task] = {}
        self._local_events: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._local_results: dict[str, Any] = {}

    def _cleanup_run(self, run_id: str) -> None:
        """Remove completed run state to prevent memory leaks."""
        self._local_tasks.pop(run_id, None)
        self._local_events.pop(run_id, None)
        self._local_results.pop(run_id, None)

    @property
    def is_server_mode(self) -> bool:
        if self._is_server_mode is None:
            raise RuntimeError("Call detect_mode() first")
        return self._is_server_mode

    async def detect_mode(self) -> str:
        """Detect server availability. Returns 'server' or 'local'."""
        if self._force_local:
            self._is_server_mode = False
            return "local"
        if self._client is not None:
            try:
                available = await self._client.ping()
                self._is_server_mode = available
                return "server" if available else "local"
            except Exception:
                self._is_server_mode = False
                return "local"
        self._is_server_mode = False
        return "local"

    # ── Dispatch ────────────────────────────────────────────────────

    async def dispatch(
        self,
        workflow_path: str | None = None,
        workflow_id: str | None = None,
        inputs: dict[str, Any] | None = None,
        text: str | None = None,
        surface_id: str | None = None,
        use_meta: bool = False,
    ) -> DispatchResult:
        if self._is_server_mode and self._client:
            return await self._client.dispatch(
                workflow_path=workflow_path,
                workflow_id=workflow_id,
                inputs=inputs,
                text=text,
                surface_id=surface_id,
                use_meta=use_meta,
            )
        return await self._dispatch_local(
            workflow_path=workflow_path,
            inputs=inputs,
            text=text,
        )

    async def _dispatch_local(
        self,
        workflow_path: str | None = None,
        inputs: dict[str, Any] | None = None,
        text: str | None = None,
    ) -> DispatchResult:
        """Run workflow directly using local engine."""
        run_id = f"local-{uuid.uuid4().hex[:8]}"

        if text:
            return DispatchResult(
                run_id=run_id,
                workflow_name="meta-controller",
                status="not_implemented",
            )

        event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._local_events[run_id] = event_queue

        if not workflow_path:
            raise ValueError("workflow_path required for local dispatch")

        from dan.utils.workflow_loader import load_graph

        graph = load_graph(Path(workflow_path))
        workflow_name = Path(workflow_path).stem

        cfg = self._engine_config
        if cfg is None:
            from dan.engine.executor import EngineConfig
            cfg = EngineConfig()

        async def event_cb(event: Any) -> None:
            d = dict(event.to_dict() if hasattr(event, "to_dict") else event)
            d["run_id"] = run_id
            try:
                event_queue.put_nowait(d)
            except asyncio.QueueFull:
                pass

        from dan.engine.scheduler import Engine

        engine = Engine(
            config=cfg,
            event_callback=event_cb,
            human_renderer=self._human_renderer,
        )

        async def run_task() -> None:
            try:
                result = await engine.run(graph, inputs=inputs or None)
                self._local_results[run_id] = result
                sentinel = {
                    "event_type": "run_completed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "data": {"result": str(result) if result else None},
                }
                try:
                    event_queue.put_nowait(sentinel)
                except asyncio.QueueFull:
                    pass
            except Exception as exc:
                sentinel = {
                    "event_type": "run_failed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "data": {"error": str(exc)},
                }
                try:
                    event_queue.put_nowait(sentinel)
                except asyncio.QueueFull:
                    pass

        task = asyncio.create_task(run_task())
        self._local_tasks[run_id] = task

        return DispatchResult(
            run_id=run_id,
            workflow_name=workflow_name,
            status="pending",
        )

    # ── Event streaming ─────────────────────────────────────────────

    async def subscribe_run(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        if self._is_server_mode and self._client:
            async for event in self._client.subscribe_run(run_id):
                yield event
            return

        queue = self._local_events.get(run_id)
        if queue is None:
            return
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield event
                    if event.get("event_type") in ("run_completed", "run_failed", "run_cancelled"):
                        return
                except asyncio.TimeoutError:
                    task = self._local_tasks.get(run_id)
                    if task and task.done():
                        while not queue.empty():
                            yield queue.get_nowait()
                        return
        finally:
            self._cleanup_run(run_id)

    # ── Cancel ──────────────────────────────────────────────────────

    async def cancel_run(self, run_id: str) -> bool:
        if self._is_server_mode and self._client:
            return await self._client.cancel_run(run_id)
        task = self._local_tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    # ── HumanNode ───────────────────────────────────────────────────

    async def get_pending_inputs(self) -> list[PendingInput]:
        if self._is_server_mode and self._client:
            return await self._client.get_pending_inputs()
        return []

    async def submit_human_input(
        self,
        run_id: str,
        request_id: str,
        response: dict[str, Any],
        responder_surface: str | None = None,
    ) -> bool:
        if self._is_server_mode and self._client:
            return await self._client.submit_human_input(
                run_id, request_id, response, responder_surface
            )
        return False

    # ── Status ──────────────────────────────────────────────────────

    async def get_activity(self) -> ActivitySnapshot:
        if self._is_server_mode and self._client:
            return await self._client.get_activity()
        return ActivitySnapshot()

    async def get_run_status(self, run_id: str) -> dict[str, Any]:
        if self._is_server_mode and self._client:
            return await self._client.get_run_status(run_id)
        task = self._local_tasks.get(run_id)
        if task is None:
            return {"run_id": run_id, "status": "not_found"}
        if task.done():
            result = self._local_results.get(run_id)
            return {"run_id": run_id, "status": "completed", "result": str(result) if result else None}
        return {"run_id": run_id, "status": "running"}

    async def list_runs(self) -> list[RunSummary]:
        if self._is_server_mode and self._client:
            return await self._client.list_runs()
        return []

    async def register_surface(self, surface_id: str, surface_type: str) -> None:
        if self._is_server_mode and self._client:
            await self._client.register_surface(surface_id, surface_type)

    async def close(self) -> None:
        if self._client:
            await self._client.close()
