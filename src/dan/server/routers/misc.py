"""Miscellaneous endpoints: health, cache, metrics, files, docs, code-refs,
test cases, memory, errors, and rules."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import uuid
import logging
import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from dan.server.capabilities.config import _update_env_file
from dan.server.paths import resolve_workspace_root
from dan.server.routers.dependencies import (
    get_app_state,
    get_run_manager,
    get_graph_store,
    get_test_case_store,
    get_mention_resolver,
    get_engine_config,
    get_memory_store,
    resolve_cache_dir,
    validate_path_segment,
)
from dan.worker.organism_log import (
    read_organism_log,
    read_organism_log_rows,
    readable_organism_log_v1_rows,
)
from dan.worker.organism_log_analysis import analyze_organism_log

logger = logging.getLogger(__name__)

router = APIRouter()

_RUNTIME_CONFIG_KEYS = (
    "DAN_CHAT_MODEL",
    "DAN_LLM_MODEL",
    "DAN_LLM_BASE_URL",
    "DAN_BOT_NAME",
    "DAN_ENABLE_TIER_POLICY",
    "DAN_FULL_TOOLS",
    "DAN_TELEMETRY",
    "DAN_LEARNING_MODE",
)

_RESTART_REQUIRED_CONFIG_KEYS = {
    "DAN_LLM_BASE_URL",
    "DAN_ENABLE_TIER_POLICY",
    "DAN_FULL_TOOLS",
    "DAN_TELEMETRY",
    "DAN_LEARNING_MODE",
}

_ORGANISM_LOG_DISCOVERY_PATTERNS = (
    ".dan-code/control-plane-events.jsonl",
    ".dan-code/runs/**/events.jsonl",
    ".dan-super/runs/**/events.jsonl",
    ".dan-research/runs/**/events.jsonl",
    ".dan-research/control-plane-events.jsonl",
)
_MAX_ORGANISM_LOG_DISCOVER_LIMIT = 100


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _normalize_env_bool(key: str, default: bool) -> str:
    raw = str(os.environ.get(key, "")).strip().lower()
    if not raw:
        return "1" if default else "0"
    return "1" if raw in ("1", "true", "yes", "on") else "0"


def _read_runtime_config_values(chat_manager: Any | None = None) -> dict[str, str]:
    chat_model = (
        getattr(chat_manager, "_chat_model", "")
        or os.environ.get("DAN_CHAT_MODEL", "").strip()
    )
    if not chat_model:
        chat_model = os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"

    return {
        "DAN_CHAT_MODEL": chat_model,
        "DAN_LLM_MODEL": os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6"),
        "DAN_LLM_BASE_URL": os.environ.get("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1"),
        "DAN_BOT_NAME": os.environ.get("DAN_BOT_NAME", "DAN"),
        "DAN_ENABLE_TIER_POLICY": _normalize_env_bool("DAN_ENABLE_TIER_POLICY", False),
        "DAN_FULL_TOOLS": _normalize_env_bool("DAN_FULL_TOOLS", True),
        "DAN_TELEMETRY": _normalize_env_bool("DAN_TELEMETRY", True),
        "DAN_LEARNING_MODE": _normalize_env_bool("DAN_LEARNING_MODE", False),
    }


def _host_has_interface_address(address: str) -> tuple[bool, str]:
    """Return whether *address* is visible on a local network interface."""
    target = address.strip()
    if not target:
        return False, ""
    ifconfig_bin = shutil.which("ifconfig")
    if not ifconfig_bin:
        return False, ""
    try:
        completed = subprocess.run(
            [ifconfig_bin],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return False, ""
    current_interface = ""
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if raw_line and not raw_line.startswith(("\t", " ")):
            current_interface = raw_line.split(":", 1)[0].strip()
        if line.startswith("inet ") and target in line.split():
            return True, current_interface
    return False, ""


def _workspace_wireguard_status() -> dict[str, Any]:
    """Read WireGuard state without mutating any host-level VPN service."""
    real_home = Path.home()
    alias_config = Path(
        os.environ.get("DAN_ALIAS_NYWG_CONFIG", str(real_home / ".config/wireguard/nywg.conf"))
    ).expanduser()
    mode = os.environ.get("DAN_WIREGUARD_MANAGED_MODE", "auto").strip() or "auto"
    if mode == "auto":
        mode = "alias-nywg" if alias_config.is_file() else "dan-phone"

    if mode == "alias-nywg":
        interface = os.environ.get("DAN_ALIAS_NYWG_INTERFACE", "nywg")
        label = os.environ.get("DAN_ALIAS_NYWG_LABEL", "com.alias.nywg")
        config_path = alias_config
        mutating_actions_enabled = False
    else:
        interface = os.environ.get("DAN_WIREGUARD_INTERFACE_NAME", "dan-phone")
        label = os.environ.get("DAN_WIREGUARD_LAUNCHD_LABEL", "com.dan.phone-wireguard")
        config_path = Path(
            os.environ.get(
                "DAN_WIREGUARD_CONFIG_PATH",
                str(_repo_root() / "ops/wireguard/generated/dan-phone.conf"),
            )
        ).expanduser()
        mutating_actions_enabled = False

    wg_bin = os.environ.get("WG_BIN") or shutil.which("wg")
    wg_stdout = ""
    wg_stderr = ""
    wg_exit_code: int | None = None
    active = False
    if wg_bin:
        try:
            completed = subprocess.run(
                [wg_bin, "show", interface],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            wg_stdout = completed.stdout.strip()
            wg_stderr = completed.stderr.strip()
            wg_exit_code = int(completed.returncode)
            active = wg_exit_code == 0 and bool(wg_stdout)
        except Exception as exc:  # pragma: no cover - defensive host probe
            wg_stderr = str(exc)
            wg_exit_code = -1

    host_tunnel_address = os.environ.get("DAN_PHONE_WIREGUARD_HOST", "10.77.77.2").strip()
    host_tunnel_active, host_tunnel_interface = _host_has_interface_address(host_tunnel_address)
    if not active and host_tunnel_active:
        active = True
        mode = "host-wireguard"
        interface = host_tunnel_interface or interface
        if wg_stderr:
            wg_stderr = (
                f"{wg_stderr}; host tunnel address {host_tunnel_address} is present "
                f"on {interface}"
            )

    return {
        "service": "wireguard",
        "mode": mode,
        "interface": interface,
        "launchd_label": label,
        "config_path": str(config_path),
        "config_present": config_path.is_file(),
        "active": active,
        "status": "active" if active else ("configured" if config_path.is_file() else "not_configured"),
        "host_tunnel_address": host_tunnel_address,
        "host_tunnel_interface": host_tunnel_interface,
        "host_tunnel_active": host_tunnel_active,
        "wg_present": bool(wg_bin),
        "wg_exit_code": wg_exit_code,
        "stdout": wg_stdout,
        "stderr": wg_stderr,
        "mutating_actions_enabled": mutating_actions_enabled,
        "safe_actions": ["status"],
        "conflict_policy": (
            "Read-only alias nywg status; this endpoint does not start, stop, "
            "restart, install, or uninstall the existing WireGuard service."
        )
        if mode == "alias-nywg"
        else (
            "Read-only DAN WireGuard status; mutating service actions are not "
            "enabled from this workspace surface."
        ),
    }


def _request_app_state(request: Request | None) -> Any | None:
    if request is None:
        return None
    return getattr(getattr(request.app, "state", None), "dan", None)


def _isoformat_utc(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return (
        datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _resolve_organism_log_root(root_path: str | None = None) -> Path:
    raw = str(root_path or resolve_workspace_root()).strip()
    candidate = Path(raw).expanduser().resolve()
    if not candidate.exists() or not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"Workspace root not found: {candidate}")
    return candidate


def _resolve_organism_log_path(
    raw_path: str,
    *,
    root_path: Path | None = None,
) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Provide a log file path.")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = (root_path or _resolve_organism_log_root()) / candidate
    resolved = candidate.resolve()
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail=f"Log file not found: {resolved}")
    return resolved


def _path_relative_to_root(path: Path, root_path: Path) -> str:
    try:
        return str(path.relative_to(root_path))
    except ValueError:
        return str(path)


def _organism_log_display_name(relative_path: str, *, product: str, stream_kind: str) -> str:
    if relative_path.endswith(".dan-code/control-plane-events.jsonl"):
        return "Code control plane"
    if relative_path.endswith(".dan-research/control-plane-events.jsonl"):
        return "Research control plane"
    match = re.search(r"(?:^|/)(turn-[^/]+)/events\.jsonl$", relative_path)
    if match and product == "dan_code":
        return f"Code {match.group(1)}"
    if match and product == "dan_super":
        return f"Super DAN {match.group(1)}"
    if match and product == "dan_research":
        return f"Research {match.group(1)}"
    file_name = Path(relative_path).name
    if stream_kind == "control_plane":
        return f"{product or 'organism'} control plane"
    return file_name


def _summarize_organism_log_file(path: Path, *, root_path: Path) -> dict[str, Any] | None:
    try:
        raw_rows = readable_organism_log_v1_rows(read_organism_log(path))
    except Exception as exc:
        logger.warning("Failed to read organism log %s: %s", path, exc)
        return None
    if not raw_rows:
        return None

    try:
        normalized = read_organism_log_rows(path)
    except Exception as exc:
        logger.warning("Failed to normalize organism log %s: %s", path, exc)
        return None
    if not normalized:
        return None

    spans = [row for row in normalized if row.record_kind == "span"]
    events = [row for row in normalized if row.record_kind == "event"]
    first = normalized[0]
    last = normalized[-1]
    stat = path.stat()
    relative_path = _path_relative_to_root(path, root_path)
    started_at = next(
        (
            row.start_timestamp or row.timestamp
            for row in spans
            if (row.start_timestamp or row.timestamp)
        ),
        first.timestamp or None,
    )
    ended_at = next(
        (
            row.end_timestamp or row.timestamp
            for row in reversed(spans)
            if (row.end_timestamp or row.timestamp)
        ),
        last.timestamp or None,
    )
    return {
        "path": str(path),
        "root_path": str(root_path),
        "relative_path": relative_path,
        "display_name": _organism_log_display_name(
            relative_path,
            product=first.product,
            stream_kind=first.stream_kind,
        ),
        "product": first.product,
        "stream_kind": first.stream_kind,
        "session_id": first.session_id,
        "turn_id": first.turn_id,
        "task_id": first.task_id,
        "trace_id": first.trace_id,
        "organism_id": first.organism_id,
        "organ_id": first.organ_id,
        "schema_version": first.schema_version,
        "event_count": len(events),
        "span_count": len(spans),
        "size_bytes": stat.st_size,
        "updated_at": _isoformat_utc(stat.st_mtime),
        "started_at": started_at,
        "ended_at": ended_at,
    }


def _discover_organism_log_files(root_path: Path, *, limit: int) -> list[dict[str, Any]]:
    requested_limit = max(1, min(int(limit), _MAX_ORGANISM_LOG_DISCOVER_LIMIT))
    candidates: dict[str, Path] = {}
    for pattern in _ORGANISM_LOG_DISCOVERY_PATTERNS:
        for candidate in root_path.glob(pattern):
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            candidates[str(resolved)] = resolved

    ordered = sorted(
        candidates.values(),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )

    summaries: list[dict[str, Any]] = []
    for candidate in ordered:
        summary = _summarize_organism_log_file(candidate, root_path=root_path)
        if summary is None:
            continue
        summaries.append(summary)
        if len(summaries) >= requested_limit:
            break
    return summaries


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------


@router.get("/health")
async def health_check(request: Request = None) -> dict[str, Any]:
    """Health check endpoint for server discovery."""
    from dan.server.runtime_config import provider_readiness_summary

    state = get_app_state(request)
    result: dict[str, Any] = {"status": "ok"}
    run_manager = state.run_manager if state is not None else None
    startup_issues = [dict(item) for item in (getattr(state, "startup_degradations", []) or [])]
    if run_manager is not None:
        runs = run_manager.list_runs()
        active = [r for r in runs if r.get("status") in ("running", "pending")]
        result["active_runs"] = len(active)
    result["startup"] = {
        "status": "degraded" if startup_issues else "ok",
        "issues": startup_issues,
    }
    engine_config = state.engine_config if state is not None else None
    if engine_config is None:
        engine_config = get_engine_config(request)
    if engine_config is not None:
        result["providers"] = provider_readiness_summary(engine_config)
    return result


@router.get("/api/health")
async def health():
    """Minimal liveness probe for desktop/editor reconnect flows."""
    return {"status": "ok", "pid": os.getpid(), "timestamp": time.time()}


@router.get("/api/config")
async def get_runtime_config(request: Request) -> dict[str, Any]:
    """Return the subset of runtime settings that the desktop UI can edit."""
    state = _request_app_state(request)
    return {
        "values": _read_runtime_config_values(
            getattr(state, "chat_manager", None),
        ),
        "restart_required_keys": sorted(_RESTART_REQUIRED_CONFIG_KEYS),
    }


@router.get("/api/workspace-wireguard")
async def get_workspace_wireguard_status() -> dict[str, Any]:
    return _workspace_wireguard_status()


@router.post("/api/config")
async def set_runtime_config(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Persist a runtime setting to both the live server env and project .env."""
    key = str(body.get("key") or "").strip().upper()
    if key not in _RUNTIME_CONFIG_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported config key '{key}'.",
        )

    raw_value = body.get("value", "")
    if isinstance(raw_value, bool):
        value = "1" if raw_value else "0"
    else:
        value = str(raw_value).strip()

    os.environ[key] = value

    state = _request_app_state(request)
    chat_manager = getattr(state, "chat_manager", None)

    if key in ("DAN_LLM_MODEL", "DAN_CHAT_MODEL") and chat_manager is not None:
        chat_manager._chat_model = (
            os.environ.get("DAN_CHAT_MODEL", "").strip()
            or os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6").strip()
            or "claude-sonnet-4-6"
        )

    if key == "DAN_BOT_NAME":
        try:
            from dan.server.concierge import identity as concierge_identity

            concierge_identity._cached_bot_name = None
        except Exception:
            pass

    try:
        _update_env_file(key, value)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Saved '{key}' in the running server, but failed to update .env: {exc}",
        ) from exc

    return {
        "status": "ok",
        "key": key,
        "value": value,
        "restart_required": key in _RESTART_REQUIRED_CONFIG_KEYS,
    }


