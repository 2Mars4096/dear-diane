"""Server lifespan and startup wiring.

Extracted from ``app.py`` (Plan 35-5).  The public entry point is
:func:`lifespan`, an async context manager consumed by ``FastAPI(lifespan=...)``.
All init helpers are pure functions of ``AppState`` — no module-level mutable
globals live here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING

from dan.server.app_state import AppState
from dan.server.paths import resolve_graphs_dir, resolve_workspace_root

if TYPE_CHECKING:
    from fastapi import FastAPI

from fastapi import FastAPI, HTTPException

logger = logging.getLogger(__name__)
_API_KEY_PLACEHOLDERS = frozenset({"your-api-key-here", "changeme", "replace-me"})
_PRIMARY_LLM_KEY_ENV_VARS = (
    "DAN_LLM_API_KEY",
    "LLM_API_KEY",
    "DAN_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "DAN_ANTHROPIC_API_KEY",
    "DAN_GOOGLE_API_KEY",
)
_active_app_state: AppState | None = None


def get_active_app_state() -> AppState | None:
    return _active_app_state


def set_active_app_state(state: AppState | None) -> None:
    global _active_app_state
    _active_app_state = state


def require_active_app_state() -> AppState:
    state = get_active_app_state()
    if state is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    return state


def _skip_adapter_autostart_for_current_process(app: Any | None = None) -> bool:
    """Keep API/ASGI tests from inheriting desktop adapter autostart state."""
    if os.environ.get("DAN_ENABLE_ADAPTER_AUTOSTART", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    return "PYTEST_CURRENT_TEST" in os.environ and isinstance(app, FastAPI)


def _isolated_memory_kernel_base_dir(state: AppState) -> str | None:
    """Avoid loading a developer's persistent memory index during pytest startup."""
    if "PYTEST_CURRENT_TEST" not in os.environ:
        return None
    return str(Path(state.graphs_dir) / "_memory_kernel")


# ---------------------------------------------------------------------------
# Config helpers (moved from app.py)
# ---------------------------------------------------------------------------


def get_llm_api_key_status() -> str:
    """Return configured/missing/placeholder for any supported primary chat key."""
    from dan.server.runtime_config import env_key_status

    status, _ = env_key_status(_PRIMARY_LLM_KEY_ENV_VARS)
    return status


def log_startup_configuration_warnings() -> None:
    status = get_llm_api_key_status()
    if status == "missing":
        logger.warning(
            "DAN_LLM_API_KEY is missing. Set DAN_LLM_API_KEY in your .env file. "
            "See .env.example."
        )
    elif status == "placeholder":
        logger.warning(
            "DAN_LLM_API_KEY is still set to a placeholder value. Update your .env "
            "file with a real key. See .env.example."
        )

    try:
        from dan.tools.shell_command import shell_sandbox_explicitly_disabled

        if shell_sandbox_explicitly_disabled():
            logger.warning(
                "Shell sandbox is explicitly disabled (DAN_SANDBOX_SHELL=0). "
                "Shell commands will run without the subprocess sandbox."
            )
    except Exception:
        logger.debug("Shell sandbox warning check failed", exc_info=True)


def _record_startup_degradation(
    state: AppState,
    subsystem: str,
    message: str,
) -> None:
    from dan.server.runtime_config import append_runtime_degradation

    append_runtime_degradation(state.startup_degradations, subsystem, message)


def get_startup_degradation_summary(state: AppState) -> dict[str, Any]:
    from dan.server.runtime_config import runtime_degradation_summary

    return runtime_degradation_summary(state.startup_degradations)


def log_startup_degradation_summary(state: AppState) -> None:
    from dan.server.runtime_config import log_runtime_degradation_summary

    log_runtime_degradation_summary(state.startup_degradations)


def _get_engine_config():
    """Build an :class:`EngineConfig` from environment variables."""
    from dan.server.runtime_config import build_engine_config_from_env

    return build_engine_config_from_env()


def _build_chat_provider_registry():
    """Create a :class:`ProviderRegistry` with all configured chat providers."""
    from dan.server.runtime_config import build_chat_provider_registry

    return build_chat_provider_registry(_get_engine_config())


def _build_model_gateway(engine_config: Any = None):
    """Build a :class:`ModelGateway` wrapping the same provider set as the registry.

    Uses ``build_gateway`` from ``llm_core`` — the single canonical factory.
    """
    from dan.llm_core.factory import build_gateway

    cfg = engine_config if engine_config is not None else _get_engine_config()
    return build_gateway(engine_config=cfg)


def build_memory_store_from_state(state: AppState | None = None):
    from dan.engine.memory_store import FileSystemMemoryStore

    app_state = state or require_active_app_state()
    run_manager = app_state.require_run_manager()
    memory_dir = (
        run_manager.engine_config.memory_dir
        if run_manager.engine_config
        else "./memory"
    )
    return FileSystemMemoryStore(memory_dir)


def get_experience_index_from_state(state: AppState | None = None):
    app_state = state or require_active_app_state()
    if app_state.experience_index_cache is not None:
        return app_state.experience_index_cache
    try:
        from dan.engine.experience import ExperienceIndex
        from dan.rag import build_embedding_registry
        from dan.rag.stores import VectorStoreConfig, VectorStoreFactory

        run_manager = app_state.require_run_manager()
        config = run_manager.engine_config
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
        app_state.experience_index_cache = ExperienceIndex(
            embedding_provider=provider,
            vector_store=vector_store,
            embedding_model=model,
        )
        return app_state.experience_index_cache
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Experience index unavailable: {exc}",
        ) from exc


def build_experience_store_from_state(
    state: AppState | None = None,
    *,
    with_index: bool = False,
):
    from dan.engine.experience import ExperienceStore

    app_state = state or require_active_app_state()
    index = get_experience_index_from_state(app_state) if with_index else None
    return ExperienceStore(
        build_memory_store_from_state(app_state),
        experience_index=index,
    )


