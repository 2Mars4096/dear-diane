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
from typing import Any, TYPE_CHECKING

from dan.server.app_state import AppState

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config helpers (moved from app.py)
# ---------------------------------------------------------------------------


def _get_engine_config():
    """Build an :class:`EngineConfig` from environment variables."""
    from dan.engine.executor import EngineConfig
    from dan.providers import ProviderConfig

    providers: dict[str, ProviderConfig] = {}
    embedding_providers: dict[str, ProviderConfig] = {}

    openai_key = os.environ.get("DAN_OPENAI_API_KEY", "")
    if openai_key:
        providers["openai"] = ProviderConfig(api_key=openai_key)

    anthropic_key = os.environ.get("DAN_ANTHROPIC_API_KEY", "")
    if anthropic_key:
        providers["anthropic"] = ProviderConfig(api_key=anthropic_key)

    google_key = os.environ.get("DAN_GOOGLE_API_KEY", "")
    if google_key:
        providers["google"] = ProviderConfig(api_key=google_key)

    from dan.rag import DEFAULT_EMBEDDING_MODEL

    default_embedding_model = os.environ.get(
        "DAN_DEFAULT_EMBEDDING_MODEL",
        DEFAULT_EMBEDDING_MODEL,
    )
    embedding_api_key = os.environ.get(
        "DAN_EMBEDDING_API_KEY",
        openai_key
        or os.environ.get("DAN_LLM_API_KEY", os.environ.get("LLM_API_KEY", "")),
    )
    embedding_base_url = os.environ.get(
        "DAN_EMBEDDING_BASE_URL",
        os.environ.get("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1"),
    )
    if embedding_api_key:
        embedding_providers["default"] = ProviderConfig(
            api_key=embedding_api_key,
            base_url=embedding_base_url,
            default_model=default_embedding_model,
        )

    if os.environ.get("DAN_ENABLE_LOCAL_EMBEDDINGS", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        embedding_providers["local"] = ProviderConfig(
            default_model=os.environ.get(
                "DAN_LOCAL_EMBEDDING_MODEL", "all-MiniLM-L6-v2"
            ),
        )

    default_model_policy = None
    if os.environ.get("DAN_ENABLE_TIER_POLICY", "0").lower() in (
        "1",
        "true",
        "yes",
    ):
        from dan.providers.model_policy import TierPolicy

        tier_map = None
        tier_map_env = os.environ.get("DAN_TIER_MAP")
        if tier_map_env:
            try:
                raw = json.loads(tier_map_env)
                from dan.providers.tier_defaults import normalize_tier_map
                tier_map = normalize_tier_map(raw) or raw
            except Exception:
                logger.warning("Failed to parse DAN_TIER_MAP JSON, using defaults")
        default_model_policy = TierPolicy(tier_map=tier_map)

    return EngineConfig(
        llm_base_url=os.environ.get(
            "DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1"
        ),
        llm_api_key=os.environ.get(
            "DAN_LLM_API_KEY", os.environ.get("LLM_API_KEY", "")
        ),
        llm_default_model=os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6"),
        checkpoint_dir=os.environ.get("DAN_CHECKPOINT_DIR", "./checkpoints"),
        checkpoint_enabled=True,
        providers=providers,
        embedding_providers=embedding_providers,
        default_embedding_model=default_embedding_model,
        default_model_policy=default_model_policy,
        cache_enabled=os.environ.get("DAN_CACHE_ENABLED", "true").lower()
        in ("1", "true", "yes"),
        cache_max_size_mb=int(os.environ.get("DAN_CACHE_MAX_SIZE_MB", "100")),
        cache_dir=os.environ.get("DAN_CACHE_DIR") or None,
        semantic_cache_threshold=float(
            os.environ.get("DAN_SEMANTIC_CACHE_THRESHOLD", "0.95")
        ),
        semantic_cache_ttl_hours=float(
            os.environ.get("DAN_SEMANTIC_CACHE_TTL_HOURS", "24")
        ),
    )


def _build_chat_provider_registry():
    """Create a :class:`ProviderRegistry` with all configured chat providers."""
    from dan.providers import ProviderConfig
    from dan.providers.openai_provider import OpenAIProvider
    from dan.providers.registry import ProviderRegistry

    registry = ProviderRegistry()
    config = _get_engine_config()
    default_config = ProviderConfig(
        api_key=config.llm_api_key, base_url=config.llm_base_url
    )
    registry.register("default", OpenAIProvider(default_config))

    for name, pconfig in config.providers.items():
        if name == "default":
            continue
        if name == "anthropic":
            try:
                from dan.providers.anthropic_provider import AnthropicProvider

                registry.register(name, AnthropicProvider(pconfig))
            except ImportError:
                pass
        elif name == "google":
            try:
                from dan.providers.google_provider import GoogleProvider

                registry.register(name, GoogleProvider(pconfig))
            except ImportError:
                pass
        else:
            registry.register(name, OpenAIProvider(pconfig))
    return registry


# ---------------------------------------------------------------------------
# MCP bridge helpers (moved from app.py)
# ---------------------------------------------------------------------------


async def _initialize_mcp_bridge_for_server(
    *,
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

    workspace_root = os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd())
    state.block_registry = BlockRegistry(workspace=Path(workspace_root))
    try:
        state.block_registry.scan()
    except OSError as exc:
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
            logger.warning(
                "Furnace startup degraded; disabling Furnace API for this process: %s",
                exc,
            )


async def init_engine(state: AppState) -> None:
    """Phase 2: create EngineConfig, TierTracker, TelemetryStore, RunManager."""
    engine_config = _get_engine_config()
    engine_config.block_registry = state.block_registry
    state.engine_config = engine_config

    from dan.providers.tier_tracker import TierSuccessTracker

    memory_dir = getattr(engine_config, "memory_dir", "./memory")
    engine_config.tier_tracker = TierSuccessTracker(Path(memory_dir))

    state.telemetry_store = None
    try:
        from dan.server.telemetry import get_telemetry_store

        state.telemetry_store = get_telemetry_store()
        await state.telemetry_store.prune()
        logger.info(
            "Telemetry store initialized (%s)", type(state.telemetry_store).__name__
        )
    except Exception:
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
    )
    # Expose the indexer getter for app-level RAG endpoints
    state._get_indexer = _get_indexer  # type: ignore[attr-defined]


