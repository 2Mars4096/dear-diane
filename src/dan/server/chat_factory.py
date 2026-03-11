"""Shared factory for ChatManager and related services.

Used by both the FastAPI server lifespan and LocalChatRuntime so that
dependency wiring stays in one place.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _run_async_init_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run async startup work from sync code, even under an active event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(fn(*args, **kwargs))

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(fn(*args, **kwargs))
        except BaseException as exc:  # pragma: no cover - surfaced to caller
            error["exc"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "exc" in error:
        raise error["exc"]
    return result.get("value")


class ChatServices:
    """Bundle of services needed for chat operation."""

    def __init__(
        self,
        graph_store: Any,
        chat_store: Any,
        chat_manager: Any,
        run_manager: Any | None = None,
        mention_resolver: Any | None = None,
        engine_config: Any | None = None,
        capability_context: Any | None = None,
        user_profile: Any | None = None,
        conversation_memory: Any | None = None,
        memory_kernel: Any | None = None,
        concierge: Any | None = None,
        dispatcher: Any | None = None,
        mcp_bridge: Any | None = None,
    ) -> None:
        self.graph_store = graph_store
        self.chat_store = chat_store
        self.chat_manager = chat_manager
        self.run_manager = run_manager
        self.mention_resolver = mention_resolver
        self.engine_config = engine_config
        self.capability_context = capability_context
        self.user_profile = user_profile
        self.conversation_memory = conversation_memory
        self.memory_kernel = memory_kernel
        self.concierge = concierge
        self.dispatcher = dispatcher
        self.mcp_bridge = mcp_bridge


def _build_engine_config() -> Any:
    """Build EngineConfig from environment variables.

    Mirrors the logic in ``app.py._get_engine_config()`` without importing
    the full FastAPI application module.
    """
    from dan.engine.executor import EngineConfig
    from dan.providers import ProviderConfig
    from dan.rag import DEFAULT_EMBEDDING_MODEL

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

    default_embedding_model = os.environ.get(
        "DAN_DEFAULT_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL,
    )
    embedding_api_key = os.environ.get(
        "DAN_EMBEDDING_API_KEY",
        openai_key or os.environ.get("DAN_LLM_API_KEY", os.environ.get("LLM_API_KEY", "")),
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

    if os.environ.get("DAN_ENABLE_LOCAL_EMBEDDINGS", "").lower() in ("1", "true", "yes"):
        embedding_providers["local"] = ProviderConfig(
            default_model=os.environ.get("DAN_LOCAL_EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
        )

    default_model_policy = None
    if os.environ.get("DAN_ENABLE_TIER_POLICY", "0").lower() in ("1", "true", "yes"):
        from dan.providers.model_policy import TierPolicy
        tier_map = None
        tier_map_env = os.environ.get("DAN_TIER_MAP")
        if tier_map_env:
            import json
            try:
                tier_map = json.loads(tier_map_env)
            except Exception:
                logger.warning("Failed to parse DAN_TIER_MAP JSON, using defaults")
        default_model_policy = TierPolicy(tier_map=tier_map)

    return EngineConfig(
        llm_base_url=os.environ.get("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1"),
        llm_api_key=os.environ.get("DAN_LLM_API_KEY", os.environ.get("LLM_API_KEY", "")),
        llm_default_model=os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6"),
        checkpoint_dir=os.environ.get("DAN_CHECKPOINT_DIR", "./checkpoints"),
        checkpoint_enabled=True,
        providers=providers,
        embedding_providers=embedding_providers,
        default_embedding_model=default_embedding_model,
        default_model_policy=default_model_policy,
        cache_enabled=os.environ.get("DAN_CACHE_ENABLED", "true").lower() in ("1", "true", "yes"),
        cache_max_size_mb=int(os.environ.get("DAN_CACHE_MAX_SIZE_MB", "100")),
        cache_dir=os.environ.get("DAN_CACHE_DIR") or None,
        semantic_cache_threshold=float(os.environ.get("DAN_SEMANTIC_CACHE_THRESHOLD", "0.95")),
        semantic_cache_ttl_hours=float(os.environ.get("DAN_SEMANTIC_CACHE_TTL_HOURS", "24")),
    )


def _build_chat_provider_registry() -> Any:
    """Build a ProviderRegistry for chat, matching ``app.py`` logic."""
    from dan.providers import ProviderConfig
    from dan.providers.registry import ProviderRegistry
    from dan.providers.openai_provider import OpenAIProvider

    registry = ProviderRegistry()
    config = _build_engine_config()
    default_config = ProviderConfig(api_key=config.llm_api_key, base_url=config.llm_base_url)
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


def _build_tool_registry() -> Any:
    """Build a ToolRegistry with built-in tools only (no domain-specific)."""
    from dan.executors.tool import ToolRegistry

    registry = ToolRegistry()
    builtin = registry.register_builtin_tools()
    logger.debug("Local mode: registered %d built-in tools: %s", len(builtin), builtin)
    return registry


def build_chat_services(
    *,
    graphs_dir: str | Path | None = None,
    workspace_root: str | Path | None = None,
) -> ChatServices:
    """Build all services needed for chat operation.

    Used by both server lifespan and local CLI mode.
    """
    from dan.server.graph_store import GraphStore
    from dan.server.chat_store import ChatStore
    from dan.server.chat_manager import ChatManager
    from dan.server.run_store import RunStore
    from dan.server.run_manager import RunManager
    from dan.server.gateway.activity import ActivityTracker
    from dan.server.capability_registry import ChatCapabilityRegistry, CapabilityContext
    from dan.server.capability_handlers import (
        register_base_capabilities,
        register_experience_capabilities,
        register_publish_capabilities,
        register_run_lifecycle_capabilities,
        register_workflow_catalog_capabilities,
    )
    from dan.server.concierge import build_concierge

    _graphs_dir = str(graphs_dir) if graphs_dir else os.environ.get("DAN_GRAPHS_DIR", "./graphs")
    _workspace = str(workspace_root) if workspace_root else os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd())

    graph_store = GraphStore(base_dir=_graphs_dir)
    chat_store = ChatStore(base_dir=_graphs_dir)
    runs_dir = os.environ.get("DAN_RUNS_DIR", os.path.join(_graphs_dir, "runs"))
    run_store = RunStore(base_dir=runs_dir)

    engine_config = _build_engine_config()

    provider_registry = _build_chat_provider_registry()

    mention_resolver = None
    try:
        from dan.server.mention_resolver import MentionResolver
        mention_resolver = MentionResolver(
            workspace_root=_workspace,
            chat_store=chat_store,
        )
    except Exception:
        logger.debug("MentionResolver not available", exc_info=True)

    telemetry_store = None
    try:
        from dan.server.telemetry import get_telemetry_store
        telemetry_store = get_telemetry_store()
    except Exception:
        logger.debug("Telemetry store not available", exc_info=True)

    tool_registry = _build_tool_registry()
    run_manager = RunManager(
        engine_config=engine_config,
        tool_registry=tool_registry,
        run_store=run_store,
        telemetry_store=telemetry_store,
    )
    activity_tracker = ActivityTracker(run_manager)

    capability_registry = ChatCapabilityRegistry()
    register_base_capabilities(capability_registry)
    register_experience_capabilities(capability_registry)
    register_run_lifecycle_capabilities(capability_registry)
    register_workflow_catalog_capabilities(capability_registry)

    capability_context = CapabilityContext(
        workflow_id="",
        graph_store=graph_store,
        run_manager=run_manager,
        run_store=run_store,
        activity_tracker=activity_tracker,
        graphs_dir=_graphs_dir,
    )

    user_profile = None
    try:
        from dan.engine.user_profile import load_user_profile

        user_profile = load_user_profile()
    except Exception:
        logger.debug("UserProfile not available", exc_info=True)

    conversation_memory = None
    try:
        from dan.engine.conversation_memory import ConversationMemoryStore

        conversation_memory = ConversationMemoryStore()
    except Exception:
        logger.debug("ConversationMemoryStore not available", exc_info=True)

    memory_kernel = None
    try:
        from dan.engine.memory_kernel import MemoryKernel
        from dan.engine.memory_kernel import DualWriteAdapter
        from dan.engine.memory_adapters import ProfileAdapter, ConversationAdapter

        memory_kernel = MemoryKernel(
            dual_write_adapter=DualWriteAdapter(
                conversation_memory=conversation_memory,
                user_profile=user_profile,
            )
        )
        if user_profile:
            imported = ProfileAdapter.import_profile(user_profile, memory_kernel)
            if imported:
                logger.debug("Imported %d items from user profile into memory kernel", imported)
        if conversation_memory:
            imported = ConversationAdapter.import_all(conversation_memory, memory_kernel)
            if imported:
                logger.debug("Imported %d conversation summaries into memory kernel", imported)
    except Exception:
        logger.debug("MemoryKernel not available", exc_info=True)
    if memory_kernel is not None:
        run_manager._memory_kernel = memory_kernel
        run_manager._config.memory_kernel = memory_kernel

    mcp_bridge = None
    try:
        from dan.mcp_bridge import MCPBridge, autoconnect_configured_mcp_servers

        mcp_bridge = MCPBridge()
        try:
            _run_async_init_sync(
                autoconnect_configured_mcp_servers,
                mcp_bridge,
                capability_registry,
                tool_registry,
            )
        except Exception:
            logger.warning("MCP auto-connect failed during local chat startup", exc_info=True)
    except Exception:
        logger.debug("MCP bridge not available", exc_info=True)

    if mcp_bridge is not None:
        from dataclasses import replace
        capability_context = replace(capability_context, mcp_bridge=mcp_bridge)

    chat_manager = ChatManager(
        provider_registry=provider_registry,
        graph_store=graph_store,
        mention_resolver=mention_resolver,
        capability_registry=capability_registry,
        capability_context=capability_context,
        user_profile=user_profile,
        conversation_memory=conversation_memory,
        memory_kernel=memory_kernel,
    )
    register_publish_capabilities(capability_registry)
    result = build_concierge(
        chat_manager=chat_manager,
        capability_context=capability_context,
        user_profile=user_profile,
        conversation_memory=conversation_memory,
        memory_kernel=memory_kernel,
        enable_dispatcher=True,
        mcp_bridge=mcp_bridge,
        capability_registry=capability_registry,
        tool_registry=tool_registry,
        telemetry_store=telemetry_store,
    )
    if isinstance(result, tuple):
        concierge, dispatcher = result
    else:
        concierge, dispatcher = result, None

    return ChatServices(
        graph_store=graph_store,
        chat_store=chat_store,
        chat_manager=chat_manager,
        run_manager=run_manager,
        mention_resolver=mention_resolver,
        engine_config=engine_config,
        capability_context=capability_context,
        user_profile=user_profile,
        conversation_memory=conversation_memory,
        memory_kernel=memory_kernel,
        concierge=concierge,
        dispatcher=dispatcher,
        mcp_bridge=mcp_bridge,
    )