# ------------------------------------------------------------------
# Cache
# ------------------------------------------------------------------


@router.post("/api/cache/clear")
async def clear_cache(request: Request):
    config = get_engine_config(request)
    base = resolve_cache_dir(config)
    deleted_files = 0

    if base.exists():
        for pattern in ("*.json", "*.tmp", "**/*.json", "**/*.tmp"):
            for p in base.glob(pattern):
                if not p.is_file():
                    continue
                try:
                    p.unlink()
                    deleted_files += 1
                except OSError:
                    pass

    return {
        "status": "cleared",
        "cache_dir": str(base),
        "deleted_files": deleted_files,
    }


@router.get("/api/cache/stats")
async def cache_stats(request: Request):
    config = get_engine_config(request)
    base = resolve_cache_dir(config)
    file_count = 0
    total_bytes = 0
    if base.exists():
        for p in base.rglob("*.json"):
            if p.is_file():
                file_count += 1
                try:
                    total_bytes += p.stat().st_size
                except OSError:
                    pass

    latest_run_cache: dict[str, Any] | None = None
    state = _request_app_state(request)
    run_manager = state.run_manager if state is not None else None
    if run_manager is not None:
        runs = run_manager.list_runs()
        if runs:
            latest = max(runs, key=lambda r: float(r.get("started_at", 0.0) or 0.0))
            record = run_manager.get_run(str(latest.get("run_id", "")))
            if record is not None and record.result is not None:
                meta = record.result.metadata
                if isinstance(meta, dict):
                    latest_run_cache = meta.get("__run_cache__")

    return {
        "cache_dir": str(base),
        "cache_enabled": config.cache_enabled,
        "cache_max_size_mb": config.cache_max_size_mb,
        "semantic_cache_threshold": config.semantic_cache_threshold,
        "semantic_cache_ttl_hours": config.semantic_cache_ttl_hours,
        "disk_file_count": file_count,
        "disk_size_bytes": total_bytes,
        "latest_run_cache": latest_run_cache,
    }


