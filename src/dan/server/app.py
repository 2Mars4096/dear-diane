"""FastAPI application — composition root.

All route handlers live in ``dan.server.routers.*``.  This module wires
them together with middleware, lifespan, and static-file serving.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from dan.engine.executor import EngineConfig
from dan.models.graph import Graph
from dan.server.chat_manager import ChatManager
from dan.server.llm_gateway import resolve_llm_provider
from dan.server.mention_resolver import MentionResolver
from dan.server.chat_store import ChatStore
from dan.server.graph_store import GraphStore
from dan.server.paths import resolve_graphs_dir
from dan.server.run_manager import RunManager
from dan.server.test_cases import TestCaseStore
from dan.publish.http_server import PublishRegistry
from dan.blocks import BlockRegistry
from dan.adapters import (
    MessagingAdapter, MessagingHumanRenderer, AdapterSessionStore,
)

logger = logging.getLogger(__name__)

_PATH_SEGMENT_RE = __import__("re").compile(r"^[A-Za-z0-9_\-]+$")


def _require_run_manager() -> RunManager:
    if _run_manager is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    return _run_manager


def _resolve_cache_dir(config: EngineConfig) -> Path:
    if config.cache_dir:
        return Path(config.cache_dir).expanduser()
    return Path.home() / ".dan" / "cache"

_graphs_dir = resolve_graphs_dir()
_graph_store = GraphStore(base_dir=_graphs_dir)
_chat_store = ChatStore(base_dir=_graphs_dir)
_test_case_store = TestCaseStore(base_dir=_graphs_dir)
_run_manager: RunManager | None = None
_chat_manager: ChatManager | None = None
_model_gateway: Any | None = None
_meta_tasks: dict[str, asyncio.Task[Any]] = {}
_meta_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
_experience_index_cache: Any | None = None
_experience_index_bootstrap_done = False

_publish_registry: PublishRegistry | None = None
_block_registry: BlockRegistry | None = None
# Adapter state lives in routers/adapters.py; accessed via __getattr__ below.
_self_knowledge_index: Any | None = None
_notification_manager: Any | None = None
_concierge: Any | None = None
_dispatcher: Any | None = None
_mcp_bridge: Any | None = None
_furnace_session_store: Any | None = None
_furnace_enabled: bool = False
_startup_degradations: list[dict[str, str]] = []


def _get_engine_config():
    """Delegate to startup module. Kept for backward compat."""
    from dan.server.startup import _get_engine_config as _impl
    return _impl()


def _build_chat_provider_registry():
    """Delegate to startup module. Kept for backward compat."""
    from dan.server.startup import _build_chat_provider_registry as _impl
    return _impl()


_mention_resolver: MentionResolver | None = None


# Lifespan is now defined in startup.py; import for FastAPI consumption
from dan.server.startup import lifespan  # noqa: E402

# Lazy re-exports for symbols that moved to router/adapter modules.
# Tests and external code that do `from dan.server.app import X` or
# `import dan.server.app as m; m.X` still find these via __getattr__.
# We cache the resolved reference so that mutations on mutable objects
# (e.g. `app_mod._active_adapters["x"] = y`) go through to the router state.
_ROUTER_REEXPORTS: dict[str, tuple[str, str]] = {
    "_chat_streams": ("dan.server.routers.chat", "_chat_streams"),
    "_run_adapter_message_handler": ("dan.server.routers.adapters", "_run_adapter_message_handler"),
    "_run_adapter_concierge": ("dan.server.routers.adapters", "_run_adapter_concierge"),
    "_run_adapter_engine": ("dan.server.routers.adapters", "_run_adapter_engine"),
    "_send_adapter_text": ("dan.server.routers.adapters", "_send_adapter_text"),
    "_active_adapters": ("dan.server.routers.adapters", "_active_adapters"),
    "_adapter_session_stores": ("dan.server.routers.adapters", "_adapter_session_stores"),
    "_adapter_start_times": ("dan.server.routers.adapters", "_adapter_start_times"),
    "_adapter_renderers": ("dan.server.routers.adapters", "_adapter_renderers"),
    "_adapter_surface_types": ("dan.server.routers.adapters", "_adapter_surface_types"),
}
_REEXPORT_CACHE: dict[str, Any] = {}


def __getattr__(name: str):
    if name in _REEXPORT_CACHE:
        return _REEXPORT_CACHE[name]
    entry = _ROUTER_REEXPORTS.get(name)
    if entry is not None:
        mod_path, attr = entry
        import importlib
        mod = importlib.import_module(mod_path)
        val = getattr(mod, attr)
        _REEXPORT_CACHE[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


app = FastAPI(title="Deep Agent Network", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _validate_path_segment(value: str, name: str) -> str:
    if not _PATH_SEGMENT_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {name}: must be alphanumeric / dash / underscore",
        )
    return value


def _get_memory_store():
    from dan.engine.memory_store import FileSystemMemoryStore
    rm = _require_run_manager()
    memory_dir = rm.engine_config.memory_dir if rm.engine_config else "./memory"
    return FileSystemMemoryStore(memory_dir)


def _get_experience_index():
    global _experience_index_cache
    if _experience_index_cache is not None:
        return _experience_index_cache
    try:
        from dan.engine.experience import ExperienceIndex
        from dan.rag import build_embedding_registry, DEFAULT_EMBEDDING_MODEL
        from dan.rag.stores import VectorStoreConfig, VectorStoreFactory

        rm = _require_run_manager()
        config = rm.engine_config
        registry = build_embedding_registry(config)
        model = config.default_embedding_model
        provider = registry.resolve(model)
        backend = os.environ.get(
            "DAN_EXPERIENCE_STORE_BACKEND",
            os.environ.get("DAN_RAG_STORE_BACKEND", "memory"),
        )
        persist_dir = os.environ.get("DAN_EXPERIENCE_PERSIST_DIR", "./rag_data")
        vector_store = VectorStoreFactory.create(
            VectorStoreConfig(
                backend=backend,
                persist_directory=persist_dir,
            ),
        )
        _experience_index_cache = ExperienceIndex(
            embedding_provider=provider,
            vector_store=vector_store,
            embedding_model=model,
        )
        return _experience_index_cache
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Experience index unavailable: {exc}",
        ) from exc


def _get_experience_store(*, with_index: bool = False):
    from dan.engine.experience import ExperienceStore

    index = _get_experience_index() if with_index else None
    return ExperienceStore(_get_memory_store(), experience_index=index)


def _build_meta_controller():
    """Build (controller, planner, session_store) — used by meta router."""
    import uuid as _uuid
    from dan.meta.controller import MetaController, MetaSessionStore
    from dan.meta.discovery import DiscoveryService
    from dan.meta.planner import WorkflowPlanner
    from dan.meta.repair import (
        RepairActionStore,
        RepairEscalator,
        StructuralRepairPlanner,
    )
    from dan.server.run_manager import RunStatus as _RS
    from dan.server.skill_library import SKILL_LIBRARY

    rm = _require_run_manager()
    memory_store = _get_memory_store()
    try:
        exp_index = _get_experience_index()
    except HTTPException:
        exp_index = None
    exp_store = _get_experience_store(with_index=exp_index is not None)

    discovery = DiscoveryService(
        experience_index=exp_index,
        experience_store=exp_store,
        graph_store=_graph_store,
        tool_registry=rm.tool_registry,
        self_knowledge=_self_knowledge_index,
        skill_library=SKILL_LIBRARY,
    )

    provider_registry = None if _chat_manager is not None else _build_chat_provider_registry()
    fallback_model = os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6")

    async def _llm_call(
        system_prompt: str,
        user_prompt: str,
        model: str | None,
        temperature: float,
    ) -> str:
        model_name = model or fallback_model
        # Prefer llm_core gateway when startup mirrored it (retries, timeout, telemetry).
        if _model_gateway is not None:
            result = await _model_gateway.complete(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model_name,
                temperature=temperature,
            )
            return result.text
        if _chat_manager is not None:
            provider = resolve_llm_provider(_chat_manager, model=model_name)
        else:
            assert provider_registry is not None
            provider = provider_registry.resolve(model_name)
        result = await provider.complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=model_name,
            temperature=temperature,
        )
        return result.text

    planner = WorkflowPlanner(
        discovery=discovery,
        graph_store=_graph_store,
        llm_call=_llm_call,
        model=rm.engine_config.planner_model or rm.engine_config.llm_default_model,
        max_retries=rm.engine_config.planner_max_retries,
        temperature=rm.engine_config.planner_temperature,
        discovery_top_k=rm.engine_config.planner_discovery_top_k,
    )

    structural_planner = StructuralRepairPlanner(
        llm_call=_llm_call,
        model=rm.engine_config.repair_model or rm.engine_config.planner_model or rm.engine_config.llm_default_model,
    )
    repair_store = RepairActionStore(memory_store)
    escalator = RepairEscalator(
        structural_planner=structural_planner,
        action_store=repair_store,
        max_attempts_per_level=rm.engine_config.max_repair_attempts_per_level,
        max_redesigns=rm.engine_config.max_redesigns_per_goal,
    )

    session_store = MetaSessionStore(memory_store)

    async def _run_workflow(
        plan: Any,
        session_id: str,
        *,
        workflow_inputs: dict[str, Any] | None = None,
        upstream_handoffs: dict[str, Any] | None = None,
        workflow_spec: dict[str, Any] | None = None,
        prepared_graph: dict[str, Any] | None = None,
        prepared_workflow_id: str | None = None,
    ) -> dict[str, Any]:
        workflow_id = str(prepared_workflow_id or "").strip()
        graph_data = prepared_graph if isinstance(prepared_graph, dict) else None
        _plan_warning: str | None = None
        if graph_data is None:
            user_text = getattr(plan, "description", None)
            exec_result = await planner.execute_plan(plan, user_text=user_text)
            workflow_id = str(exec_result.get("workflow_id", "")).strip()
            graph_data = exec_result.get("graph")
            if exec_result.get("legacy_fallback"):
                _plan_warning = exec_result.get("warning")
        elif isinstance(graph_data, dict) and graph_data.get("legacy_fallback"):
            _plan_warning = graph_data.get("warning")
        if not workflow_id:
            workflow_id = f"meta-{_uuid.uuid4().hex[:10]}"
        if not isinstance(graph_data, dict):
            return {
                "success": False,
                "workflow_id": workflow_id,
                "error_context": "Planner execution did not return a graph",
            }

        _graph_store.save_graph(workflow_id, graph_data)

        graph_model = Graph.model_validate(graph_data)
        rec = await rm.start_run(
            graph_model,
            graph_id=workflow_id,
            inputs=dict(workflow_inputs or {}),
            session_id=session_id,
            goal_context={
                "meta_workflow_spec": dict(workflow_spec or {}),
                "upstream_handoffs": dict(upstream_handoffs or {}),
            },
        )
        while True:
            current = rm.get_run(rec.run_id)
            if current is None:
                return {
                    "success": False,
                    "workflow_id": workflow_id,
                    "error_context": f"Run {rec.run_id} disappeared",
                }
            if current.status in (_RS.COMPLETED, _RS.FAILED, _RS.CANCELLED):
                break
            await asyncio.sleep(0.1)

        current = rm.get_run(rec.run_id)
        if current is None:
            return {
                "success": False,
                "workflow_id": workflow_id,
                "error_context": f"Run {rec.run_id} missing after completion",
            }
        snapshot = current.snapshot()
        principle_dicts: list[dict[str, Any]] = []
        ps = rm._get_principle_store()
        if ps is not None:
            try:
                principles = await ps.load_principles(workflow_id)
                principle_dicts = [p.model_dump() for p in principles]
            except Exception:
                logger.debug("Failed loading principles for %s", workflow_id, exc_info=True)
        result = {
            "success": bool(snapshot.get("success", False)),
            "run_id": rec.run_id,
            "workflow_id": workflow_id,
            "errors": snapshot.get("errors", {}),
            "error_context": str(snapshot.get("errors", "")),
            "principles": principle_dicts,
            "outputs": snapshot.get("outputs", {}),
        }
        if _plan_warning:
            result["warning"] = _plan_warning
            result["legacy_fallback"] = True
        return result

    from dan.server.routers.meta import _meta_subscribers

    async def _emit_meta_event(event: dict[str, Any]) -> None:
        sid = event.get("session_id", "")
        for queue in _meta_subscribers.get(sid, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Meta subscriber queue full for session %s", sid)

    controller = MetaController(
        planner=planner,
        repair_escalator=escalator,
        experience_store=exp_store,
        session_store=session_store,
        run_workflow=_run_workflow,
        emit_event=_emit_meta_event,
        graph_loader=_graph_store.get_graph,
        graph_saver=_graph_store.save_graph,
    )
    return controller, planner, session_store


def _get_publish_registry() -> PublishRegistry:
    if _publish_registry is None:
        raise HTTPException(status_code=503, detail="Publish registry not initialised")
    return _publish_registry


def _cleanup_export_dir(path: str) -> None:
    """Remove temp directory after file response is sent."""
    shutil.rmtree(path, ignore_errors=True)


def _get_block_registry() -> BlockRegistry:
    if _block_registry is None:
        raise HTTPException(status_code=503, detail="Block registry not initialised")
    return _block_registry


# ------------------------------------------------------------------
# Router inclusions — all route handlers live under server/routers/
# ------------------------------------------------------------------

from dan.server.routers.misc import router as misc_router
from dan.server.routers.graphs import router as graphs_router
from dan.server.routers.rag import router as rag_router
from dan.server.routers.runs import router as runs_router
from dan.server.routers.experiences import router as experiences_router
from dan.server.routers.meta import router as meta_router
from dan.server.routers.publishing import router as publishing_router
from dan.server.routers.blocks import router as blocks_router
from dan.server.routers.chat import router as chat_router
from dan.server.routers.adapters import router as adapters_router
from dan.server.routers.furnace import router as furnace_router

app.include_router(misc_router)
app.include_router(graphs_router)
app.include_router(rag_router)
app.include_router(runs_router)
app.include_router(experiences_router)
app.include_router(meta_router)
app.include_router(publishing_router)
app.include_router(blocks_router)
app.include_router(chat_router)
app.include_router(adapters_router)
app.include_router(furnace_router)


# ------------------------------------------------------------------
# Compatibility re-exports — keep these so existing imports from
# ``dan.server.app`` continue to work until the next cleanup phase.
# ------------------------------------------------------------------

from dan.server.routers.graphs import (  # noqa: F401
    CreateGraphRequest,
    ApplyMutationRequest,
)
from dan.server.routers.runs import (  # noqa: F401
    RunRequest,
    ResumeRequest,
)
from dan.server.routers.chat import (  # noqa: F401
    ChatMessageRequest,
    ChatMentionRef,
)


# ------------------------------------------------------------------
# Static file serving for the built editor (production)
# ------------------------------------------------------------------

_editor_dist = os.path.join(os.path.dirname(__file__), "..", "..", "..", "editor", "dist")
if os.path.isdir(_editor_dist):
    app.mount("/", StaticFiles(directory=_editor_dist, html=True), name="editor")