def build_meta_controller_from_state(state: AppState | None = None):
    """Build (controller, planner, session_store) from the canonical AppState."""
    import uuid as _uuid

    from dan.models.graph import Graph
    from dan.meta.controller import MetaController, MetaSessionStore
    from dan.meta.discovery import DiscoveryService
    from dan.meta.planner import WorkflowPlanner
    from dan.meta.repair import (
        RepairActionStore,
        RepairEscalator,
        StructuralRepairPlanner,
    )
    from dan.server.llm_gateway import resolve_llm_provider
    from dan.server.routers.meta import _meta_subscribers
    from dan.server.run_manager import RunStatus as _RS
    from dan.server.skill_library import SKILL_LIBRARY

    app_state = state or require_active_app_state()
    graph_store = app_state.require_graph_store()
    rm = app_state.require_run_manager()
    memory_store = build_memory_store_from_state(app_state)
    try:
        exp_index = get_experience_index_from_state(app_state)
    except HTTPException:
        exp_index = None
    exp_store = build_experience_store_from_state(
        app_state,
        with_index=exp_index is not None,
    )

    discovery = DiscoveryService(
        experience_index=exp_index,
        experience_store=exp_store,
        graph_store=graph_store,
        tool_registry=rm.tool_registry,
        self_knowledge=app_state.self_knowledge_index,
        skill_library=SKILL_LIBRARY,
    )

    chat_manager = app_state.chat_manager
    provider_registry = (
        None if chat_manager is not None else _build_chat_provider_registry()
    )
    fallback_model = os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6")

    async def _llm_call(
        system_prompt: str,
        user_prompt: str,
        model: str | None,
        temperature: float,
    ) -> str:
        model_name = model or fallback_model
        if chat_manager is not None:
            provider = resolve_llm_provider(chat_manager, model=model_name)
            result = await provider.complete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=model_name,
                temperature=temperature,
            )
            return result.text
        if app_state.model_gateway is not None:
            result = await app_state.model_gateway.complete(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model_name,
                temperature=temperature,
            )
            return result.text
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
        graph_store=graph_store,
        llm_call=_llm_call,
        model=rm.engine_config.planner_model or rm.engine_config.llm_default_model,
        max_retries=rm.engine_config.planner_max_retries,
        temperature=rm.engine_config.planner_temperature,
        discovery_top_k=rm.engine_config.planner_discovery_top_k,
    )

    structural_planner = StructuralRepairPlanner(
        llm_call=_llm_call,
        model=(
            rm.engine_config.repair_model
            or rm.engine_config.planner_model
            or rm.engine_config.llm_default_model
        ),
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
        plan_warning: str | None = None
        if graph_data is None:
            user_text = getattr(plan, "description", None)
            exec_result = await planner.execute_plan(plan, user_text=user_text)
            workflow_id = str(exec_result.get("workflow_id", "")).strip()
            graph_data = exec_result.get("graph")
            if exec_result.get("legacy_fallback"):
                plan_warning = exec_result.get("warning")
        elif isinstance(graph_data, dict) and graph_data.get("legacy_fallback"):
            plan_warning = graph_data.get("warning")
        if not workflow_id:
            workflow_id = f"meta-{_uuid.uuid4().hex[:10]}"
        if not isinstance(graph_data, dict):
            return {
                "success": False,
                "workflow_id": workflow_id,
                "error_context": "Planner execution did not return a graph",
            }

        graph_store.save_graph(workflow_id, graph_data)

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
        principle_store = rm._get_principle_store()
        if principle_store is not None:
            try:
                principles = await principle_store.load_principles(workflow_id)
                principle_dicts = [p.model_dump() for p in principles]
            except Exception:
                logger.debug(
                    "Failed loading principles for %s",
                    workflow_id,
                    exc_info=True,
                )
        result = {
            "success": bool(snapshot.get("success", False)),
            "run_id": rec.run_id,
            "workflow_id": workflow_id,
            "errors": snapshot.get("errors", {}),
            "error_context": str(snapshot.get("errors", "")),
            "principles": principle_dicts,
            "outputs": snapshot.get("outputs", {}),
        }
        if plan_warning:
            result["warning"] = plan_warning
            result["legacy_fallback"] = True
        return result

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
        graph_loader=graph_store.get_graph,
        graph_saver=graph_store.save_graph,
    )
    return controller, planner, session_store


# ---------------------------------------------------------------------------
# MCP bridge helpers (moved from app.py)
# ---------------------------------------------------------------------------


async def _initialize_mcp_bridge_for_server(
    *,
    state: AppState,
    capability_registry: Any,
    tool_registry: Any | None,
    capability_context: Any,
) -> Any | None:
    """Create the MCP bridge for the main server path and auto-connect configured servers."""
    try:
        from dan.mcp_bridge import MCPBridge, autoconnect_configured_mcp_servers
    except Exception:
        logger.debug("MCP bridge not available", exc_info=True)
        capability_context.mcp_bridge = None
        return None

    bridge = MCPBridge()
    try:
        await autoconnect_configured_mcp_servers(
            bridge,
            capability_registry,
            tool_registry,
        )
    except Exception:
        _record_startup_degradation(
            state,
            "mcp_bridge",
            "auto-connect failed; MCP-backed tools may be unavailable",
        )
        logger.warning("MCP bridge startup failed", exc_info=True)
    capability_context.mcp_bridge = bridge
    return bridge


async def _shutdown_mcp_bridge_for_server(bridge: Any | None) -> None:
    if bridge is None:
        return
    try:
        await bridge.shutdown()
    except Exception:
        logger.debug("MCP bridge shutdown failed", exc_info=True)


# ---------------------------------------------------------------------------
# Publish helpers (moved from app.py)
# ---------------------------------------------------------------------------


def _auto_register_published_workflows(
    registry: Any, graph_store: Any, graphs_dir: str
) -> None:
    """Scan ``graphs/*.publish.json`` and auto-register previously published workflows."""
    graphs_path = Path(graphs_dir)
    if not graphs_path.exists():
        return
    for pub_file in graphs_path.glob("*.publish.json"):
        graph_id = pub_file.name.replace(".publish.json", "")
        try:
            pub_config = json.loads(pub_file.read_text(encoding="utf-8"))
            if not pub_config.get("enabled", False):
                continue
            graph_data = graph_store.get_graph(graph_id)
            if graph_data is None:
                logger.warning("Published graph %s not found, skipping", graph_id)
                continue
            from dan.models.graph import Graph

            graph = Graph.model_validate(graph_data)
            registry.register(
                graph,
                api_key=pub_config.get("api_key"),
                rate_limit=pub_config.get("rate_limit"),
            )
            logger.info("Auto-registered published workflow: %s", graph_id)
        except Exception:
            logger.warning("Failed to auto-register %s", pub_file, exc_info=True)


async def _deferred_telemetry_prune(store: Any) -> None:
    """Run telemetry prune in the background so it doesn't block startup."""
    try:
        await store.prune()
    except Exception:
        logger.debug("Deferred telemetry prune failed", exc_info=True)


async def _consolidation_loop(
    kernel: Any, interval_hours: float, graph_store: Any
) -> None:
    """Periodically run memory consolidation in the background."""
    try:
        while True:
            await asyncio.sleep(interval_hours * 3600)
            try:
                result = await kernel.run_consolidation_async(graph_store=graph_store)
                logger.info("Memory consolidation: %s", result)
            except Exception:
                logger.debug("Memory consolidation failed", exc_info=True)
    except asyncio.CancelledError:
        return


# ---------------------------------------------------------------------------
# Tool registry construction (stays near lifespan, references _get_indexer)
# ---------------------------------------------------------------------------