async def init_capabilities(state: AppState) -> None:
    """Phase 3: create ChatCapabilityRegistry, register 7 capability groups, create CapabilityContext."""
    from dan.server.capability_handlers import (
        register_base_capabilities,
        register_experience_capabilities,
        register_introspection_capabilities,
        register_publish_capabilities,
        register_run_lifecycle_capabilities,
        register_tool_capabilities,
        register_workflow_catalog_capabilities,
    )
    from dan.server.capability_registry import CapabilityContext, ChatCapabilityRegistry

    state.capability_registry = ChatCapabilityRegistry()
    register_base_capabilities(state.capability_registry)
    register_experience_capabilities(state.capability_registry)
    register_run_lifecycle_capabilities(state.capability_registry)
    register_workflow_catalog_capabilities(state.capability_registry)
    register_tool_capabilities(state.capability_registry)
    register_introspection_capabilities(state.capability_registry)

    state.capability_context = CapabilityContext(
        workflow_id="",
        graph_store=state.graph_store,
        run_manager=state.run_manager,
        run_store=state.run_store,
        graphs_dir=state.graphs_dir,
        test_case_store=state.test_case_store,
    )

    state.mcp_bridge = await _initialize_mcp_bridge_for_server(
        capability_registry=state.capability_registry,
        tool_registry=(
            state.run_manager.tool_registry if state.run_manager is not None else None
        ),
        capability_context=state.capability_context,
    )


