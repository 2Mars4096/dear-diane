"""Local chat runtime — runs ChatManager in-process without a server."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

from dan.agent_runtime.graph_summary import compute_graph_revision
from dan.server.control_plane import parse_control_plane_mode

DAN_DIR = Path.home() / ".dan"
LOCAL_ROOT = DAN_DIR / "local"


def _resolve_local_root() -> Path:
    override = os.environ.get("DAN_LOCAL_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return LOCAL_ROOT


def _cli_control_plane_override(explicit: str | None = None) -> str | None:
    if explicit is not None:
        parsed = parse_control_plane_mode(explicit)
        if parsed is not None:
            return parsed
    for env_key in ("DAN_CLI_CONTROL_PLANE", "DAN_CHAT_CONTROL_PLANE"):
        try:
            parsed = parse_control_plane_mode(os.environ.get(env_key))
        except ValueError:
            continue
        if parsed is not None:
            return parsed
    return None


class LocalChatRuntime:
    """In-process chat runtime mirroring the ChatClient interface.

    All methods match the signatures in ``ChatClient`` (``src/dan/cli/chat.py``)
    so the REPL loop works with either backend.
    """

    def __init__(self) -> None:
        self._services: Any | None = None
        self._app_state: Any | None = None
        self._initialized = False

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
        control_plane_mode: str | None = None,
    ) -> dict[str, Any]:
        await self._ensure_init()
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
            control_plane_mode=_cli_control_plane_override(control_plane_mode),
        )
        return await chat_message(self._build_request_proxy(), req, concierge=True)

    async def stream_chat_events(
        self,
        channel_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        from dan.server.routers.chat import iter_local_chat_stream_events

        async for item in iter_local_chat_stream_events(channel_id):
            yield item

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