def _build_tool_registry(get_indexer: Any) -> Any:
    """Build the server-side :class:`ToolRegistry` with built-in + domain + custom tools."""
    from dan.executors.tool import ToolRegistry

    registry = ToolRegistry()
    builtin = registry.register_builtin_tools()
    logger.info("Registered %d built-in tools: %s", len(builtin), builtin)

    from dan.server.tools import register_server_tools

    register_server_tools(registry, get_indexer=get_indexer)

    custom_tools_dir = Path(os.environ.get("DAN_CUSTOM_TOOLS_DIR", "custom_tools"))
    if custom_tools_dir.is_dir():
        try:
            from dan.meta.authoring import RuntimeAuthor

            custom_ids = RuntimeAuthor.register_custom_tools(custom_tools_dir, registry)
            if custom_ids:
                logger.info(
                    "Registered %d custom tools: %s", len(custom_ids), custom_ids
                )
        except Exception:
            logger.debug("Custom tool discovery failed", exc_info=True)

    return registry


# ---------------------------------------------------------------------------
# Phased initialisation
# ---------------------------------------------------------------------------


def init_learning_tiers() -> None:
    """Resolve learning tier config and set env vars accordingly."""
    try:
        from dan.engine.learning_tiers import (
            features_enabled_at_tier,
            resolve_learning_tier,
        )

        _lt = resolve_learning_tier()
        _enabled = features_enabled_at_tier(_lt)
        if "prompt_variant_proposals" in _enabled or "ab_prompt_promotion" in _enabled:
            os.environ.setdefault("DAN_PROMPT_OPTIMIZATION", "1")
        if "model_recommendations" in _enabled:
            os.environ.setdefault("DAN_MODEL_LEARNING", "1")
        if "topology_suggestions" in _enabled:
            os.environ.setdefault("DAN_TOPOLOGY_LEARNING", "1")
        if "skill_refinement" in _enabled:
            os.environ.setdefault("DAN_SKILL_LEARNING", "1")
        if _lt >= 1:
            logger.info(
                "Learning tier %d active — enabled features: %s",
                _lt,
                ", ".join(sorted(_enabled)),
            )
    except Exception:
        logger.debug("Tier-based learning activation skipped", exc_info=True)

    if os.environ.get("DAN_LEARNING_MODE") == "1":
        os.environ.setdefault("DAN_PROMPT_OPTIMIZATION", "1")
        os.environ.setdefault("DAN_MODEL_LEARNING", "1")
        os.environ.setdefault("DAN_TOPOLOGY_LEARNING", "1")
        os.environ.setdefault("DAN_SKILL_LEARNING", "1")


async def init_stores(state: AppState) -> None:
    """Phase 1: create stores and block registry."""
    from dan.blocks import BlockRegistry
    from dan.server.run_store import RunStore

    graphs_dir = state.graphs_dir
    runs_dir = os.environ.get("DAN_RUNS_DIR", os.path.join(graphs_dir, "runs"))
    state.run_store = RunStore(base_dir=runs_dir)

    workspace_root = resolve_workspace_root()
    state.block_registry = BlockRegistry(workspace=Path(workspace_root))
    try:
        state.block_registry.scan()
    except OSError as exc:
        _record_startup_degradation(
            state,
            "block_registry",
            "scan degraded; continuing with partial in-memory registry",
        )
        logger.warning(
            "Block registry scan degraded during startup; continuing with partial in-memory registry: %s",
            exc,
        )

    state.furnace_enabled = os.environ.get(
        "DAN_FURNACE_API_ENABLED", "1"
    ).lower() in ("1", "true", "yes")
    if state.furnace_enabled:
        from dan.engine.recipe.session_store import FurnaceSessionStore

        try:
            furnace_dir = os.environ.get("DAN_FURNACE_DIR")
            state.furnace_session_store = FurnaceSessionStore(
                base_dir=furnace_dir or None
            )
            logger.info("Furnace API enabled (sessions dir: %s)", state.furnace_session_store._base_dir)
        except OSError as exc:
            state.furnace_enabled = False
            state.furnace_session_store = None
            _record_startup_degradation(
                state,
                "furnace_api",
                "disabled during startup; recipe sessions unavailable",
            )
            logger.warning(
                "Furnace startup degraded; disabling Furnace API for this process: %s",
                exc,
            )


async def init_engine(state: AppState) -> None:
    """Phase 2: create EngineConfig, TierTracker, TelemetryStore, RunManager."""
    os.environ["DAN_WORKSPACE_ROOT"] = resolve_workspace_root()
    engine_config = _get_engine_config()
    engine_config.block_registry = state.block_registry
    state.engine_config = engine_config

    from dan.server.runtime_config import provider_readiness_summary

    provider_summary = provider_readiness_summary(engine_config)
    for issue in provider_summary["issues"]:
        _record_startup_degradation(
            state,
            str(issue.get("subsystem") or "providers"),
            str(issue.get("message") or "provider readiness issue"),
        )

    from dan.providers.tier_tracker import TierSuccessTracker

    memory_dir = getattr(engine_config, "memory_dir", "./memory")
    engine_config.tier_tracker = TierSuccessTracker(Path(memory_dir))

    state.telemetry_store = None
    try:
        from dan.server.telemetry import get_telemetry_store

        state.telemetry_store = get_telemetry_store()
        logger.info(
            "Telemetry store initialized (%s)", type(state.telemetry_store).__name__
        )
        asyncio.create_task(
            _deferred_telemetry_prune(state.telemetry_store),
            name="deferred-telemetry-prune",
        )
    except Exception:
        _record_startup_degradation(
            state,
            "telemetry",
            "telemetry store failed to initialize",
        )
        logger.debug("Telemetry store init skipped", exc_info=True)

    # Lazy indexer — closure captures engine config
    _rag_indexer: Any = None
    _rag_lock = asyncio.Lock()

    async def _get_indexer():
        nonlocal _rag_indexer
        if _rag_indexer is not None:
            return _rag_indexer
        async with _rag_lock:
            if _rag_indexer is not None:
                return _rag_indexer
            from dan.rag import build_embedding_registry
            from dan.rag.indexer import Indexer
            from dan.rag.stores import VectorStoreConfig, VectorStoreFactory

            config = _get_engine_config()
            model = config.default_embedding_model
            registry = build_embedding_registry(config)
            provider = registry.resolve(model)
            backend = os.environ.get("DAN_RAG_STORE_BACKEND", "memory")
            persist_dir = os.environ.get("DAN_RAG_PERSIST_DIR", "./rag_data")
            store = VectorStoreFactory.create(
                VectorStoreConfig(backend=backend, persist_directory=persist_dir),
            )
            _rag_indexer = Indexer(
                embedding_provider=provider,
                embedding_model=model,
                store=store,
            )
            return _rag_indexer

    from dan.server.run_manager import RunManager

    state.run_manager = RunManager(
        engine_config=engine_config,
        tool_registry=_build_tool_registry(_get_indexer),
        run_store=state.run_store,
        telemetry_store=state.telemetry_store,
        graph_loader=state.graph_store.get_graph,
    )
    # Expose the indexer getter for app-level RAG endpoints
    state._get_indexer = _get_indexer  # type: ignore[attr-defined]