# ------------------------------------------------------------------
# Mutation metrics
# ------------------------------------------------------------------


@router.get("/api/metrics/mutations")
async def get_mutation_metrics():
    from dan.server.mutation_metrics import mutation_metrics
    return mutation_metrics.summary()


@router.post("/api/metrics/mutations/reset")
async def reset_mutation_metrics():
    from dan.server.mutation_metrics import mutation_metrics
    return mutation_metrics.reset()


# ------------------------------------------------------------------
# Files / docs / code-refs
# ------------------------------------------------------------------


@router.get("/api/files/list")
async def list_workspace_files(request: Request):
    resolver = get_mention_resolver(request)
    if resolver is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    files = resolver.file_resolver.list_files()
    return {"files": files}


@router.get("/api/organism-logs")
async def list_organism_logs(
    root_path: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Discover known organism-log files under one workspace root."""

    resolved_root = _resolve_organism_log_root(root_path)
    logs = _discover_organism_log_files(resolved_root, limit=limit)
    return {
        "root_path": str(resolved_root),
        "logs": logs,
    }


@router.get("/api/organism-logs/analyze")
async def analyze_organism_log_file(
    path: str,
    root_path: str | None = None,
) -> dict[str, Any]:
    """Analyze one organism-log file into timeline and dependency payloads."""

    resolved_root = _resolve_organism_log_root(root_path)
    resolved_path = _resolve_organism_log_path(path, root_path=resolved_root)
    summary = _summarize_organism_log_file(resolved_path, root_path=resolved_root)
    if summary is None:
        raise HTTPException(
            status_code=400,
            detail="Selected file is not a readable organism_log_v1 JSONL trace.",
        )
    analysis = analyze_organism_log(resolved_path)
    return {
        "path": str(resolved_path),
        "log": summary,
        "analysis": analysis.model_dump(mode="json"),
    }


@router.get("/api/docs/list")
async def list_docs(request: Request):
    resolver = get_mention_resolver(request)
    if resolver is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    docs = resolver.docs_resolver.list_docs()
    return {"docs": docs}


_DEFAULT_NOTES_WORKSPACE_ROOT = "~/.codex/memories"
_DEFAULT_CONTENT_PROJECT_NAME = "my-knowledge-base"
_WORKSPACE_NOTE_DIR_NAMES = ("",)
_WORKSPACE_NOTE_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
}
_WORKSPACE_FILE_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".dan-code",
    ".dan-research",
    ".dan-super",
}
_TEXT_FILE_SUFFIXES = {
    ".cfg",
    ".css",
    ".csv",
    ".env",
    ".gitignore",
    ".go",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".log",
    ".md",
    ".mdx",
    ".py",
    ".r",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_WORKSPACE_NOTE_SUMMARY_CACHE: dict[str, dict[str, Any]] = {}
_WORKSPACE_NOTE_READ_CACHE: dict[str, dict[str, Any]] = {}
_WORKSPACE_NOTE_PREVIEW_CACHE: dict[str, dict[str, Any]] = {}
_WORKSPACE_NOTE_CACHE_LIMIT = 512


def _expand_workspace_home(raw: str) -> Path:
    return Path(raw).expanduser()


def _content_bootstrap_root_candidates() -> list[Path]:
    """Mirror the desktop v1 content bootstrap root order for the notes workspace."""

    configured = [
        _expand_workspace_home(value)
        for value in str(os.environ.get("DAN_DEFAULT_CONTENT_ROOTS") or "").split(os.pathsep)
        if value.strip()
    ]
    home = Path.home()
    local_named = [
        Path("/Volumes/data/Dropbox/Projects") / _DEFAULT_CONTENT_PROJECT_NAME,
        home / "Dropbox" / "Projects" / _DEFAULT_CONTENT_PROJECT_NAME,
        home / "Projects" / _DEFAULT_CONTENT_PROJECT_NAME,
    ]
    heuristic: list[Path] = []
    for base in (Path.cwd(), Path(resolve_workspace_root())):
        try:
            resolved = base.expanduser().resolve()
        except OSError:
            resolved = base.expanduser()
        heuristic.extend(
            [
                resolved.parent / _DEFAULT_CONTENT_PROJECT_NAME,
                resolved.parent.parent / _DEFAULT_CONTENT_PROJECT_NAME,
            ],
        )

    seen: set[str] = set()
    candidates: list[Path] = []
    for candidate in [*configured, *local_named, *heuristic]:
        normalized = str(candidate.expanduser())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(candidate.expanduser())
    return candidates


def _notes_root_from_content_project(project_root: Path) -> Path:
    content_root = project_root / "content"
    if content_root.exists() and content_root.is_dir():
        return content_root
    content_notes = project_root / "content" / "notes"
    if content_notes.exists() and content_notes.is_dir():
        return content_notes
    return project_root


def _default_workspace_notes_root() -> Path:
    for candidate in _content_bootstrap_root_candidates():
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.exists() and resolved.is_dir():
            return _notes_root_from_content_project(resolved).resolve()
    return Path(_DEFAULT_NOTES_WORKSPACE_ROOT).expanduser().resolve()


def _workspace_notes_root() -> Path:
    explicit = os.environ.get("DAN_NOTES_WORKSPACE_ROOT") or os.environ.get("DAN_NOTES_ROOT")
    root = Path(explicit).expanduser().resolve() if explicit else _default_workspace_notes_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _workspace_files_root(root_path: str | None = None) -> Path:
    raw = str(root_path or resolve_workspace_root()).strip()
    root = Path(raw).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Workspace root not found: {root}")
    return root


def _workspace_root_candidate(raw_path: str, *, default_root: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = default_root / candidate
    try:
        return candidate.resolve(strict=False)
    except OSError:
        return candidate


def _workspace_root_suggestion(
    path: Path,
    *,
    kind: str,
) -> dict[str, str] | None:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return None
    if not resolved.exists() or not resolved.is_dir():
        return None
    return {
        "path": str(resolved),
        "name": resolved.name or str(resolved),
        "label": str(resolved),
        "kind": kind,
    }


def _append_workspace_root_suggestion(
    suggestions: list[dict[str, str]],
    seen: set[str],
    path: Path,
    *,
    kind: str,
    limit: int,
) -> None:
    if len(suggestions) >= limit:
        return
    summary = _workspace_root_suggestion(path, kind=kind)
    if summary is None or summary["path"] in seen:
        return
    seen.add(summary["path"])
    suggestions.append(summary)


def _matching_workspace_root_dirs(base: Path, fragment: str) -> list[Path]:
    prefix = fragment.lower()
    try:
        children = [
            child
            for child in base.iterdir()
            if child.is_dir()
            and (not child.name.startswith(".") or prefix.startswith("."))
            and child.name.lower().startswith(prefix)
        ]
    except OSError:
        return []
    return sorted(children, key=lambda child: child.name.lower())


def _resolve_workspace_note_path(raw_path: str) -> Path:
    root = _workspace_notes_root()
    raw_path = str(raw_path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=422, detail="path is required")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Note path must stay inside workspace root")
    if resolved.suffix.lower() not in {".md", ".mdx"}:
        raise HTTPException(status_code=400, detail="Only Markdown notes are supported")
    return resolved


def _resolve_workspace_note_entry_path(raw_path: str) -> Path:
    root = _workspace_notes_root()
    raw_path = str(raw_path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=422, detail="path is required")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Note path must stay inside workspace root")
    return resolved


def _resolve_workspace_file_path(raw_path: str, *, root_path: str | None = None) -> tuple[Path, Path]:
    root = _workspace_files_root(root_path)
    raw_path = str(raw_path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=422, detail="path is required")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="File path must stay inside workspace root")
    return resolved, root


def _resolve_workspace_file_entry_path(
    raw_path: str,
    *,
    root_path: str | None = None,
) -> tuple[Path, Path]:
    root = _workspace_files_root(root_path)
    raw_path = str(raw_path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=422, detail="path is required")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="File path must stay inside workspace root")
    return resolved, root


def _move_workspace_entry(source: Path, destination: Path, root: Path, *, label: str) -> Path:
    if source == root:
        raise HTTPException(status_code=400, detail=f"Cannot move the {label} root")
    if not source.exists():
        raise HTTPException(status_code=404, detail=f"{label.title()} path not found")
    if destination == root:
        raise HTTPException(status_code=400, detail=f"Cannot replace the {label} root")
    if destination.exists():
        raise HTTPException(status_code=409, detail="Destination already exists")
    if source.is_dir():
        try:
            destination.relative_to(source)
            raise HTTPException(status_code=400, detail="Cannot move a folder inside itself")
        except ValueError:
            pass
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        moved = shutil.move(str(source), str(destination))
    except HTTPException:
        raise
    except (OSError, shutil.Error) as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return Path(moved).resolve(strict=False)


def _fallback_note_title(path: Path, root: Path) -> str:
    if path.name.lower() == "index.md" and path.parent != root:
        raw = path.parent.name
    else:
        raw = path.stem
    return raw.replace("-", " ").replace("_", " ").strip() or path.name


def _cache_signature(path: Path, stat: os.stat_result) -> str:
    return f"{path}:{stat.st_mtime_ns}:{stat.st_size}"


def _trim_note_cache(cache: dict[str, dict[str, Any]]) -> None:
    if len(cache) <= _WORKSPACE_NOTE_CACHE_LIMIT:
        return
    for key in list(cache.keys())[: max(1, len(cache) - _WORKSPACE_NOTE_CACHE_LIMIT)]:
        cache.pop(key, None)


def _clear_workspace_note_caches() -> None:
    _WORKSPACE_NOTE_SUMMARY_CACHE.clear()
    _WORKSPACE_NOTE_READ_CACHE.clear()
    _WORKSPACE_NOTE_PREVIEW_CACHE.clear()


def _markdown_note_title_from_text(sample: str) -> str | None:
    metadata = _markdown_note_metadata_from_text(sample)
    if metadata["title"]:
        return str(metadata["title"])
    heading_match = re.search(r"^#\s+(.+?)\s*$", sample, re.MULTILINE)
    if heading_match:
        title = heading_match.group(1).strip()
        if title:
            return title
    return None


def _strip_yaml_comment(value: str) -> str:
    quote = ""
    for index, char in enumerate(value):
        if char in ("'", '"') and (index == 0 or value[index - 1] != "\\"):
            quote = "" if quote == char else quote or char
        if char == "#" and not quote and (index == 0 or value[index - 1].isspace()):
            return value[:index].strip()
    return value.strip()


def _yaml_scalar(value: str) -> str | bool:
    cleaned = _strip_yaml_comment(value)
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return cleaned.strip().strip("\"'")


def _yaml_list(value: str | bool | list[str]) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if item]
    if isinstance(value, bool):
        return []
    cleaned = str(value or "").strip()
    if not cleaned:
        return []
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]
    return [
        str(_yaml_scalar(item)).strip()
        for item in cleaned.split(",")
        if str(_yaml_scalar(item)).strip()
    ]


def _parse_yaml_frontmatter(raw: str) -> dict[str, str | bool | list[str]]:
    data: dict[str, str | bool | list[str]] = {}
    active_list_key = ""
    for line in raw.splitlines():
        if not line.strip():
            continue
        list_match = re.match(r"^\s*-\s+(.+?)\s*$", line)
        if list_match and active_list_key:
            current = data.get(active_list_key)
            data[active_list_key] = [
                *(current if isinstance(current, list) else []),
                str(_yaml_scalar(list_match.group(1))),
            ]
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if value:
            active_list_key = ""
            scalar = _yaml_scalar(value)
            data[key] = _yaml_list(str(scalar)) if str(value).strip().startswith("[") else scalar
        else:
            active_list_key = key
            data[key] = []
    return data


def _metadata_string(data: dict[str, str | bool | list[str]], key: str) -> str:
    value = data.get(key)
    if isinstance(value, list):
        return ", ".join(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value or "").strip()


def _metadata_bool(data: dict[str, str | bool | list[str]], key: str) -> bool:
    value = data.get(key)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() == "true"


def _markdown_note_metadata_from_text(sample: str) -> dict[str, Any]:
    frontmatter = re.match(r"^---\s*\n(?P<body>[\s\S]*?)\n---", sample)
    data = _parse_yaml_frontmatter(frontmatter.group("body")) if frontmatter else {}
    return {
        "title": _metadata_string(data, "title"),
        "layout": _metadata_string(data, "layout"),
        "date": _metadata_string(data, "date"),
        "lastmod": _metadata_string(data, "lastmod"),
        "page_id": _metadata_string(data, "pageID"),
        "draft": _metadata_bool(data, "draft"),
        "tags": _yaml_list(data.get("tags", [])),
        "categories": _yaml_list(data.get("categories", [])),
    }


def _markdown_note_citations_from_text(content: str) -> list[str]:
    seen: set[str] = set()
    citations: list[str] = []
    for match in re.finditer(r"(^|[\s([{'\"])@([A-Za-z0-9][A-Za-z0-9_-]{2,})", content):
        page_id = match.group(2)
        if page_id not in seen:
            seen.add(page_id)
            citations.append(page_id)
    return citations


def _markdown_note_citations(path: Path) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return _markdown_note_citations_from_text(content)


def _strip_markdown_frontmatter(content: str) -> str:
    match = re.match(r"^---\s*\r?\n[\s\S]*?\r?\n---\s*(?:\r?\n|$)", content)
    return content[match.end():].lstrip() if match else content.lstrip()


def _format_hugo_preview_body(body: str) -> str:
    """Mirror the GUI's readable Hugo/Markdown preview normalization."""

    def _summary_repl(match: re.Match[str]) -> str:
        return f"> Summary transclusion: @{match.group(1)}"

    def _shortcode_repl(match: re.Match[str]) -> str:
        shortcode = match.group(1)
        args = (match.group(2) or "").strip()
        return f"`{shortcode}{f' {args}' if args else ''}`"

    body = re.sub(r'\{\{<\s*summary\s+"([^"]+)"\s*>\}\}', _summary_repl, body)
    body = re.sub(r"\{\{<\s*([^>\s]+)([\s\S]*?)>\}\}", _shortcode_repl, body)
    return re.sub(
        r"(^|[\s(])@([A-Za-z0-9][A-Za-z0-9_-]+)",
        r"\1[@\2](#\2)",
        body,
        flags=re.MULTILINE,
    )


def _compile_markdown_preview_html(body: str) -> tuple[str, str]:
    """Compile Markdown for phone previews while keeping a plain fallback."""

    try:
        import markdown as markdown_lib

        return (
            markdown_lib.markdown(
                body,
                extensions=["extra", "sane_lists", "toc"],
                output_format="html5",
            ),
            "python-markdown",
        )
    except Exception:
        escaped = html.escape(body)
        return f"<pre>{escaped}</pre>", "escaped-text"


def _workspace_note_preview_route(relative_path: str) -> str:
    path = Path(relative_path)
    parts = list(path.parts)
    if not parts:
        return "/"
    if parts[-1].lower() in {"index.md", "_index.md"}:
        parts = parts[:-1]
    else:
        parts[-1] = Path(parts[-1]).with_suffix("").name
    cleaned = [part for part in parts if part and part != "."]
    return "/" + "/".join(cleaned) + ("/" if cleaned else "")


def _markdown_note_metadata(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            sample = handle.read(16_384)
    except OSError:
        return _markdown_note_metadata_from_text("")
    return _markdown_note_metadata_from_text(sample)


def _markdown_note_title(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            sample = handle.read(16_384)
    except OSError:
        return None
    return _markdown_note_title_from_text(sample)


def _workspace_note_summary(path: Path, root: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file() or path.suffix.lower() not in {".md", ".mdx"}:
        return None
    if path.name.lower() == "_index.md":
        return None
    try:
        relative_path = str(path.relative_to(root))
    except ValueError:
        relative_path = str(path)
    cache_key = _cache_signature(path, stat)
    cached = _WORKSPACE_NOTE_SUMMARY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    metadata = _markdown_note_metadata(path)
    relative_parts = Path(relative_path).parts
    section = relative_parts[0] if len(relative_parts) > 1 else "root"
    summary = {
        "path": str(path),
        "relative_path": relative_path,
        "title": metadata["title"] or _fallback_note_title(path, root),
        "layout": metadata["layout"],
        "section": section,
        "tags": metadata["tags"],
        "categories": metadata["categories"],
        "citations": _markdown_note_citations(path),
        "page_id": metadata["page_id"],
        "date": metadata["date"],
        "lastmod": metadata["lastmod"],
        "draft": metadata["draft"],
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }
    _WORKSPACE_NOTE_SUMMARY_CACHE[cache_key] = summary
    _trim_note_cache(_WORKSPACE_NOTE_SUMMARY_CACHE)
    return summary


def _workspace_file_summary(path: Path, root: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    try:
        relative_path = str(path.relative_to(root))
    except ValueError:
        relative_path = str(path)
    if relative_path == ".":
        return None
    return {
        "path": str(path),
        "relative_path": relative_path,
        "name": path.name,
        "parent": "" if path.parent == root else str(path.parent.relative_to(root)),
        "is_directory": path.is_dir(),
        "size": stat.st_size if path.is_file() else 0,
        "mtime": stat.st_mtime,
        "depth": len(Path(relative_path).parts) - 1,
    }


def _looks_like_text_file(path: Path, sample: bytes) -> bool:
    if b"\x00" in sample:
        return False
    if path.suffix.lower() in _TEXT_FILE_SUFFIXES:
        return True
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


@router.get("/api/workspace-notes")
async def list_workspace_notes(limit: int = 1000) -> dict[str, Any]:
    """List Hugo/Markdown pages from the always-on content workspace."""

    root = _workspace_notes_root()
    requested_limit = max(1, min(int(limit), 2000))
    seen: set[Path] = set()
    notes: list[dict[str, Any]] = []
    for dirname in _WORKSPACE_NOTE_DIR_NAMES:
        base = root / dirname if dirname else root
        if not base.exists() or not base.is_dir():
            continue
        max_depth = 8
        for current, dirs, files in os.walk(base):
            current_path = Path(current)
            try:
                depth = len(current_path.relative_to(base).parts)
            except ValueError:
                depth = 0
            dirs[:] = [
                item
                for item in dirs
                if item not in _WORKSPACE_NOTE_SKIP_DIRS
                and not item.startswith(".dan")
                and depth < max_depth
            ]
            dirs.sort(key=str.lower)
            for filename in sorted(files, key=str.lower):
                path = (current_path / filename).resolve()
                if path in seen:
                    continue
                summary = _workspace_note_summary(path, root)
                if summary is None:
                    continue
                seen.add(path)
                notes.append(summary)
                if len(notes) >= requested_limit:
                    break
            if len(notes) >= requested_limit:
                break
        if len(notes) >= requested_limit:
            break
    notes.sort(key=lambda item: (str(item["relative_path"]).count("/"), str(item["relative_path"])))
    return {"root": str(root), "notes": notes[:requested_limit]}


@router.get("/api/workspace-roots")
async def list_workspace_roots(
    query: str | None = None,
    limit: int = 18,
) -> dict[str, Any]:
    """Suggest nearby development roots for the chunk workspace root switcher."""

    default_root = _workspace_files_root()
    requested_limit = max(1, min(int(limit), 50))
    suggestions: list[dict[str, str]] = []
    seen: set[str] = set()
    _append_workspace_root_suggestion(
        suggestions,
        seen,
        default_root,
        kind="current",
        limit=requested_limit,
    )

    raw_query = str(query or "").strip()
    if raw_query:
        candidate = _workspace_root_candidate(raw_query, default_root=default_root)
        if candidate.exists() and candidate.is_dir():
            _append_workspace_root_suggestion(
                suggestions,
                seen,
                candidate,
                kind="match",
                limit=requested_limit,
            )
            # Exact root edits usually switch peer workspaces. A trailing slash
            # explicitly asks to browse into the chosen directory instead.
            base = candidate if raw_query.endswith(("/", "\\")) else candidate.parent
            fragment = ""
        else:
            base = candidate.parent
            fragment = candidate.name
        for child in _matching_workspace_root_dirs(base, fragment):
            _append_workspace_root_suggestion(
                suggestions,
                seen,
                child,
                kind="match",
                limit=requested_limit,
            )
    else:
        for child in _matching_workspace_root_dirs(default_root.parent, ""):
            _append_workspace_root_suggestion(
                suggestions,
                seen,
                child,
                kind="nearby",
                limit=requested_limit,
            )

    return {"root": str(default_root), "suggestions": suggestions[:requested_limit]}


@router.get("/api/workspace-files")
async def list_workspace_file_tree(
    root_path: str | None = None,
    limit: int = 2500,
    max_depth: int = 6,
) -> dict[str, Any]:
    """List a VS Code-style development workspace file tree."""

    root = _workspace_files_root(root_path)
    requested_limit = max(1, min(int(limit), 5000))
    requested_depth = max(1, min(int(max_depth), 12))
    entries: list[dict[str, Any]] = []
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        try:
            depth = len(current_path.relative_to(root).parts)
        except ValueError:
            depth = 0
        dirs[:] = [
            item
            for item in dirs
            if item not in _WORKSPACE_FILE_SKIP_DIRS
            and not item.startswith(".dan")
            and depth < requested_depth
        ]
        for dirname in sorted(dirs, key=str.lower):
            summary = _workspace_file_summary((current_path / dirname).resolve(), root)
            if summary is not None:
                entries.append(summary)
                if len(entries) >= requested_limit:
                    break
        if len(entries) >= requested_limit:
            break
        for filename in sorted(files, key=str.lower):
            if filename == ".DS_Store":
                continue
            summary = _workspace_file_summary((current_path / filename).resolve(), root)
            if summary is not None:
                entries.append(summary)
                if len(entries) >= requested_limit:
                    break
        if len(entries) >= requested_limit:
            break
    entries.sort(key=lambda item: (str(item["relative_path"]).count("/"), not item["is_directory"], str(item["relative_path"]).lower()))
    return {"root": str(root), "entries": entries[:requested_limit]}


@router.get("/api/workspace-files/read")
async def read_workspace_file(
    path: str,
    root_path: str | None = None,
    limit: int = 200_000,
) -> dict[str, Any]:
    resolved, root = _resolve_workspace_file_path(path, root_path=root_path)
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    read_limit = max(1024, min(int(limit), 1_000_000))
    try:
        with resolved.open("rb") as handle:
            sample = handle.read(min(read_limit + 1, 1_000_001))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if not _looks_like_text_file(resolved, sample[:4096]):
        raise HTTPException(status_code=400, detail="Selected file is not text-readable")
    truncated = len(sample) > read_limit
    if truncated:
        sample = sample[:read_limit]
    try:
        content = sample.decode("utf-8")
    except UnicodeDecodeError:
        content = sample.decode("utf-8", errors="replace")
    summary = _workspace_file_summary(resolved, root)
    return {
        "file": summary,
        "content": content,
        "truncated": truncated,
        "root": str(root),
    }


@router.post("/api/workspace-files/mkdir")
async def create_workspace_folder(body: dict[str, Any]) -> dict[str, Any]:
    target, root = _resolve_workspace_file_entry_path(
        str(body.get("path") or ""),
        root_path=str(body.get("root_path") or "") or None,
    )
    if target == root:
        raise HTTPException(status_code=400, detail="Cannot create the workspace root")
    if target.exists() and not target.is_dir():
        raise HTTPException(status_code=409, detail="A file already exists at that path")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        "status": "ok",
        "root": str(root),
        "file": _workspace_file_summary(target, root),
    }


@router.post("/api/workspace-files/move")
async def move_workspace_file(body: dict[str, Any]) -> dict[str, Any]:
    root_path = str(body.get("root_path") or "") or None
    source, root = _resolve_workspace_file_entry_path(
        str(body.get("source") or ""),
        root_path=root_path,
    )
    destination, root = _resolve_workspace_file_entry_path(
        str(body.get("destination") or ""),
        root_path=str(root),
    )
    moved = _move_workspace_entry(source, destination, root, label="workspace")
    return {
        "status": "ok",
        "root": str(root),
        "file": _workspace_file_summary(moved, root),
    }


@router.get("/api/workspace-notes/read")
async def read_workspace_note(path: str) -> dict[str, Any]:
    resolved = _resolve_workspace_note_path(path)
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Note not found")
    try:
        stat = resolved.stat()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    cache_key = _cache_signature(resolved, stat)
    cached = _WORKSPACE_NOTE_READ_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Note is not valid UTF-8 text")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    root = _workspace_notes_root()
    try:
        relative_path = str(resolved.relative_to(root))
    except ValueError:
        relative_path = str(resolved)
    metadata = _markdown_note_metadata_from_text(content[:16_384])
    relative_parts = Path(relative_path).parts
    section = relative_parts[0] if len(relative_parts) > 1 else "root"
    summary = {
        "path": str(resolved),
        "relative_path": relative_path,
        "title": metadata["title"] or _fallback_note_title(resolved, root),
        "layout": metadata["layout"],
        "section": section,
        "tags": metadata["tags"],
        "categories": metadata["categories"],
        "citations": _markdown_note_citations_from_text(content),
        "page_id": metadata["page_id"],
        "date": metadata["date"],
        "lastmod": metadata["lastmod"],
        "draft": metadata["draft"],
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }
    result = {"note": summary, "content": content}
    _WORKSPACE_NOTE_READ_CACHE[cache_key] = result
    _WORKSPACE_NOTE_SUMMARY_CACHE[cache_key] = summary
    _trim_note_cache(_WORKSPACE_NOTE_READ_CACHE)
    _trim_note_cache(_WORKSPACE_NOTE_SUMMARY_CACHE)
    return result


@router.post("/api/workspace-notes/move")
async def move_workspace_note(body: dict[str, Any]) -> dict[str, Any]:
    root = _workspace_notes_root()
    source = _resolve_workspace_note_entry_path(str(body.get("source") or ""))
    destination = _resolve_workspace_note_entry_path(str(body.get("destination") or ""))
    if source.is_file() and source.suffix.lower() not in {".md", ".mdx"}:
        raise HTTPException(status_code=400, detail="Only Markdown notes can be moved")
    moved = _move_workspace_entry(source, destination, root, label="notes")
    _clear_workspace_note_caches()
    summary_path = moved
    if moved.is_dir():
        for candidate_name in ("index.md", "index.mdx"):
            candidate = moved / candidate_name
            if candidate.exists():
                summary_path = candidate
                break
    note = _workspace_note_summary(summary_path, root) if summary_path.exists() else None
    return {
        "status": "ok",
        "root": str(root),
        "note": note,
    }


@router.get("/api/workspace-notes/preview")
async def preview_workspace_note(path: str) -> dict[str, Any]:
    """Return a cached, live-compiled phone preview for a Hugo/Markdown page."""

    resolved = _resolve_workspace_note_path(path)
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Note not found")
    try:
        stat = resolved.stat()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    cache_key = _cache_signature(resolved, stat)
    cached = _WORKSPACE_NOTE_PREVIEW_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Note is not valid UTF-8 text")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    root = _workspace_notes_root()
    try:
        relative_path = str(resolved.relative_to(root))
    except ValueError:
        relative_path = str(resolved)
    metadata = _markdown_note_metadata_from_text(content[:16_384])
    body = _strip_markdown_frontmatter(content)
    preview_body = _format_hugo_preview_body(body)
    compiled_html, compiler = _compile_markdown_preview_html(preview_body)
    relative_parts = Path(relative_path).parts
    section = relative_parts[0] if len(relative_parts) > 1 else "root"
    summary = {
        "path": str(resolved),
        "relative_path": relative_path,
        "title": metadata["title"] or _fallback_note_title(resolved, root),
        "layout": metadata["layout"],
        "section": section,
        "tags": metadata["tags"],
        "categories": metadata["categories"],
        "citations": _markdown_note_citations_from_text(content),
        "page_id": metadata["page_id"],
        "date": metadata["date"],
        "lastmod": metadata["lastmod"],
        "draft": metadata["draft"],
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }
    result = {
        "note": summary,
        "body_markdown": body,
        "preview_markdown": preview_body,
        "compiled_html": compiled_html,
        "compiler": compiler,
        "cache_key": cache_key,
        "route_path": _workspace_note_preview_route(relative_path),
        "compiled_at": datetime.now(timezone.utc).isoformat(),
    }
    _WORKSPACE_NOTE_PREVIEW_CACHE[cache_key] = result
    _WORKSPACE_NOTE_SUMMARY_CACHE[cache_key] = summary
    _trim_note_cache(_WORKSPACE_NOTE_PREVIEW_CACHE)
    _trim_note_cache(_WORKSPACE_NOTE_SUMMARY_CACHE)
    return result


@router.put("/api/workspace-notes/write")
async def write_workspace_note(body: dict[str, Any]) -> dict[str, Any]:
    resolved = _resolve_workspace_note_path(str(body.get("path") or ""))
    content = body.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=422, detail="content must be a string")
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    try:
        stat = resolved.stat()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    root = _workspace_notes_root()
    try:
        relative_path = str(resolved.relative_to(root))
    except ValueError:
        relative_path = str(resolved)
    metadata = _markdown_note_metadata_from_text(content[:16_384])
    relative_parts = Path(relative_path).parts
    section = relative_parts[0] if len(relative_parts) > 1 else "root"
    summary = {
        "path": str(resolved),
        "relative_path": relative_path,
        "title": metadata["title"] or _fallback_note_title(resolved, root),
        "layout": metadata["layout"],
        "section": section,
        "tags": metadata["tags"],
        "categories": metadata["categories"],
        "citations": _markdown_note_citations_from_text(content),
        "page_id": metadata["page_id"],
        "date": metadata["date"],
        "lastmod": metadata["lastmod"],
        "draft": metadata["draft"],
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }
    cache_key = _cache_signature(resolved, stat)
    _WORKSPACE_NOTE_READ_CACHE[cache_key] = {"note": summary, "content": content}
    _WORKSPACE_NOTE_SUMMARY_CACHE[cache_key] = summary
    _trim_note_cache(_WORKSPACE_NOTE_READ_CACHE)
    _trim_note_cache(_WORKSPACE_NOTE_SUMMARY_CACHE)
    return {"status": "ok", "note": summary}


@router.get("/api/code-refs/{workflow_id}")
async def list_code_refs(workflow_id: str, request: Request):
    gs = get_graph_store(request)
    graph_dict = gs.get_graph(workflow_id)
    if graph_dict is None:
        raise HTTPException(status_code=404, detail=f"Graph '{workflow_id}' not found")
    from dan.server.mention_resolver import CodeResolver
    refs = CodeResolver.list_code_refs(graph_dict)
    return {"refs": refs}


# ------------------------------------------------------------------
# Test cases
# ------------------------------------------------------------------

from dan.server.test_cases import NodeTestCase, TestCaseRunResult


@router.get("/api/test-cases/{workflow_id}/{node_id}")
async def list_test_cases(workflow_id: str, node_id: str, request: Request):
    validate_path_segment(workflow_id, "workflow_id")
    store = get_test_case_store(request)
    cases = store.list_cases(workflow_id, node_id)
    return {"cases": [c.model_dump() for c in cases]}


@router.post("/api/test-cases/{workflow_id}/{node_id}")
async def create_or_update_test_case(
    workflow_id: str,
    node_id: str,
    request: Request,
    body: dict[str, Any],
):
    validate_path_segment(workflow_id, "workflow_id")
    store = get_test_case_store(request)
    body.setdefault("node_id", node_id)
    body.setdefault("updated_at", time.time())
    if "id" not in body:
        body["id"] = str(uuid.uuid4())
    if "created_at" not in body:
        body["created_at"] = time.time()
    case = NodeTestCase.model_validate(body)
    store.save_case(workflow_id, node_id, case)
    return {"case": case.model_dump()}


@router.delete("/api/test-cases/{workflow_id}/{node_id}/{case_id}")
async def delete_test_case(
    workflow_id: str,
    node_id: str,
    case_id: str,
    request: Request,
):
    validate_path_segment(workflow_id, "workflow_id")
    store = get_test_case_store(request)
    if not store.delete_case(workflow_id, node_id, case_id):
        raise HTTPException(status_code=404, detail=f"Test case '{case_id}' not found")
    return {"status": "deleted", "case_id": case_id}


@router.post("/api/test-cases/{workflow_id}/{node_id}/{case_id}/run")
async def run_test_case(
    workflow_id: str,
    node_id: str,
    case_id: str,
    request: Request,
):
    import asyncio

    validate_path_segment(workflow_id, "workflow_id")
    rm = get_run_manager(request)
    gs = get_graph_store(request)
    store = get_test_case_store(request)

    case = store.get_case(workflow_id, node_id, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Test case '{case_id}' not found")

    graph = gs.load_as_model(workflow_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{workflow_id}' not found")

    target_node = graph.node_by_id(node_id)
    if target_node is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found in graph")

    from dan.models.graph import Graph as GraphModel

    synthetic = GraphModel(
        nodes=[target_node],
        entry_points=[node_id],
        exit_points=[node_id],
    )

    run_id = f"test-{case_id}-{int(time.time() * 1000)}"
    record = await rm.start_run(
        synthetic,
        graph_id=workflow_id,
        inputs=case.inputs,
        run_id=run_id,
    )

    deadline = time.time() + 120
    while True:
        current = rm.get_run(record.run_id)
        if current is None:
            break
        if current.status.value in ("completed", "failed"):
            break
        if time.time() > deadline:
            break
        await asyncio.sleep(0.1)

    current = rm.get_run(record.run_id)
    actual_outputs: dict[str, Any] = {}
    execution_metadata: dict[str, Any] = {}
    error_msg: str | None = None

    if current and current.result:
        actual_outputs = current.result.outputs or {}
        if node_id in actual_outputs and isinstance(actual_outputs[node_id], dict):
            actual_outputs = actual_outputs[node_id]
        if current.result.errors:
            error_msg = "; ".join(
                f"{k}: {v}" for k, v in current.result.errors.items()
            )
        execution_metadata = {
            "run_id": record.run_id,
            "elapsed_seconds": current.elapsed_seconds,
            "total_tokens": current.total_tokens,
            "total_cost": current.total_cost,
            "model": current.model,
        }

    passed = True
    diff: dict[str, Any] | None = None

    if error_msg:
        passed = False
    elif case.expected_outputs is not None:
        diff = {}
        for key, expected_val in case.expected_outputs.items():
            actual_val = actual_outputs.get(key)
            if actual_val != expected_val:
                diff[key] = {"expected": expected_val, "actual": actual_val}
        passed = len(diff) == 0
        if not diff:
            diff = None

    result = TestCaseRunResult(
        passed=passed,
        actual_outputs=actual_outputs,
        expected_outputs=case.expected_outputs,
        diff=diff,
        execution_metadata=execution_metadata,
        error=error_msg,
    )
    return result.model_dump()


# ------------------------------------------------------------------
# Memory
# ------------------------------------------------------------------


@router.get("/api/memory/{workflow_id}/{session_id}")
async def list_memory_keys(workflow_id: str, session_id: str, request: Request):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(session_id, "session_id")
    store = get_memory_store(request)
    keys = await store.list_keys(workflow_id, session_id)
    return {"workflow_id": workflow_id, "session_id": session_id, "keys": keys}


@router.get("/api/memory/{workflow_id}/{session_id}/{key:path}")
async def read_memory_entry(
    workflow_id: str,
    session_id: str,
    key: str,
    request: Request,
):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(session_id, "session_id")
    store = get_memory_store(request)
    entry = await store.read(workflow_id, session_id, key)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Memory key '{key}' not found")
    return entry.model_dump()


@router.delete("/api/memory/{workflow_id}/{session_id}")
async def clear_session_memory(workflow_id: str, session_id: str, request: Request):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(session_id, "session_id")
    store = get_memory_store(request)
    await store.clear_session(workflow_id, session_id)
    return {"status": "cleared", "workflow_id": workflow_id, "session_id": session_id}


@router.get("/api/memory/{workflow_id}")
async def list_sessions(workflow_id: str, request: Request):
    validate_path_segment(workflow_id, "workflow_id")
    store = get_memory_store(request)
    sessions = await store.list_sessions(workflow_id)
    return {"workflow_id": workflow_id, "sessions": sessions}


# ------------------------------------------------------------------
# Error memory
# ------------------------------------------------------------------


@router.get("/api/errors/{workflow_id}")
async def list_error_memory(workflow_id: str, request: Request, limit: int = 50):
    rm = get_run_manager(request)
    index = rm._get_error_memory_index()
    if index is None:
        return {"errors": [], "message": "Error memory not enabled"}
    try:
        stats = await index.stats(workflow_id)
        return {"workflow_id": workflow_id, **stats}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/api/errors/{workflow_id}")
async def clear_error_memory(workflow_id: str, request: Request):
    rm = get_run_manager(request)
    index = rm._get_error_memory_index()
    if index is None:
        raise HTTPException(status_code=400, detail="Error memory not enabled")
    await index.clear(workflow_id)
    return {"status": "cleared", "workflow_id": workflow_id}


@router.get("/api/errors/{workflow_id}/search")
async def search_error_memory(
    workflow_id: str,
    request: Request,
    q: str = "",
    top_k: int = 5,
):
    rm = get_run_manager(request)
    index = rm._get_error_memory_index()
    if index is None:
        raise HTTPException(status_code=400, detail="Error memory not enabled")
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")
    results = await index.query_similar(workflow_id, q.strip(), top_k=top_k)
    return {"workflow_id": workflow_id, "query": q, "results": results}


# ------------------------------------------------------------------
# Rules
# ------------------------------------------------------------------


@router.get("/api/rules/{workflow_id}")
async def list_generated_rules(
    workflow_id: str,
    request: Request,
    status: str | None = None,
):
    validate_path_segment(workflow_id, "workflow_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        return {"rules": [], "message": "Self-evolving rules not enabled"}
    rules = manager.list_rules(workflow_id, status=status)
    return {
        "workflow_id": workflow_id,
        "rules": [r.model_dump() for r in rules],
    }


@router.post("/api/rules/{workflow_id}/{rule_id}/disable")
async def disable_generated_rule(
    workflow_id: str,
    rule_id: str,
    request: Request,
):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(rule_id, "rule_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    if not manager.disable_rule(workflow_id, rule_id):
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    rm.emit_rule_lifecycle_event(workflow_id, "rule_disabled", {
        "rule_id": rule_id,
        "reason": "manual_api",
    })
    return {"status": "disabled", "rule_id": rule_id}


@router.post("/api/rules/{workflow_id}/{rule_id}/enable")
async def enable_generated_rule(
    workflow_id: str,
    rule_id: str,
    request: Request,
):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(rule_id, "rule_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    if not manager.enable_rule(workflow_id, rule_id):
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return {"status": "enabled", "rule_id": rule_id}


@router.post("/api/rules/{workflow_id}/{rule_id}/approve")
async def approve_generated_rule(
    workflow_id: str,
    rule_id: str,
    request: Request,
):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(rule_id, "rule_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    if not manager.enable_rule(workflow_id, rule_id):
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return {"status": "approved", "rule_id": rule_id}


@router.delete("/api/rules/{workflow_id}/{rule_id}")
async def delete_generated_rule(
    workflow_id: str,
    rule_id: str,
    request: Request,
):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(rule_id, "rule_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    if not manager.delete_rule(workflow_id, rule_id):
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return {"status": "deleted", "rule_id": rule_id}


@router.post("/api/rules/{workflow_id}/rollback")
async def rollback_generated_rules(
    workflow_id: str,
    request: Request,
    body: dict[str, Any],
):
    validate_path_segment(workflow_id, "workflow_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    before = body.get("before")
    if not before:
        raise HTTPException(status_code=400, detail="'before' timestamp is required")
    disabled = manager.rollback(workflow_id, float(before))
    return {"disabled_count": len(disabled), "disabled_rule_ids": disabled}


@router.get("/api/rules/{workflow_id}/stats")
async def generated_rules_stats(workflow_id: str, request: Request):
    validate_path_segment(workflow_id, "workflow_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        return {"message": "Self-evolving rules not enabled"}
    return manager.stats(workflow_id)
