"""Typed container for all server-side runtime state."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from dan.server.graph_store import GraphStore
from dan.server.chat_store import ChatStore
from dan.server.run_manager import RunManager
from dan.server.run_store import RunStore
from dan.server.test_cases import TestCaseStore


@dataclass
class AppState:
    """Typed container for all server-side runtime state.

    Created once during ``lifespan()`` and attached to ``app.state.dan``.
    Fields are ``None`` until the corresponding init phase completes.
    """

    # -- Stores (created early, never None after init_stores) ----------------
    graph_store: GraphStore | None = None
    chat_store: ChatStore | None = None
    test_case_store: TestCaseStore | None = None
    run_store: RunStore | None = None
    chat_v2_store: Any = None

    # -- Managers ------------------------------------------------------------
    run_manager: RunManager | None = None
    chat_manager: Any = None
    mention_resolver: Any = None

    # -- Registries ----------------------------------------------------------
    publish_registry: Any = None
    block_registry: Any = None
    capability_registry: Any = None
    capability_context: Any = None

    # -- Concierge / dispatch ------------------------------------------------
    concierge: Any = None
    dispatcher: Any = None
    dan_v2_runtime: Any = None

    # -- MCP -----------------------------------------------------------------
    mcp_bridge: Any = None

    # -- Notifications -------------------------------------------------------
    notification_manager: Any = None

    # -- Knowledge -----------------------------------------------------------
    self_knowledge_index: Any = None

    # -- Experience ----------------------------------------------------------
    experience_index_cache: Any = None
    experience_index_bootstrap_done: bool = False

    # -- Meta sessions -------------------------------------------------------
    meta_tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    meta_subscribers: dict[str, list[asyncio.Queue]] = field(
        default_factory=lambda: defaultdict(list),
    )
    build_meta_controller: Any = None

    # -- Background tasks ----------------------------------------------------
    background_tasks: list[asyncio.Task] = field(default_factory=list)
    task_scheduler: Any = None
    consolidation_task: asyncio.Task | None = None

    # -- Engine config (set during init_engine) ------------------------------
    engine_config: Any = None
    telemetry_store: Any = None

    # -- LLM gateway (set during init_chat, wraps same provider registry) ---
    model_gateway: Any = None

    # -- Memory subsystems ---------------------------------------------------
    user_profile: Any = None
    conversation_memory: Any = None
    memory_kernel: Any = None

    # -- Adapters (adapter runtime state) ------------------------------------
    active_adapters: dict[str, tuple[Any, asyncio.Task]] = field(default_factory=dict)
    adapter_session_stores: dict[str, Any] = field(default_factory=dict)
    adapter_start_times: dict[str, float] = field(default_factory=dict)
    adapter_renderers: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    adapter_surface_types: dict[str, str] = field(default_factory=dict)

    # -- Furnace / recipe distillation ----------------------------------------
    furnace_session_store: Any = None
    furnace_enabled: bool = False

    # -- Misc ----------------------------------------------------------------
    graphs_dir: str = ""
    skill_store: Any = None
    startup_degradations: list[dict[str, str]] = field(default_factory=list)

    # -- Accessor guards -----------------------------------------------------

    def require_graph_store(self) -> GraphStore:
        if self.graph_store is None:
            raise HTTPException(
                status_code=503, detail="Graph store not initialised"
            )
        return self.graph_store

    def require_chat_store(self) -> ChatStore:
        if self.chat_store is None:
            raise HTTPException(
                status_code=503, detail="Chat store not initialised"
            )
        return self.chat_store

    def require_chat_v2_store(self) -> Any:
        if self.chat_v2_store is None:
            raise HTTPException(
                status_code=503, detail="Chat V2 store not initialised"
            )
        return self.chat_v2_store

    def require_test_case_store(self) -> TestCaseStore:
        if self.test_case_store is None:
            raise HTTPException(
                status_code=503, detail="Test case store not initialised"
            )
        return self.test_case_store

    def require_run_manager(self) -> RunManager:
        if self.run_manager is None:
            raise HTTPException(
                status_code=503, detail="Server not fully initialised"
            )
        return self.run_manager

    def require_chat_manager(self) -> Any:
        if self.chat_manager is None:
            raise HTTPException(
                status_code=503, detail="Chat not initialised"
            )
        return self.chat_manager

    def require_publish_registry(self) -> Any:
        if self.publish_registry is None:
            raise HTTPException(
                status_code=503, detail="Publish registry not initialised"
            )
        return self.publish_registry

    def require_block_registry(self) -> Any:
        if self.block_registry is None:
            raise HTTPException(
                status_code=503, detail="Block registry not initialised"
            )
        return self.block_registry

    def require_engine_config(self) -> Any:
        if self.engine_config is None:
            raise HTTPException(
                status_code=503, detail="Engine config not initialised"
            )
        return self.engine_config

    def require_furnace_session_store(self) -> Any:
        if self.furnace_session_store is None:
            raise HTTPException(
                status_code=503, detail="Furnace not initialised"
            )
        return self.furnace_session_store
