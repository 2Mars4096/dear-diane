"""Adapter management endpoints and runtime logic."""

from __future__ import annotations

import asyncio
import json
import os
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dan.server.routers.dependencies import (
    get_graph_store,
    get_run_manager,
    get_block_registry,
    get_dispatcher,
    get_engine_config,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level adapter state
_active_adapters: dict[str, tuple[Any, asyncio.Task[Any]]] = {}
_adapter_session_stores: dict[str, Any] = {}
_adapter_start_times: dict[str, float] = {}
_adapter_renderers: dict[str, tuple[Any, Any]] = {}
_adapter_surface_types: dict[str, str] = {}


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class AdapterStartRequest(BaseModel):
    type: str
    workflow_path: str = ""
    config: dict[str, Any] = {}


class AdapterStopRequest(BaseModel):
    adapter_id: str


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _load_workflow_for_adapter(path: str):
    import importlib.util
    from dan.models.graph import Graph

    gs = get_graph_store()
    data = gs.get_graph(path)
    if data is not None:
        return Graph.model_validate(data)

    p = Path(path)
    if not p.exists():
        if path.endswith((".json", ".md", ".py")):
            raise FileNotFoundError(f"File not found: {path}")
        raise ValueError(f"Cannot load workflow: '{path}' is not a valid file or graph ID")

    if p.suffix.lower() == ".json":
        with open(p) as f:
            return Graph.model_validate(json.load(f))
    if p.suffix.lower() == ".md" or (p.is_dir() and p.exists()):
        from dan.loader import load
        return load(p)
    if p.suffix.lower() == ".py":
        spec = importlib.util.spec_from_file_location("_dan_user_workflow", str(p))
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load Python module from: {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        if hasattr(mod, "graph"):
            return mod.graph
        if hasattr(mod, "build") and callable(mod.build):
            return mod.build()
        raise ValueError(
            f"Python file {path} must export a 'graph' attribute "
            "or a 'build()' function returning a Graph."
        )

    raise ValueError(f"Cannot load workflow: '{path}' has unsupported format")


async def _send_adapter_text(adapter: Any, external_id: str, text: str) -> None:
    if hasattr(adapter, "_send_text"):
        if hasattr(adapter, "_jid_map"):
            await adapter._send_text(external_id, text)
        else:
            try:
                thread_id: int | None = None
                if ":" in str(external_id):
                    chat_raw, thread_raw = str(external_id).split(":", 1)
                    chat_id = int(chat_raw)
                    thread_id = int(thread_raw) if thread_raw else None
                else:
                    chat_id = int(external_id)
            except (ValueError, TypeError):
                chat_id = external_id  # type: ignore[assignment]
                thread_id = None
            if isinstance(chat_id, int):
                await adapter._send_text(chat_id, text, thread_id=thread_id)
            else:
                await adapter._send_text(chat_id, text)
    elif hasattr(adapter, "send_prompt"):
        sid = None
        if hasattr(adapter, "_session_map"):
            sid = adapter._session_map.get(external_id)
            if sid is None:
                try:
                    sid = adapter._session_map.get(int(external_id))
                except (ValueError, TypeError):
                    pass
        if sid is not None:
            await adapter.send_prompt(sid, text)
        else:
            logger.debug("No session for external_id %s, trying direct send", external_id)


def _run_adapter_message_handler(
    adapter_id: str,
    external_id: str,
    message_text: str,
) -> None:
    adapter_entry = _active_adapters.get(adapter_id)
    if adapter_entry is None:
        logger.warning("Adapter %s: not found, ignoring message", adapter_id)
        return

    adapter, _ = adapter_entry
    surface = _adapter_surface_types.get(adapter_id, "adapter")

    async def _handle_workflows_command() -> None:
        try:
            graphs = get_graph_store().list_graphs()
        except Exception:
            graphs = []
        if not graphs:
            await _send_adapter_text(adapter, external_id, "No saved workflows found.")
            return
        lines = ["*Saved Workflows*\n"]
        for i, g in enumerate(graphs[:20], 1):
            name = g.get("name", g.get("graph_id", "?"))
            desc = g.get("description", "")
            line = f"{i}. *{name}*"
            if desc:
                line += f" — {desc[:80]}"
            lines.append(line)
        if len(graphs) > 20:
            lines.append(f"\n…and {len(graphs) - 20} more.")
        await _send_adapter_text(adapter, external_id, "\n".join(lines))

    if message_text.strip().lower() == "/workflows":
        asyncio.create_task(
            _handle_workflows_command(),
            name=f"adapter-{adapter_id}-workflows",
        )
        return

    _dispatcher = get_dispatcher()
    if _dispatcher is not None:
        asyncio.create_task(
            _run_adapter_concierge(adapter_id, adapter, surface, external_id, message_text),
            name=f"adapter-{adapter_id}-dispatch",
        )
        return

    entry = _adapter_renderers.get(adapter_id)
    if entry is None:
        logger.warning("Adapter %s: no renderer/graph, ignoring message", adapter_id)
        return
    _base_renderer, graph = entry
    if graph is None:
        logger.warning("Adapter %s: no workflow graph loaded and no dispatcher, ignoring", adapter_id)
        return
    asyncio.create_task(
        _run_adapter_engine(adapter_id, adapter, _base_renderer, graph, external_id, message_text),
        name=f"adapter-{adapter_id}-engine",
    )


async def _run_adapter_concierge(
    adapter_id: str,
    adapter: Any,
    surface: str,
    external_id: str,
    message_text: str,
) -> None:
    from dan.server.concierge.models import SurfaceMessage

    _dispatcher = get_dispatcher()

    async def _relay_adapter_event(event: Any) -> None:
        evt_type = getattr(event, "type", "")
        if (
            evt_type == "chat_complete"
            and getattr(event, "detected_mode", None) == "progress_ack"
        ):
            return
        if evt_type in {"chat_complete", "chat_mutation", "chat_interrupted"}:
            content = getattr(event, "content", "")
            if content:
                await _send_adapter_text(adapter, external_id, content)
            return
        if evt_type == "chat_error":
            error = getattr(event, "error", "")
            if error:
                await _send_adapter_text(adapter, external_id, error)
            return
        if evt_type == "chat_multi_part":
            for part in getattr(event, "parts", []) or []:
                if part:
                    await _send_adapter_text(adapter, external_id, part)
            return
        if evt_type == "chat_queued" and _dispatcher is not None:
            queue_position = max(int(getattr(event, "queue_position", 0) or 0), 1)
            if queue_position == 1:
                queued_text = (
                    "Queued behind an earlier message — I'll reply here when it's done."
                )
            else:
                queued_text = (
                    f"Queued behind {queue_position} earlier messages — "
                    "I'll reply here when it's done."
                )
            await _send_adapter_text(adapter, external_id, queued_text)

            queued_channel = str(getattr(event, "stream_channel_id", "") or "").strip()
            if not queued_channel:
                return
            bus = _dispatcher.get_response_bus(queued_channel)
            if bus is None:
                return
            try:
                while True:
                    queued_event = await bus.get()
                    if queued_event is None:
                        break
                    await _relay_adapter_event(queued_event)
            finally:
                _dispatcher.cleanup_response_bus(queued_channel)

    msg = SurfaceMessage(
        surface=surface,
        external_id=external_id,
        text=message_text,
        metadata={"adapter_id": adapter_id},
    )
    try:
        async for event in _dispatcher.dispatch(msg):
            await _relay_adapter_event(event)
    except Exception:
        logger.exception("Adapter %s concierge dispatch failed", adapter_id)
        try:
            await _send_adapter_text(
                adapter, external_id, "Something went wrong. Please try again.",
            )
        except Exception:
            pass


async def _run_adapter_engine(
    adapter_id: str,
    adapter: Any,
    base_renderer: Any,
    graph: Any,
    external_id: str,
    message_text: str,
) -> None:
    from dan.adapters.base import SessionState
    from dan.adapters import MessagingHumanRenderer, AdapterSessionStore

    session_store = _adapter_session_stores.get(adapter_id)
    if session_store is None:
        logger.warning("Adapter %s: no session store, ignoring message", adapter_id)
        return

    session = await session_store.create(external_id)
    session_id = session.session_id

    if hasattr(adapter, "register_session"):
        try:
            chat_id = int(external_id) if external_id.isdigit() else external_id
            adapter.register_session(session_id, chat_id)
        except (ValueError, TypeError):
            adapter.register_session(session_id, external_id)

    session_renderer = MessagingHumanRenderer(adapter, session_store)
    session_renderer.active_session_id = session_id
    await session_store.update_state(session_id, SessionState.RUNNING)

    from dan.engine import Engine, EngineConfig

    cfg = get_engine_config()
    engine_config = EngineConfig(
        llm_api_key=cfg.llm_api_key or os.environ.get("DAN_LLM_API_KEY", os.environ.get("LLM_API_KEY", "")),
        llm_base_url=cfg.llm_base_url or "https://api.vectorengine.ai/v1",
        llm_default_model=cfg.llm_default_model or "claude-sonnet-4-6",
        block_registry=get_block_registry(),
    )
    engine = Engine(config=engine_config, human_renderer=session_renderer)

    inputs = {"message": message_text, "user_input": message_text, "input": message_text}
    try:
        result = await engine.run(graph, inputs=inputs)
        if not result.success and result.errors:
            logger.warning("Adapter %s run failed: %s", adapter_id, result.errors)
    except Exception:
        logger.exception("Adapter %s engine run failed", adapter_id)
    finally:
        await session_store.remove(session_id)
        if hasattr(adapter, "unregister_session"):
            adapter.unregister_session(session_id)


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.post("/api/adapters/start")
async def start_adapter(req: AdapterStartRequest):
    from dan.adapters import (
        EmailAdapter, EmailAdapterConfig,
        TelegramAdapter, TelegramAdapterConfig,
        WhatsAppAdapter, WhatsAppAdapterConfig,
        MessagingAdapter, MessagingHumanRenderer, AdapterSessionStore,
    )

    adapter_id = str(uuid.uuid4())[:12]
    adapter_type = req.type.lower()
    config_data = {**req.config, "workflow_path": req.workflow_path}

    adapter: MessagingAdapter
    if adapter_type == "email":
        adapter_config = EmailAdapterConfig(**config_data)
        adapter = EmailAdapter(adapter_config)
    elif adapter_type == "telegram":
        adapter_config = TelegramAdapterConfig(**config_data)
        adapter = TelegramAdapter(adapter_config)
    elif adapter_type == "whatsapp":
        adapter_config = WhatsAppAdapterConfig(**config_data)
        adapter = WhatsAppAdapter(adapter_config)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown adapter type: {req.type}")

    session_store = AdapterSessionStore()
    _adapter_session_stores[adapter_id] = session_store

    graph = None
    workflow_path = req.workflow_path
    if workflow_path:
        try:
            graph = _load_workflow_for_adapter(workflow_path)
        except Exception as exc:
            logger.warning("Could not load workflow for adapter %s: %s", adapter_id, exc)

    renderer = MessagingHumanRenderer(adapter, session_store)

    async def _on_msg(ext_id: str, text: str) -> None:
        _run_adapter_message_handler(adapter_id, ext_id, text)

    adapter.set_message_callback(_on_msg)

    async def _run_adapter() -> None:
        try:
            await adapter.start()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Adapter %s (%s) crashed", adapter_id, adapter_type)

    task = asyncio.create_task(_run_adapter(), name=f"adapter-{adapter_id}")
    _active_adapters[adapter_id] = (adapter, task)
    _adapter_start_times[adapter_id] = time.time()
    _adapter_renderers[adapter_id] = (renderer, graph)
    _adapter_surface_types[adapter_id] = adapter_type

    return {
        "status": "started",
        "adapter_id": adapter_id,
        "type": adapter_type,
    }


@router.post("/api/adapters/stop")
async def stop_adapter(req: AdapterStopRequest):
    entry = _active_adapters.pop(req.adapter_id, None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Adapter '{req.adapter_id}' not found")

    adapter, task = entry
    if not task.done():
        task.cancel()
    try:
        await adapter.stop()
    except Exception:
        pass

    _adapter_session_stores.pop(req.adapter_id, None)
    _adapter_start_times.pop(req.adapter_id, None)
    _adapter_renderers.pop(req.adapter_id, None)
    _adapter_surface_types.pop(req.adapter_id, None)

    return {"status": "stopped", "adapter_id": req.adapter_id}


@router.get("/api/adapters/status")
async def adapter_status():
    results = []
    now = time.time()
    for aid, (adapter, task) in _active_adapters.items():
        store = _adapter_session_stores.get(aid)
        session_count = 0
        if store is not None:
            sessions = await store.all_sessions()
            session_count = len(sessions)

        started_at = _adapter_start_times.get(aid, now)
        results.append({
            "adapter_id": aid,
            "type": type(adapter).__name__.replace("Adapter", "").lower(),
            "running": not task.done(),
            "session_count": session_count,
            "uptime_seconds": round(now - started_at, 1),
        })
    return results
