"""Shared FastAPI dependencies for router modules.

Thin wrappers that access module-level globals from app.py via deferred
imports to avoid circular-import issues during startup.
"""

from __future__ import annotations

from typing import Any


def get_graph_store():
    from dan.server.app import _graph_store
    return _graph_store


def get_chat_store():
    from dan.server.app import _chat_store
    return _chat_store


def get_run_manager():
    from dan.server.app import _run_manager
    if _run_manager is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    return _run_manager


def get_chat_manager():
    from dan.server.app import _chat_manager
    if _chat_manager is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Chat not initialised")
    return _chat_manager


def get_test_case_store():
    from dan.server.app import _test_case_store
    return _test_case_store


def get_publish_registry():
    from dan.server.app import _publish_registry
    if _publish_registry is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Publish registry not initialised")
    return _publish_registry


def get_block_registry():
    from dan.server.app import _block_registry
    if _block_registry is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Block registry not initialised")
    return _block_registry


def get_concierge():
    from dan.server.app import _concierge
    return _concierge


def get_dispatcher():
    from dan.server.app import _dispatcher
    return _dispatcher


def get_mention_resolver():
    from dan.server.app import _mention_resolver
    return _mention_resolver


def get_engine_config():
    from dan.server.app import _get_engine_config
    return _get_engine_config()


def get_graphs_dir() -> str:
    from dan.server.app import _graphs_dir
    return _graphs_dir


def get_memory_store():
    from dan.server.app import _get_memory_store
    return _get_memory_store()


def get_experience_index():
    from dan.server.app import _get_experience_index
    return _get_experience_index()


def get_experience_store(*, with_index: bool = False):
    from dan.server.app import _get_experience_store
    return _get_experience_store(with_index=with_index)


def validate_path_segment(value: str, name: str) -> str:
    from dan.server.app import _validate_path_segment
    return _validate_path_segment(value, name)


def resolve_cache_dir(config: Any):
    from dan.server.app import _resolve_cache_dir
    return _resolve_cache_dir(config)


def get_furnace_session_store():
    from dan.server.app import _furnace_session_store
    if _furnace_session_store is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Furnace not initialised")
    return _furnace_session_store


def is_furnace_enabled() -> bool:
    from dan.server.app import _furnace_enabled
    return _furnace_enabled