async def init_capabilities(state: AppState) -> None:
    """Phase 3: create ChatCapabilityRegistry, register 7 capability groups, create CapabilityContext."""
    from dan.server.capability_handlers import (
        register_common_capabilities,
    )
    from dan.server.capability_registry import CapabilityContext, ChatCapabilityRegistry

    state.capability_registry = ChatCapabilityRegistry()
    register_common_capabilities(state.capability_registry)

    state.capability_context = CapabilityContext(
        workflow_id="",
        graph_store=state.graph_store,
        run_manager=state.run_manager,
        run_store=state.run_store,
        graphs_dir=state.graphs_dir,
        test_case_store=state.test_case_store,
    )

    state.mcp_bridge = await _initialize_mcp_bridge_for_server(
        state=state,
        capability_registry=state.capability_registry,
        tool_registry=(
            state.run_manager.tool_registry if state.run_manager is not None else None
        ),
        capability_context=state.capability_context,
    )


async def init_managers(state: AppState) -> None:
    """Phase 4: create MentionResolver, load memory subsystems, create ChatManager."""
    import time as _time
    _p4_t0 = _time.perf_counter()

    def _p4_ms() -> str:
        return f"{(_time.perf_counter() - _p4_t0) * 1000:.0f}ms"

    from dan.server.chat_manager import ChatManager
    from dan.server.mention_resolver import CodeResolver, MentionResolver
    logger.debug("  phase 4 sub: imports done [%s]", _p4_ms())

    workspace_root = resolve_workspace_root()
    state.mention_resolver = MentionResolver(
        workspace_root=workspace_root,
        chat_store=state.chat_store,
    )

    # User profile
    state.user_profile = None
    try:
        from dan.engine.user_profile import load_user_profile

        state.user_profile = load_user_profile()
    except Exception:
        logger.debug("UserProfile load skipped", exc_info=True)

    # Conversation memory
    state.conversation_memory = None
    try:
        from dan.engine.conversation_memory import ConversationMemoryStore

        state.conversation_memory = ConversationMemoryStore()
    except Exception:
        logger.debug("Conversation memory load skipped", exc_info=True)
    logger.debug("  phase 4 sub: memory stores loaded [%s]", _p4_ms())

    # Memory kernel — constructor is fast; import_profile / import_all are
    # deferred to a background task because they scan the full conversation
    # history and can take 30+ seconds on a well-used installation.
    state.memory_kernel = None
    try:
        from functools import partial

        from dan.engine.memory_kernel import DualWriteAdapter, MemoryKernel
        from dan.server.concierge.domain_learning import (
            consolidate_memory_kernel_domain_templates,
        )
        from dan.server.concierge.feature_gates import engine_feature_enabled

        state.memory_kernel = MemoryKernel(
            base_dir=_isolated_memory_kernel_base_dir(state),
            dual_write_adapter=DualWriteAdapter(
                conversation_memory=state.conversation_memory,
                user_profile=state.user_profile,
            ),
            domain_consolidation_hook=partial(
                consolidate_memory_kernel_domain_templates,
                feature_enabled=engine_feature_enabled,
            ),
        )
    except Exception:
        logger.debug("Memory kernel load skipped", exc_info=True)

    if state.memory_kernel is not None:
        def _sync_memory_kernel_import() -> None:
            """Run in a thread so the blocking I/O doesn't stall the event loop."""
            try:
                from dan.engine.memory_adapters import (
                    ConversationAdapter,
                    ProfileAdapter,
                )

                if state.user_profile:
                    p_imported = ProfileAdapter.import_profile(
                        state.user_profile, state.memory_kernel,
                    )
                    if p_imported:
                        logger.debug(
                            "Imported %d profile items into memory kernel",
                            p_imported,
                        )
                if state.conversation_memory:
                    c_imported = ConversationAdapter.import_all(
                        state.conversation_memory, state.memory_kernel,
                    )
                    if c_imported:
                        logger.debug(
                            "Imported %d conversation items into memory kernel",
                            c_imported,
                        )
            except Exception:
                logger.debug(
                    "Deferred memory kernel import failed", exc_info=True,
                )

        asyncio.create_task(
            asyncio.to_thread(_sync_memory_kernel_import),
            name="deferred-memory-kernel-import",
        )
    logger.debug("  phase 4 sub: memory kernel done [%s]", _p4_ms())

    if state.memory_kernel is not None and state.run_manager is not None:
        state.run_manager._memory_kernel = state.memory_kernel
        state.run_manager._config.memory_kernel = state.memory_kernel

    _provider_registry = _build_chat_provider_registry()
    logger.debug("  phase 4 sub: provider registry built [%s]", _p4_ms())

    try:
        state.model_gateway = _build_model_gateway(state.engine_config)
        logger.debug("  phase 4 sub: model gateway built [%s]", _p4_ms())
        if state.run_manager is not None:
            state.run_manager.model_gateway = state.model_gateway
    except Exception:
        logger.warning("ModelGateway construction failed; degrading", exc_info=True)
        _record_startup_degradation(
            state,
            "model_gateway",
            "construction failed; shared gateway unavailable",
        )

    state.chat_manager = ChatManager(
        provider_registry=_provider_registry,
        graph_store=state.graph_store,
        mention_resolver=state.mention_resolver,
        chat_store=state.chat_store,
        capability_registry=state.capability_registry,
        capability_context=state.capability_context,
        user_profile=state.user_profile,
        conversation_memory=state.conversation_memory,
        memory_kernel=state.memory_kernel,
        telemetry_store=state.telemetry_store,
    )
    state.chat_manager.model_gateway = state.model_gateway
    logger.debug("  phase 4 sub: ChatManager created [%s]", _p4_ms())

    state.capability_context.chat_manager = state.chat_manager


