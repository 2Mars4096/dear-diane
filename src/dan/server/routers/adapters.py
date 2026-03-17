"""Adapter management endpoints and runtime logic."""

from __future__ import annotations

import asyncio
from importlib import util as importlib_util
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from dan.server.capabilities.config import _update_env_file
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
_adapter_status_snapshots: dict[str, dict[str, Any]] = {}
_adapter_event_subscribers: dict[str, set[asyncio.Queue[dict[str, Any] | None]]] = {}
_adapter_event_snapshots: dict[str, dict[str, dict[str, Any]]] = {}

_TELEGRAM_DESKTOP_BOT_KEY = "desktop-ui"
_DEFAULT_WHATSAPP_WEB_DIR = Path.home() / ".dan" / "whatsapp-web"
_DEFAULT_WHATSAPP_WEB_DB_PATH = _DEFAULT_WHATSAPP_WEB_DIR / "session.sqlite3"
_DEFAULT_WHATSAPP_WEB_CONFIG_PATH = _DEFAULT_WHATSAPP_WEB_DIR / "config.json"


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


def _mask_secret(value: str) -> str | None:
    secret = str(value or "").strip()
    if not secret:
        return None
    if len(secret) <= 6:
        if len(secret) <= 3:
            return "***"
        return f"{secret[:2]}...{secret[-1:]}"
    return f"{secret[:4]}...{secret[-4:]}"


def _coerce_int_list(values: Any) -> list[int]:
    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[int] = []
    for value in values:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _coerce_str_list(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}.",
        suffix=path.suffix or ".json",
    )
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def _build_dependency_summary(
    *,
    module_name: str,
    package_name: str,
    install_hint: str,
) -> dict[str, Any]:
    return {
        "dependency_module": module_name,
        "dependency_package": package_name,
        "dependency_installed": importlib_util.find_spec(module_name) is not None,
        "install_hint": install_hint,
    }


def _remove_sqlite_artifacts(path: Path) -> None:
    candidates = [
        path,
        path.with_name(f"{path.name}-shm"),
        path.with_name(f"{path.name}-wal"),
    ]
    for candidate in candidates:
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _load_telegram_desktop_state() -> dict[str, Any]:
    from dan.adapters.telegram_config import load_fleet_config

    try:
        fleet = load_fleet_config()
    except Exception:
        logger.exception("Failed to load Telegram desktop config summary")
        return {"bot_token": "", "bot_username": "", "allowed_chat_ids": []}

    bot = fleet.bots.get(_TELEGRAM_DESKTOP_BOT_KEY)
    if bot is None:
        return {"bot_token": "", "bot_username": "", "allowed_chat_ids": []}
    return {
        "bot_token": str(bot.token or "").strip(),
        "bot_username": str(getattr(bot, "username", "") or "").strip(),
        "allowed_chat_ids": _coerce_int_list(bot.allowed_users),
    }


def _save_telegram_desktop_state(
    *,
    bot_token: str | None = None,
    bot_username: str | None = None,
    allowed_chat_ids: list[int] | None = None,
) -> dict[str, Any]:
    from dan.adapters.telegram_config import (
        TelegramBotConfig,
        load_fleet_config,
        save_fleet_config,
    )

    fleet = load_fleet_config()
    current = fleet.bots.get(_TELEGRAM_DESKTOP_BOT_KEY)
    existing_allowed = _coerce_int_list(current.allowed_users if current else [])
    existing_username = str(getattr(current, "username", "") or "").strip()
    next_token = (
        str(bot_token or "").strip()
        or str(current.token if current else "").strip()
        or os.environ.get("DAN_TELEGRAM_BOT_TOKEN", "").strip()
    )
    next_username = str(bot_username or "").strip() or existing_username
    next_allowed = allowed_chat_ids if allowed_chat_ids is not None else existing_allowed

    if current is None and not next_token and not next_allowed and not next_username:
        return {"bot_token": "", "bot_username": "", "allowed_chat_ids": []}

    bot_config = current or TelegramBotConfig(default=False)
    bot_config.token = next_token
    bot_config.username = next_username
    bot_config.allowed_users = next_allowed
    bot_config.default = False
    fleet.bots[_TELEGRAM_DESKTOP_BOT_KEY] = bot_config
    save_fleet_config(fleet)

    if next_token:
        os.environ["DAN_TELEGRAM_BOT_TOKEN"] = next_token
        _update_env_file("DAN_TELEGRAM_BOT_TOKEN", next_token)

    return {
        "bot_token": next_token,
        "bot_username": next_username,
        "allowed_chat_ids": next_allowed,
    }


