"""Adapter management endpoints and runtime logic."""

from __future__ import annotations

import asyncio
from importlib import util as importlib_util
import inspect
import json
import logging
import os
import re
import shlex
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
import httpx
from pydantic import BaseModel

from dan.agent_runtime.super_tui_contract import (
    SUPER_TUI_AGENT_CAPABILITIES,
    SUPER_TUI_DEFAULT_BACKEND,
    SUPER_TUI_SURFACE_PROFILE,
    build_super_tui_agent_execute_payload,
    build_super_tui_surface_context,
)
from dan.adapters.wechat_official_account_adapter import (
    build_encrypted_callback_reply,
    build_passive_text_reply,
    decrypt_encrypted_callback_echostr,
    decrypt_encrypted_callback_xml,
    parse_incoming_xml,
    validate_callback_encrypt_type,
    verify_signature,
)
from dan.server.control_plane import parse_control_plane_mode
from dan.server.control_plane import resolve_control_plane_mode
from dan.server.capabilities.config import _update_env_file
from dan.server.routers.dependencies import (
    get_concierge,
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
_adapter_conversation_history: dict[str, list[dict[str, str]]] = {}
_adapter_history_locks: dict[str, asyncio.Lock] = {}

_heartbeat_task: asyncio.Task[Any] | None = None
_heartbeat_interval: int = int(os.environ.get("DAN_ADAPTER_HEARTBEAT_SECONDS", "60"))
_last_phase_message_times: dict[str, float] = {}

_TELEGRAM_DESKTOP_BOT_KEY = "desktop-ui"
_DEFAULT_WHATSAPP_WEB_DIR = Path.home() / ".dan" / "whatsapp-web"
_DEFAULT_WHATSAPP_WEB_DB_PATH = _DEFAULT_WHATSAPP_WEB_DIR / "session.sqlite3"
_DEFAULT_WHATSAPP_WEB_CONFIG_PATH = _DEFAULT_WHATSAPP_WEB_DIR / "config.json"
_DEFAULT_WECHAT_OFFICIAL_ACCOUNT_DIR = Path.home() / ".dan" / "wechat-official-account"
_DEFAULT_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH = (
    _DEFAULT_WECHAT_OFFICIAL_ACCOUNT_DIR / "config.json"
)
_TELEGRAM_WORKSPACE_MENU_PAGE_SIZE = 8
_adapter_telegram_workspace_menus: dict[str, dict[str, Any]] = {}
_adapter_telegram_session_menus: dict[str, dict[str, Any]] = {}


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class AdapterStartRequest(BaseModel):
    type: str
    workflow_path: str = ""
    config: dict[str, Any] = {}


def _control_plane_env_key_suffix(*parts: str) -> str:
    joined = "_".join(str(part or "").strip() for part in parts if str(part or "").strip())
    return re.sub(r"[^A-Z0-9]+", "_", joined.upper()).strip("_")


def _adapter_control_plane_override(adapter_id: str, surface: str) -> str | None:
    pure_telegram_v2 = surface == "telegram" and not _telegram_adapter_allows_v1()
    candidates: list[str] = []
    for value in (
        _control_plane_env_key_suffix(surface),
        _control_plane_env_key_suffix(adapter_id),
        _control_plane_env_key_suffix(surface, adapter_id),
    ):
        if value and value not in candidates:
            candidates.append(value)
    for suffix in candidates:
        try:
            parsed = parse_control_plane_mode(
                os.environ.get(f"DAN_{suffix}_CONTROL_PLANE"),
            )
        except ValueError:
            continue
        if parsed is not None:
            if pure_telegram_v2 and parsed == "v1":
                return "v2"
            return parsed
    try:
        parsed = parse_control_plane_mode(os.environ.get("DAN_ADAPTERS_CONTROL_PLANE"))
    except ValueError:
        parsed = None
    if pure_telegram_v2 and parsed == "v1":
        return "v2"
    if parsed is not None:
        return parsed
    if pure_telegram_v2:
        return "v2"
    return None


def _telegram_adapter_allows_v1() -> bool:
    return str(os.environ.get("DAN_TELEGRAM_ALLOW_V1") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _adapter_requested_mode(text: str) -> tuple[str, str]:
    stripped = str(text or "").strip()
    if not stripped:
        return "auto", stripped
    first, _, rest = stripped.partition(" ")
    command = first.split("@", 1)[0].lower()
    if command in {"/agent", "/run", "/build", "/new"}:
        return "agent", rest.strip()
    if command in {"/append", "/inject", "/continue", "/continue-after-current"}:
        return "agent", stripped
    if stripped.lower().startswith("agent:"):
        return "agent", stripped.split(":", 1)[1].strip()
    return "auto", stripped


def _adapter_agent_command(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return ""
    first = stripped.split(maxsplit=1)[0].split("@", 1)[0].lower()
    command = first.lstrip("/")
    if command in {
        "agent",
        "run",
        "build",
        "new",
        "append",
        "inject",
        "continue",
        "continue-after-current",
    }:
        return command
    return ""


def _adapter_command_payload_text(text: str) -> str:
    stripped = str(text or "").strip()
    command = _adapter_agent_command(stripped)
    if command in {"append", "inject", "continue", "continue-after-current"}:
        _first, _sep, rest = stripped.partition(" ")
        return rest.strip()
    return stripped


def _adapter_effective_requested_mode(
    *,
    surface: str,
    control_plane_mode: str | None,
    requested_mode: str,
    text: str,
) -> str:
    mode = str(requested_mode or "auto").strip().lower() or "auto"
    if (
        str(surface or "").strip().lower() == "telegram"
        and control_plane_mode == "v2"
        and mode == "auto"
        and not _adapter_v2_control_command(text)
        and _adapter_v2_auto_text_should_run_agent(text)
    ):
        return "agent"
    return mode


def _adapter_v2_control_command(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return ""
    first = stripped.split(maxsplit=1)[0].split("@", 1)[0].lower()
    command = first.lstrip("/")
    if command in {
        "status",
        "cancel",
        "help",
        "start",
        "workspace",
        "session",
        "sessions",
        "tasks",
        "reset",
        "clear",
    }:
        return command
    return ""


def _adapter_v2_auto_text_should_run_agent(text: str) -> bool:
    lower = " ".join(str(text or "").lower().split())
    if not lower:
        return False
    if "[attachment:" in lower or "[voice note:" in lower:
        return any(
            cue in lower
            for cue in (
                "analyze",
                "extract",
                "build",
                "turn into",
                "compare",
                "summarize",
            )
        )
    has_path_context = bool(
        re.search(r"(^|\s)(/[^ ]+|~/[^ ]+|[a-z]:\\)", lower)
    ) or any(
        cue in lower
        for cue in (
            "this path",
            "path:",
            "repo:",
            "workspace:",
            "in /",
            "in ~/",
        )
    )
    if has_path_context and any(
        cue in lower
        for cue in (
            "help",
            "do this",
            "work on",
            "fix",
            "patch",
            "build",
            "implement",
            "edit",
            "modify",
            "run tests",
            "create",
        )
    ):
        return True
    return any(
        cue in lower
        for cue in (
            "implement",
            "build",
            "create file",
            "write to",
            "refactor",
            "run tests",
            "make a website",
            "patch",
            "edit the",
            "modify the",
            "fix the repo",
            "fix this repo",
            "long running",
        )
    )


def _adapter_history_key(adapter_id: str, surface: str, external_id: str) -> str:
    return ":".join(
        part
        for part in (
            str(surface or "adapter").strip() or "adapter",
            str(adapter_id or "").strip(),
            str(external_id or "").strip(),
        )
        if part
    )


def _compact_adapter_context_text(value: Any, *, limit: int = 1600) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


async def _append_adapter_history_turn(
    history_key: str,
    turn: dict[str, str],
) -> list[dict[str, str]]:
    lock = _adapter_history_locks.setdefault(history_key, asyncio.Lock())
    async with lock:
        history = list(_adapter_conversation_history.get(history_key, []))
        role = str(turn.get("role") or "").strip()
        content = _compact_adapter_context_text(turn.get("content"))
        if role in {"user", "assistant"} and content:
            history.append({"role": role, "content": content})
        if len(history) > 40:
            history = history[-40:]
        _adapter_conversation_history[history_key] = history
        return list(history)


async def _remove_adapter_history_turn(
    history_key: str,
    turn: dict[str, str],
) -> None:
    lock = _adapter_history_locks.setdefault(history_key, asyncio.Lock())
    async with lock:
        history = list(_adapter_conversation_history.get(history_key, []))
        role = str(turn.get("role") or "").strip()
        content = _compact_adapter_context_text(turn.get("content"))
        for idx in range(len(history) - 1, -1, -1):
            if history[idx].get("role") == role and history[idx].get("content") == content:
                del history[idx]
                break
        _adapter_conversation_history[history_key] = history


async def _clear_adapter_history(history_key: str) -> None:
    if not history_key:
        return
    lock = _adapter_history_locks.setdefault(history_key, asyncio.Lock())
    async with lock:
        _adapter_conversation_history.pop(history_key, None)


def _adapter_reply_context(ctx: Any | None) -> dict[str, str]:
    if ctx is None:
        return {}
    reply_to_message_id = str(getattr(ctx, "reply_to_message_id", "") or "").strip()
    reply_to_text = _compact_adapter_context_text(
        getattr(ctx, "reply_to_text", ""),
        limit=1200,
    )
    reply: dict[str, str] = {}
    if reply_to_message_id:
        reply["reply_to_message_id"] = reply_to_message_id
    if reply_to_text:
        reply["reply_to_text"] = reply_to_text
    return reply


def _adapter_workspace_context(surface: str, adapter_id: str) -> dict[str, Any]:
    surface_suffix = _control_plane_env_key_suffix(surface)
    adapter_suffix = _control_plane_env_key_suffix(adapter_id)
    root = (
        os.environ.get(f"DAN_{surface_suffix}_WORKSPACE_ROOT") if surface_suffix else None
    ) or (
        os.environ.get(f"DAN_{adapter_suffix}_WORKSPACE_ROOT") if adapter_suffix else None
    ) or os.environ.get("DAN_ADAPTERS_WORKSPACE_ROOT") or os.environ.get(
        "DAN_WORKSPACE_ROOT",
    )
    workspace_id = (
        os.environ.get(f"DAN_{surface_suffix}_WORKSPACE_ID") if surface_suffix else None
    ) or (
        os.environ.get(f"DAN_{adapter_suffix}_WORKSPACE_ID") if adapter_suffix else None
    ) or os.environ.get("DAN_ADAPTERS_WORKSPACE_ID")
    context: dict[str, Any] = {}
    if root:
        context["workspace_root"] = root
        context["workspace_source"] = f"{surface or 'adapter'}_env"
    if workspace_id:
        context["workspace_id"] = workspace_id
    return context


def _telegram_state_dir() -> Path:
    configured = str(os.environ.get("DAN_TELEGRAM_STATE_DIR") or "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".dan" / "telegram"


def _telegram_surface_state_path() -> Path:
    return _telegram_state_dir() / "surface-state.json"


def _load_telegram_surface_state() -> dict[str, Any]:
    path = _telegram_surface_state_path()
    if not path.exists():
        return {
            "active_workspaces": {},
            "recent_workspaces": {},
            "active_sessions": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload.setdefault("active_workspaces", {})
            payload.setdefault("recent_workspaces", {})
            payload.setdefault("active_sessions", {})
            return payload
    except Exception:
        logger.debug("Failed to load Telegram surface state", exc_info=True)
    return {
        "active_workspaces": {},
        "recent_workspaces": {},
        "active_sessions": {},
    }


def _save_telegram_surface_state(state: dict[str, Any]) -> None:
    path = _telegram_surface_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        logger.debug("Failed to save Telegram surface state", exc_info=True)


def _adapter_telegram_workspace_context(
    *,
    adapter_id: str,
    external_id: str,
    history_key: str,
) -> dict[str, Any]:
    state = _load_telegram_surface_state()
    active = state.setdefault("active_workspaces", {})
    for key in (history_key, external_id, f"adapter:{adapter_id}", "adapter:telegram"):
        value = str(active.get(key) or "").strip()
        if value:
            return {
                "workspace_root": value,
                "workspace_id": _workspace_id_from_path(value),
                "workspace_source": "telegram_menu",
            }
    base = _adapter_workspace_context("telegram", adapter_id)
    if base:
        base = dict(base)
        base.setdefault("workspace_source", "telegram_env")
        return base
    return {
        "workspace_root": "~",
        "workspace_id": "~",
        "workspace_source": "default_home",
    }


def _adapter_telegram_session_binding(
    *,
    external_id: str,
    history_key: str,
) -> dict[str, Any]:
    state = _load_telegram_surface_state()
    sessions = state.setdefault("active_sessions", {})
    for key in (history_key, external_id):
        if key and isinstance(sessions.get(key), dict):
            return dict(sessions[key])
    return {}


def _select_adapter_telegram_workspace(
    *,
    adapter_id: str,
    external_id: str,
    history_key: str,
    root: str,
) -> None:
    state = _load_telegram_surface_state()
    selected = str(_normalize_workspace_menu_path(root))
    active = state.setdefault("active_workspaces", {})
    active[history_key] = selected
    active[external_id] = selected
    active[f"adapter:{adapter_id}"] = selected
    active["adapter:telegram"] = selected
    recents = state.setdefault("recent_workspaces", {})
    values = [item for item in list(recents.get("adapter:telegram", [])) if item != selected]
    recents["adapter:telegram"] = [selected, *values][:12]
    _save_telegram_surface_state(state)


def _recent_adapter_telegram_workspaces() -> list[str]:
    state = _load_telegram_surface_state()
    values = state.setdefault("recent_workspaces", {}).get("adapter:telegram", [])
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if str(item).strip()]


def _set_adapter_telegram_session_binding(
    *,
    external_id: str,
    history_key: str,
    binding: dict[str, Any] | None,
) -> None:
    state = _load_telegram_surface_state()
    sessions = state.setdefault("active_sessions", {})
    if binding:
        sessions[history_key] = dict(binding)
        sessions[external_id] = dict(binding)
    else:
        sessions.pop(history_key, None)
        sessions.pop(external_id, None)
    _save_telegram_surface_state(state)


def _force_reset_adapter_telegram_session(binding: dict[str, Any]) -> str:
    run_id = str(binding.get("run_id") or "").strip()
    if not run_id:
        return ""
    try:
        from dan.server.chat_v2 import AgentRunCommand
        from dan.server.routers.dependencies import get_chat_v2_store
    except Exception:
        return ""
    try:
        store = get_chat_v2_store()
        run = store.get_run(run_id)
        if run is None:
            return ""
        if run.status not in {"completed", "failed", "blocked", "stopped"}:
            stop_event = store.record_agent_command(
                AgentRunCommand(
                    command="stop",
                    task_id=str(binding.get("task_id") or run.task_id or ""),
                    run_id=run_id,
                    idempotency_key=f"telegram-reset-{uuid.uuid4().hex}",
                    payload={"reason": "telegram_reset"},
                )
            )
            confirmed = store.confirm_agent_run_stopped(
                run_id,
                checkpoint="telegram.reset",
                reason="Telegram reset forced this run out of the active lane.",
            )
            return str(
                (confirmed.summary if confirmed is not None else "")
                or stop_event.summary
                or "Telegram reset forced the active run to stopped."
            )
    except Exception:
        logger.debug("Failed to force-reset Telegram session", exc_info=True)
    return ""


def _telegram_menu_command(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return ""
    command = stripped.split(maxsplit=1)[0].split("@", 1)[0].lower()
    if command in {"/workspace", "/workspaces", "/ws"}:
        return "workspace"
    if command in {"/session", "/sessions", "/tasks", "/status"}:
        return "session"
    if command in {"/reset", "/clear"}:
        return "reset"
    return ""


def _telegram_command_argument(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return ""
    try:
        parts = shlex.split(stripped)
    except ValueError:
        parts = stripped.split(maxsplit=1)
    if len(parts) <= 1:
        return ""
    return " ".join(parts[1:]).strip()


def _adapter_telegram_message_lane_key(history_key: str, ctx: Any | None) -> str:
    message_id = getattr(ctx, "message_id", None)
    if message_id is None:
        return history_key
    return f"{history_key}:m{message_id}"


def _normalize_workspace_menu_path(value: Any, *, base: Any = None) -> Path:
    raw = str(value or "").strip() or str(Path.cwd())
    if raw.startswith("$HOME/") or raw == "$HOME":
        raw = str(Path.home()) + raw[len("$HOME") :]
    path = Path(raw).expanduser()
    if not path.is_absolute():
        base_path = Path(str(base or Path.cwd())).expanduser()
        path = base_path / path
    try:
        return path.resolve(strict=False)
    except Exception:
        return path


def _workspace_child_dirs(path: Path) -> list[Path]:
    try:
        if not path.exists() or not path.is_dir():
            return []
        dirs = [
            item
            for item in path.iterdir()
            if item.is_dir() and not item.name.startswith(".")
        ]
        dirs.sort(key=lambda item: item.name.lower())
        return dirs[:100]
    except Exception:
        return []


def _short_path_label(path: str, *, max_len: int = 32) -> str:
    text = str(path or "").strip()
    name = Path(text).name or text
    label = name if len(name) <= max_len else name[: max_len - 3].rstrip() + "..."
    return label or (text[-max_len:] if len(text) > max_len else text)


def _workspace_id_from_path(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    return Path(text).name or text


def _split_menu_callback(data: str) -> tuple[str, str, str, str]:
    parts = str(data or "").split(":", 3)
    while len(parts) < 4:
        parts.append("")
    return parts[0], parts[1], parts[2], parts[3]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class AdapterStopRequest(BaseModel):
    adapter_id: str


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _load_workflow_for_adapter(path: str):
    import importlib.util
    from dan.migration.gate_migration import maybe_migrate_graph_dict
    from dan.models.graph import Graph

    gs = get_graph_store()
    data = None
    try:
        data = gs.get_graph(path)
    except ValueError:
        data = None
    if data is not None:
        data = maybe_migrate_graph_dict(data)
        return Graph.model_validate(data)

    p = Path(path)
    if not p.exists():
        if path.endswith((".json", ".md", ".py")):
            raise FileNotFoundError(f"File not found: {path}")
        raise ValueError(f"Cannot load workflow: '{path}' is not a valid file or graph ID")

    if p.suffix.lower() == ".json":
        with open(p) as f:
            return Graph.model_validate(maybe_migrate_graph_dict(json.load(f)))
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
            chat_id, thread_id = _adapter_external_target(external_id)
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


async def _send_adapter_telegram_menu(
    adapter: Any,
    external_id: str,
    text: str,
    buttons: list[list[tuple[str, str]]],
    *,
    ctx: Any | None = None,
    message_id: int | None = None,
    reply_to: int | None = None,
) -> int | None:
    chat_id = getattr(ctx, "chat_id", None)
    thread_id = getattr(ctx, "thread_id", None)
    if chat_id is None:
        chat_id, fallback_thread = _adapter_external_target(external_id)
        if thread_id is None:
            thread_id = fallback_thread
    if hasattr(adapter, "send_menu") and isinstance(chat_id, int):
        return await adapter.send_menu(
            chat_id,
            text,
            buttons,
            message_id=message_id,
            reply_to=reply_to,
            thread_id=thread_id,
        )
    await _send_adapter_text(adapter, external_id, text)
    return None


def _adapter_external_target(external_id: str) -> tuple[int | str, int | None]:
    try:
        thread_id: int | None = None
        if ":" in str(external_id):
            chat_raw, thread_raw = str(external_id).split(":", 1)
            chat_id = int(chat_raw)
            thread_id = int(thread_raw) if thread_raw else None
        else:
            chat_id = int(external_id)
        return chat_id, thread_id
    except (ValueError, TypeError):
        return external_id, None


async def _send_adapter_progress_or_edit(
    adapter: Any,
    external_id: str,
    text: str,
    *,
    message_id: int | None = None,
) -> int | None:
    if hasattr(adapter, "send_or_edit") and not hasattr(adapter, "_jid_map"):
        chat_id, thread_id = _adapter_external_target(external_id)
        if isinstance(chat_id, int):
            try:
                return await adapter.send_or_edit(
                    chat_id,
                    text,
                    message_id,
                    thread_id=thread_id,
                )
            except Exception:
                logger.debug(
                    "Adapter progress edit failed for %s",
                    external_id,
                    exc_info=True,
                )
    await _send_adapter_text(adapter, external_id, text)
    return message_id


async def _handle_adapter_telegram_menu_command(
    *,
    adapter_id: str,
    adapter: Any,
    external_id: str,
    text: str,
    ctx: Any | None,
    history_key: str,
) -> bool:
    command = _telegram_menu_command(text)
    if command == "workspace":
        workspace_arg = _telegram_command_argument(text)
        await _show_adapter_workspace_menu(
            adapter_id=adapter_id,
            adapter=adapter,
            external_id=external_id,
            ctx=ctx,
            history_key=history_key,
            path=workspace_arg or None,
            reply_to=getattr(ctx, "message_id", None),
        )
        return True
    if command == "session":
        await _show_adapter_session_menu(
            adapter=adapter,
            external_id=external_id,
            ctx=ctx,
            history_key=history_key,
            reply_to=getattr(ctx, "message_id", None),
        )
        return True
    if command == "reset":
        binding = _adapter_telegram_session_binding(
            external_id=external_id,
            history_key=history_key,
        )
        stop_summary = _force_reset_adapter_telegram_session(binding)
        _set_adapter_telegram_session_binding(
            external_id=external_id,
            history_key=history_key,
            binding=None,
        )
        await _clear_adapter_history(history_key)
        summary = (
            "Telegram state reset. Existing DAN Super task status was forced out of this lane; your next Agent request starts fresh."
            if stop_summary
            else "Telegram state reset. Your next Agent request starts fresh."
        )
        await _send_adapter_text(adapter, external_id, summary)
        return True
    return False


async def _show_adapter_workspace_menu(
    *,
    adapter_id: str,
    adapter: Any,
    external_id: str,
    ctx: Any | None,
    history_key: str,
    path: str | None = None,
    page: int = 0,
    message_id: int | None = None,
    reply_to: int | None = None,
    menu_id: str | None = None,
    note: str = "",
) -> None:
    workspace = _adapter_telegram_workspace_context(
        adapter_id=adapter_id,
        external_id=external_id,
        history_key=history_key,
    )
    current = (
        str(workspace.get("workspace_root") or "")
        if workspace.get("workspace_source") == "telegram_menu"
        else ""
    )
    browse_root = _normalize_workspace_menu_path(
        path
        or current
        or workspace.get("workspace_root")
        or Path.cwd(),
        base=current or workspace.get("workspace_root") or Path.cwd(),
    )
    entries = _workspace_child_dirs(browse_root)
    max_page = max(0, (len(entries) - 1) // _TELEGRAM_WORKSPACE_MENU_PAGE_SIZE)
    page = max(0, min(page, max_page))
    visible = entries[
        page * _TELEGRAM_WORKSPACE_MENU_PAGE_SIZE:
        (page + 1) * _TELEGRAM_WORKSPACE_MENU_PAGE_SIZE
    ]
    menu_id = menu_id or uuid.uuid4().hex[:8]
    recents = _recent_adapter_telegram_workspaces()
    _adapter_telegram_workspace_menus[menu_id] = {
        "adapter_id": adapter_id,
        "external_id": external_id,
        "history_key": history_key,
        "path": str(browse_root),
        "page": page,
        "entries": [str(item) for item in visible],
        "recents": list(recents),
    }
    lines = ["DAN Super workspace"]
    lines.append(f"Selected: {current or 'default workspace'}")
    lines.append(f"Browsing: {browse_root}")
    if note:
        lines.extend(["", note])
    lines.extend(["", "Tap Down to enter a folder, Parent to go up, then Select this folder."])
    if not visible:
        lines.append("No child folders are visible here.")
    buttons: list[list[tuple[str, str]]] = []
    for idx, root in enumerate(recents[:4]):
        buttons.append([(f"Saved: {_short_path_label(root)}", f"danws:{menu_id}:recent:{idx}")])
    for idx, child in enumerate(visible):
        buttons.append([(f"Down: {_short_path_label(str(child))}", f"danws:{menu_id}:open:{idx}")])
    nav: list[tuple[str, str]] = []
    if browse_root.parent != browse_root:
        nav.append(("Parent", f"danws:{menu_id}:up:0"))
    if page > 0:
        nav.append(("Prev", f"danws:{menu_id}:page:{page - 1}"))
    if page < max_page:
        nav.append(("Next", f"danws:{menu_id}:page:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append(
        [
            ("Select this folder", f"danws:{menu_id}:select:0"),
            ("Refresh", f"danws:{menu_id}:refresh:0"),
        ]
    )
    buttons.append([("Sessions", f"danws:{menu_id}:sessions:0")])
    await _send_adapter_telegram_menu(
        adapter,
        external_id,
        "\n".join(lines),
        buttons,
        ctx=ctx,
        message_id=message_id,
        reply_to=reply_to,
    )


async def _handle_adapter_workspace_callback(
    *,
    adapter_id: str,
    adapter: Any,
    external_id: str,
    data: str,
    ctx: Any | None,
) -> None:
    _prefix, menu_id, action, raw_value = _split_menu_callback(data)
    state = _adapter_telegram_workspace_menus.get(menu_id)
    if not state:
        await _send_adapter_telegram_menu(
            adapter,
            external_id,
            "This workspace menu expired. Send /workspace to open a fresh one.",
            [],
            ctx=ctx,
            message_id=getattr(ctx, "message_id", None),
        )
        return
    history_key = str(state.get("history_key") or "")
    external_id = str(state.get("external_id") or external_id)
    current_path = str(state.get("path") or Path.cwd())
    page = int(state.get("page") or 0)
    if action == "open":
        entries = list(state.get("entries") or [])
        idx = _safe_int(raw_value)
        if 0 <= idx < len(entries):
            current_path = entries[idx]
            page = 0
    elif action == "recent":
        recents = list(state.get("recents") or [])
        idx = _safe_int(raw_value)
        if 0 <= idx < len(recents):
            current_path = str(recents[idx])
            _select_adapter_telegram_workspace(
                adapter_id=adapter_id,
                external_id=external_id,
                history_key=history_key,
                root=current_path,
            )
            await _show_adapter_workspace_menu(
                adapter_id=adapter_id,
                adapter=adapter,
                external_id=external_id,
                ctx=ctx,
                history_key=history_key,
                path=current_path,
                message_id=getattr(ctx, "message_id", None),
                menu_id=menu_id,
                note="Workspace selected.",
            )
            return
    elif action == "up":
        current_path = str(Path(current_path).expanduser().parent)
        page = 0
    elif action == "page":
        page = _safe_int(raw_value)
    elif action == "refresh":
        pass
    elif action == "sessions":
        await _show_adapter_session_menu(
            adapter=adapter,
            external_id=external_id,
            ctx=ctx,
            history_key=history_key,
            message_id=getattr(ctx, "message_id", None),
        )
        return
    elif action == "select":
        _select_adapter_telegram_workspace(
            adapter_id=adapter_id,
            external_id=external_id,
            history_key=history_key,
            root=current_path,
        )
        await _show_adapter_workspace_menu(
            adapter_id=adapter_id,
            adapter=adapter,
            external_id=external_id,
            ctx=ctx,
            history_key=history_key,
            path=current_path,
            message_id=getattr(ctx, "message_id", None),
            menu_id=menu_id,
            note="Workspace selected.",
        )
        return
    await _show_adapter_workspace_menu(
        adapter_id=adapter_id,
        adapter=adapter,
        external_id=external_id,
        ctx=ctx,
        history_key=history_key,
        path=current_path,
        page=page,
        message_id=getattr(ctx, "message_id", None),
        menu_id=menu_id,
    )


async def _show_adapter_session_menu(
    *,
    adapter: Any,
    external_id: str,
    ctx: Any | None,
    history_key: str,
    message_id: int | None = None,
    reply_to: int | None = None,
    menu_id: str | None = None,
    note: str = "",
) -> None:
    tasks = _adapter_available_tasks(external_id)
    menu_id = menu_id or uuid.uuid4().hex[:8]
    _adapter_telegram_session_menus[menu_id] = {
        "external_id": external_id,
        "history_key": history_key,
        "tasks": tasks,
    }
    binding = _adapter_telegram_session_binding(
        external_id=external_id,
        history_key=history_key,
    )
    display_tasks = tasks[:12]
    lines = ["DAN Super sessions"]
    if binding.get("task_id"):
        resumed = _adapter_task_title(
            next((task for task in tasks if str(task.get("task_id") or "") == str(binding.get("task_id") or "")), {})
        )
        lines.append(f"Resumed: {resumed or 'selected session'}")
    if note:
        lines.extend(["", note])
    if not tasks:
        lines.extend(["", "No DAN Super sessions are attached to this Telegram chat yet."])
    else:
        lines.extend([
            "",
            "Select a session to resume it. Active sessions accept your next message as steering.",
        ])
        display_idx = 1
        for group in _adapter_session_workspace_groups(display_tasks):
            lines.append(f"[{group['label']}]")
            for task in group["tasks"]:
                status = _adapter_session_status_label(task)
                title = _adapter_task_title(task)
                latest = _compact_adapter_context_text(task.get("latest_progress"), limit=72)
                suffix = f" - {latest}" if latest and latest != title else ""
                lines.append(f"{display_idx}. {status} {title}{suffix}".rstrip())
                display_idx += 1
        if len(tasks) > len(display_tasks):
            lines.append(f"Showing latest {len(display_tasks)} of {len(tasks)} sessions.")
    buttons: list[list[tuple[str, str]]] = []
    for idx, task in enumerate(display_tasks):
        status = str(task.get("status") or "").strip().lower()
        title = _adapter_task_button_label(task)
        if status in {"running", "queued", "waiting_dependency", "paused", "needs_input"}:
            buttons.append(
                [
                    (f"Steer: {title}", f"dansn:{menu_id}:use:{idx}"),
                    ("Next", f"dansn:{menu_id}:continue:{idx}"),
                    ("Status", f"dansn:{menu_id}:status:{idx}"),
                ]
            )
        else:
            buttons.append(
                [
                    (f"Resume: {title}", f"dansn:{menu_id}:use:{idx}"),
                    ("Status", f"dansn:{menu_id}:status:{idx}"),
                ]
            )
    buttons.append(
        [
            ("Refresh", f"dansn:{menu_id}:refresh:0"),
            ("Clear resume", f"dansn:{menu_id}:clear:0"),
        ]
    )
    buttons.append([("Workspace", f"dansn:{menu_id}:workspace:0")])
    await _send_adapter_telegram_menu(
        adapter,
        external_id,
        "\n".join(lines),
        buttons,
        ctx=ctx,
        message_id=message_id,
        reply_to=reply_to,
    )


async def _handle_adapter_session_callback(
    *,
    adapter_id: str,
    adapter: Any,
    external_id: str,
    data: str,
    ctx: Any | None,
) -> None:
    _prefix, menu_id, action, raw_value = _split_menu_callback(data)
    state = _adapter_telegram_session_menus.get(menu_id)
    if not state:
        await _send_adapter_telegram_menu(
            adapter,
            external_id,
            "This session menu expired. Send /session to open a fresh one.",
            [],
            ctx=ctx,
            message_id=getattr(ctx, "message_id", None),
        )
        return
    external_id = str(state.get("external_id") or external_id)
    history_key = str(state.get("history_key") or "")
    tasks = list(state.get("tasks") or [])
    idx = _safe_int(raw_value)
    if action == "refresh":
        await _show_adapter_session_menu(
            adapter=adapter,
            external_id=external_id,
            ctx=ctx,
            history_key=history_key,
            message_id=getattr(ctx, "message_id", None),
            menu_id=menu_id,
        )
        return
    if action == "clear":
        _set_adapter_telegram_session_binding(
            external_id=external_id,
            history_key=history_key,
            binding=None,
        )
        await _show_adapter_session_menu(
            adapter=adapter,
            external_id=external_id,
            ctx=ctx,
            history_key=history_key,
            message_id=getattr(ctx, "message_id", None),
            menu_id=menu_id,
            note="Session resume cleared.",
        )
        return
    if action == "workspace":
        await _show_adapter_workspace_menu(
            adapter_id=adapter_id,
            adapter=adapter,
            external_id=external_id,
            ctx=ctx,
            history_key=history_key,
            message_id=getattr(ctx, "message_id", None),
        )
        return
    if not (0 <= idx < len(tasks)):
        return
    task = dict(tasks[idx])
    if action == "status":
        latest = _compact_adapter_context_text(task.get("latest_progress"), limit=1200)
        metadata = dict(task.get("metadata") or {})
        text = "\n".join(
            line
            for line in (
                f"Session: {_adapter_task_title(task)}",
                f"Status: {task.get('status', 'unknown')}",
                f"Phase: {task.get('phase', '')}",
                f"Workspace: {_adapter_workspace_label(metadata.get('workspace_root') or task.get('workspace_root') or '')}",
                f"Latest: {latest}" if latest else "",
                f"Task id: {task.get('task_id', '')}",
            )
            if line
        )
        await _send_adapter_telegram_menu(
            adapter,
            external_id,
            text,
            [[("Back to sessions", f"dansn:{menu_id}:refresh:0")]],
            ctx=ctx,
            message_id=getattr(ctx, "message_id", None),
        )
        return
    if action in {"use", "append", "continue"}:
        queue_action = "append" if action != "continue" else "continue_after_current"
        if action == "use" and str(task.get("status") or "") in {
            "completed",
            "failed",
            "blocked",
            "stopped",
        }:
            queue_action = ""
        binding = {
            "task_id": str(task.get("task_id") or ""),
            "run_id": str(dict(task.get("metadata") or {}).get("active_run_id") or ""),
            "status": str(task.get("status") or ""),
            "queue_action": queue_action,
        }
        _set_adapter_telegram_session_binding(
            external_id=external_id,
            history_key=history_key,
            binding=binding,
        )
        note = (
            "Session resumed. Next message will append to the active run."
            if queue_action == "append"
            else "Session resumed. Next Agent-like message will continue from this task."
        )
        await _show_adapter_session_menu(
            adapter=adapter,
            external_id=external_id,
            ctx=ctx,
            history_key=history_key,
            message_id=getattr(ctx, "message_id", None),
            menu_id=menu_id,
            note=note,
        )


async def _handle_adapter_telegram_callback_query(
    adapter_id: str,
    adapter: Any,
    external_id: str,
    data: str,
    ctx: Any | None = None,
) -> None:
    if data.startswith("danws:"):
        await _handle_adapter_workspace_callback(
            adapter_id=adapter_id,
            adapter=adapter,
            external_id=external_id,
            data=data,
            ctx=ctx,
        )
    elif data.startswith("dansn:"):
        await _handle_adapter_session_callback(
            adapter_id=adapter_id,
            adapter=adapter,
            external_id=external_id,
            data=data,
            ctx=ctx,
        )


def _adapter_available_tasks(thread_id: str) -> list[dict[str, Any]]:
    try:
        from dan.server.routers.dependencies import get_chat_v2_store

        store = get_chat_v2_store()
        if hasattr(store, "list_task_records"):
            records = store.list_task_records(limit=60)
            if records:
                return [_adapter_task_session_payload(store, record) for record in records]
        return [
            _adapter_enrich_task_snapshot(store, snapshot.model_dump(mode="json"))
            for snapshot in store.list_thread_tasks(thread_id, limit=20)
        ]
    except Exception:
        logger.debug("Failed to load Telegram adapter DAN Super sessions", exc_info=True)
        return []


def _adapter_task_session_payload(store: Any, task: Any) -> dict[str, Any]:
    if hasattr(task, "snapshot"):
        payload = task.snapshot().model_dump(mode="json")
    elif hasattr(task, "model_dump"):
        payload = task.model_dump(mode="json")
    else:
        payload = dict(task or {})
    metadata = payload.setdefault("metadata", {})
    metadata.setdefault("workspace_root", getattr(task, "workspace_root", ""))
    metadata.setdefault("workspace_id", getattr(task, "workspace_id", ""))
    active_run_id = str(getattr(task, "active_run_id", None) or metadata.get("active_run_id") or "")
    return _adapter_enrich_task_snapshot(store, payload, active_run_id=active_run_id)


def _adapter_enrich_task_snapshot(
    store: Any,
    payload: dict[str, Any],
    *,
    active_run_id: str = "",
) -> dict[str, Any]:
    metadata = payload.setdefault("metadata", {})
    run = None
    run_id = active_run_id or str(metadata.get("active_run_id") or "")
    if run_id and hasattr(store, "get_run"):
        run = store.get_run(run_id)
    if run is None and hasattr(store, "list_run_records"):
        runs = store.list_run_records(task_id=str(payload.get("task_id") or ""), limit=1)
        run = runs[0] if runs else None
    command_text = _adapter_run_command_text(run)
    if command_text:
        metadata["command_text"] = command_text
        payload["title"] = _compact_adapter_context_text(command_text, limit=96)
    elif not payload.get("title"):
        latest = _compact_adapter_context_text(payload.get("latest_progress"), limit=96)
        payload["title"] = latest or "Untitled DAN Super session"
    return payload


def _adapter_run_command_text(run: Any | None) -> str:
    if run is None:
        return ""
    command = getattr(run, "command", None)
    payload = getattr(command, "payload", {}) if command is not None else {}
    if not isinstance(payload, dict):
        return ""
    for key in ("text", "objective", "message", "prompt"):
        value = _compact_adapter_context_text(payload.get(key), limit=200)
        if value:
            return value
    return ""


def _adapter_session_workspace_groups(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for task in tasks:
        metadata = dict(task.get("metadata") or {})
        root = str(task.get("workspace_root") or metadata.get("workspace_root") or "").strip()
        workspace_id = str(task.get("workspace_id") or metadata.get("workspace_id") or "").strip()
        key = root or workspace_id or "default"
        group = index.get(key)
        if group is None:
            group = {
                "key": key,
                "label": _adapter_workspace_label(root or workspace_id or "default workspace"),
                "tasks": [],
            }
            index[key] = group
            groups.append(group)
        group["tasks"].append(task)
    return groups


def _adapter_workspace_label(path: str) -> str:
    text = str(path or "").strip()
    if not text or text == "~":
        return "~"
    return _short_path_label(text, max_len=40)


def _adapter_task_title(task: dict[str, Any]) -> str:
    metadata = dict(task.get("metadata") or {})
    for value in (
        task.get("title"),
        task.get("objective"),
        metadata.get("command_text"),
        metadata.get("objective"),
        metadata.get("title"),
        task.get("latest_progress"),
    ):
        title = _compact_adapter_context_text(value, limit=96)
        if title:
            return title
    return "Untitled DAN Super session"


def _adapter_task_button_label(task: dict[str, Any]) -> str:
    return _compact_adapter_context_text(_adapter_task_title(task), limit=28)


def _adapter_session_status_label(task: dict[str, Any]) -> str:
    status = str(task.get("status") or "unknown").strip().lower()
    if status == "running":
        return "running"
    if status in {"queued", "waiting_dependency"}:
        return "queued"
    if status in {"needs_input", "paused"}:
        return status.replace("_", " ")
    if status in {"completed", "failed", "blocked", "stopped"}:
        return status
    return status or "unknown"


async def _send_adapter_v2_agent_run_command(
    *,
    adapter: Any,
    external_id: str,
    body: dict[str, Any],
    selected_session: dict[str, Any],
) -> str:
    from dan.server.chat_v2 import AgentRunCommand
    from dan.server.routers.dependencies import get_chat_v2_store

    message_text = str(body.get("message") or "")
    explicit_command = _adapter_agent_command(message_text)
    command_name = str(selected_session.get("queue_action") or "").strip()
    if explicit_command in {"append", "inject"}:
        command_name = "append"
    elif explicit_command in {"continue", "continue-after-current"}:
        command_name = "continue_after_current"
    if command_name == "append":
        command_name = "append_followup"
    if command_name not in {"append_followup", "continue_after_current"}:
        return ""
    run_id = str(selected_session.get("run_id") or "").strip()
    task_id = str(selected_session.get("task_id") or "").strip()
    if not run_id:
        return ""
    store = get_chat_v2_store()
    run = store.get_run(run_id)
    if run is None:
        await _send_adapter_text(adapter, external_id, "That DAN Super run was not found. Use /session to refresh.")
        return ""
    command = AgentRunCommand(
        command=command_name,
        task_id=task_id or run.task_id,
        run_id=run_id,
        idempotency_key=uuid.uuid4().hex,
        payload={
            "text": _adapter_command_payload_text(message_text),
            "surface_context": dict(body.get("surface_context") or {}),
            "history": list(body.get("history") or []),
        },
    )
    event = store.record_agent_command(command)
    summary = str(event.summary or "").strip()
    if not summary:
        summary = (
            "Steering note queued for the active DAN Super run."
            if command_name == "append_followup"
            else "Queued after the current DAN Super run."
        )
    await _send_adapter_text(adapter, external_id, summary)
    return summary


async def _stream_v2_agent_run_events_to_adapter(
    adapter: Any,
    external_id: str,
    run_id: str,
) -> str:
    """Project a local V2 Agent run event log into one adapter progress bubble."""

    from dan.server.chat_v2_progress import AgentProgressStateMachine
    from dan.server.routers.dependencies import get_chat_v2_store

    terminal_statuses = {"completed", "failed", "blocked", "stopped"}
    store = get_chat_v2_store()
    progress = AgentProgressStateMachine(run_id=run_id)
    heartbeat_interval = max(
        0.0,
        float(os.environ.get("DAN_TELEGRAM_V2_PROGRESS_INTERVAL", "10")),
    )
    current_msg_id: int | None = None
    seen = 0
    last_progress_at = time.monotonic()

    while True:
        events = store.load_run_events(run_id)
        if len(events) > seen:
            for event in events[seen:]:
                snapshot = progress.observe(event)
                current_msg_id = await _send_adapter_progress_or_edit(
                    adapter,
                    external_id,
                    progress.render_status(),
                    message_id=current_msg_id,
                )
                last_progress_at = time.monotonic()
                if snapshot.terminal:
                    return snapshot.latest_summary or snapshot.detail
            seen = len(events)
            continue

        run = store.get_run(run_id)
        if run is None or run.status in terminal_statuses:
            break

        now = time.monotonic()
        if heartbeat_interval > 0 and now - last_progress_at >= heartbeat_interval:
            current_msg_id = await _send_adapter_progress_or_edit(
                adapter,
                external_id,
                progress.render_status(heartbeat=True),
                message_id=current_msg_id,
            )
            last_progress_at = now
        await asyncio.sleep(0.25)

    snapshot = progress.snapshot()
    return snapshot.latest_summary or snapshot.detail


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


def _coerce_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    return bool(value)


def _coerce_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_wechat_callback_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "callback"

    if "://" in text:
        parsed = urlparse(text)
        text = parsed.path or ""

    text = text.strip().strip("/")
    prefix = "api/adapters/wechat/"
    if text.startswith(prefix):
        text = text[len(prefix):]

    return text or "callback"


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
        return {
            "bot_token": "", "bot_username": "", "allowed_chat_ids": [],
            "auto_start": False, "commands": [], "mini_app_url": None,
        }
    return {
        "bot_token": str(bot.token or "").strip(),
        "bot_username": str(getattr(bot, "username", "") or "").strip(),
        "allowed_chat_ids": _coerce_int_list(bot.allowed_users),
        "auto_start": bool(getattr(bot, "auto_start", False)),
        "commands": list(getattr(bot, "commands", None) or []),
        "mini_app_url": str(getattr(bot, "mini_app_url", "") or "").strip() or None,
    }


def _save_telegram_desktop_state(
    *,
    bot_token: str | None = None,
    bot_username: str | None = None,
    allowed_chat_ids: list[int] | None = None,
    auto_start: bool | None = None,
    commands: list[dict[str, str]] | None = None,
    mini_app_url: str | None = ...,  # type: ignore[assignment]
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
        return {
            "bot_token": "", "bot_username": "", "allowed_chat_ids": [],
            "commands": [], "mini_app_url": None,
        }

    bot_config = current or TelegramBotConfig(default=False)
    bot_config.token = next_token
    bot_config.username = next_username
    bot_config.allowed_users = next_allowed
    bot_config.default = False
    if auto_start is not None:
        bot_config.auto_start = auto_start
    if commands is not None:
        bot_config.commands = commands
    if mini_app_url is not ...:
        bot_config.mini_app_url = str(mini_app_url or "").strip()
    fleet.bots[_TELEGRAM_DESKTOP_BOT_KEY] = bot_config
    save_fleet_config(fleet)

    if next_token:
        os.environ["DAN_TELEGRAM_BOT_TOKEN"] = next_token
        _update_env_file("DAN_TELEGRAM_BOT_TOKEN", next_token)

    return {
        "bot_token": next_token,
        "bot_username": next_username,
        "allowed_chat_ids": next_allowed,
        "auto_start": bool(bot_config.auto_start),
        "commands": list(bot_config.commands),
        "mini_app_url": str(bot_config.mini_app_url or "").strip() or None,
    }


def _load_whatsapp_web_settings() -> dict[str, Any]:
    if not _DEFAULT_WHATSAPP_WEB_CONFIG_PATH.exists():
        return {
            "allowed_jids": [],
            "db_path": str(_DEFAULT_WHATSAPP_WEB_DB_PATH),
            "auto_start": False,
        }
    try:
        raw = json.loads(_DEFAULT_WHATSAPP_WEB_CONFIG_PATH.read_text())
    except Exception:
        logger.exception("Failed to load WhatsApp Web config summary")
        return {
            "allowed_jids": [],
            "db_path": str(_DEFAULT_WHATSAPP_WEB_DB_PATH),
            "auto_start": False,
        }
    if not isinstance(raw, dict):
        return {
            "allowed_jids": [],
            "db_path": str(_DEFAULT_WHATSAPP_WEB_DB_PATH),
            "auto_start": False,
        }
    return {
        "allowed_jids": _coerce_str_list(raw.get("allowed_jids")),
        "db_path": str(raw.get("db_path") or "").strip()
        or str(_DEFAULT_WHATSAPP_WEB_DB_PATH),
        "auto_start": bool(raw.get("auto_start", False)),
    }


def _save_whatsapp_web_settings(
    *,
    allowed_jids: list[str] | None = None,
    db_path: str | None = None,
    auto_start: bool | None = None,
) -> dict[str, Any]:
    current = _load_whatsapp_web_settings()
    if allowed_jids is not None:
        current["allowed_jids"] = _coerce_str_list(allowed_jids)
    if db_path is not None:
        current["db_path"] = str(db_path or "").strip() or current["db_path"]
    if auto_start is not None:
        current["auto_start"] = auto_start
    _atomic_write_json(_DEFAULT_WHATSAPP_WEB_CONFIG_PATH, current)
    return current


def _load_wechat_official_account_settings() -> dict[str, Any]:
    if not _DEFAULT_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH.exists():
        return {
            "app_id": "",
            "app_secret": "",
            "token": "",
            "encoding_aes_key": "",
            "webhook_url": "",
            "callback_path": "callback",
            "account_name": "",
            "app_name": "",
            "welcome_message": "Welcome! Send a message to start a workflow.",
            "support_encrypted_callbacks": False,
            "passive_reply_budget_seconds": 4.0,
            "passive_reply_fallback_text": "Working on it...",
            "api_base_url": "",
            "access_token_refresh_margin_seconds": 300.0,
            "server_url": "",
            "auto_start": False,
        }
    try:
        raw = json.loads(_DEFAULT_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH.read_text())
    except Exception:
        logger.exception("Failed to load WeChat Official Account config summary")
        return {
            "app_id": "",
            "app_secret": "",
            "token": "",
            "encoding_aes_key": "",
            "webhook_url": "",
            "callback_path": "callback",
            "account_name": "",
            "app_name": "",
            "welcome_message": "Welcome! Send a message to start a workflow.",
            "support_encrypted_callbacks": False,
            "passive_reply_budget_seconds": 4.0,
            "passive_reply_fallback_text": "Working on it...",
            "api_base_url": "",
            "access_token_refresh_margin_seconds": 300.0,
            "server_url": "",
            "auto_start": False,
        }
    if not isinstance(raw, dict):
        return {
            "app_id": "",
            "app_secret": "",
            "token": "",
            "encoding_aes_key": "",
            "webhook_url": "",
            "callback_path": "callback",
            "account_name": "",
            "app_name": "",
            "welcome_message": "Welcome! Send a message to start a workflow.",
            "support_encrypted_callbacks": False,
            "passive_reply_budget_seconds": 4.0,
            "passive_reply_fallback_text": "Working on it...",
            "api_base_url": "",
            "access_token_refresh_margin_seconds": 300.0,
            "server_url": "",
            "auto_start": False,
        }
    return {
        "app_id": str(raw.get("app_id") or "").strip(),
        "app_secret": str(raw.get("app_secret") or "").strip(),
        "token": str(raw.get("token") or "").strip(),
        "encoding_aes_key": str(raw.get("encoding_aes_key") or "").strip(),
        "webhook_url": str(raw.get("webhook_url") or "").strip(),
        "callback_path": _normalize_wechat_callback_path(raw.get("callback_path") or ""),
        "account_name": str(raw.get("account_name") or "").strip(),
        "app_name": str(raw.get("app_name") or "").strip(),
        "welcome_message": (
            str(raw.get("welcome_message") or "").strip()
            if "welcome_message" in raw
            else "Welcome! Send a message to start a workflow."
        ),
        "support_encrypted_callbacks": bool(raw.get("support_encrypted_callbacks", False)),
        "passive_reply_budget_seconds": float(raw.get("passive_reply_budget_seconds") or 4.0),
        "passive_reply_fallback_text": str(
            raw.get("passive_reply_fallback_text") or "Working on it..."
        ).strip()
        or "Working on it...",
        "api_base_url": str(raw.get("api_base_url") or "").strip(),
        "access_token_refresh_margin_seconds": float(
            raw.get("access_token_refresh_margin_seconds") or 300.0
        ),
        "server_url": str(raw.get("server_url") or "").strip(),
        "auto_start": bool(raw.get("auto_start", False)),
    }


def _save_wechat_official_account_settings(
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    token: str | None = None,
    encoding_aes_key: str | None = None,
    webhook_url: str | None = None,
    callback_path: str | None = None,
    account_name: str | None = None,
    app_name: str | None = None,
    welcome_message: str | None = None,
    support_encrypted_callbacks: bool | None = None,
    passive_reply_budget_seconds: float | None = None,
    passive_reply_fallback_text: str | None = None,
    api_base_url: str | None = None,
    access_token_refresh_margin_seconds: float | None = None,
    server_url: str | None = None,
    auto_start: bool | None = None,
) -> dict[str, Any]:
    current = _load_wechat_official_account_settings()
    if app_id is not None:
        current["app_id"] = str(app_id or "").strip()
    if app_secret is not None:
        current["app_secret"] = str(app_secret or "").strip()
    if token is not None:
        current["token"] = str(token or "").strip()
    if encoding_aes_key is not None:
        current["encoding_aes_key"] = str(encoding_aes_key or "").strip()
    if webhook_url is not None:
        current["webhook_url"] = str(webhook_url or "").strip()
    if callback_path is not None:
        current["callback_path"] = _normalize_wechat_callback_path(callback_path)
    if account_name is not None:
        current["account_name"] = str(account_name or "").strip()
    if app_name is not None:
        current["app_name"] = str(app_name or "").strip()
    if welcome_message is not None:
        current["welcome_message"] = str(welcome_message or "").strip()
    if support_encrypted_callbacks is not None:
        current["support_encrypted_callbacks"] = bool(support_encrypted_callbacks)
    if passive_reply_budget_seconds is not None:
        current["passive_reply_budget_seconds"] = max(float(passive_reply_budget_seconds), 0.1)
    if passive_reply_fallback_text is not None:
        current["passive_reply_fallback_text"] = (
            str(passive_reply_fallback_text or "").strip() or "Working on it..."
        )
    if api_base_url is not None:
        current["api_base_url"] = str(api_base_url or "").strip()
    if access_token_refresh_margin_seconds is not None:
        current["access_token_refresh_margin_seconds"] = max(
            float(access_token_refresh_margin_seconds),
            0.0,
        )
    if server_url is not None:
        current["server_url"] = str(server_url or "").strip()
    if auto_start is not None:
        current["auto_start"] = auto_start
    _DEFAULT_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(_DEFAULT_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH, current)
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
        connection_state = str(payload.get("connection_state") or "").strip().lower()
        if connection_state:
            patch["connection_state"] = connection_state
        if "paired" in payload:
            patch["paired"] = payload.get("paired")
        if payload.get("last_error") is not None:
            patch["last_error"] = payload.get("last_error")
        elif connection_state in {"connected", "disconnected"}:
            patch["last_error"] = None
        if patch:
            _merge_adapter_snapshot(adapter_id, patch)

    snapshots = _adapter_event_snapshots.setdefault(adapter_id, {})
    snapshots[event_type] = payload
    for queue in list(_adapter_event_subscribers.get(adapter_id, set())):
        queue.put_nowait(payload)


async def _adapter_heartbeat_loop() -> None:
    while True:
        await asyncio.sleep(_heartbeat_interval)
        for adapter_id, (adapter, _task) in list(_active_adapters.items()):
            snapshot = await _read_adapter_connection_snapshot(adapter_id, adapter)
            if snapshot is not None:
                _merge_adapter_snapshot(adapter_id, snapshot)


def _ensure_heartbeat_running() -> None:
    global _heartbeat_task
    if _heartbeat_task is None or _heartbeat_task.done():
        _heartbeat_task = asyncio.create_task(
            _adapter_heartbeat_loop(), name="adapter-heartbeat",
        )


def _cancel_heartbeat() -> None:
    global _heartbeat_task
    if _heartbeat_task is not None and not _heartbeat_task.done():
        _heartbeat_task.cancel()
    _heartbeat_task = None


async def _read_adapter_connection_snapshot(
    adapter_id: str,
    adapter: Any,
) -> dict[str, Any] | None:
    if not hasattr(adapter, "get_connection_snapshot"):
        return None
    try:
        live_snapshot = adapter.get_connection_snapshot()
        if inspect.isawaitable(live_snapshot):
            live_snapshot = await live_snapshot
    except Exception:
        logger.debug("Failed to read adapter snapshot for %s", adapter_id, exc_info=True)
        return None
    if isinstance(live_snapshot, dict):
        return dict(live_snapshot)
    return None


async def _build_adapter_status_payload(
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

    live_snapshot = await _read_adapter_connection_snapshot(adapter_id, adapter)
    if live_snapshot is not None:
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


async def _find_active_surface_snapshot(surface_type: str) -> dict[str, Any]:
    now = time.time()
    for adapter_id, (adapter, task) in _active_adapters.items():
        if _adapter_surface_types.get(adapter_id) != surface_type:
            continue
        return await _build_adapter_status_payload(adapter_id, adapter, task, now)
    return {}


def _adapter_ids_for_surface(surface_type: str) -> list[str]:
    normalized = str(surface_type or "").strip().lower()
    return [
        adapter_id
        for adapter_id, current_surface in _adapter_surface_types.items()
        if current_surface == normalized
    ]


def _find_active_surface_adapter(surface_type: str) -> tuple[str, Any] | None:
    normalized = str(surface_type or "").strip().lower()
    for adapter_id, (adapter, task) in _active_adapters.items():
        if _adapter_surface_types.get(adapter_id) != normalized:
            continue
        if _adapter_is_running(adapter, task):
            return adapter_id, adapter
    return None


async def _build_telegram_config_summary() -> dict[str, Any]:
    state = _load_telegram_desktop_state()
    env_token = os.environ.get("DAN_TELEGRAM_BOT_TOKEN", "").strip()
    token = env_token or state["bot_token"]
    live = await _find_active_surface_snapshot("telegram")
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
        "auto_start": state.get("auto_start", False),
        "commands": state.get("commands", []),
        "mini_app_url": state.get("mini_app_url"),
        "connection_state": live.get("connection_state"),
        "last_error": live.get("last_error"),
        **dependency,
    }


async def _build_whatsapp_web_config_summary() -> dict[str, Any]:
    settings = _load_whatsapp_web_settings()
    session_db = Path(settings["db_path"])
    live = await _find_active_surface_snapshot("whatsapp-web")
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
        "auto_start": settings.get("auto_start", False),
        "paired": paired,
        "connection_state": live.get("connection_state") or "disconnected",
        "last_error": live.get("last_error"),
        "db_path": settings["db_path"],
        "session_db_exists": session_db.exists(),
        **dependency,
    }


async def _build_wechat_official_account_config_summary() -> dict[str, Any]:
    settings = _load_wechat_official_account_settings()
    live = await _find_active_surface_snapshot("wechat")
    dependency = _build_dependency_summary(
        module_name="cryptography",
        package_name="cryptography",
        install_hint="pip install 'dan[wechat]'",
    )
    configured = bool(
        settings["app_id"] or settings["app_secret"] or settings["token"] or settings["encoding_aes_key"],
    )
    return {
        "type": "wechat",
        "configured": configured,
        "app_id": settings["app_id"] or None,
        "account_name": settings["account_name"] or None,
        "app_name": settings["app_name"] or None,
        "welcome_message": settings["welcome_message"],
        "masked_app_secret": _mask_secret(settings["app_secret"]),
        "masked_token": _mask_secret(settings["token"]),
        "masked_encoding_aes_key": _mask_secret(settings["encoding_aes_key"]),
        "webhook_url": settings["webhook_url"] or None,
        "callback_path": settings["callback_path"],
        "support_encrypted_callbacks": settings["support_encrypted_callbacks"],
        "passive_reply_budget_seconds": settings["passive_reply_budget_seconds"],
        "passive_reply_fallback_text": settings["passive_reply_fallback_text"],
        "api_base_url": settings["api_base_url"] or None,
        "access_token_refresh_margin_seconds": settings["access_token_refresh_margin_seconds"],
        "server_url": settings["server_url"] or None,
        "auto_start": settings.get("auto_start", False),
        "connection_state": live.get("connection_state"),
        "last_error": live.get("last_error"),
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


def _prepare_wechat_official_account_start_config(config_data: dict[str, Any]) -> dict[str, Any]:
    settings = _load_wechat_official_account_settings()
    prepared = dict(config_data)
    for key in (
        "app_id",
        "app_secret",
        "token",
        "encoding_aes_key",
        "webhook_url",
        "callback_path",
        "account_name",
        "app_name",
        "welcome_message",
        "support_encrypted_callbacks",
        "passive_reply_budget_seconds",
        "passive_reply_fallback_text",
        "api_base_url",
        "access_token_refresh_margin_seconds",
        "server_url",
    ):
        current_value = prepared.get(key, ...)
        if current_value is ... or current_value is None or current_value == "":
            if settings[key] not in {"", None}:
                prepared[key] = settings[key]
    if "welcome_message" not in prepared:
        prepared["welcome_message"] = settings["welcome_message"]
    # Let the adapter model defaults stand when these tuning fields are blank.
    if str(prepared.get("api_base_url", "") or "").strip() == "":
        prepared.pop("api_base_url", None)
    return prepared


def _validate_wechat_callback_mode(adapter: Any, encrypt_type: str) -> str:
    normalized = str(encrypt_type or "").strip().lower()
    try:
        validate_callback_encrypt_type(
            encrypt_type,
            encrypted_callbacks_enabled=bool(
                getattr(getattr(adapter, "config", None), "support_encrypted_callbacks", False),
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if normalized == "aes":
        config = getattr(adapter, "config", None)
        if not str(getattr(config, "app_id", "") or "").strip():
            raise HTTPException(
                status_code=400,
                detail="WeChat app_id is required for encrypted callbacks",
            )
        if not str(getattr(config, "encoding_aes_key", "") or "").strip():
            raise HTTPException(
                status_code=400,
                detail="WeChat encoding_aes_key is required for encrypted callbacks",
            )
    return normalized


def _validate_wechat_callback_path(adapter: Any, callback_path: str) -> None:
    configured = _normalize_wechat_callback_path(
        getattr(getattr(adapter, "config", None), "callback_path", "callback"),
    )
    requested = _normalize_wechat_callback_path(callback_path)
    if configured != requested:
        raise HTTPException(status_code=404, detail="No active WeChat callback at this path")


def _wechat_surface_identity(
    *,
    adapter_id: str,
    adapter: Any,
    external_id: str,
    account_id: str,
) -> tuple[str, dict[str, Any]]:
    surface_id = str(getattr(adapter.config, "app_id", "") or account_id or adapter_id)
    surface_context = {
        "identity": {
            "platform": "wechat",
            "channel": "official_account",
            "app_id": str(getattr(adapter.config, "app_id", "") or ""),
            "account_id": account_id,
            "openid": external_id,
        },
        "adapter_instructions": (
            "You are replying in a WeChat Official Account conversation. "
            "Keep the response concise, messaging-friendly, and clear."
        ),
    }
    return surface_id, surface_context


def _wechat_relay_server_url(adapter: Any) -> str | None:
    value = str(getattr(getattr(adapter, "config", None), "server_url", "") or "").strip()
    return value.rstrip("/") or None


def _build_wechat_chat_request_body(
    *,
    adapter_id: str,
    adapter: Any,
    external_id: str,
    message_text: str,
    account_id: str,
    session_id: str | None = None,
    control_plane_mode: str | None = None,
) -> dict[str, Any]:
    surface_id, surface_context = _wechat_surface_identity(
        adapter_id=adapter_id,
        adapter=adapter,
        external_id=external_id,
        account_id=account_id,
    )
    payload = {
        "workflow_id": "_scratch",
        "message": message_text,
        "history": [],
        "thread_id": session_id or external_id,
        "session_id": session_id or external_id,
        "mode": "auto",
        "surface": f"wechat:{surface_id}",
        "surface_type": "wechat",
        "surface_id": surface_id,
        "surface_context": surface_context,
    }
    if control_plane_mode is not None:
        payload["control_plane_mode"] = control_plane_mode
    return payload


def _build_adapter_chat_request_body(
    *,
    adapter_id: str,
    surface: str,
    external_id: str,
    message_text: str,
    history: list[dict[str, str]] | None = None,
    ctx: Any | None = None,
    control_plane_mode: str | None = None,
    conversation_key: str | None = None,
    lane_key: str | None = None,
    include_selected_session: bool = True,
) -> dict[str, Any]:
    surface_id = str(adapter_id or surface or "adapter").strip() or "adapter"
    surface_type = str(surface or "adapter").strip() or "adapter"
    session_id = str(external_id or surface_id).strip() or surface_id
    history_key = str(conversation_key or "").strip() or _adapter_history_key(
        adapter_id,
        surface_type,
        session_id,
    )
    lane_key = str(lane_key or "").strip() or history_key
    payload = {
        "workflow_id": "_scratch",
        "message": message_text,
        "history": list(history or []),
        "thread_id": session_id,
        "session_id": session_id,
        "mode": "auto",
        "surface": f"{surface_type}:{surface_id}",
        "surface_type": surface_type,
        "surface_id": surface_id,
        "surface_context": {
            "adapter": {
                "adapter_id": adapter_id,
                "external_id": external_id,
                "surface": surface_type,
            },
            **_adapter_workspace_context(surface_type, adapter_id),
        },
    }
    if surface_type == "telegram":
        workspace = _adapter_telegram_workspace_context(
            adapter_id=adapter_id,
            external_id=external_id,
            history_key=history_key,
        )
        surface_context = build_super_tui_surface_context(
            workspace_root=str(workspace.get("workspace_root") or "~"),
            workspace_source=str(workspace.get("workspace_source") or "telegram"),
            conversation_recent_turns=list(history or [])[-12:],
            extra=payload["surface_context"],
        )
        if workspace.get("workspace_id"):
            surface_context["workspace_id"] = workspace["workspace_id"]
        surface_context.update(
            {
                "ui_surface": "telegram",
                "agent_profile": SUPER_TUI_SURFACE_PROFILE,
                "agent_backend": SUPER_TUI_DEFAULT_BACKEND,
                "gui_for": "dan super-tui",
                "capabilities": list(
                    dict.fromkeys(
                        [
                            *SUPER_TUI_AGENT_CAPABILITIES,
                            "message_edit",
                            "threaded_replies",
                            "media_download",
                            "workspace_menu",
                            "session_menu",
                        ]
                    )
                ),
            }
        )
        payload["surface_context"] = surface_context
        chat_id, thread_id = _adapter_external_target(external_id)
        native_chat_id = getattr(ctx, "chat_id", None)
        native_thread_id = getattr(ctx, "thread_id", None)
        payload["surface_context"]["telegram"] = {
            "chat_id": native_chat_id if native_chat_id is not None else chat_id,
            "message_thread_id": (
                native_thread_id if native_thread_id is not None else thread_id
            ),
            "message_id": getattr(ctx, "message_id", None),
            "reply_to_message_id": getattr(ctx, "reply_to_message_id", None),
            "reply_to_text": getattr(ctx, "reply_to_text", None),
            "from_user_id": getattr(ctx, "from_user_id", None),
            "from_user_username": getattr(ctx, "from_user_username", None),
            "chat_type": getattr(ctx, "chat_type", "private"),
            "sender_chat_id": getattr(ctx, "sender_chat_id", None),
            "sender_chat_username": getattr(ctx, "sender_chat_username", None),
        }
        payload["surface_context"]["conversation"] = {
            **dict(payload["surface_context"].get("conversation") or {}),
            "conversation_key": history_key,
            "lane_key": lane_key,
            "reply_lane_key": lane_key if _adapter_reply_context(ctx) else None,
            "history_turn_count": len(history or []),
            "history_window": min(len(history or []), 40),
        }
        selected_session = (
            _adapter_telegram_session_binding(
                external_id=external_id,
                history_key=history_key,
            )
            if include_selected_session
            else {}
        )
        if selected_session:
            payload["surface_context"]["selected_session"] = dict(selected_session)
            task_id = str(selected_session.get("task_id") or "").strip()
            queue_action = str(selected_session.get("queue_action") or "").strip()
            if task_id:
                payload["surface_context"]["task_id"] = task_id
            if queue_action:
                payload["surface_context"]["queue_action"] = queue_action
    if control_plane_mode is not None:
        payload["control_plane_mode"] = control_plane_mode
    return payload


async def _ensure_wechat_adapter_session(
    *,
    adapter_id: str,
    adapter: Any,
    external_id: str,
) -> str | None:
    external = str(external_id or "").strip()
    if not external:
        return None

    session_id: str | None = None
    store = _adapter_session_stores.get(adapter_id)
    if store is not None:
        session = await store.get_by_external(external)
        if session is None:
            session = await store.create(external)
        else:
            await store.update_state(session.session_id, session.state)
        session_id = str(session.session_id or "").strip() or None

    if not session_id:
        session_id = external

    if hasattr(adapter, "register_session"):
        try:
            adapter.register_session(session_id, external)
        except Exception:
            logger.debug(
                "Failed to register WeChat adapter session %s for %s",
                session_id,
                external,
                exc_info=True,
            )
    return session_id


async def _start_wechat_chat_stream(
    *,
    adapter_id: str,
    adapter: Any,
    external_id: str,
    message_text: str,
    account_id: str,
    session_id: str | None = None,
    server_url: str | None = None,
) -> str | None:
    from dan.server.routers.chat import ChatMessageRequest, chat_message

    control_plane_override = _adapter_control_plane_override(adapter_id, "wechat")
    body = _build_wechat_chat_request_body(
        adapter_id=adapter_id,
        adapter=adapter,
        external_id=external_id,
        message_text=message_text,
        account_id=account_id,
        session_id=session_id,
        control_plane_mode=control_plane_override,
    )
    relay_server_url = str(server_url or "").strip().rstrip("/")
    if relay_server_url:
        try:
            async with httpx.AsyncClient(base_url=relay_server_url, timeout=120.0) as client:
                response = await client.post("/api/chat/message", json=body)
        except Exception:
            logger.warning(
                "WeChat chat relay request failed for adapter %s via %s",
                adapter_id,
                relay_server_url,
                exc_info=True,
            )
            return None
        if response.status_code != 200:
            logger.warning(
                "WeChat chat relay request failed for adapter %s: status=%s body=%s",
                adapter_id,
                response.status_code,
                response.text[:500],
            )
            return None
        try:
            payload = response.json()
        except ValueError:
            logger.warning(
                "WeChat chat relay returned non-JSON payload for adapter %s",
                adapter_id,
            )
            return None
        return str(payload.get("stream_channel_id") or "").strip() or None

    req = ChatMessageRequest.model_validate(body)
    response = await chat_message(req, concierge=True)
    return str(response.get("stream_channel_id") or "").strip() or None


def _finalize_wechat_stream_reply(
    *,
    collected_tokens: list[str],
    complete_content: str,
    error_message: str,
) -> str | None:
    full_reply = "".join(collected_tokens).strip()
    complete_content = str(complete_content or "").strip()
    error_message = str(error_message or "").strip()

    if complete_content:
        if not full_reply:
            full_reply = complete_content
        elif complete_content not in full_reply:
            full_reply = f"{full_reply}\n\n{complete_content}".strip()
    elif error_message:
        if full_reply:
            full_reply = f"{full_reply}\n\n{error_message}".strip()
        else:
            full_reply = error_message
    return full_reply or None


async def _drain_wechat_local_chat_stream(channel_id: str) -> str | None:
    from dan.server.chat_stream_buffer import should_preserve_chat_stream
    from dan.server.routers.chat import (
        _chat_produce_tasks,
        _chat_streams,
        _touch_chat_stream,
    )

    next_channel = channel_id
    seen_channels: set[str] = set()
    collected_tokens: list[str] = []
    complete_content = ""
    error_message = ""

    while next_channel:
        current_channel = next_channel
        next_channel = ""
        if current_channel in seen_channels:
            logger.warning(
                "Skipping repeated queued channel redirect for WeChat stream %s",
                current_channel,
            )
            break
        seen_channels.add(current_channel)

        entry = _chat_streams.get(current_channel)
        if entry is None:
            logger.debug("WeChat chat stream %s is no longer available", current_channel)
            break

        queue, _ = entry
        current_event: Any | None = None
        queue.attach_consumer()
        task = _chat_produce_tasks.get(current_channel)
        queue.prime_reconnect_snapshot(producer_running=task is not None and not task.done())
        try:
            while True:
                current_event = await queue.get()
                if current_event is None:
                    current_event = None
                    break
                _touch_chat_stream(current_channel)

                if not isinstance(current_event, dict):
                    current_event = None
                    continue

                evt_type = str(current_event.get("type") or "").strip()
                if evt_type == "chat_queued":
                    redirected = str(current_event.get("stream_channel_id") or "").strip()
                    current_event = None
                    if redirected and redirected not in seen_channels:
                        next_channel = redirected
                        break
                    continue
                if evt_type == "chat_token":
                    collected_tokens.append(
                        str(current_event.get("delta", current_event.get("token", "")) or "")
                    )
                    current_event = None
                    continue
                if evt_type == "chat_complete":
                    if current_event.get("detected_mode") == "progress_ack":
                        current_event = None
                        continue
                    complete_content = str(current_event.get("content") or "").strip()
                    current_event = None
                    return _finalize_wechat_stream_reply(
                        collected_tokens=collected_tokens,
                        complete_content=complete_content,
                        error_message=error_message,
                    )
                if evt_type == "chat_multi_part":
                    complete_content = "\n\n".join(
                        str(part or "").strip()
                        for part in (current_event.get("parts") or [])
                        if str(part or "").strip()
                    )
                    current_event = None
                    return _finalize_wechat_stream_reply(
                        collected_tokens=collected_tokens,
                        complete_content=complete_content,
                        error_message=error_message,
                    )
                if evt_type == "chat_mutation":
                    mutation_plan = current_event.get("mutation_plan") or {}
                    complete_content = str(current_event.get("content") or "").strip()
                    if not complete_content and isinstance(mutation_plan, dict):
                        description = str(mutation_plan.get("description") or "").strip()
                        if description:
                            complete_content = description
                    current_event = None
                    return _finalize_wechat_stream_reply(
                        collected_tokens=collected_tokens,
                        complete_content=complete_content,
                        error_message=error_message,
                    )
                if evt_type == "chat_interrupted":
                    complete_content = str(current_event.get("content") or "").strip()
                    if not complete_content:
                        complete_content = "The request was interrupted."
                    current_event = None
                    return _finalize_wechat_stream_reply(
                        collected_tokens=collected_tokens,
                        complete_content=complete_content,
                        error_message=error_message,
                    )
                if evt_type == "chat_error":
                    error_message = str(current_event.get("error") or "").strip()
                    current_event = None
                    return _finalize_wechat_stream_reply(
                        collected_tokens=collected_tokens,
                        complete_content=complete_content,
                        error_message=error_message,
                    )

                current_event = None
        except Exception:
            if current_event is not None:
                queue.requeue_front(current_event)
                current_event = None
                _touch_chat_stream(current_channel)
            raise
        finally:
            queue.detach_consumer()
            task = _chat_produce_tasks.get(current_channel)
            producer_running = task is not None and not task.done()
            preserve_stream = should_preserve_chat_stream(
                queue,
                producer_running=producer_running,
            )
            if preserve_stream:
                _chat_streams[current_channel] = (queue, time.monotonic())
                if not producer_running:
                    _chat_produce_tasks.pop(current_channel, None)
            else:
                _chat_streams.pop(current_channel, None)
                task = _chat_produce_tasks.pop(current_channel, None)
                if task is not None and not task.done():
                    task.cancel()

    return _finalize_wechat_stream_reply(
        collected_tokens=collected_tokens,
        complete_content=complete_content,
        error_message=error_message,
    )


async def _iter_wechat_remote_chat_stream_events(
    *,
    channel_id: str,
    server_url: str,
):
    import websockets

    ws_url = server_url.rstrip("/").replace("http://", "ws://").replace(
        "https://", "wss://",
    )
    next_channel = channel_id
    seen_channels: set[str] = set()

    while next_channel:
        current_channel = next_channel
        next_channel = ""
        if current_channel in seen_channels:
            logger.warning(
                "Skipping repeated queued channel redirect for WeChat relay stream %s",
                current_channel,
            )
            break
        seen_channels.add(current_channel)
        url = f"{ws_url}/api/chat/{current_channel}/events"

        async with websockets.connect(url, ping_interval=None, ping_timeout=None) as ws:
            if not hasattr(ws, "recv"):
                async for ws_msg in ws:
                    event = json.loads(ws_msg)
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "chat_queued":
                        redirected = str(event.get("stream_channel_id") or "").strip()
                        if redirected and redirected not in seen_channels:
                            next_channel = redirected
                            break
                        continue
                    if event.get("type") == "ping":
                        continue
                    yield event
                continue

            while True:
                try:
                    ws_msg = await ws.recv()
                except Exception as exc:
                    if exc.__class__.__name__.startswith("ConnectionClosed"):
                        break
                    raise
                event = json.loads(ws_msg)
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "chat_queued":
                    redirected = str(event.get("stream_channel_id") or "").strip()
                    if redirected and redirected not in seen_channels:
                        next_channel = redirected
                        break
                    continue
                if event.get("type") == "ping":
                    continue
                yield event


async def _drain_wechat_remote_chat_stream(
    *,
    channel_id: str,
    server_url: str,
) -> str | None:
    collected_tokens: list[str] = []
    complete_content = ""
    error_message = ""

    async for current_event in _iter_wechat_remote_chat_stream_events(
        channel_id=channel_id,
        server_url=server_url,
    ):
        evt_type = str(current_event.get("type") or "").strip()
        if evt_type == "chat_token":
            collected_tokens.append(
                str(current_event.get("delta", current_event.get("token", "")) or "")
            )
            continue
        if evt_type == "chat_complete":
            if current_event.get("detected_mode") == "progress_ack":
                continue
            complete_content = str(current_event.get("content") or "").strip()
            return _finalize_wechat_stream_reply(
                collected_tokens=collected_tokens,
                complete_content=complete_content,
                error_message=error_message,
            )
        if evt_type == "chat_multi_part":
            complete_content = "\n\n".join(
                str(part or "").strip()
                for part in (current_event.get("parts") or [])
                if str(part or "").strip()
            )
            return _finalize_wechat_stream_reply(
                collected_tokens=collected_tokens,
                complete_content=complete_content,
                error_message=error_message,
            )
        if evt_type == "chat_mutation":
            mutation_plan = current_event.get("mutation_plan") or {}
            complete_content = str(current_event.get("content") or "").strip()
            if not complete_content and isinstance(mutation_plan, dict):
                description = str(mutation_plan.get("description") or "").strip()
                if description:
                    complete_content = description
            return _finalize_wechat_stream_reply(
                collected_tokens=collected_tokens,
                complete_content=complete_content,
                error_message=error_message,
            )
        if evt_type == "chat_interrupted":
            complete_content = str(current_event.get("content") or "").strip()
            if not complete_content:
                complete_content = "The request was interrupted."
            return _finalize_wechat_stream_reply(
                collected_tokens=collected_tokens,
                complete_content=complete_content,
                error_message=error_message,
            )
        if evt_type == "chat_error":
            error_message = str(current_event.get("error") or "").strip()
            return _finalize_wechat_stream_reply(
                collected_tokens=collected_tokens,
                complete_content=complete_content,
                error_message=error_message,
            )

    return _finalize_wechat_stream_reply(
        collected_tokens=collected_tokens,
        complete_content=complete_content,
        error_message=error_message,
    )


async def _drain_wechat_chat_stream(
    channel_id: str,
    *,
    server_url: str | None = None,
) -> str | None:
    relay_server_url = str(server_url or "").strip().rstrip("/")
    if relay_server_url:
        return await _drain_wechat_remote_chat_stream(
            channel_id=channel_id,
            server_url=relay_server_url,
        )
    return await _drain_wechat_local_chat_stream(channel_id)


async def _send_wechat_followup_text(adapter: Any, external_id: str, text: str) -> None:
    from dan.server.concierge.actions import split_message_for_surface, strip_html_for_messaging

    clean = strip_html_for_messaging(str(text or "").strip())
    if not clean:
        return
    parts = split_message_for_surface(clean, "wechat")
    for idx, part in enumerate(parts):
        await _send_adapter_text(adapter, external_id, part)
        if idx + 1 < len(parts):
            await asyncio.sleep(0.1)


async def _await_wechat_followup_delivery(
    *,
    adapter_id: str,
    adapter: Any,
    external_id: str,
    result_task: asyncio.Task[str | None],
) -> None:
    try:
        reply_text = await result_task
        if reply_text:
            await _send_wechat_followup_text(adapter, external_id, reply_text)
    except Exception:
        logger.debug(
            "WeChat follow-up delivery failed for adapter %s",
            adapter_id,
            exc_info=True,
        )


def _run_adapter_message_handler(
    adapter_id: str,
    external_id: str,
    message_text: str,
    ctx: Any | None = None,
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
    _concierge = get_concierge()
    if _dispatcher is not None or _concierge is not None:
        asyncio.create_task(
            _run_adapter_concierge(
                adapter_id,
                adapter,
                surface,
                external_id,
                message_text,
                ctx=ctx,
            ),
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
    *,
    ctx: Any | None = None,
) -> None:
    from dan.server.routers.chat import (
        ChatMessageRequest,
        chat_message,
        iter_local_chat_stream_events,
    )

    control_plane_override = _adapter_control_plane_override(adapter_id, surface)
    raw_agent_command = _adapter_agent_command(message_text)
    requested_mode, message_text = _adapter_requested_mode(message_text)
    history_key = _adapter_history_key(adapter_id, surface, external_id)
    if str(surface or "").strip().lower() == "telegram":
        if await _handle_adapter_telegram_menu_command(
            adapter_id=adapter_id,
            adapter=adapter,
            external_id=external_id,
            text=message_text,
            ctx=ctx,
            history_key=history_key,
        ):
            return
    effective_mode = _adapter_effective_requested_mode(
        surface=surface,
        control_plane_mode=control_plane_override,
        requested_mode=requested_mode,
        text=message_text,
    )
    selected_session = (
        _adapter_telegram_session_binding(
            external_id=external_id,
            history_key=history_key,
        )
        if str(surface or "").strip().lower() == "telegram"
        else {}
    )
    if (
        str(surface or "").strip().lower() == "telegram"
        and control_plane_override == "v2"
        and selected_session
        and effective_mode == "auto"
        and not _adapter_v2_control_command(message_text)
    ):
        effective_mode = "agent"
    if effective_mode == "agent" and not message_text.strip():
        await _send_adapter_text(adapter, external_id, "Usage: /agent describe the task to run.")
        return
    telegram_lane_key = history_key
    if (
        str(surface or "").strip().lower() == "telegram"
        and control_plane_override == "v2"
        and effective_mode == "agent"
        and (not selected_session or raw_agent_command == "new")
    ):
        telegram_lane_key = _adapter_telegram_message_lane_key(history_key, ctx)
    user_turn = {"role": "user", "content": message_text}
    history = await _append_adapter_history_turn(history_key, user_turn)

    def _event_value(event: Any, field: str, default: Any = "") -> Any:
        if isinstance(event, dict):
            return event.get(field, default)
        return getattr(event, field, default)

    async def _relay_adapter_event(event: Any) -> None:
        evt_type = str(_event_value(event, "type", "") or "")
        if (
            evt_type == "chat_complete"
            and _event_value(event, "detected_mode", None) == "progress_ack"
        ):
            phase_label = str(_event_value(event, "phase_label", "") or "")
            if not phase_label:
                return
            now = time.monotonic()
            if now - _last_phase_message_times.get(external_id, 0) < 15.0:
                return
            _last_phase_message_times[external_id] = now
            await _send_adapter_text(adapter, external_id, phase_label)
            return
        if evt_type in {"chat_complete", "chat_mutation", "chat_interrupted"}:
            content = str(_event_value(event, "content", "") or "")
            if content:
                await _send_adapter_text(adapter, external_id, content)
                await _append_adapter_history_turn(
                    history_key,
                    {"role": "assistant", "content": content},
                )
            return
        if evt_type == "chat_error":
            error = str(_event_value(event, "error", "") or "")
            if error:
                await _send_adapter_text(adapter, external_id, error)
            return
        if evt_type == "chat_multi_part":
            assistant_parts: list[str] = []
            for part in (_event_value(event, "parts", []) or []):
                if part:
                    text = str(part)
                    assistant_parts.append(text)
                    await _send_adapter_text(adapter, external_id, text)
            if assistant_parts:
                await _append_adapter_history_turn(
                    history_key,
                    {"role": "assistant", "content": "\n\n".join(assistant_parts)},
                )
            return
        if evt_type == "chat_queued":
            queue_position = max(int(_event_value(event, "queue_position", 0) or 0), 1)
            queued_text = f"Queued (position {queue_position}) — I'll reply when ready."
            await _send_adapter_text(adapter, external_id, queued_text)
            return

    body = _build_adapter_chat_request_body(
        adapter_id=adapter_id,
        surface=surface,
        external_id=external_id,
        message_text=message_text,
        history=history,
        ctx=ctx,
        control_plane_mode=control_plane_override,
        conversation_key=history_key,
        lane_key=telegram_lane_key,
        include_selected_session=raw_agent_command != "new",
    )
    body["mode"] = effective_mode
    req = ChatMessageRequest.model_validate(body)
    try:
        selected_session_command_allowed = raw_agent_command in {
            "",
            "append",
            "inject",
            "continue",
            "continue-after-current",
        }
        if (
            control_plane_override == "v2"
            and str(surface or "").strip().lower() == "telegram"
            and selected_session
            and str(selected_session.get("queue_action") or "").strip()
            and str(selected_session.get("run_id") or "").strip()
            and selected_session_command_allowed
        ):
            summary = await _send_adapter_v2_agent_run_command(
                adapter=adapter,
                external_id=external_id,
                body=body,
                selected_session=selected_session,
            )
            if summary:
                await _append_adapter_history_turn(
                    history_key,
                    {"role": "assistant", "content": summary},
                )
            return
        if control_plane_override == "v2" and effective_mode == "agent":
            from dan.server.routers.chat_v2 import (
                AgentRunExecuteRequest,
                create_agent_run,
                execute_agent_run,
            )

            created = await create_agent_run(req=req)
            control = created.get("v2_control_plane") if isinstance(created, dict) else {}
            control = control if isinstance(control, dict) else {}
            run_id = str(control.get("run_id") or "").strip()
            if not run_id:
                event = created.get("event") if isinstance(created, dict) else {}
                event = event if isinstance(event, dict) else {}
                await _send_adapter_text(
                    adapter,
                    external_id,
                    str(event.get("summary") or "Agent run was not created."),
                )
                return
            backend = (
                os.environ.get("DAN_TELEGRAM_V2_AGENT_BACKEND")
                or os.environ.get("DAN_CHAT_V2_AGENT_BACKEND")
                or SUPER_TUI_DEFAULT_BACKEND
            )
            if str(surface or "").strip().lower() == "telegram":
                await execute_agent_run(
                    run_id,
                    execute=AgentRunExecuteRequest.model_validate(
                        build_super_tui_agent_execute_payload(
                            backend=backend,
                            background=True,
                            surface="telegram:server-adapter",
                            metadata={
                                "surface": "telegram:server-adapter",
                                "requested_from": "telegram",
                                "gui_for": "dan super-tui",
                            },
                        )
                    ),
                )
                summary = await _stream_v2_agent_run_events_to_adapter(
                    adapter,
                    external_id,
                    run_id,
                )
                if summary:
                    await _append_adapter_history_turn(
                        history_key,
                        {"role": "assistant", "content": summary},
                    )
                return
            await _send_adapter_text(adapter, external_id, "Accepted Agent run. Working...")
            executed = await execute_agent_run(
                run_id,
                execute=AgentRunExecuteRequest(backend=backend),
            )
            result = executed.get("result") if isinstance(executed, dict) else {}
            result = result if isinstance(result, dict) else {}
            summary = str(result.get("summary") or executed.get("status") or "").strip()
            token_usage = result.get("token_usage") if isinstance(result, dict) else {}
            if isinstance(token_usage, dict) and token_usage:
                from dan.server.chat_v2 import format_token_usage

                usage_text = format_token_usage(token_usage)
                if usage_text != "unavailable":
                    summary = f"{summary}\n\nTokens: {usage_text}" if summary else f"Tokens: {usage_text}"
            artifacts = result.get("artifact_refs") if isinstance(result, dict) else []
            if isinstance(artifacts, list) and artifacts:
                paths = [
                    str(item.get("path") or "")
                    for item in artifacts
                    if isinstance(item, dict) and item.get("path")
                ]
                if paths:
                    summary = f"{summary}\n\nArtifacts:\n" + "\n".join(f"- {path}" for path in paths[:8])
            await _send_adapter_text(
                adapter,
                external_id,
                summary or "Agent run completed.",
            )
            if summary:
                await _append_adapter_history_turn(
                    history_key,
                    {"role": "assistant", "content": summary},
                )
            return

        if control_plane_override == "v2":
            from dan.server.routers.chat_v2 import chat_v2_message

            response = await chat_v2_message(req=req, concierge=True)
        else:
            response = await chat_message(req, concierge=True)
        channel_id = str(response.get("stream_channel_id") or "").strip()
        if not channel_id:
            return
        async for event in iter_local_chat_stream_events(channel_id):
            await _relay_adapter_event(event)
    except Exception:
        logger.exception("Adapter %s chat relay failed", adapter_id)
        await _remove_adapter_history_turn(history_key, user_turn)
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

    try:
        run_manager = get_run_manager()
        engine = run_manager._make_engine(
            human_renderer=session_renderer,
            block_registry=get_block_registry(),
        )
    except Exception:
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

    if not _active_adapters:
        _cancel_heartbeat()

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
    try:
        from dan.adapters import (
            WeChatOfficialAccountAdapter,
            WeChatOfficialAccountAdapterConfig,
        )
    except ImportError:
        from dan.adapters.wechat_official_account_adapter import (
            WeChatOfficialAccountAdapter,
            WeChatOfficialAccountAdapterConfig,
        )

    adapter_type = req.type.lower()

    existing = _adapter_ids_for_surface(adapter_type)
    running_ids = [
        aid for aid in existing
        if aid in _active_adapters
        and _adapter_is_running(_active_adapters[aid][0], _active_adapters[aid][1])
    ]
    if running_ids:
        raise HTTPException(
            status_code=409,
            detail=f"Adapter type '{adapter_type}' is already running (id={running_ids[0]})",
        )

    adapter_id = str(uuid.uuid4())[:12]
    config_data = {**req.config, "workflow_path": req.workflow_path}
    if adapter_type == "telegram":
        config_data = _prepare_telegram_start_config(config_data)
    elif adapter_type == "whatsapp-web":
        config_data = _prepare_whatsapp_web_start_config(config_data)
    elif adapter_type == "wechat":
        config_data = _prepare_wechat_official_account_start_config(config_data)

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
    elif adapter_type == "wechat":
        adapter_config = WeChatOfficialAccountAdapterConfig(**config_data)
        adapter = WeChatOfficialAccountAdapter(adapter_config)
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

    async def _on_msg(ext_id: str, text: str, ctx: Any | None = None) -> None:
        _run_adapter_message_handler(adapter_id, ext_id, text, ctx=ctx)

    try:
        adapter.set_message_callback(_on_msg, with_context=True)
    except TypeError:
        adapter.set_message_callback(_on_msg)
    if adapter_type == "telegram" and hasattr(adapter, "set_callback_query_callback"):
        async def _on_callback(ext_id: str, data: str, ctx: Any | None = None) -> None:
            await _handle_adapter_telegram_callback_query(
                adapter_id,
                adapter,
                ext_id,
                data,
                ctx=ctx,
            )

        try:
            adapter.set_callback_query_callback(_on_callback, with_context=True)
        except TypeError:
            adapter.set_callback_query_callback(_on_callback)
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

    _ensure_heartbeat_running()

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
        payload = await _build_adapter_status_payload(aid, adapter, task, now)
        if store is not None:
            sessions = await store.all_sessions()
            payload["session_count"] = len(sessions)
        results.append(payload)
    return results


@router.get("/api/adapters/config/{adapter_type}")
async def get_adapter_config(adapter_type: str):
    normalized = adapter_type.strip().lower()
    if normalized == "telegram":
        return await _build_telegram_config_summary()
    if normalized == "whatsapp-web":
        return await _build_whatsapp_web_config_summary()
    if normalized == "wechat":
        return await _build_wechat_official_account_config_summary()
    raise HTTPException(status_code=404, detail=f"Adapter config '{adapter_type}' not found")


@router.post("/api/adapters/config/{adapter_type}")
async def save_adapter_config(adapter_type: str, body: dict[str, Any]):
    normalized = adapter_type.strip().lower()

    if normalized == "telegram":
        from dan.cli.bot import verify_bot_token

        token = str(body.get("bot_token") or "").strip()
        allowed_chat_ids = _coerce_int_list(body.get("allowed_chat_ids"))
        auto_start_val = body.get("auto_start")
        info: dict[str, Any] | None = None
        if token:
            info = await asyncio.to_thread(verify_bot_token, token)
            if info is None:
                raise HTTPException(status_code=400, detail="Invalid Telegram bot token.")

        commands_val = body.get("commands")
        commands: list[dict[str, str]] | None = None
        if commands_val is not None:
            commands = [
                {"command": str(c.get("command", "")), "description": str(c.get("description", ""))}
                for c in commands_val
                if isinstance(c, dict) and c.get("command")
            ]

        mini_app_url_sentinel: str | None | type[...] = ...
        if "mini_app_url" in body:
            raw_url = body.get("mini_app_url")
            mini_app_url_sentinel = str(raw_url).strip() if raw_url else None

        state = _save_telegram_desktop_state(
            bot_token=token or None,
            bot_username=str(info.get("username") or "").strip() if info else None,
            allowed_chat_ids=allowed_chat_ids if "allowed_chat_ids" in body else None,
            auto_start=bool(auto_start_val) if auto_start_val is not None else None,
            commands=commands,
            mini_app_url=mini_app_url_sentinel,  # type: ignore[arg-type]
        )
        summary = await _build_telegram_config_summary()
        summary["bot_username"] = state["bot_username"] or None
        summary["allowed_chat_ids"] = state["allowed_chat_ids"]
        summary["allowed_chat_count"] = len(state["allowed_chat_ids"])
        summary["auto_start"] = state.get("auto_start", False)
        summary["commands"] = state.get("commands", [])
        summary["mini_app_url"] = state.get("mini_app_url")
        return summary

    if normalized == "whatsapp-web":
        wa_auto_start = body.get("auto_start")
        settings = _save_whatsapp_web_settings(
            allowed_jids=_coerce_str_list(body.get("allowed_jids"))
            if "allowed_jids" in body
            else None,
            db_path=str(body.get("db_path") or "").strip() or None
            if "db_path" in body
            else None,
            auto_start=bool(wa_auto_start) if wa_auto_start is not None else None,
        )
        summary = await _build_whatsapp_web_config_summary()
        summary["allowed_jids"] = settings["allowed_jids"]
        summary["allowed_jid_count"] = len(settings["allowed_jids"])
        summary["db_path"] = settings["db_path"]
        summary["auto_start"] = settings.get("auto_start", False)
        return summary

    if normalized == "wechat":
        wechat_auto_start = body.get("auto_start")
        settings = _save_wechat_official_account_settings(
            app_id=(
                str(body.get("app_id") or "").strip()
                if "app_id" in body
                else None
            ),
            app_secret=(
                str(body.get("app_secret") or "").strip()
                if "app_secret" in body
                else None
            ),
            token=(
                str(body.get("token") or "").strip()
                if "token" in body
                else None
            ),
            encoding_aes_key=(
                str(body.get("encoding_aes_key") or "").strip()
                if "encoding_aes_key" in body
                else None
            ),
            webhook_url=(
                str(body.get("webhook_url") or "").strip()
                if "webhook_url" in body
                else None
            ),
            callback_path=(
                str(body.get("callback_path") or "").strip()
                if "callback_path" in body
                else None
            ),
            account_name=(
                str(body.get("account_name") or "").strip()
                if "account_name" in body
                else None
            ),
            app_name=(
                str(body.get("app_name") or "").strip()
                if "app_name" in body
                else None
            ),
            welcome_message=(
                str(body.get("welcome_message") or "").strip()
                if "welcome_message" in body
                else None
            ),
            support_encrypted_callbacks=(
                _coerce_optional_bool(body.get("support_encrypted_callbacks"))
                if "support_encrypted_callbacks" in body
                else None
            ),
            passive_reply_budget_seconds=(
                _coerce_optional_float(body.get("passive_reply_budget_seconds"))
                if "passive_reply_budget_seconds" in body
                else None
            ),
            passive_reply_fallback_text=(
                str(body.get("passive_reply_fallback_text") or "").strip()
                if "passive_reply_fallback_text" in body
                else None
            ),
            api_base_url=(
                str(body.get("api_base_url") or "").strip()
                if "api_base_url" in body
                else None
            ),
            access_token_refresh_margin_seconds=(
                _coerce_optional_float(body.get("access_token_refresh_margin_seconds"))
                if "access_token_refresh_margin_seconds" in body
                else None
            ),
            server_url=(
                str(body.get("server_url") or "").strip()
                if "server_url" in body
                else None
            ),
            auto_start=bool(wechat_auto_start) if wechat_auto_start is not None else None,
        )
        summary = await _build_wechat_official_account_config_summary()
        summary["app_id"] = settings["app_id"] or None
        summary["masked_app_secret"] = _mask_secret(settings["app_secret"])
        summary["masked_token"] = _mask_secret(settings["token"])
        summary["masked_encoding_aes_key"] = _mask_secret(settings["encoding_aes_key"])
        summary["webhook_url"] = settings["webhook_url"] or None
        summary["callback_path"] = settings["callback_path"]
        summary["account_name"] = settings["account_name"] or None
        summary["app_name"] = settings["app_name"] or None
        summary["welcome_message"] = settings["welcome_message"]
        summary["support_encrypted_callbacks"] = settings["support_encrypted_callbacks"]
        summary["passive_reply_budget_seconds"] = settings["passive_reply_budget_seconds"]
        summary["passive_reply_fallback_text"] = settings["passive_reply_fallback_text"]
        summary["api_base_url"] = settings["api_base_url"] or None
        summary["access_token_refresh_margin_seconds"] = (
            settings["access_token_refresh_margin_seconds"]
        )
        summary["server_url"] = settings["server_url"] or None
        summary["auto_start"] = settings.get("auto_start", False)
        return summary

    raise HTTPException(status_code=404, detail=f"Adapter config '{adapter_type}' not found")


@router.post("/api/adapters/config/{adapter_type}/reset")
async def reset_adapter_config(adapter_type: str):
    normalized = adapter_type.strip().lower()
    if normalized == "wechat":
        for adapter_id in list(_adapter_ids_for_surface("wechat")):
            await _stop_active_adapter(adapter_id, missing_ok=True)
        try:
            _DEFAULT_WECHAT_OFFICIAL_ACCOUNT_CONFIG_PATH.unlink()
        except FileNotFoundError:
            pass
        return await _build_wechat_official_account_config_summary()

    if normalized != "whatsapp-web":
        raise HTTPException(status_code=404, detail=f"Adapter config '{adapter_type}' not found")

    settings = _load_whatsapp_web_settings()
    for adapter_id in list(_adapter_ids_for_surface("whatsapp-web")):
        await _stop_active_adapter(adapter_id, missing_ok=True)

    db_path = Path(settings["db_path"])
    _remove_sqlite_artifacts(db_path)
    return await _build_whatsapp_web_config_summary()


async def _wechat_callback_verify_impl(
    callback_path: str,
    *,
    signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    echostr: str = "",
    encrypt_type: str = "",
    msg_signature: str = "",
):
    entry = _find_active_surface_adapter("wechat")
    if entry is None:
        raise HTTPException(status_code=404, detail="No active WeChat adapter")

    _adapter_id, adapter = entry
    _validate_wechat_callback_path(adapter, callback_path)
    encrypt_mode = _validate_wechat_callback_mode(adapter, encrypt_type)
    token = str(getattr(getattr(adapter, "config", None), "token", "") or "")
    if not verify_signature(token, signature, timestamp, nonce):
        raise HTTPException(status_code=403, detail="Invalid WeChat signature")
    if encrypt_mode == "aes":
        try:
            echostr = decrypt_encrypted_callback_echostr(
                echostr,
                token=token,
                msg_signature=msg_signature,
                timestamp=timestamp,
                nonce=nonce,
                encoding_aes_key=str(getattr(adapter.config, "encoding_aes_key", "") or ""),
                app_id=str(getattr(adapter.config, "app_id", "") or ""),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PlainTextResponse(echostr)


@router.get("/api/adapters/wechat/callback")
async def wechat_callback_verify(
    signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    echostr: str = "",
    encrypt_type: str = "",
    msg_signature: str = "",
):
    return await _wechat_callback_verify_impl(
        "callback",
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        echostr=echostr,
        encrypt_type=encrypt_type,
        msg_signature=msg_signature,
    )


@router.get("/api/adapters/wechat/{callback_path:path}")
async def wechat_callback_verify_at_path(
    callback_path: str,
    signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    echostr: str = "",
    encrypt_type: str = "",
    msg_signature: str = "",
):
    return await _wechat_callback_verify_impl(
        callback_path,
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        echostr=echostr,
        encrypt_type=encrypt_type,
        msg_signature=msg_signature,
    )


async def _wechat_callback_message_impl(
    callback_path: str,
    *,
    request: Request,
    signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    encrypt_type: str = "",
    msg_signature: str = "",
):
    entry = _find_active_surface_adapter("wechat")
    if entry is None:
        raise HTTPException(status_code=404, detail="No active WeChat adapter")

    adapter_id, adapter = entry
    _validate_wechat_callback_path(adapter, callback_path)
    encrypt_mode = _validate_wechat_callback_mode(adapter, encrypt_type)
    token = str(getattr(getattr(adapter, "config", None), "token", "") or "")
    if not verify_signature(token, signature, timestamp, nonce):
        raise HTTPException(status_code=403, detail="Invalid WeChat signature")

    body = await request.body()
    if not body:
        return PlainTextResponse("")

    normalized_body = body
    try:
        if encrypt_mode == "aes":
            decrypted_body = decrypt_encrypted_callback_xml(
                body,
                token=token,
                msg_signature=msg_signature,
                timestamp=timestamp,
                nonce=nonce,
                encoding_aes_key=str(getattr(adapter.config, "encoding_aes_key", "") or ""),
                app_id=str(getattr(adapter.config, "app_id", "") or ""),
            )
            normalized_body = decrypted_body.encode("utf-8")
        parsed = parse_incoming_xml(normalized_body)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    external_id = str(parsed.get("from_user_name") or "").strip()
    account_id = str(parsed.get("to_user_name") or "").strip()
    msg_type = str(parsed.get("msg_type") or "").strip().lower()
    event = str(parsed.get("event") or "").strip().lower()
    message_text = str(parsed.get("text") or parsed.get("content") or "").strip()
    relay_server_url = _wechat_relay_server_url(adapter)
    session_id = (
        await _ensure_wechat_adapter_session(
            adapter_id=adapter_id,
            adapter=adapter,
            external_id=external_id,
        )
        if external_id
        else None
    )

    pending_by_session = getattr(adapter, "_pending", {})
    if session_id:
        pending_future = pending_by_session.get(session_id)
        if pending_future is not None and not pending_future.done():
            adapter.handle_incoming_xml(normalized_body)
            return PlainTextResponse("")

    if msg_type == "event" and event == "unsubscribe":
        return PlainTextResponse("")

    reply_text: str | None = None
    result_task: asyncio.Task[str | None] | None = None
    if msg_type == "event" and event == "subscribe":
        reply_text = str(
            getattr(getattr(adapter, "config", None), "welcome_message", "") or "",
        ).strip() or "Connected to DAN."
    elif external_id and message_text:
        chat_stream_id = await _start_wechat_chat_stream(
            adapter_id=adapter_id,
            adapter=adapter,
            external_id=external_id,
            message_text=message_text,
            account_id=account_id,
            session_id=session_id,
            server_url=relay_server_url,
        )
        if chat_stream_id:
            result_task = asyncio.create_task(
                _drain_wechat_chat_stream(
                    chat_stream_id,
                    server_url=relay_server_url,
                ),
                name=f"wechat-chat-{external_id}",
            )
            timeout_seconds = float(
                getattr(adapter.config, "passive_reply_budget_seconds", 4.0) or 4.0,
            )
            try:
                reply_text = await asyncio.wait_for(
                    asyncio.shield(result_task),
                    timeout=max(timeout_seconds, 0.1),
                )
            except asyncio.TimeoutError:
                logger.debug(
                    "WeChat passive reply timed out for adapter %s after %.2fs",
                    adapter_id,
                    timeout_seconds,
                )
                asyncio.create_task(
                    _await_wechat_followup_delivery(
                        adapter_id=adapter_id,
                        adapter=adapter,
                        external_id=external_id,
                        result_task=result_task,
                    ),
                    name=f"wechat-followup-{external_id}",
                )
            except Exception:
                logger.debug(
                    "WeChat chat stream collection failed for adapter %s",
                    adapter_id,
                    exc_info=True,
                )

    if not reply_text and msg_type in {"text", "event"}:
        fallback_text = str(
            getattr(
                getattr(adapter, "config", None),
                "passive_reply_fallback_text",
                "Working on it...",
            )
            or "Working on it..."
        ).strip()
        reply_text = fallback_text or None

    if not reply_text or not external_id:
        return PlainTextResponse("")

    reply_xml = build_passive_text_reply(external_id, account_id, reply_text)
    if encrypt_mode == "aes":
        try:
            reply_xml = build_encrypted_callback_reply(
                reply_xml,
                token=token,
                encoding_aes_key=str(getattr(adapter.config, "encoding_aes_key", "") or ""),
                app_id=str(getattr(adapter.config, "app_id", "") or ""),
                timestamp=timestamp,
                nonce=nonce,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Response(content=reply_xml, media_type="application/xml")


@router.post("/api/adapters/wechat/callback")
async def wechat_callback_message(
    request: Request,
    signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    encrypt_type: str = "",
    msg_signature: str = "",
):
    return await _wechat_callback_message_impl(
        "callback",
        request=request,
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        encrypt_type=encrypt_type,
        msg_signature=msg_signature,
    )


@router.post("/api/adapters/wechat/{callback_path:path}")
async def wechat_callback_message_at_path(
    callback_path: str,
    request: Request,
    signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    encrypt_type: str = "",
    msg_signature: str = "",
):
    return await _wechat_callback_message_impl(
        callback_path,
        request=request,
        signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        encrypt_type=encrypt_type,
        msg_signature=msg_signature,
    )


@router.post("/api/adapters/{adapter_id}/apply-commands")
async def apply_commands(adapter_id: str, body: dict[str, Any]):
    entry = _active_adapters.get(adapter_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Adapter '{adapter_id}' not found")
    adapter, _task = entry
    surface = _adapter_surface_types.get(adapter_id, "")
    if surface != "telegram":
        raise HTTPException(status_code=400, detail="apply-commands is only supported for Telegram adapters")

    raw_commands = body.get("commands", [])
    command_pairs: list[tuple[str, str]] = [
        (str(c.get("command", "")), str(c.get("description", "")))
        for c in raw_commands
        if isinstance(c, dict) and c.get("command")
    ]
    await adapter.register_custom_commands(command_pairs)
    return {"status": "applied"}


@router.post("/api/adapters/{adapter_id}/apply-menu")
async def apply_menu(adapter_id: str, body: dict[str, Any]):
    entry = _active_adapters.get(adapter_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Adapter '{adapter_id}' not found")
    adapter, _task = entry

    url = str(body.get("url") or body.get("mini_app_url") or "").strip() or None
    await adapter.set_menu_button(url)
    return {"status": "applied"}


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