async def init_integrations(state: AppState, app: FastAPI) -> None:
    """Phase 5: PublishRegistry, gateway, notifications, concierge/dispatcher."""
    from dan.publish.http_server import PublishRegistry, create_publish_router

    engine_config = state.engine_config
    state.publish_registry = PublishRegistry(engine_config)
    state.publish_registry.human_timeout = 300.0

    from dan.publish.runtime import LocalRuntime

    state.publish_registry.runtime = LocalRuntime(
        engine_config=engine_config,
        human_timeout=300.0,
        model_gateway=state.model_gateway,
    )
    _auto_register_published_workflows(
        state.publish_registry, state.graph_store, state.graphs_dir
    )
    router = create_publish_router(state.publish_registry)
    app.include_router(router, prefix="/api/published", tags=["published"])

    state.capability_context.publish_registry = state.publish_registry
    state.capability_context.block_registry = state.block_registry

    from dan.server.capability_handlers import register_publish_capabilities

    register_publish_capabilities(state.capability_registry)

    from dan.server.gateway.router import init_gateway
    from dan.server.gateway.router import router as gateway_router

    def _build_meta_controller_factory() -> tuple[Any, Any, Any]:
        return build_meta_controller_from_state(state)

    state.build_meta_controller = _build_meta_controller_factory

    workspace_root = resolve_workspace_root()
    workspace_root_path = Path(workspace_root)
    init_gateway(
        run_manager=state.run_manager,
        workspace=workspace_root_path,
        graph_store=state.graph_store,
        build_meta_controller=state.build_meta_controller,
    )
    app.include_router(gateway_router)

    # Notifications
    state.notification_manager = None
    try:
        from dan.notifications import NotificationManager, load_notification_config
        from dan.server.gateway.router import _event_bus as _gw_event_bus

        if _gw_event_bus is not None:
            state.capability_context.event_bus = _gw_event_bus
            state.notification_manager = NotificationManager(
                load_notification_config()
            )
            await state.notification_manager.start(_gw_event_bus)
            logger.info(
                "Notification manager started with %d channel(s)",
                len(state.notification_manager.channels),
            )
    except Exception:
        _record_startup_degradation(
            state,
            "notifications",
            "notification manager failed to initialize",
        )
        logger.warning("Notification manager startup failed", exc_info=True)

    try:
        from dan.server.gateway.router import _activity_tracker as _gw_tracker

        if _gw_tracker is not None:
            state.capability_context.activity_tracker = _gw_tracker
    except ImportError:
        pass

    # Self-knowledge RAG indexing (19-5) — deferred to background task to avoid
    # blocking startup (refresh involves embedding operations).
    state.self_knowledge_index = None

    async def _deferred_self_knowledge() -> None:
        try:
            from dan.rag import DEFAULT_EMBEDDING_MODEL, build_embedding_registry
            from dan.meta.self_knowledge import SelfKnowledgeIndex

            embedding_registry = build_embedding_registry(engine_config)
            _emb_model = getattr(
                engine_config, "default_embedding_model", DEFAULT_EMBEDDING_MODEL
            )
            if not embedding_registry.has_provider("default"):
                return
            provider = embedding_registry.resolve(_emb_model)
            _project_root = Path(__file__).resolve().parents[3]
            docs_dir = _project_root / "docs"
            doc_paths = [
                p for fname in ("llm-api-guide.md", "architecture.md")
                if (p := docs_dir / fname).exists()
            ]
            if not doc_paths:
                return
            idx = SelfKnowledgeIndex(
                embedding_provider=provider, embedding_model=_emb_model,
            )
            examples_dir = _project_root / "examples"
            await idx.refresh(
                doc_paths=doc_paths,
                examples_dir=examples_dir if examples_dir.is_dir() else None,
                tool_registry=(
                    state.run_manager._tool_registry if state.run_manager else None
                ),
            )
            state.self_knowledge_index = idx
            app.state.self_knowledge_index = idx
            logger.info("Self-knowledge index refreshed (%d docs)", len(doc_paths))
        except Exception:
            _record_startup_degradation(
                state, "self_knowledge",
                "self-knowledge indexing failed to initialize",
            )
            logger.debug("Self-knowledge indexing skipped", exc_info=True)

    state._deferred_self_knowledge_task = asyncio.create_task(
        _deferred_self_knowledge(), name="deferred-self-knowledge",
    )
    app.state.self_knowledge_index = state.self_knowledge_index

    # Wire experience/discovery into capability context (25-2)
    try:
        _exp_index = get_experience_index_from_state(state)
    except Exception:
        _exp_index = None
    _exp_store = build_experience_store_from_state(
        state,
        with_index=_exp_index is not None,
    )

    if state.memory_kernel is not None:
        if getattr(state.memory_kernel, "_dual_write", None) is not None:
            state.memory_kernel._dual_write._experience_store = _exp_store

        async def _deferred_experience_import() -> None:
            try:
                from dan.engine.memory_adapters import ExperienceAdapter

                imported = await ExperienceAdapter.import_all(
                    _exp_store, state.memory_kernel,
                )
                if imported:
                    logger.debug(
                        "Imported %d workflow experiences into memory kernel",
                        imported,
                    )
            except Exception:
                logger.debug(
                    "Experience import into memory kernel skipped", exc_info=True,
                )

        asyncio.create_task(
            _deferred_experience_import(), name="deferred-experience-import",
        )

    from dan.meta.discovery import DiscoveryService
    from dan.server.skill_library import SKILL_LIBRARY

    state.capability_context.experience_store = _exp_store
    state.capability_context.experience_index = _exp_index
    state.capability_context.discovery_service = DiscoveryService(
        experience_index=_exp_index,
        experience_store=_exp_store,
        graph_store=state.graph_store,
        tool_registry=(
            state.run_manager.tool_registry if state.run_manager else None
        ),
        self_knowledge=state.self_knowledge_index,
        memory_kernel=state.memory_kernel,
        skill_library=SKILL_LIBRARY,
    )
    state.capability_context.principle_store = (
        state.run_manager._get_principle_store() if state.run_manager else None
    )

    # Concierge + dispatcher
    try:
        from dan.server.concierge import build_concierge

        meta_controller, _meta_planner, _meta_store = state.build_meta_controller()

        result = build_concierge(
            chat_manager=state.chat_manager,
            capability_context=state.capability_context,
            user_profile=state.user_profile,
            conversation_memory=state.conversation_memory,
            meta_controller=meta_controller,
            memory_kernel=state.memory_kernel,
            enable_dispatcher=True,
            mcp_bridge=state.mcp_bridge,
            capability_registry=state.capability_registry,
            tool_registry=(
                state.run_manager.tool_registry
                if state.run_manager is not None
                else None
            ),
            telemetry_store=state.telemetry_store,
        )
        if isinstance(result, tuple):
            state.concierge, state.dispatcher = result
        else:
            state.concierge = result
            state.dispatcher = None
    except Exception:
        _record_startup_degradation(
            state,
            "concierge",
            "concierge dispatcher failed to initialize",
        )
        logger.warning("Concierge startup failed", exc_info=True)
        state.concierge = None
        state.dispatcher = None


