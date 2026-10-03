"""Built-in tool: list directory contents with optional glob filtering."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from diane.tools._workspace import validate_path

_DEFAULT_LIMIT = 200
_MAX_LIMIT = 1000

TOOL_METADATA = {
    "tool_id": "list_directory",
    "description": (
        "List files and directories. Relative paths resolve against the workspace root; "
        "absolute and ~/ paths are allowed. Supports optional "
        "glob pattern filtering, recursive traversal, and paged continuation via "
        "`start_after`. Results are sorted by relative path and return structured "
        "entries plus truncation metadata."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path. Relative paths resolve against the workspace root; absolute and ~/ paths are allowed.",
                "default": ".",
            },
            "glob_pattern": {
                "type": "string",
                "description": "Glob pattern to filter entries (e.g. '*.py', '**/*.md').",
            },
            "recursive": {
                "type": "boolean",
                "description": "If true, list entries recursively.",
                "default": False,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum entries to return in this page (default 200, max 1000).",
                "default": 200,
            },
            "start_after": {
                "type": "string",
                "description": (
                    "Return only entries whose relative path sorts after this value. "
                    "Use the previous page's `next_start_after` to continue a truncated listing."
                ),
            },
        },
    },
    "examples": [
        {
            "input": {"path": "src", "glob_pattern": "*.py", "limit": 50},
            "output": {
                "entries": [{"name": "main.py", "path": "src/main.py", "type": "file", "size": 1234}],
                "count": 1,
                "total_count": 1,
                "remaining_count": 0,
                "truncated": False,
                "next_start_after": None,
            },
        },
    ],
    "category": "file",
    "returns": (
        "dict with entries (list of {name, path, type, size}), count (page size), "
        "total_count, remaining_count, truncated, limit, and next_start_after"
    ),
}


def _normalize_limit(limit: Any) -> int:
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    return max(1, min(parsed, _MAX_LIMIT))


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


async def list_directory(
    path: str = ".",
    glob_pattern: str | None = None,
    recursive: bool = False,
    limit: int = _DEFAULT_LIMIT,
    start_after: str | None = None,
    **_kwargs,
) -> dict:
    resolved = validate_path(path)

    if not os.path.isdir(resolved):
        raise FileNotFoundError(
            f"Directory not found: '{path}'. Check the path and try again."
        )

    dir_path = Path(resolved)
    root = Path(validate_path("."))

    if glob_pattern:
        if recursive:
            matches = list(dir_path.rglob(glob_pattern))
        else:
            matches = list(dir_path.glob(glob_pattern))
    elif recursive:
        matches = list(dir_path.rglob("*"))
    else:
        matches = list(dir_path.iterdir())

    effective_limit = _normalize_limit(limit)
    cursor = (start_after or "").strip()
    entries = []
    for p in sorted(matches):
        display_path = _display_path(p, root)
        if cursor and display_path <= cursor:
            continue
        entry = {
            "name": p.name,
            "path": display_path,
            "type": "directory" if p.is_dir() else "file",
            "size": p.stat().st_size if p.is_file() else 0,
        }
        entries.append(entry)
    total_count = len(entries)
    paged_entries = entries[:effective_limit]
    truncated = total_count > len(paged_entries)
    next_start_after = (
        str(paged_entries[-1]["path"])
        if truncated and paged_entries
        else None
    )

    return {
        "entries": paged_entries,
        "count": len(paged_entries),
        "total_count": total_count,
        "remaining_count": max(total_count - len(paged_entries), 0),
        "truncated": truncated,
        "limit": effective_limit,
        "start_after": cursor or None,
        "next_start_after": next_start_after,
        "sort_order": "path_asc",
    }