def _load_whatsapp_web_settings() -> dict[str, Any]:
    if not _DEFAULT_WHATSAPP_WEB_CONFIG_PATH.exists():
        return {
            "allowed_jids": [],
            "db_path": str(_DEFAULT_WHATSAPP_WEB_DB_PATH),
        }
    try:
        raw = json.loads(_DEFAULT_WHATSAPP_WEB_CONFIG_PATH.read_text())
    except Exception:
        logger.exception("Failed to load WhatsApp Web config summary")
        return {
            "allowed_jids": [],
            "db_path": str(_DEFAULT_WHATSAPP_WEB_DB_PATH),
        }
    if not isinstance(raw, dict):
        return {
            "allowed_jids": [],
            "db_path": str(_DEFAULT_WHATSAPP_WEB_DB_PATH),
        }
    return {
        "allowed_jids": _coerce_str_list(raw.get("allowed_jids")),
        "db_path": str(raw.get("db_path") or "").strip()
        or str(_DEFAULT_WHATSAPP_WEB_DB_PATH),
    }


def _save_whatsapp_web_settings(
    *,
    allowed_jids: list[str] | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    current = _load_whatsapp_web_settings()
    if allowed_jids is not None:
        current["allowed_jids"] = _coerce_str_list(allowed_jids)
    if db_path is not None:
        current["db_path"] = str(db_path or "").strip() or current["db_path"]
    _atomic_write_json(_DEFAULT_WHATSAPP_WEB_CONFIG_PATH, current)
    return current


def _build_qr_svg_data_uri(qr_data: str) -> str | None:
    value = str(qr_data or "").strip()
    if not value:
        return None
    try:
        import segno

        return segno.make_qr(value).svg_data_uri(scale=6)
    except Exception:
        logger.debug("Failed to render QR data as SVG", exc_info=True)
        return None


def _merge_adapter_snapshot(adapter_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    current = _adapter_status_snapshots.get(adapter_id, {})
    next_snapshot = {**current, **patch}
    _adapter_status_snapshots[adapter_id] = next_snapshot
    return next_snapshot


def _adapter_is_running(adapter: Any, task: asyncio.Task[Any]) -> bool:
    running = getattr(adapter, "_running", None)
    if isinstance(running, bool):
        return running
    return not task.done()


def _close_adapter_event_streams(adapter_id: str) -> None:
    subscribers = _adapter_event_subscribers.pop(adapter_id, set())
    for queue in subscribers:
        queue.put_nowait(None)
    _adapter_event_snapshots.pop(adapter_id, None)


def _publish_adapter_event(adapter_id: str, event: dict[str, Any]) -> None:
    event_type = str(event.get("type") or "status").strip().lower() or "status"
    payload: dict[str, Any] = {
        "type": event_type,
        "adapter_id": adapter_id,
        "timestamp": time.time(),
    }
    payload.update({k: v for k, v in event.items() if k != "type"})
    payload = _json_safe(payload)

    if event_type == "qr":
        qr_data = str(payload.get("qr_data") or "").strip()
        payload["qr_data"] = qr_data
        payload["svg_data_uri"] = _build_qr_svg_data_uri(qr_data)
        _merge_adapter_snapshot(
            adapter_id,
            {
                "connection_state": "pairing",
                "paired": False,
                "last_error": None,
                "qr_data": qr_data or None,
            },
        )
    elif event_type == "pair_status":
        status = str(payload.get("status") or "").strip().lower()
        if status in {"paired", "connected", "success"}:
            _merge_adapter_snapshot(
                adapter_id,
                {
                    "connection_state": "connected",
                    "paired": True,
                    "last_error": None,
                    "qr_data": None,
                },
            )
        elif status in {"failed", "error"}:
            _merge_adapter_snapshot(
                adapter_id,
                {
                    "connection_state": "error",
                    "paired": False,
                    "last_error": str(payload.get("message") or "WhatsApp pairing failed."),
                },
            )
        elif status in {"pairing", "waiting"}:
            _merge_adapter_snapshot(
                adapter_id,
                {
                    "connection_state": "pairing",
                    "paired": False,
                },
            )
        elif status == "disconnected":
            _merge_adapter_snapshot(
                adapter_id,
                {
                    "connection_state": "disconnected",
                    "last_error": None,
                    "qr_data": None,
                },
            )
    elif event_type == "error":
        _merge_adapter_snapshot(
            adapter_id,
            {
                "connection_state": str(payload.get("connection_state") or "error"),
                "last_error": str(payload.get("message") or "Adapter error"),
            },
        )
    elif event_type == "status":
        patch: dict[str, Any] = {}
        if payload.get("connection_state"):
            patch["connection_state"] = str(payload["connection_state"])
        if "paired" in payload:
            patch["paired"] = payload.get("paired")
        if payload.get("last_error") is not None:
            patch["last_error"] = payload.get("last_error")
        if patch:
            _merge_adapter_snapshot(adapter_id, patch)

    snapshots = _adapter_event_snapshots.setdefault(adapter_id, {})
    snapshots[event_type] = payload
    for queue in list(_adapter_event_subscribers.get(adapter_id, set())):
        queue.put_nowait(payload)


def _build_adapter_status_payload(
    adapter_id: str,
    adapter: Any,
    task: asyncio.Task[Any],
    now: float,
) -> dict[str, Any]:
    surface_type = _adapter_surface_types.get(adapter_id) or (
        type(adapter).__name__.replace("Adapter", "").lower()
    )
    running = _adapter_is_running(adapter, task)
    snapshot = dict(_adapter_status_snapshots.get(adapter_id, {}))

    if hasattr(adapter, "get_connection_snapshot"):
        try:
            live_snapshot = adapter.get_connection_snapshot()
        except Exception:
            logger.debug("Failed to read adapter snapshot for %s", adapter_id, exc_info=True)
        else:
            if isinstance(live_snapshot, dict):
                snapshot.update(live_snapshot)
                _adapter_status_snapshots[adapter_id] = snapshot

    connection_state = str(
        snapshot.get("connection_state") or ("connected" if running else "disconnected"),
    )
    last_error = snapshot.get("last_error")
    paired = snapshot.get("paired")
    if running and connection_state == "disconnected":
        connection_state = "connected"
    if not running and last_error and connection_state == "disconnected":
        connection_state = "error"

    return {
        "adapter_id": adapter_id,
        "type": surface_type,
        "running": running,
        "session_count": 0,
        "uptime_seconds": round(now - _adapter_start_times.get(adapter_id, now), 1),
        "connection_state": connection_state,
        "last_error": last_error,
        "paired": paired,
    }


def _find_active_surface_snapshot(surface_type: str) -> dict[str, Any]:
    now = time.time()
    for adapter_id, (adapter, task) in _active_adapters.items():
        if _adapter_surface_types.get(adapter_id) != surface_type:
            continue
        return _build_adapter_status_payload(adapter_id, adapter, task, now)
    return {}


def _adapter_ids_for_surface(surface_type: str) -> list[str]:
    normalized = str(surface_type or "").strip().lower()
    return [
        adapter_id
        for adapter_id, current_surface in _adapter_surface_types.items()
        if current_surface == normalized
    ]


def _build_telegram_config_summary() -> dict[str, Any]:
    state = _load_telegram_desktop_state()
    env_token = os.environ.get("DAN_TELEGRAM_BOT_TOKEN", "").strip()
    token = env_token or state["bot_token"]
    live = _find_active_surface_snapshot("telegram")
    dependency = _build_dependency_summary(
        module_name="telegram",
        package_name="python-telegram-bot",
        install_hint="pip install 'dan[messaging]'",
    )
    return {
        "type": "telegram",
        "configured": bool(token),
        "masked_token": _mask_secret(token),
        "bot_username": state["bot_username"] or None,
        "allowed_chat_ids": state["allowed_chat_ids"],
        "allowed_chat_count": len(state["allowed_chat_ids"]),
        "connection_state": live.get("connection_state"),
        "last_error": live.get("last_error"),
        **dependency,
    }


def _build_whatsapp_web_config_summary() -> dict[str, Any]:
    settings = _load_whatsapp_web_settings()
    session_db = Path(settings["db_path"])
    live = _find_active_surface_snapshot("whatsapp-web")
    dependency = _build_dependency_summary(
        module_name="neonize",
        package_name="neonize",
        install_hint="pip install 'dan[whatsapp-web]'",
    )
    paired = live.get("paired")
    if not isinstance(paired, bool):
        paired = session_db.exists()
    return {
        "type": "whatsapp-web",
        "configured": bool(settings["allowed_jids"] or session_db.exists()),
        "allowed_jids": settings["allowed_jids"],
        "allowed_jid_count": len(settings["allowed_jids"]),
        "paired": paired,
        "connection_state": live.get("connection_state") or "disconnected",
        "last_error": live.get("last_error"),
        "db_path": settings["db_path"],
        "session_db_exists": session_db.exists(),
        **dependency,
    }


def _prepare_telegram_start_config(config_data: dict[str, Any]) -> dict[str, Any]:
    state = _load_telegram_desktop_state()
    prepared = dict(config_data)
    if not prepared.get("bot_token") and state["bot_token"]:
        prepared["bot_token"] = state["bot_token"]
    if "allowed_chat_ids" not in prepared and state["allowed_chat_ids"]:
        prepared["allowed_chat_ids"] = state["allowed_chat_ids"]
    return prepared


def _prepare_whatsapp_web_start_config(config_data: dict[str, Any]) -> dict[str, Any]:
    settings = _load_whatsapp_web_settings()
    prepared = dict(config_data)
    if not prepared.get("db_path"):
        prepared["db_path"] = settings["db_path"]
    if "allowed_jids" not in prepared and settings["allowed_jids"]:
        prepared["allowed_jids"] = settings["allowed_jids"]
    return prepared


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


async def _stop_active_adapter(adapter_id: str, *, missing_ok: bool = False) -> bool:
    entry = _active_adapters.pop(adapter_id, None)
    if entry is None:
        return False if missing_ok else False

    adapter, task = entry
    if hasattr(adapter, "set_event_callback"):
        try:
            adapter.set_event_callback(None)
        except Exception:
            logger.debug("Failed to clear event callback for %s", adapter_id, exc_info=True)
    if not task.done():
        task.cancel()
    try:
        await adapter.stop()
    except Exception:
        pass

    _adapter_session_stores.pop(adapter_id, None)
    _adapter_start_times.pop(adapter_id, None)
    _adapter_renderers.pop(adapter_id, None)
    _adapter_surface_types.pop(adapter_id, None)
    _adapter_status_snapshots.pop(adapter_id, None)
    _close_adapter_event_streams(adapter_id)
    return True


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.post("/api/adapters/start")
async def start_adapter(req: AdapterStartRequest):
    from dan.adapters import (
        EmailAdapter, EmailAdapterConfig,
        TelegramAdapter, TelegramAdapterConfig,
        WhatsAppAdapter, WhatsAppAdapterConfig,
        WhatsAppWebAdapter, WhatsAppWebAdapterConfig,
        MessagingAdapter, MessagingHumanRenderer, AdapterSessionStore,
    )

    adapter_id = str(uuid.uuid4())[:12]
    adapter_type = req.type.lower()
    config_data = {**req.config, "workflow_path": req.workflow_path}
    if adapter_type == "telegram":
        config_data = _prepare_telegram_start_config(config_data)
    elif adapter_type == "whatsapp-web":
        config_data = _prepare_whatsapp_web_start_config(config_data)

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
    elif adapter_type == "whatsapp-web":
        adapter_config = WhatsAppWebAdapterConfig(**config_data)
        adapter = WhatsAppWebAdapter(adapter_config)
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
    if hasattr(adapter, "set_event_callback"):
        adapter.set_event_callback(
            lambda event: _publish_adapter_event(adapter_id, dict(event)),
        )

    _merge_adapter_snapshot(
        adapter_id,
        {
            "connection_state": "starting",
            "last_error": None,
            "paired": None,
        },
    )

    task: asyncio.Task[Any] | None = None

    async def _run_adapter() -> None:
        try:
            await adapter.start()
            if (
                adapter_type != "whatsapp-web"
                and task is not None
                and _adapter_is_running(adapter, task)
            ):
                _merge_adapter_snapshot(
                    adapter_id,
                    {
                        "connection_state": "connected",
                        "last_error": None,
                    },
                )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Adapter %s (%s) crashed", adapter_id, adapter_type)
            message = str(exc).strip() or exc.__class__.__name__
            _merge_adapter_snapshot(
                adapter_id,
                {
                    "connection_state": "error",
                    "last_error": message,
                },
            )
            _publish_adapter_event(
                adapter_id,
                {
                    "type": "error",
                    "connection_state": "error",
                    "message": message,
                },
            )

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
    if not await _stop_active_adapter(req.adapter_id, missing_ok=True):
        raise HTTPException(status_code=404, detail=f"Adapter '{req.adapter_id}' not found")

    return {"status": "stopped", "adapter_id": req.adapter_id}


@router.get("/api/adapters/status")
async def adapter_status():
    results = []
    now = time.time()
    for aid, (adapter, task) in _active_adapters.items():
        store = _adapter_session_stores.get(aid)
        payload = _build_adapter_status_payload(aid, adapter, task, now)
        if store is not None:
            sessions = await store.all_sessions()
            payload["session_count"] = len(sessions)
        results.append(payload)
    return results


@router.get("/api/adapters/config/{adapter_type}")
async def get_adapter_config(adapter_type: str):
    normalized = adapter_type.strip().lower()
    if normalized == "telegram":
        return _build_telegram_config_summary()
    if normalized == "whatsapp-web":
        return _build_whatsapp_web_config_summary()
    raise HTTPException(status_code=404, detail=f"Adapter config '{adapter_type}' not found")


@router.post("/api/adapters/config/{adapter_type}")
async def save_adapter_config(adapter_type: str, body: dict[str, Any]):
    normalized = adapter_type.strip().lower()

    if normalized == "telegram":
        from dan.cli.bot import verify_bot_token

        token = str(body.get("bot_token") or "").strip()
        allowed_chat_ids = _coerce_int_list(body.get("allowed_chat_ids"))
        info: dict[str, Any] | None = None
        if token:
            info = await asyncio.to_thread(verify_bot_token, token)
            if info is None:
                raise HTTPException(status_code=400, detail="Invalid Telegram bot token.")
        state = _save_telegram_desktop_state(
            bot_token=token or None,
            bot_username=str(info.get("username") or "").strip() if info else None,
            allowed_chat_ids=allowed_chat_ids if "allowed_chat_ids" in body else None,
        )
        summary = _build_telegram_config_summary()
        summary["bot_username"] = state["bot_username"] or None
        summary["allowed_chat_ids"] = state["allowed_chat_ids"]
        summary["allowed_chat_count"] = len(state["allowed_chat_ids"])
        return summary

    if normalized == "whatsapp-web":
        settings = _save_whatsapp_web_settings(
            allowed_jids=_coerce_str_list(body.get("allowed_jids"))
            if "allowed_jids" in body
            else None,
            db_path=str(body.get("db_path") or "").strip() or None
            if "db_path" in body
            else None,
        )
        summary = _build_whatsapp_web_config_summary()
        summary["allowed_jids"] = settings["allowed_jids"]
        summary["allowed_jid_count"] = len(settings["allowed_jids"])
        summary["db_path"] = settings["db_path"]
        return summary

    raise HTTPException(status_code=404, detail=f"Adapter config '{adapter_type}' not found")


@router.post("/api/adapters/config/{adapter_type}/reset")
async def reset_adapter_config(adapter_type: str):
    normalized = adapter_type.strip().lower()
    if normalized != "whatsapp-web":
        raise HTTPException(status_code=404, detail=f"Adapter config '{adapter_type}' not found")

    settings = _load_whatsapp_web_settings()
    for adapter_id in list(_adapter_ids_for_surface("whatsapp-web")):
        await _stop_active_adapter(adapter_id, missing_ok=True)

    db_path = Path(settings["db_path"])
    _remove_sqlite_artifacts(db_path)
    return _build_whatsapp_web_config_summary()


@router.get("/api/adapters/{adapter_id}/events")
async def adapter_events(adapter_id: str):
    if adapter_id not in _active_adapters and adapter_id not in _adapter_event_snapshots:
        raise HTTPException(status_code=404, detail=f"Adapter '{adapter_id}' not found")

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    subscribers = _adapter_event_subscribers.setdefault(adapter_id, set())
    subscribers.add(queue)

    async def event_stream():
        try:
            snapshot_events = sorted(
                _adapter_event_snapshots.get(adapter_id, {}).values(),
                key=lambda event: float(event.get("timestamp") or 0.0),
            )
            for event in snapshot_events:
                yield f"data: {json.dumps(event, default=str)}\n\n"

            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:
            subscribers = _adapter_event_subscribers.get(adapter_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    _adapter_event_subscribers.pop(adapter_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