async def init_background(state: AppState, app: FastAPI) -> None:
    """Phase 6: scheduler, follow-ups, consolidation loop, skill store, feature log."""
    async def _dispatch_scheduled_action(
        action: str,
        trigger_context: Any,
        delivery_target: Any,
        *,
        entry: Any | None = None,
    ) -> str:
        scheduled_workflow_id = str(
            getattr(entry, "workflow_id", "") or ""
        ).strip()
        if scheduled_workflow_id:
            if state.run_manager is None or state.graph_store is None:
                raise RuntimeError("Workflow scheduling is unavailable.")

            graph_dict = state.graph_store.get_graph(scheduled_workflow_id)
            if graph_dict is None:
                raise RuntimeError(
                    f"Workflow '{scheduled_workflow_id}' not found."
                )
            from dan.server.workflow_guards import (
                WorkflowContractError,
            )

            try:
                handle = await state.run_manager.launch_run(
                    graph_dict,
                    graph_id=scheduled_workflow_id,
                    inputs=getattr(entry, "workflow_inputs", None) or None,
                    run_policy=getattr(entry, "workflow_run_policy", None) or None,
                    bus=getattr(state.capability_context, "event_bus", None),
                    surface_id=(
                        delivery_target.conversation_key
                        or delivery_target.user_id
                        or trigger_context.thread_key
                    ),
                    guard_action="schedule_execution",
                )
            except WorkflowContractError as exc:
                raise RuntimeError(str(exc)) from exc

            inputs = getattr(entry, "workflow_inputs", None) or None
            record = handle.record

            input_keys = sorted((inputs or {}).keys())
            if input_keys:
                return (
                    f"Started workflow `{scheduled_workflow_id}` as run `{record.run_id}` "
                    f"with inputs: {', '.join(input_keys)}."
                )
            return f"Started workflow `{scheduled_workflow_id}` as run `{record.run_id}`."

        return await _dispatch_scheduled_chat_action_via_router(
            state,
            action,
            trigger_context,
            delivery_target,
        )

    # 31-7: Scheduled task background loop
    state.task_scheduler = None
    try:
        from dan.server.concierge.scheduler import (
            SchedulerAuthority,
            create_server_scheduler,
            resolve_scheduler_authority,
        )

        _schedule_store = getattr(state.concierge, "_schedule_store", None)
        _schedule_history_store = getattr(
            state.concierge, "_schedule_history_store", None
        )
        if _schedule_store is not None:
            _sched_authority = resolve_scheduler_authority()
            if _sched_authority == SchedulerAuthority.SERVICE:
                logger.info(
                    "Service daemon holds schedule lease — server scheduler deferred"
                )
            else:
                state.task_scheduler = create_server_scheduler(
                    store=_schedule_store,
                    dispatch_fn=_dispatch_scheduled_action,
                    history_store=_schedule_history_store,
                    event_bus=getattr(state.capability_context, "event_bus", None),
                )
                if state.concierge is not None:
                    state.concierge._task_scheduler = state.task_scheduler
                await state.task_scheduler.start()
                app.state.task_scheduler = state.task_scheduler
                logger.info("Task scheduler started (authority=SERVER)")
    except Exception:
        _record_startup_degradation(
            state,
            "task_scheduler",
            "task scheduler failed to initialize",
        )
        logger.debug("Task scheduler startup skipped", exc_info=True)

    # 31-15: Learning tier activation — store resolved tier on concierge
    try:
        from dan.engine.learning_tiers import resolve_learning_tier

        _learning_tier = resolve_learning_tier()
        if state.concierge is not None:
            state.concierge._learning_tier = _learning_tier
        logger.info("Learning tier: %d", _learning_tier)
    except Exception:
        logger.debug("Learning tier resolution skipped", exc_info=True)

    # 31-13: Surface presence tracker
    try:
        _presence_tracker = getattr(state.concierge, "_presence_tracker", None)  # noqa: F841
        logger.info("Presence tracker initialized")
    except Exception:
        logger.debug("Presence tracker initialization skipped", exc_info=True)

    # 31-17: Computer control config
    try:
        from dan.server.concierge.computer_policy import ComputerControlConfig

        _computer_config = getattr(
            state.concierge, "_computer_config", None
        ) or ComputerControlConfig.load()
        if state.concierge is not None:
            state.concierge._computer_config = _computer_config
        logger.info(
            "Computer control config loaded (browser=%s, desktop=%s)",
            (
                _computer_config.chunk_policies.browser.enabled
                if hasattr(_computer_config, "chunk_policies")
                else "N/A"
            ),
            (
                _computer_config.chunk_policies.window.enabled
                if hasattr(_computer_config, "chunk_policies")
                else "N/A"
            ),
        )
    except Exception:
        logger.debug("Computer control config load skipped", exc_info=True)

    # Skill store
    state.skill_store = None
    try:
        from dan.server.skill_store import SkillStore, default_external_skill_dirs

        _extra_dirs: list[Path] = default_external_skill_dirs()
        _legacy_dir = Path(os.environ.get("DAN_CUSTOM_SKILLS_DIR", "custom_skills"))
        if _legacy_dir.is_dir():
            _extra_dirs.append(_legacy_dir)

        _project_skills = Path(
            os.environ.get("DAN_PROJECT_SKILLS_DIR", ".dan/skills")
        )

        state.skill_store = SkillStore(
            project_dir=_project_skills if _project_skills.is_dir() else None,
            extra_dirs=_extra_dirs,
        )
        _n_skills = state.skill_store.scan()
        _n_added = state.skill_store.populate_skill_library()
        if state.concierge is not None:
            state.concierge._skill_store = state.skill_store
        if _n_skills:
            logger.info(
                "Skill store: %d skill(s) loaded (%d new), dirs=%s",
                _n_skills,
                _n_added,
                [
                    str(d)
                    for d in [
                        state.skill_store.user_dir,
                        state.skill_store.project_dir,
                    ]
                    + _extra_dirs
                    if d
                ],
            )
    except Exception:
        _record_startup_degradation(
            state,
            "skill_store",
            "skill store failed to initialize",
        )
        logger.debug("Skill store startup failed", exc_info=True)

    # Memory consolidation loop
    state.consolidation_task = None
    if state.memory_kernel is not None:
        _consolidation_interval_hours = float(
            os.environ.get("DAN_MEMORY_CONSOLIDATION_INTERVAL", "6")
        )
        if _consolidation_interval_hours > 0:
            state.consolidation_task = asyncio.create_task(
                _consolidation_loop(
                    state.memory_kernel,
                    _consolidation_interval_hours,
                    state.graph_store,
                )
            )
            logger.info(
                "Memory consolidation scheduled every %.1f hours",
                _consolidation_interval_hours,
            )

    # Startup feature log
    engine_config = state.engine_config
    model_name = getattr(engine_config, "llm_default_model", "claude-sonnet-4-6")
    tier_policy = (
        "on" if os.environ.get("DAN_ENABLE_TIER_POLICY") == "1" else "off"
    )
    try:
        from dan.engine.learning_tiers import (
            features_enabled_at_tier,
            resolve_learning_tier,
        )

        _startup_tier = resolve_learning_tier()
        _startup_features = features_enabled_at_tier(_startup_tier)
        learning = (
            f"tier {_startup_tier} ({', '.join(sorted(_startup_features))})"
        )
    except Exception:
        learning = "on" if os.environ.get("DAN_LEARNING_MODE") == "1" else "off"

    mcp_count = (
        len(state.mcp_bridge._clients)
        if state.mcp_bridge and hasattr(state.mcp_bridge, "_clients")
        else 0
    )
    notif_count = (
        len(state.notification_manager.channels)
        if state.notification_manager
        else 0
    )
    autonomy = os.environ.get("DAN_CONCIERGE_AUTONOMY", "auto")
    scheduler_status = "on" if state.task_scheduler is not None else "off"

    logger.info(
        "DAN Server started | Model: %s | Tier: %s | Learning: %s"
        " | MCP: %d server(s) | Notifications: %d channel(s)"
        " | Autonomy: %s | Scheduler: %s",
        model_name,
        tier_policy,
        learning,
        mcp_count,
        notif_count,
        autonomy,
        scheduler_status,
    )
    log_startup_degradation_summary(state)


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Adapter auto-start
# ---------------------------------------------------------------------------

