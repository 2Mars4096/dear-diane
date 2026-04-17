"""Local chat runtime — runs ChatManager in-process without a server."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

from dan.agent_runtime.graph_summary import compute_graph_revision
from dan.server.control_plane import resolve_control_plane_mode

logger = logging.getLogger(__name__)

DAN_DIR = Path.home() / ".dan"
LOCAL_ROOT = DAN_DIR / "local"


def _resolve_local_root() -> Path:
    override = os.environ.get("DAN_LOCAL_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return LOCAL_ROOT


class LocalChatRuntime:
    """In-process chat runtime mirroring the ChatClient interface.

    All methods match the signatures in ``ChatClient`` (``src/dan/cli/chat.py``)
    so the REPL loop works with either backend.
    """

    def __init__(self) -> None:
        self._services: Any | None = None
        self._app_state: Any | None = None
        self._initialized = False
        self._pending_streams: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}
        self._stream_queue_maxsize = 1024
        self._stream_cleanup_ttl_seconds = 300

    # ------------------------------------------------------------------
    # Lazy bootstrap
    # ------------------------------------------------------------------

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        from dan.server.chat_factory import build_chat_services

        local_root = _resolve_local_root()
        graphs_dir = local_root / "graphs"
        graphs_dir.mkdir(parents=True, exist_ok=True)
        self._services = build_chat_services(
            graphs_dir=str(graphs_dir),
            workspace_root=str(Path.cwd()),
            project_store_base_dir=local_root / "projects",
            surface="local",
        )
        from dan.server.app_state import AppState

        self._app_state = AppState(
            graph_store=self._services.graph_store,
            chat_store=self._services.chat_store,
            run_manager=self._services.run_manager,
            chat_manager=self._services.chat_manager,
            mention_resolver=self._services.mention_resolver,
            concierge=self._services.concierge,
            dispatcher=self._services.dispatcher,
            engine_config=self._services.engine_config,
            graphs_dir=str(graphs_dir),
        )
        if self._services.graph_store.get_graph("_scratch") is None:
            self._services.graph_store.save_graph(
                "_scratch", {"nodes": [], "edges": []}
            )
        self._initialized = True

    def _new_stream_queue(self) -> asyncio.Queue[dict[str, Any] | None]:
        return asyncio.Queue(maxsize=self._stream_queue_maxsize)

    def _enqueue_stream_item(
        self,
        queue: asyncio.Queue[dict[str, Any] | None],
        item: dict[str, Any] | None,
    ) -> None:
        """Best-effort bounded enqueue; drop oldest when full."""
        try:
            queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            pass

        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass

    def _schedule_stream_cleanup(
        self,
        channel_id: str,
        queue: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        async def _cleanup_later() -> None:
            await asyncio.sleep(self._stream_cleanup_ttl_seconds)
            if self._pending_streams.get(channel_id) is queue:
                self._pending_streams.pop(channel_id, None)

        asyncio.create_task(_cleanup_later())

    def _build_request_proxy(self) -> Any:
        return SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(dan=self._app_state))
        )

    # ------------------------------------------------------------------
    # ChatClient-compatible interface
    # ------------------------------------------------------------------

    async def ping(self) -> tuple[bool, str | None]:
        return (True, None)

    async def get_health(self) -> dict[str, Any]:
        from dan.server.runtime_config import runtime_degradation_summary

        try:
            await self._ensure_init()
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            return {
                "status": "degraded",
                "startup": {
                    "status": "degraded",
                    "issues": [
                        {
                            "subsystem": "local_runtime",
                            "message": message,
                        }
                    ],
                },
            }

        startup = runtime_degradation_summary(
            getattr(self._services, "startup_degradations", [])
        )
        return {
            "status": "ok",
            "startup": startup,
            "mode_limitations": [
                dict(item)
                for item in getattr(self._services, "mode_limitations", [])
            ],
        }

    async def close(self) -> None:
        pass

    async def send_chat_message(
        self,
        workflow_id: str,
        message: str,
        *,
        history: list[dict[str, str]] | None = None,
        thread_id: str | None = None,
        client_graph_revision: str | None = None,
        mode: str = "build",
    ) -> dict[str, Any]:
        await self._ensure_init()
        if resolve_control_plane_mode() == "v2":
            from dan.server.routers.chat import ChatMessageRequest, chat_message

            req = ChatMessageRequest(
                workflow_id=workflow_id,
                message=message,
                history=history or [],
                thread_id=thread_id,
                session_id=thread_id,
                client_graph_revision=client_graph_revision,
                mode=mode,
                surface="cli:local",
                surface_type="cli",
                surface_id="local",
            )
            return await chat_message(self._build_request_proxy(), req, concierge=True)

        s = self._services
        from dan.server.chat_manager import (
            build_debug_context,
            detect_chat_mode,
            normalize_chat_mode,
            recent_run_failed_for_workflow,
        )

        from dan.server.scoped_run import parse_run_command
        run_cmd = parse_run_command(message)
        if run_cmd is not None:
            return await self._handle_run_command(workflow_id, run_cmd)

        channel_id = uuid.uuid4().hex[:12]
        queue = self._new_stream_queue()
        self._pending_streams[channel_id] = queue
        normalized_mode = normalize_chat_mode(mode)
        graph_dict = s.graph_store.get_graph(workflow_id)
        if normalized_mode == "auto":
            recent_run_failed = False
            if getattr(s, "run_manager", None) is not None:
                runs = s.run_manager.list_runs()
                recent_run_failed = recent_run_failed_for_workflow(runs, workflow_id)
            normalized_mode = detect_chat_mode(message, recent_run_failed)
        debug_context = ""
        if normalized_mode == "debug" and getattr(s, "run_manager", None) is not None:
            debug_context = build_debug_context(s.run_manager.list_runs(), workflow_id)

        async def _stream() -> None:
            try:
                dispatcher = getattr(s, "dispatcher", None)
                concierge = getattr(s, "concierge", None)
                has_dispatcher = (
                    dispatcher is not None
                    and callable(getattr(dispatcher, "dispatch", None))
                    and not hasattr(dispatcher, "assert_called")
                )
                has_concierge = (
                    concierge is not None
                    and callable(getattr(concierge, "process", None))
                    and not hasattr(concierge, "assert_called")
                )
                if has_dispatcher or has_concierge:
                    from dan.server.concierge import SurfaceMessage

                    source_msg = SurfaceMessage(
                        surface="cli",
                        external_id=thread_id or workflow_id or "local-cli",
                        text=message,
                        metadata={
                            "workflow_id": workflow_id,
                            "thread_id": thread_id,
                            "client_graph_revision": client_graph_revision,
                            "mode": normalized_mode,
                            "debug_context": debug_context,
                            "request_history": history or [],
                        },
                    )
                    if has_dispatcher:
                        event_stream = dispatcher.dispatch(source_msg)
                    else:
                        event_stream = concierge.process(source_msg)
                else:
                    event_stream = s.chat_manager.send_message_with_tools(
                        workflow_id=workflow_id,
                        message=message,
                        history=history or [],
                        thread_id=thread_id,
                        client_graph_revision=client_graph_revision,
                        mode=normalized_mode,
                        debug_context=debug_context,
                    )
                async for event in event_stream:
                    ev_dict = (
                        event.model_dump()
                        if hasattr(event, "model_dump")
                        else {"type": str(type(event).__name__)}
                    )
                    if ev_dict.get("type") == "chat_queued" and dispatcher is not None:
                        queued_ch = ev_dict.get("stream_channel_id", "")
                        if queued_ch:
                            queued_q = self._new_stream_queue()
                            self._pending_streams[queued_ch] = queued_q

                            async def _pipe_queued(ch: str, qq: asyncio.Queue) -> None:
                                bus = dispatcher.get_response_bus(ch)
                                if bus is None:
                                    return
                                try:
                                    while True:
                                        bus_event = await bus.get()
                                        if bus_event is None:
                                            break
                                        self._enqueue_stream_item(
                                            qq,
                                            bus_event.model_dump() if hasattr(bus_event, "model_dump") else bus_event,
                                        )
                                except Exception:
                                    pass
                                finally:
                                    self._enqueue_stream_item(qq, None)
                                    dispatcher.cleanup_response_bus(ch)

                            _pipe_task = asyncio.create_task(_pipe_queued(queued_ch, queued_q))
                            if hasattr(dispatcher, '_track_task'):
                                dispatcher._track_task(_pipe_task)
                    self._enqueue_stream_item(queue, ev_dict)
            except Exception as exc:
                self._enqueue_stream_item(queue, {"type": "chat_error", "error": str(exc)})
            finally:
                self._enqueue_stream_item(queue, None)
                self._schedule_stream_cleanup(channel_id, queue)

        asyncio.create_task(_stream())
        return {"stream_channel_id": channel_id}

    async def stream_chat_events(
        self,
        channel_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        if channel_id.startswith("chat-"):
            from dan.server.routers.chat import iter_local_chat_stream_events

            async for item in iter_local_chat_stream_events(channel_id):
                yield item
            return
        queue = self._pending_streams.get(channel_id)
        if queue is None:
            return
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            if self._pending_streams.get(channel_id) is queue:
                self._pending_streams.pop(channel_id, None)

    async def apply_mutation(
        self,
        graph_id: str,
        mutation_plan: dict[str, Any],
    ) -> dict[str, Any]:
        await self._ensure_init()
        s = self._services
        from dan.server.graph_mutator import GraphMutator, MutationPlan

        graph_dict = s.graph_store.get_graph(graph_id)
        if graph_dict is None:
            return {"success": False, "errors": [{"message": f"Graph '{graph_id}' not found"}]}

        current_rev = compute_graph_revision(graph_dict)
        plan = MutationPlan.model_validate(mutation_plan)
        mutator = GraphMutator()

        dry = mutator.dry_run(graph_dict, plan, current_revision=current_rev)
        if not dry.success:
            return {
                "success": False,
                "errors": [{"message": e.message} for e in (dry.errors or [])],
            }

        result = mutator.apply(graph_dict, plan, current_revision=current_rev)
        if not result.success:
            return {
                "success": False,
                "errors": [{"message": e.message} for e in (result.errors or [])],
            }

        s.graph_store.save_graph(graph_id, result.new_graph)
        new_rev = compute_graph_revision(result.new_graph)
        return {
            "success": True,
            "new_graph": result.new_graph,
            "graph_revision": new_rev,
        }

    async def get_graph(self, graph_id: str) -> dict[str, Any] | None:
        await self._ensure_init()
        return self._services.graph_store.get_graph(graph_id)

    async def list_graphs(self) -> list[dict[str, Any]]:
        await self._ensure_init()
        return self._services.graph_store.list_graphs()

    async def create_graph(
        self, graph_id: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        await self._ensure_init()
        s = self._services
        if s.graph_store.get_graph(graph_id) is not None:
            raise RuntimeError(f"Workflow '{graph_id}' already exists")
        graph_data = data if data is not None else {"nodes": [], "edges": []}
        s.graph_store.save_graph(graph_id, graph_data)
        return {"graph_id": graph_id, "data": graph_data}

    async def save_graph(self, graph_id: str, data: dict[str, Any]) -> None:
        await self._ensure_init()
        self._services.graph_store.save_graph(graph_id, data)

    async def submit_human_input(
        self,
        run_id: str,
        request_id: str,
        response: dict[str, Any],
    ) -> bool:
        await self._ensure_init()
        rm = self._services.run_manager
        if rm is None:
            return False
        return rm.submit_human_input(run_id, request_id, response)

    async def cancel_run(self, run_id: str) -> bool:
        await self._ensure_init()
        rm = self._services.run_manager
        if rm is None:
            return False
        return rm.cancel_run(run_id)

    # ------------------------------------------------------------------
    # Internal: handle /run commands locally
    # ------------------------------------------------------------------

    async def _handle_run_command(
        self, workflow_id: str, run_cmd: dict[str, Any]
    ) -> dict[str, Any]:
        s = self._services
        from dan.models.graph import Graph
        from dan.server.scoped_run import build_scoped_graph, map_run_event_to_chat_block

        graph_dict = s.graph_store.get_graph(workflow_id)
        if graph_dict is None:
            return {"type": "run_error", "error": {"message": f"Workflow '{workflow_id}' not found"}}

        scope = run_cmd.get("scope", "full")
        try:
            graph_model = Graph.model_validate(graph_dict)
            scoped_result = build_scoped_graph(
                graph=graph_model,
                scope=scope,
                target_node_id=run_cmd.get("target_node_id"),
                target_subgraph_key=run_cmd.get("target_subgraph_key"),
            )
            if scoped_result.error:
                return {"type": "run_error", "error": {"message": scoped_result.error.message}}
            if scoped_result.graph is None:
                return {"type": "run_error", "error": {"message": "Could not build scoped graph"}}
        except Exception as exc:
            return {"type": "run_error", "error": {"message": str(exc)}}

        channel_id = uuid.uuid4().hex[:12]
        queue = self._new_stream_queue()
        self._pending_streams[channel_id] = queue

        rm = s.run_manager
        run_id = uuid.uuid4().hex[:12]
        target_desc = scoped_result.scope_metadata.get("scope", scope)

        async def _run_and_stream() -> None:
            try:
                record = await rm.start_run(
                    graph=scoped_result.graph,
                    graph_id=workflow_id,
                    run_id=run_id,
                    inputs=run_cmd.get("inputs"),
                )
                event_queue = rm.subscribe(record.run_id)
                try:
                    while True:
                        try:
                            event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                        except asyncio.TimeoutError:
                            if rm.run_is_settled_for_stream(record.run_id):
                                break
                            continue
                        block = map_run_event_to_chat_block(
                            event, scope=scope, target=target_desc,
                        )
                        if block is not None:
                            self._enqueue_stream_item(queue, {"type": "chat_run_event", "run_event": block})
                        ev_type = event.get("event_type", "")
                        if ev_type in (
                            "run_completed",
                            "run_cancelled",
                            "automatic_recovery_completed",
                        ):
                            break
                finally:
                    rm.unsubscribe(record.run_id, event_queue)
            except Exception as exc:
                self._enqueue_stream_item(queue, {
                    "type": "chat_run_event",
                    "run_event": {
                        "event_type": "run_failed",
                        "summary": f"Run failed: {exc}",
                    },
                })
            finally:
                self._enqueue_stream_item(queue, None)
                self._schedule_stream_cleanup(channel_id, queue)

        asyncio.create_task(_run_and_stream())
        return {
            "type": "run_started",
            "run_id": run_id,
            "scope": scope,
            "stream_channel_id": channel_id,
        }
