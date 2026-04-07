"""Shared FastAPI dependencies for router modules.

Request-backed paths should prefer the typed ``app.state.dan`` container.
Globals from ``dan.server.app`` remain as a compatibility fallback for
startup-time callers and older direct unit tests that do not carry a request.
"""

from __future__ import annotations

import importlib
import os
from typing import Any

from fastapi import HTTPException


def _get_app_state(connection: Any | None):
    if connection is None:
        return None
    app = getattr(connection, "app", None)
    state = getattr(app, "state", None)
    if state is None:
        return None
    return getattr(state, "dan", None)


def get_app_state(connection: Any | None = None):
    return _get_app_state(connection)


def _get_app_module_attr(name: str) -> Any:
    app_module = importlib.import_module("dan.server.app")
    return getattr(app_module, name)


def get_graph_store(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        return state.require_graph_store()
    return _get_app_module_attr("_graph_store")


def get_chat_store(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        return state.require_chat_store()
    return _get_app_module_attr("_chat_store")


def get_run_manager(connection: Any | None = None):
    state = _get_app_state(connection)
    app_run_manager = _get_app_module_attr("_run_manager")
    if state is not None:
        state_run_manager = state.run_manager
        if app_run_manager is not None and app_run_manager is not state_run_manager:
            return app_run_manager
        if state_run_manager is not None:
            return state_run_manager
    if app_run_manager is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    return app_run_manager


def get_chat_manager(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        return state.require_chat_manager()
    _chat_manager = _get_app_module_attr("_chat_manager")
    if _chat_manager is None:
        raise HTTPException(status_code=503, detail="Chat not initialised")
    return _chat_manager


def get_test_case_store(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        return state.require_test_case_store()
    return _get_app_module_attr("_test_case_store")


def get_publish_registry(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        return state.require_publish_registry()
    _publish_registry = _get_app_module_attr("_publish_registry")
    if _publish_registry is None:
        raise HTTPException(status_code=503, detail="Publish registry not initialised")
    return _publish_registry


def get_block_registry(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        return state.require_block_registry()
    _block_registry = _get_app_module_attr("_block_registry")
    if _block_registry is None:
        raise HTTPException(status_code=503, detail="Block registry not initialised")
    return _block_registry


def get_concierge(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        return state.concierge
    return _get_app_module_attr("_concierge")


def get_dispatcher(connection: Any | None = None):
    state = _get_app_state(connection)
    app_dispatcher = _get_app_module_attr("_dispatcher")
    if state is not None:
        state_dispatcher = state.dispatcher
        if (
            app_dispatcher is not None
            and app_dispatcher is not state_dispatcher
        ):
            return app_dispatcher
        return state_dispatcher
    return app_dispatcher


def get_mention_resolver(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        return state.mention_resolver
    return _get_app_module_attr("_mention_resolver")


def get_engine_config(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        return state.require_engine_config()
    return _get_app_module_attr("_get_engine_config")()


def get_graphs_dir(connection: Any | None = None) -> str:
    state = _get_app_state(connection)
    if state is not None:
        return str(state.graphs_dir or "")
    return _get_app_module_attr("_graphs_dir")


def get_memory_store(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        from dan.engine.memory_store import FileSystemMemoryStore

        run_manager = state.require_run_manager()
        memory_dir = (
            run_manager.engine_config.memory_dir
            if run_manager.engine_config
            else "./memory"
        )
        return FileSystemMemoryStore(memory_dir)
    return _get_app_module_attr("_get_memory_store")()


def get_experience_index(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        if state.experience_index_cache is not None:
            return state.experience_index_cache
        try:
            from dan.engine.experience import ExperienceIndex
            from dan.rag import build_embedding_registry
            from dan.rag.stores import VectorStoreConfig, VectorStoreFactory

            run_manager = state.require_run_manager()
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
            state.experience_index_cache = ExperienceIndex(
                embedding_provider=provider,
                vector_store=vector_store,
                embedding_model=model,
            )
            return state.experience_index_cache
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Experience index unavailable: {exc}",
            ) from exc
    return _get_app_module_attr("_get_experience_index")()


def get_experience_store(connection: Any | None = None, *, with_index: bool = False):
    state = _get_app_state(connection)
    if state is not None:
        from dan.engine.experience import ExperienceStore

        index = get_experience_index(connection) if with_index else None
        return ExperienceStore(get_memory_store(connection), experience_index=index)
    return _get_app_module_attr("_get_experience_store")(with_index=with_index)


def validate_path_segment(value: str, name: str) -> str:
    return _get_app_module_attr("_validate_path_segment")(value, name)


def resolve_cache_dir(config: Any):
    return _get_app_module_attr("_resolve_cache_dir")(config)


def get_build_meta_controller(connection: Any | None = None):
    state = _get_app_state(connection)
    builder = getattr(state, "build_meta_controller", None) if state is not None else None
    if callable(builder):
        return builder
    return _get_app_module_attr("_build_meta_controller")


def get_furnace_session_store(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        return state.require_furnace_session_store()
    _furnace_session_store = _get_app_module_attr("_furnace_session_store")
    if _furnace_session_store is None:
        raise HTTPException(status_code=503, detail="Furnace not initialised")
    return _furnace_session_store


def is_furnace_enabled(connection: Any | None = None) -> bool:
    state = _get_app_state(connection)
    if state is not None:
        return bool(state.furnace_enabled)
    return _get_app_module_attr("_furnace_enabled")