async def init_managers(state: AppState) -> None:
    """Phase 4: create MentionResolver, load memory subsystems, create ChatManager."""
    from dan.server.chat_manager import ChatManager
    from dan.server.mention_resolver import CodeResolver, MentionResolver

    workspace_root = os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd())
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

    # Memory kernel
    state.memory_kernel = None
    try:
        from dan.engine.memory_adapters import ConversationAdapter, ProfileAdapter
        from dan.engine.memory_kernel import DualWriteAdapter, MemoryKernel

        state.memory_kernel = MemoryKernel(
            dual_write_adapter=DualWriteAdapter(
                conversation_memory=state.conversation_memory,
                user_profile=state.user_profile,
            )
        )
        if state.user_profile:
            imported = ProfileAdapter.import_profile(
                state.user_profile, state.memory_kernel
            )
            if imported:
                logger.debug(
                    "Imported %d profile items into memory kernel", imported
                )
        if state.conversation_memory:
            imported = ConversationAdapter.import_all(
                state.conversation_memory, state.memory_kernel
            )
            if imported:
                logger.debug(
                    "Imported %d conversation items into memory kernel", imported
                )
    except Exception:
        logger.debug("Memory kernel load skipped", exc_info=True)

    if state.memory_kernel is not None and state.run_manager is not None:
        state.run_manager._memory_kernel = state.memory_kernel
        state.run_manager._config.memory_kernel = state.memory_kernel

    state.chat_manager = ChatManager(
        provider_registry=_build_chat_provider_registry(),
        graph_store=state.graph_store,
        mention_resolver=state.mention_resolver,
        capability_registry=state.capability_registry,
        capability_context=state.capability_context,
        user_profile=state.user_profile,
        conversation_memory=state.conversation_memory,
        memory_kernel=state.memory_kernel,
        telemetry_store=state.telemetry_store,
    )

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

    workspace_root = os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd())
    workspace_root_path = Path(workspace_root)
    init_gateway(
        run_manager=state.run_manager,
        workspace=workspace_root_path,
        graph_store=state.graph_store,
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
        logger.warning("Notification manager startup failed", exc_info=True)

    try:
        from dan.server.gateway.router import _activity_tracker as _gw_tracker

        if _gw_tracker is not None:
            state.capability_context.activity_tracker = _gw_tracker
    except ImportError:
        pass

    # Self-knowledge RAG indexing (19-5)
    state.self_knowledge_index = None
    try:
        from dan.rag import DEFAULT_EMBEDDING_MODEL, build_embedding_registry
        from dan.meta.self_knowledge import SelfKnowledgeIndex

        embedding_registry = build_embedding_registry(engine_config)
        _emb_model = getattr(
            engine_config, "default_embedding_model", DEFAULT_EMBEDDING_MODEL
        )
        if embedding_registry.has_provider("default"):
            provider = embedding_registry.resolve(_emb_model)
            _project_root = Path(__file__).resolve().parents[3]
            docs_dir = _project_root / "docs"
            doc_paths = []
            for fname in ("llm-api-guide.md", "architecture.md"):
                p = docs_dir / fname
                if p.exists():
                    doc_paths.append(p)

            if doc_paths:
                state.self_knowledge_index = SelfKnowledgeIndex(
                    embedding_provider=provider,
                    embedding_model=_emb_model,
                )
                examples_dir = _project_root / "examples"
                await state.self_knowledge_index.refresh(
                    doc_paths=doc_paths,
                    examples_dir=examples_dir if examples_dir.is_dir() else None,
                    tool_registry=(
                        state.run_manager._tool_registry
                        if state.run_manager
                        else None
                    ),
                )
                logger.info(
                    "Self-knowledge index refreshed (%d docs)", len(doc_paths)
                )
    except Exception:
        logger.debug("Self-knowledge indexing skipped", exc_info=True)

    app.state.self_knowledge_index = state.self_knowledge_index

    # Wire experience/discovery into capability context (25-2)
    from dan.server.app import (
        _get_experience_index,
        _get_experience_store,
        _build_meta_controller,
    )

    try:
        _exp_index = _get_experience_index()
    except Exception:
        _exp_index = None
    _exp_store = _get_experience_store(with_index=_exp_index is not None)

    if state.memory_kernel is not None:
        try:
            from dan.engine.memory_adapters import ExperienceAdapter

            imported = await ExperienceAdapter.import_all(_exp_store, state.memory_kernel)
            if imported:
                logger.debug(
                    "Imported %d workflow experiences into memory kernel", imported
                )
            if getattr(state.memory_kernel, "_dual_write", None) is not None:
                state.memory_kernel._dual_write._experience_store = _exp_store
        except Exception:
            logger.debug(
                "Experience import into memory kernel skipped", exc_info=True
            )

    from dan.meta.discovery import DiscoveryService

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
    )
    state.capability_context.principle_store = (
        state.run_manager._get_principle_store() if state.run_manager else None
    )

    # Concierge + dispatcher
    try:
        from dan.server.concierge import build_concierge

        meta_controller, _meta_planner, _meta_store = _build_meta_controller()

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
        logger.warning("Concierge startup failed", exc_info=True)
        state.concierge = None
        state.dispatcher = None


