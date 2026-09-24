"""Work/Notes filesystem, preview, learning, and health endpoints."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
import logging
import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from dan.notes import (
    DEFAULT_CONTENT_PROJECT_NAME,
    DEFAULT_NOTES_WORKSPACE_ROOT,
    content_bootstrap_root_candidates,
    default_workspace_notes_root,
    expand_workspace_home,
    notes_root_from_content_project,
    workspace_notes_root,
)
from dan.server.paths import resolve_workspace_root
from dan.workspace_roots import canonicalize_workspace_root

logger = logging.getLogger(__name__)

router = APIRouter()

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


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


# ------------------------------------------------------------------
# Active Work/Notes endpoints
# ------------------------------------------------------------------

@router.get("/health")
async def health_check() -> dict[str, Any]:
    return {"status": "ok", "pid": os.getpid(), "timestamp": time.time()}


@router.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "pid": os.getpid(), "timestamp": time.time()}


@router.get("/api/workspace-wireguard")
async def get_workspace_wireguard_status() -> dict[str, Any]:
    return _workspace_wireguard_status()


_DEFAULT_NOTES_WORKSPACE_ROOT = DEFAULT_NOTES_WORKSPACE_ROOT
_DEFAULT_CONTENT_PROJECT_NAME = DEFAULT_CONTENT_PROJECT_NAME
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
_WORKSPACE_LEARN_DIR_NAME = ".dan-learn"
_WORKSPACE_LEARN_COURSE_VERSION = 1


def _expand_workspace_home(raw: str) -> Path:
    return expand_workspace_home(raw)


def _content_bootstrap_root_candidates() -> list[Path]:
    return content_bootstrap_root_candidates(
        workspace_root=resolve_workspace_root(),
        cwd=Path.cwd(),
    )


def _notes_root_from_content_project(project_root: Path) -> Path:
    return notes_root_from_content_project(project_root)


def _default_workspace_notes_root() -> Path:
    return default_workspace_notes_root(
        workspace_root=resolve_workspace_root(),
        cwd=Path.cwd(),
    )


def _workspace_notes_root() -> Path:
    return workspace_notes_root(
        workspace_root=resolve_workspace_root(),
        cwd=Path.cwd(),
        create=True,
    )


def _workspace_files_root(root_path: str | None = None) -> Path:
    raw = str(root_path or resolve_workspace_root()).strip()
    root = canonicalize_workspace_root(raw)
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Workspace root not found: {root}")
    return root


def _workspace_root_candidate(raw_path: str, *, default_root: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = default_root / candidate
    try:
        return canonicalize_workspace_root(candidate.resolve(strict=False))
    except OSError:
        return canonicalize_workspace_root(candidate)


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


def _decode_workspace_file_preview_root(token: str) -> str | None:
    if not token or token == "-":
        return None
    try:
        padding = "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(f"{token}{padding}".encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace preview root") from exc
    decoded = decoded.strip()
    return decoded or None


def _workspace_file_response(path: Path) -> FileResponse:
    media_type, _ = mimetypes.guess_type(path.name)
    safe_name = re.sub(r'["\r\n]', "_", path.name)
    return FileResponse(
        path,
        media_type=media_type or "application/octet-stream",
        filename=safe_name,
        content_disposition_type="inline",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
        },
    )


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

    def _callout_tone_classes(kind: str) -> dict[str, str]:
        if kind in {"warning", "caution"}:
            return {
                "shell": "border-amber-300/80 bg-amber-50/90 text-amber-950 dark:border-amber-700/80 dark:bg-amber-950/35 dark:text-amber-100",
                "header": "border-amber-200/80 bg-amber-100/70 text-amber-900 dark:border-amber-800/80 dark:bg-amber-900/35 dark:text-amber-100",
                "marker": "border-amber-500/70 bg-amber-200 text-amber-950 dark:border-amber-500/70 dark:bg-amber-700/40 dark:text-amber-100",
            }
        if kind in {"danger", "error"}:
            return {
                "shell": "border-red-300/80 bg-red-50/90 text-red-950 dark:border-red-800/80 dark:bg-red-950/35 dark:text-red-100",
                "header": "border-red-200/80 bg-red-100/70 text-red-900 dark:border-red-800/80 dark:bg-red-900/35 dark:text-red-100",
                "marker": "border-red-500/70 bg-red-200 text-red-950 dark:border-red-500/70 dark:bg-red-700/40 dark:text-red-100",
            }
        return {
            "shell": "border-slate-300/80 bg-slate-50/90 text-slate-900 dark:border-slate-700/80 dark:bg-slate-900/60 dark:text-slate-100",
            "header": "border-slate-200/80 bg-slate-100/70 text-slate-800 dark:border-slate-700/80 dark:bg-slate-800/60 dark:text-slate-100",
            "marker": "border-slate-400/70 bg-slate-200 text-slate-900 dark:border-slate-500/70 dark:bg-slate-700 dark:text-slate-100",
        }

    def _callout_repl(match: re.Match[str]) -> str:
        args = (match.group(1) or "").strip()
        inner = (match.group(2) or "").strip()
        title_match = re.search(r'"([^"]+)"', args)
        title = title_match.group(1).strip() if title_match else ""
        variant = re.sub(r'"[^"]*"', "", args).strip().split()
        raw_kind = variant[0] if variant else "note"
        kind = raw_kind.lower() if re.match(r"^[a-z0-9_-]+$", raw_kind, re.I) else "note"
        label_parts = []
        if kind and kind != "note":
            label_parts.append(kind.capitalize())
        if title:
            label_parts.append(title)
        label = ": ".join(label_parts) or "Note"
        tone = _callout_tone_classes(kind)
        paragraphs = [
            " ".join(part.strip() for part in paragraph.splitlines() if part.strip())
            for paragraph in re.split(r"\n{2,}", inner)
        ]
        body_html = "".join(
            f'<p class="m-0 leading-7 text-current">{html.escape(paragraph)}</p>'
            for paragraph in paragraphs
            if paragraph
        ) or '<p class="m-0 leading-7 text-current">No callout content.</p>'
        return (
            f'<aside class="dan-markdown-callout my-4 overflow-hidden rounded-md border shadow-sm {tone["shell"]}" '
            f'data-callout-kind="{html.escape(kind)}">'
            f'<div class="dan-markdown-callout-header flex items-center gap-2 border-b px-3 py-2 {tone["header"]}">'
            f'<span class="dan-markdown-callout-marker grid h-5 w-5 flex-none place-items-center rounded border text-[11px] font-bold leading-none {tone["marker"]}">!</span>'
            f'<span class="text-sm font-semibold leading-5">{html.escape(label)}</span>'
            "</div>"
            f'<div class="dan-markdown-callout-body space-y-2 px-3 py-3 text-base leading-7">{body_html}</div>'
            "</aside>"
        )

    def _shortcode_repl(match: re.Match[str]) -> str:
        shortcode = match.group(1)
        args = (match.group(2) or "").strip()
        return f"`{shortcode}{f' {args}' if args else ''}`"

    body = re.sub(r'\{\{<\s*summary\s+"([^"]+)"\s*>\}\}', _summary_repl, body)
    body = re.sub(r"\{\{<\s*callout\b([^>]*)>\}\}([\s\S]*?)\{\{<\s*/callout\s*>\}\}", _callout_repl, body)
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


def _workspace_note_relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _workspace_note_content_signature(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _workspace_note_learn_path(path: Path, root: Path) -> Path:
    relative_path = _workspace_note_relative_path(path, root)
    digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:24]
    return root / _WORKSPACE_LEARN_DIR_NAME / "courses" / f"{digest}.json"


def _clean_markdown_text(value: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", value)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~>#|]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _learning_summary(value: str, limit: int = 260) -> str:
    text = _clean_markdown_text(value)
    if len(text) <= limit:
        return text
    sentence = re.split(r"(?<=[.!?])\s+", text[: limit + 120], maxsplit=1)[0].strip()
    if 60 <= len(sentence) <= limit:
        return sentence
    return f"{text[:limit].rstrip()}..."


def _learning_word_count(value: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", _clean_markdown_text(value)))


def _learning_session_id(index: int) -> str:
    return f"session-{index + 1:02d}"


def _markdown_learning_sections(body: str, fallback_title: str) -> list[dict[str, str]]:
    heading_pattern = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
    matches = list(heading_pattern.finditer(body))
    if not matches:
        return []

    sections: list[dict[str, str]] = []
    preface = body[: matches[0].start()].strip()
    if _learning_word_count(preface) >= 80:
        sections.append({"title": "Orientation", "body": preface})

    for index, match in enumerate(matches):
        title = _clean_markdown_text(match.group(2)) or f"Session {index + 1}"
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        section_body = body[start:end].strip()
        if _learning_word_count(section_body) < 8 and len(matches) > 1:
            continue
        if index == 0 and title.strip().lower() == fallback_title.strip().lower():
            if _learning_word_count(section_body) < 120:
                continue
        sections.append({"title": title, "body": section_body or title})
    return sections


def _paragraph_learning_sections(body: str, fallback_title: str) -> list[dict[str, str]]:
    paragraphs = [
        item.strip()
        for item in re.split(r"\n\s*\n", body)
        if _learning_word_count(item) >= 12
    ]
    if not paragraphs:
        compact = _clean_markdown_text(body)
        return [{"title": fallback_title or "Manual", "body": compact}] if compact else []

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    target_words = 520
    for paragraph in paragraphs:
        words = _learning_word_count(paragraph)
        if current and current_words + words > target_words:
            chunks.append("\n\n".join(current))
            current = []
            current_words = 0
        current.append(paragraph)
        current_words += words
    if current:
        chunks.append("\n\n".join(current))

    return [
        {
            "title": f"{fallback_title or 'Manual'} · Part {index + 1}",
            "body": chunk,
        }
        for index, chunk in enumerate(chunks)
    ]


def _bounded_learning_sections(body: str, fallback_title: str) -> list[dict[str, str]]:
    sections = _markdown_learning_sections(body, fallback_title)
    if len(sections) < 2:
        sections = _paragraph_learning_sections(body, fallback_title)
    if len(sections) <= 10:
        return sections

    target_count = 10
    merged: list[dict[str, str]] = []
    bucket_size = max(1, round(len(sections) / target_count))
    for start in range(0, len(sections), bucket_size):
        group = sections[start : start + bucket_size]
        if not group:
            continue
        title = group[0]["title"]
        if len(group) > 1:
            title = f"{title} + {len(group) - 1} more"
        merged.append(
            {
                "title": title,
                "body": "\n\n".join(item["body"] for item in group),
            }
        )
    return merged[:target_count]


def _generate_workspace_learn_course(
    *,
    note_path: Path,
    root: Path,
    summary: dict[str, Any],
    content: str,
) -> dict[str, Any]:
    relative_path = _workspace_note_relative_path(note_path, root)
    note_title = str(summary.get("title") or note_path.stem or "Manual")
    body = _strip_markdown_frontmatter(content)
    sections = _bounded_learning_sections(body, note_title)
    now = datetime.now(timezone.utc).isoformat()
    course_id = "learn-" + hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]
    sessions: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        title = section["title"].strip() or f"Session {index + 1}"
        section_body = section["body"].strip()
        words = _learning_word_count(section_body)
        sessions.append(
            {
                "id": _learning_session_id(index),
                "title": title,
                "summary": _learning_summary(section_body or title),
                "duration_minutes": max(8, min(45, round(words / 135) * 5 or 10)),
                "source_heading": title,
                "body": section_body[:7000],
                "objectives": [
                    f"Understand the core idea in {title}.",
                    f"Connect {title} back to {note_title}.",
                ],
                "practice": [
                    "Write a three-bullet recall note without looking back.",
                    "Name one question or example to revisit before moving on.",
                ],
            }
        )

    if not sessions:
        sessions.append(
            {
                "id": _learning_session_id(0),
                "title": note_title,
                "summary": "Read the manual as a single study session.",
                "duration_minutes": 15,
                "source_heading": note_title,
                "body": body[:7000],
                "objectives": [f"Read {note_title} end to end."],
                "practice": ["Write a short recall note after reading."],
            }
        )

    return {
        "version": _WORKSPACE_LEARN_COURSE_VERSION,
        "course_id": course_id,
        "note_path": str(note_path),
        "note_relative_path": relative_path,
        "note_title": note_title,
        "note_mtime": summary.get("mtime"),
        "content_signature": _workspace_note_content_signature(content),
        "source": "dan-notes-heading-split",
        "generated_at": now,
        "updated_at": now,
        "stale": False,
        "sessions": sessions,
        "progress": {
            "active_session_id": sessions[0]["id"],
            "completed_session_ids": [],
        },
    }


def _read_workspace_learn_course(course_path: Path) -> dict[str, Any] | None:
    if not course_path.exists():
        return None
    try:
        data = json.loads(course_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_workspace_learn_course(course_path: Path, course: dict[str, Any]) -> None:
    try:
        course_path.parent.mkdir(parents=True, exist_ok=True)
        course_path.write_text(
            json.dumps(course, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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


@router.get("/api/workspace-skills")
async def list_workspace_skills(
    root_path: str | None = None,
    limit: int = 80,
) -> dict[str, Any]:
    """List installed skill mention tokens for the workspace composer."""

    root = _workspace_files_root(root_path)
    requested_limit = max(1, min(int(limit), 200))
    try:
        from dan.skills.invocation import load_skill_catalog, skill_token

        rows = load_skill_catalog(str(root))
    except Exception as exc:
        logger.warning("Failed to load workspace skill suggestions: %s", exc)
        rows = []

    skills: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in rows:
        token = skill_token(item)
        if not token or token in seen:
            continue
        seen.add(token)
        skills.append(
            {
                "token": token,
                "name": str(item.get("name") or token),
                "description": str(item.get("description") or ""),
                "source_scope": str(item.get("source_scope") or ""),
            }
        )
        if len(skills) >= requested_limit:
            break
    return {"root": str(root), "skills": skills}


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
    def _inside(candidate: Path) -> Path | None:
        # Symlinks may point outside the project (e.g. DAN's skill pool); never list or follow those.
        resolved = candidate.resolve()
        return resolved if resolved == root or root in resolved.parents else None

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
            and _inside(current_path / item) is not None
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
            inside = _inside(current_path / filename)
            if inside is None:
                continue
            summary = _workspace_file_summary(inside, root)
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


@router.get("/api/workspace-files/preview/{root_token}/{path:path}")
async def preview_workspace_file(
    root_token: str,
    path: str,
) -> FileResponse:
    root_path = _decode_workspace_file_preview_root(root_token)
    resolved, _root = _resolve_workspace_file_path(path, root_path=root_path)
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return _workspace_file_response(resolved)


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


@router.get("/api/workspace-notes/learn/course")
async def get_workspace_note_learn_course(path: str) -> dict[str, Any]:
    """Return an existing note-linked course without generating one."""

    resolved = _resolve_workspace_note_path(path)
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Note not found")
    root = _workspace_notes_root()
    summary = _workspace_note_summary(resolved, root)
    if summary is None:
        raise HTTPException(status_code=404, detail="Note not found")
    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Note is not valid UTF-8 text")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    course_path = _workspace_note_learn_path(resolved, root)
    course = _read_workspace_learn_course(course_path)
    if course is not None:
        course = {
            **course,
            "note_path": str(resolved),
            "note_relative_path": _workspace_note_relative_path(resolved, root),
            "note_title": summary.get("title") or course.get("note_title") or resolved.stem,
            "stale": course.get("content_signature") != _workspace_note_content_signature(content),
        }
    return {
        "status": "ok",
        "root": str(root),
        "course_path": str(course_path),
        "note": summary,
        "course": course,
    }


@router.post("/api/workspace-notes/learn/course")
async def generate_workspace_note_learn_course(body: dict[str, Any]) -> dict[str, Any]:
    """Generate and persist course sessions for a note on explicit request."""

    resolved = _resolve_workspace_note_path(str(body.get("path") or ""))
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Note not found")
    root = _workspace_notes_root()
    summary = _workspace_note_summary(resolved, root)
    if summary is None:
        raise HTTPException(status_code=404, detail="Note not found")
    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Note is not valid UTF-8 text")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    course_path = _workspace_note_learn_path(resolved, root)
    existing = _read_workspace_learn_course(course_path)
    previous_progress = existing.get("progress") if isinstance(existing, dict) else None
    course = _generate_workspace_learn_course(
        note_path=resolved,
        root=root,
        summary=summary,
        content=content,
    )
    if isinstance(previous_progress, dict):
        session_ids = {str(session.get("id")) for session in course["sessions"]}
        completed = [
            str(item)
            for item in previous_progress.get("completed_session_ids", [])
            if str(item) in session_ids
        ]
        active_session_id = str(previous_progress.get("active_session_id") or "")
        course["progress"] = {
            "active_session_id": active_session_id if active_session_id in session_ids else course["sessions"][0]["id"],
            "completed_session_ids": completed,
        }
    _write_workspace_learn_course(course_path, course)
    return {
        "status": "ok",
        "root": str(root),
        "course_path": str(course_path),
        "note": summary,
        "course": course,
    }


@router.put("/api/workspace-notes/learn/course/progress")
async def update_workspace_note_learn_progress(body: dict[str, Any]) -> dict[str, Any]:
    """Persist per-note learning progress for an existing generated course."""

    resolved = _resolve_workspace_note_path(str(body.get("path") or ""))
    root = _workspace_notes_root()
    course_path = _workspace_note_learn_path(resolved, root)
    course = _read_workspace_learn_course(course_path)
    if course is None:
        raise HTTPException(status_code=404, detail="Learning course has not been generated")
    session_ids = {str(session.get("id")) for session in course.get("sessions", [])}
    active_session_id = str(body.get("active_session_id") or "")
    completed_session_ids = [
        str(item)
        for item in body.get("completed_session_ids", [])
        if str(item) in session_ids
    ]
    course["progress"] = {
        "active_session_id": active_session_id if active_session_id in session_ids else None,
        "completed_session_ids": completed_session_ids,
    }
    course["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_workspace_learn_course(course_path, course)
    return {
        "status": "ok",
        "root": str(root),
        "course_path": str(course_path),
        "course": course,
    }