_ADAPTERS_STATE_PATH = Path.home() / ".dan" / "adapters-state.json"
_TELEGRAM_CONFIG_PATH = Path.home() / ".dan" / "telegram" / "config.json"
_WHATSAPP_WEB_CONFIG_PATH = Path.home() / ".dan" / "whatsapp-web" / "config.json"
_WHATSAPP_WEB_DB_PATH = Path.home() / ".dan" / "whatsapp-web" / "session.sqlite3"
_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH = (
    Path.home() / ".dan" / "wechat-official-account" / "config.json"
)


async def init_adapters(app: FastAPI) -> None:
    """Restore adapters that have auto_start=True in their config."""
    if _skip_adapter_autostart_for_current_process(app):
        logger.info("Skipping adapter autostart under pytest-style test execution")
        return

    adapters_to_start: list[tuple[str, dict[str, Any]]] = []

    previously_running: set[str] = set()
    try:
        if _ADAPTERS_STATE_PATH.exists():
            entries = json.loads(_ADAPTERS_STATE_PATH.read_text())
            for entry in entries:
                if isinstance(entry, dict):
                    previously_running.add(str(entry.get("type", "")))
    except Exception:
        logger.debug("Failed to read adapters state file", exc_info=True)

    try:
        if _TELEGRAM_CONFIG_PATH.exists():
            raw = json.loads(_TELEGRAM_CONFIG_PATH.read_text())
            bots = raw.get("bots", {})
            desktop_bot = bots.get("desktop-ui", {})
            token = str(desktop_bot.get("token", "")).strip()
            auto_start = bool(desktop_bot.get("auto_start", False))
            should_start = auto_start or "telegram" in previously_running
            if should_start and token:
                adapters_to_start.append(("telegram", {"bot_token": token}))
    except Exception:
        logger.warning("Failed to read Telegram config for autostart", exc_info=True)

    try:
        if _WHATSAPP_WEB_CONFIG_PATH.exists():
            raw = json.loads(_WHATSAPP_WEB_CONFIG_PATH.read_text())
            auto_start = bool(raw.get("auto_start", False))
            db_path = str(raw.get("db_path", "")).strip() or str(_WHATSAPP_WEB_DB_PATH)
            should_start = auto_start or "whatsapp-web" in previously_running
            if should_start and Path(db_path).exists():
                adapters_to_start.append(("whatsapp-web", {"db_path": db_path}))
    except Exception:
        logger.warning("Failed to read WhatsApp Web config for autostart", exc_info=True)

    try:
        if _WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH.exists():
            raw = json.loads(_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH.read_text())
            auto_start = bool(raw.get("auto_start", False))
            should_start = auto_start or "wechat" in previously_running
            app_id = str(raw.get("app_id", "")).strip()
            app_secret = str(raw.get("app_secret", "")).strip()
            token = str(raw.get("token", "")).strip()
            encoding_aes_key = str(raw.get("encoding_aes_key", "")).strip()
            webhook_url = str(raw.get("webhook_url", "")).strip()
            callback_path = str(raw.get("callback_path", "")).strip()
            account_name = str(raw.get("account_name", "")).strip()
            app_name = str(raw.get("app_name", "")).strip()
            has_welcome_message = "welcome_message" in raw
            welcome_message = str(raw.get("welcome_message", "")).strip()
            support_encrypted_callbacks = bool(raw.get("support_encrypted_callbacks", False))
            passive_reply_budget_seconds = raw.get("passive_reply_budget_seconds")
            passive_reply_fallback_text = str(
                raw.get("passive_reply_fallback_text", "")
            ).strip()
            api_base_url = str(raw.get("api_base_url", "")).strip()
            access_token_refresh_margin_seconds = raw.get(
                "access_token_refresh_margin_seconds"
            )
            server_url = str(raw.get("server_url", "")).strip()
            # Token is the minimum viable runtime credential for callback verification.
            # Reference-only fields like webhook_url/server_url should not trigger autostart.
            has_runtime_config = bool(token)
            if should_start and has_runtime_config:
                config: dict[str, Any] = {}
                if app_id:
                    config["app_id"] = app_id
                if app_secret:
                    config["app_secret"] = app_secret
                if token:
                    config["token"] = token
                if encoding_aes_key:
                    config["encoding_aes_key"] = encoding_aes_key
                if webhook_url:
                    config["webhook_url"] = webhook_url
                if callback_path:
                    config["callback_path"] = callback_path
                if account_name:
                    config["account_name"] = account_name
                if app_name:
                    config["app_name"] = app_name
                if has_welcome_message:
                    config["welcome_message"] = welcome_message
                if support_encrypted_callbacks:
                    config["support_encrypted_callbacks"] = True
                if passive_reply_budget_seconds is not None:
                    config["passive_reply_budget_seconds"] = passive_reply_budget_seconds
                if passive_reply_fallback_text:
                    config["passive_reply_fallback_text"] = passive_reply_fallback_text
                if api_base_url:
                    config["api_base_url"] = api_base_url
                if access_token_refresh_margin_seconds is not None:
                    config["access_token_refresh_margin_seconds"] = (
                        access_token_refresh_margin_seconds
                    )
                if server_url:
                    config["server_url"] = server_url
                adapters_to_start.append(("wechat", config))
    except Exception:
        logger.warning("Failed to read WeChat config for autostart", exc_info=True)

    if not adapters_to_start:
        return

    async def _start_adapter_safe(adapter_type: str, config: dict[str, Any]) -> None:
        try:
            from dan.server.routers.adapters import (
                AdapterStartRequest,
                start_adapter,
            )

            req = AdapterStartRequest(type=adapter_type, config=config)
            result = await start_adapter(req)
            logger.info(
                "Auto-started %s adapter (id=%s)",
                adapter_type, result.get("adapter_id"),
            )
        except Exception:
            logger.warning("Failed to auto-start %s adapter", adapter_type, exc_info=True)

    for i, (adapter_type, config) in enumerate(adapters_to_start):
        if i > 0:
            await asyncio.sleep(2)
        asyncio.create_task(
            _start_adapter_safe(adapter_type, config),
            name=f"autostart-{adapter_type}",
        )


def _scheduled_surface_type(trigger_context: Any, delivery_target: Any) -> str:
    return str(
        getattr(delivery_target, "surface", "")
        or getattr(trigger_context, "source_surface", "")
        or "schedule"
    ).strip() or "schedule"


def _scheduled_surface_id(trigger_context: Any, delivery_target: Any) -> str:
    return str(
        getattr(delivery_target, "conversation_key", "")
        or getattr(delivery_target, "user_id", "")
        or getattr(trigger_context, "thread_key", "")
        or getattr(trigger_context, "user_id", "")
        or getattr(trigger_context, "task_id", "")
        or getattr(delivery_target, "project_id", "")
        or "scheduled-task"
    ).strip() or "scheduled-task"