async def init_background(state: AppState, app: FastAPI) -> None:
    """Phase 6: scheduler, follow-ups, consolidation loop, skill store, feature log."""
    from dan.server.terminal_output import collect_terminal_content

    # Build scheduled-action dispatch closure
    async def _collect_concierge_terminal_content(surface_msg: Any) -> str:
        if state.dispatcher is not None:
            event_stream = state.dispatcher.dispatch(surface_msg)
        elif state.concierge is not None:
            event_stream = state.concierge.process(surface_msg)
        else:
            return ""
        reassurance_messages = (
            set(getattr(state.concierge, "_REASSURANCE_MESSAGES", []))
            if state.concierge is not None
            else set()
        )
        return await collect_terminal_content(
            event_stream,
            reassurance_messages=reassurance_messages,
        )

    async def _dispatch_scheduled_action(
        action: str, trigger_context: Any, delivery_target: Any
    ) -> str:
        from dan.server.concierge.models import SurfaceMessage

        surface_msg = SurfaceMessage(
            surface=delivery_target.surface
            or trigger_context.source_surface
            or "schedule",
            external_id=str(
                delivery_target.conversation_key
                or delivery_target.user_id
                or trigger_context.user_id
                or trigger_context.task_id
                or delivery_target.project_id
                or "scheduled-task"
            ),
            text=action,
            metadata={
                "thread_id": delivery_target.thread_key
                or trigger_context.thread_key,
                "scheduled_trigger": True,
                "trigger_context": trigger_context.model_dump(mode="json"),
                "delivery_target": delivery_target.model_dump(mode="json"),
            },
        )
        return await _collect_concierge_terminal_content(surface_msg)

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
        from dan.server.skill_store import SkillStore

        _extra_dirs: list[Path] = []
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


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


async def shutdown(state: AppState) -> None:
    """Cleanly tear down all background tasks and integrations."""
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
    from dan.server.graph_store import GraphStore
    from dan.server.chat_store import ChatStore
    from dan.server.test_cases import TestCaseStore

    graphs_dir = os.environ.get("DAN_GRAPHS_DIR", "./graphs")

    state = AppState(
        graphs_dir=graphs_dir,
        graph_store=GraphStore(base_dir=graphs_dir),
        chat_store=ChatStore(base_dir=graphs_dir),
        test_case_store=TestCaseStore(base_dir=graphs_dir),
    )

    init_learning_tiers()
    await init_stores(state)
    await init_engine(state)
    _mirror_state_to_globals(state)  # expose run_manager etc. before later phases need them
    await init_capabilities(state)
    await init_managers(state)
    _mirror_state_to_globals(state)  # expose chat_manager etc. before integrations
    await init_integrations(state, app)
    await init_background(state, app)

    app.state.dan = state
    app.state.mcp_bridge = state.mcp_bridge

    _mirror_state_to_globals(state)  # final pass to catch anything set by integrations/background

    yield

    await shutdown(state)


def _mirror_state_to_globals(state: AppState) -> None:
    """Populate ``app.py`` module globals from the canonical :class:`AppState`.

    This keeps ``from dan.server.app import _graph_store`` working until
    all call sites are migrated to ``request.app.state.dan``.
    """
    import dan.server.app as _app_mod

    _app_mod._graph_store = state.graph_store
    _app_mod._chat_store = state.chat_store
    _app_mod._test_case_store = state.test_case_store
    _app_mod._run_manager = state.run_manager
    _app_mod._chat_manager = state.chat_manager
    _app_mod._mention_resolver = state.mention_resolver
    _app_mod._publish_registry = state.publish_registry
    _app_mod._block_registry = state.block_registry
    _app_mod._concierge = state.concierge
    _app_mod._dispatcher = state.dispatcher
    _app_mod._mcp_bridge = state.mcp_bridge
    _app_mod._notification_manager = state.notification_manager
    _app_mod._self_knowledge_index = state.self_knowledge_index
    _app_mod._experience_index_cache = state.experience_index_cache
    _app_mod._furnace_session_store = state.furnace_session_store
    _app_mod._furnace_enabled = state.furnace_enabled
