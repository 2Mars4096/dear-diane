"""Shared FastAPI dependencies for router modules.

Request-backed paths prefer the typed ``app.state.dan`` container.
Direct callers without a request fall back to the live startup-owned
``AppState`` instead of mirrored ``dan.server.app`` globals.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from dan.server.control_plane import build_dan_v2_runtime, resolve_control_plane_mode


_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _get_active_app_state():
    from dan.server.startup import get_active_app_state

    return get_active_app_state()


def _get_legacy_app_attr(name: str):
    try:
        from dan.server import app as app_module
    except Exception:
        return None
    return getattr(app_module, name, None)


def _get_app_state(connection: Any | None):
    request_state = None
    if connection is not None:
        app = getattr(connection, "app", None)
        state = getattr(app, "state", None)
        if state is not None:
            request_state = getattr(state, "dan", None)
    if request_state is not None:
        return request_state
    return _get_active_app_state()


def get_app_state(connection: Any | None = None):
    return _get_app_state(connection)


def _require_app_state(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    return state


def get_graph_store(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        return state.require_graph_store()
    graph_store = _get_legacy_app_attr("_graph_store")
    if graph_store is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    return graph_store


def get_chat_store(connection: Any | None = None):
    return _require_app_state(connection).require_chat_store()


def get_run_manager(connection: Any | None = None):
    state = _get_app_state(connection)
    legacy_run_manager = _get_legacy_app_attr("_run_manager")
    if state is not None:
        state_run_manager = state.run_manager
        if legacy_run_manager is not None and legacy_run_manager is not state_run_manager:
            return legacy_run_manager
        return state.require_run_manager()
    if legacy_run_manager is not None:
        return legacy_run_manager
    raise HTTPException(status_code=503, detail="Server not fully initialised")


def get_chat_manager(connection: Any | None = None):
    return _require_app_state(connection).require_chat_manager()


def get_test_case_store(connection: Any | None = None):
    return _require_app_state(connection).require_test_case_store()


def get_publish_registry(connection: Any | None = None):
    return _require_app_state(connection).require_publish_registry()


def get_block_registry(connection: Any | None = None):
    return _require_app_state(connection).require_block_registry()


def get_concierge(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None and state.concierge is not None:
        return state.concierge
    return _get_legacy_app_attr("_concierge")


def get_dispatcher(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None and state.dispatcher is not None:
        return state.dispatcher
    return _get_legacy_app_attr("_dispatcher")


def get_dan_v2_runtime(connection: Any | None = None):
    state = _require_app_state(connection)
    if state.dan_v2_runtime is None:
        state.dan_v2_runtime = build_dan_v2_runtime(
            chat_manager=state.require_chat_manager(),
            run_manager=state.run_manager,
        )
    return state.dan_v2_runtime


def get_control_plane_mode(_connection: Any | None = None):
    return resolve_control_plane_mode(os.environ.get("DAN_CONTROL_PLANE"))


def get_mention_resolver(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None and state.mention_resolver is not None:
        return state.mention_resolver
    return _get_legacy_app_attr("_mention_resolver")


def get_engine_config(connection: Any | None = None):
    state = _get_app_state(connection)
    if state is not None:
        return state.require_engine_config()
    from dan.server.startup import _get_engine_config

    return _get_engine_config()


def get_graphs_dir(connection: Any | None = None) -> str:
    state = _get_app_state(connection)
    if state is not None:
        return str(state.graphs_dir or "")
    from dan.server.paths import resolve_graphs_dir

    return resolve_graphs_dir()


def get_memory_store(connection: Any | None = None):
    from dan.server.startup import build_memory_store_from_state

    return build_memory_store_from_state(_require_app_state(connection))


def get_experience_index(connection: Any | None = None):
    from dan.server.startup import get_experience_index_from_state

    return get_experience_index_from_state(_require_app_state(connection))


def get_experience_store(connection: Any | None = None, *, with_index: bool = False):
    from dan.server.startup import build_experience_store_from_state

    return build_experience_store_from_state(
        _require_app_state(connection),
        with_index=with_index,
    )


def validate_path_segment(value: str, name: str) -> str:
    if not _PATH_SEGMENT_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {name}: must be alphanumeric / dash / underscore",
        )
    return value


def resolve_cache_dir(config: Any):
    cache_dir = getattr(config, "cache_dir", None)
    if cache_dir:
        return Path(cache_dir).expanduser()
    return Path.home() / ".dan" / "cache"


def get_build_meta_controller(connection: Any | None = None):
    state = _get_app_state(connection)
    builder = getattr(state, "build_meta_controller", None) if state is not None else None
    if callable(builder):
        return builder
    from dan.server.startup import build_meta_controller_from_state

    return lambda: build_meta_controller_from_state(_require_app_state(connection))


def get_furnace_session_store(connection: Any | None = None):
    return _require_app_state(connection).require_furnace_session_store()


def is_furnace_enabled(connection: Any | None = None) -> bool:
    state = _get_app_state(connection)
    return bool(state.furnace_enabled) if state is not None else False
