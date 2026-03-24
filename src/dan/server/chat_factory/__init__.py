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

from dan.server.runtime_config import (
    append_runtime_degradation,
    log_runtime_degradation_summary,
)

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
        startup_degradations: list[dict[str, str]] | None = None,
        mode_limitations: list[dict[str, str]] | None = None,
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
        self.model_gateway: Any | None = None
        self.startup_degradations = [dict(item) for item in (startup_degradations or [])]
        self.mode_limitations = [dict(item) for item in (mode_limitations or [])]


LOCAL_MODE_LIMITATIONS: list[dict[str, str]] = [
    {
        "category": "transport",
        "message": "Local mode is in-process CLI only; it does not expose an HTTP or remote-client surface.",
    },
    {
        "category": "streaming",
        "message": "Stream channels are process-local and are not durable across restarts.",
    },
    {
        "category": "lifecycle",
        "message": "Local runtime state lives inside the CLI process and disappears when the process exits.",
    },
]


def _build_engine_config() -> Any:
    """Build EngineConfig from environment variables.

    Mirrors the logic in ``app.py._get_engine_config()`` without importing
    the full FastAPI application module.
    """
    from dan.server.runtime_config import build_engine_config_from_env

    return build_engine_config_from_env()


def _build_chat_provider_registry() -> Any:
    """Build a ProviderRegistry for chat, matching ``app.py`` logic."""
    from dan.server.runtime_config import build_chat_provider_registry

    return build_chat_provider_registry(_build_engine_config())


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
    project_store_base_dir: str | Path | None = None,
    surface: str = "server",
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
        register_common_capabilities,
        register_publish_capabilities,
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
    startup_degradations: list[dict[str, str]] = []

    model_gateway = None
    try:
        from dan.llm_core.factory import build_gateway

        model_gateway = build_gateway(engine_config=engine_config)
    except Exception:
        append_runtime_degradation(
            startup_degradations,
            "model_gateway",
            "construction failed; shared gateway unavailable",
        )
        logger.warning("ModelGateway construction failed; degrading", exc_info=True)

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
        append_runtime_degradation(
            startup_degradations,
            "telemetry",
            "telemetry store failed to initialize",
        )
        logger.debug("Telemetry store not available", exc_info=True)

    tool_registry = _build_tool_registry()
    run_manager = RunManager(
        engine_config=engine_config,
        tool_registry=tool_registry,
        run_store=run_store,
        telemetry_store=telemetry_store,
        graph_loader=graph_store.get_graph,
        model_gateway=model_gateway,
    )
    activity_tracker = ActivityTracker(run_manager)

    capability_registry = ChatCapabilityRegistry()
    register_common_capabilities(capability_registry)

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
        from functools import partial

        from dan.engine.memory_kernel import MemoryKernel
        from dan.engine.memory_kernel import DualWriteAdapter
        from dan.engine.memory_adapters import ProfileAdapter, ConversationAdapter
        from dan.server.concierge.domain_learning import (
            consolidate_memory_kernel_domain_templates,
        )
        from dan.server.concierge.feature_gates import engine_feature_enabled

        memory_kernel = MemoryKernel(
            dual_write_adapter=DualWriteAdapter(
                conversation_memory=conversation_memory,
                user_profile=user_profile,
            ),
            domain_consolidation_hook=partial(
                consolidate_memory_kernel_domain_templates,
                feature_enabled=engine_feature_enabled,
            ),
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
            append_runtime_degradation(
                startup_degradations,
                "mcp_bridge",
                "auto-connect failed; MCP-backed tools may be unavailable",
            )
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
        chat_store=chat_store,
        capability_registry=capability_registry,
        capability_context=capability_context,
        user_profile=user_profile,
        conversation_memory=conversation_memory,
        memory_kernel=memory_kernel,
        telemetry_store=telemetry_store,
    )
    chat_manager.model_gateway = model_gateway
    capability_context.chat_manager = chat_manager
    register_publish_capabilities(capability_registry)
    try:
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
            project_store_base_dir=project_store_base_dir,
        )
        if isinstance(result, tuple):
            concierge, dispatcher = result
        else:
            concierge, dispatcher = result, None
    except Exception:
        append_runtime_degradation(
            startup_degradations,
            "concierge",
            "concierge dispatcher failed to initialize",
        )
        logger.warning("Concierge startup failed in local chat bootstrap", exc_info=True)
        concierge, dispatcher = None, None

    services = ChatServices(
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
        startup_degradations=startup_degradations,
        mode_limitations=LOCAL_MODE_LIMITATIONS if surface == "local" else [],
    )
    services.model_gateway = model_gateway
    log_runtime_degradation_summary(
        startup_degradations,
        prefix="Local startup degradation summary",
    )
    return services