def _build_scheduled_chat_request(
    action: str,
    trigger_context: Any,
    delivery_target: Any,
):
    from dan.server.routers.chat import ChatMessageRequest

    surface_type = _scheduled_surface_type(trigger_context, delivery_target)
    surface_id = _scheduled_surface_id(trigger_context, delivery_target)
    thread_id = str(
        getattr(delivery_target, "thread_key", "")
        or getattr(trigger_context, "thread_key", "")
        or surface_id
    ).strip() or surface_id
    trigger_payload = (
        trigger_context.model_dump(mode="json")
        if hasattr(trigger_context, "model_dump")
        else {}
    )
    delivery_payload = (
        delivery_target.model_dump(mode="json")
        if hasattr(delivery_target, "model_dump")
        else {}
    )
    return ChatMessageRequest(
        workflow_id="_scratch",
        message=str(action or ""),
        history=[],
        thread_id=thread_id,
        session_id=thread_id,
        mode="auto",
        surface=f"{surface_type}:{surface_id}",
        surface_type=surface_type,
        surface_id=surface_id,
        surface_context={
            "scheduled_trigger": True,
            "trigger_context": trigger_payload,
            "delivery_target": delivery_payload,
        },
    )


def _build_state_request_proxy(state: AppState) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(dan=state))
    )


async def _dispatch_scheduled_chat_action_via_router(
    state: AppState,
    action: str,
    trigger_context: Any,
    delivery_target: Any,
) -> str:
    from dan.server.routers.chat import (
        chat_message,
        iter_local_chat_stream_events,
    )
    from dan.server.terminal_output import collect_terminal_content

    req = _build_scheduled_chat_request(
        action,
        trigger_context,
        delivery_target,
    )
    response = await chat_message(
        _build_state_request_proxy(state),
        req,
        concierge=True,
    )
    channel_id = str(response.get("stream_channel_id") or "").strip()
    if not channel_id:
        raise RuntimeError("Scheduled action did not produce a response stream")
    reassurance_messages = (
        set(getattr(state.concierge, "_REASSURANCE_MESSAGES", []))
        if getattr(state, "concierge", None) is not None
        else set()
    )
    return await collect_terminal_content(
        iter_local_chat_stream_events(channel_id),
        reassurance_messages=reassurance_messages,
    )


def _persist_adapter_state() -> None:
    """Write active adapter types to disk so init_adapters can restore them."""
    try:
        from dan.server.routers.adapters import (
            _active_adapters,
            _adapter_surface_types,
        )

        entries = []
        for aid, (adapter, task) in _active_adapters.items():
            if task.done():
                continue
            surface = _adapter_surface_types.get(aid, "unknown")
            entries.append({"adapter_id": aid, "type": surface})

        _ADAPTERS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ADAPTERS_STATE_PATH.write_text(json.dumps(entries, indent=2))
        logger.info("Persisted %d adapter(s) state to %s", len(entries), _ADAPTERS_STATE_PATH)
    except Exception:
        logger.debug("Failed to persist adapter state", exc_info=True)


async def shutdown(state: AppState) -> None:
    """Cleanly tear down all background tasks and integrations."""
    _persist_adapter_state()

    if state.task_scheduler is not None:
        try:
            await state.task_scheduler.stop()
        except Exception:
            logger.debug("Task scheduler shutdown failed", exc_info=True)

    if state.consolidation_task is not None and not state.consolidation_task.done():
        state.consolidation_task.cancel()
        try:
            await state.consolidation_task
        except asyncio.CancelledError:
            pass

    if state.run_manager is not None:
        try:
            await state.run_manager.shutdown()
        except Exception:
            logger.debug("Run manager shutdown failed", exc_info=True)

    if state.telemetry_store is not None:
        try:
            await state.telemetry_store.close()
        except Exception:
            logger.debug("Telemetry store shutdown failed", exc_info=True)

    if state.notification_manager is not None:
        try:
            from dan.server.gateway.router import _event_bus as _gw_event_bus

            if _gw_event_bus is not None:
                await state.notification_manager.stop(_gw_event_bus)
        except Exception:
            logger.debug("Notification manager shutdown failed", exc_info=True)
        state.notification_manager = None

    if (
        state.publish_registry is not None
        and state.publish_registry.runtime is not None
    ):
        await state.publish_registry.runtime.close()

    try:
        from dan.server.routers.adapters import _active_adapters

        for aid, (adapter, task) in list(_active_adapters.items()):
            if not task.done():
                task.cancel()
            try:
                await adapter.stop()
            except Exception:
                pass
        _active_adapters.clear()
    except ImportError:
        pass

    await _shutdown_mcp_bridge_for_server(state.mcp_bridge)
    state.mcp_bridge = None

    try:
        from dan.tools._browser_session import close_controller

        await close_controller()
    except Exception:
        logger.debug("Browser session shutdown failed", exc_info=True)


# ---------------------------------------------------------------------------
# Composed lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Async context manager consumed by ``FastAPI(lifespan=...)``.

    Creates an :class:`AppState`, runs phased init, attaches it to
    ``app.state.dan``, yields, then shuts down.
    """
    import time as _time

    _t0 = _time.perf_counter()

    def _phase_ms() -> str:
        return f"{(_time.perf_counter() - _t0) * 1000:.0f}ms"

    from dan.server.graph_store import GraphStore
    from dan.server.chat_store import ChatStore
    from dan.server.test_cases import TestCaseStore

    graphs_dir = resolve_graphs_dir()

    state = AppState(
        graphs_dir=graphs_dir,
        graph_store=GraphStore(base_dir=graphs_dir),
        chat_store=ChatStore(base_dir=graphs_dir),
        test_case_store=TestCaseStore(base_dir=graphs_dir),
    )
    set_active_app_state(state)

    try:
        log_startup_configuration_warnings()
        init_learning_tiers()

        await init_stores(state)
        logger.info("Startup phase 1/6 (stores) [%s]", _phase_ms())

        await init_engine(state)
        logger.info("Startup phase 2/6 (engine) [%s]", _phase_ms())

        await init_capabilities(state)
        logger.info("Startup phase 3/6 (capabilities) [%s]", _phase_ms())

        await init_managers(state)
        logger.info("Startup phase 4/6 (managers) [%s]", _phase_ms())

        await init_integrations(state, app)
        logger.info("Startup phase 5/6 (integrations) [%s]", _phase_ms())

        await init_background(state, app)
        logger.info("Startup phase 6/6 (background) [%s]", _phase_ms())

        app.state.dan = state
        app.state.mcp_bridge = state.mcp_bridge
        app.state.startup_summary = get_startup_degradation_summary(state)

        await init_adapters(app)
        logger.info("Startup complete [%s]", _phase_ms())
    except Exception:
        if get_active_app_state() is state:
            set_active_app_state(None)
        raise

    try:
        yield
    finally:
        try:
            await shutdown(state)
        finally:
            if get_active_app_state() is state:
                set_active_app_state(None)
