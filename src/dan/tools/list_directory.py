"""Built-in tool: list directory contents with optional glob filtering."""

from __future__ import annotations

import os
from pathlib import Path

from dan.tools._workspace import validate_path

TOOL_METADATA = {
    "tool_id": "list_directory",
    "description": (
        "List files and directories. Relative paths resolve against the workspace root; "
        "absolute and ~/ paths are allowed. Supports optional "
        "glob pattern filtering and recursive traversal. "
        "Returns structured entries with name, type, and size."
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
        },
    },
    "examples": [
        {
            "input": {"path": "src", "glob_pattern": "*.py"},
            "output": {
                "entries": [{"name": "main.py", "path": "src/main.py", "type": "file", "size": 1234}],
                "count": 1,
            },
        },
    ],
    "category": "file",
    "returns": "dict with entries (list of {name, path, type, size}) and count",
}


async def list_directory(
    path: str = ".",
    glob_pattern: str | None = None,
    recursive: bool = False,
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

    entries = []
    for p in sorted(matches):
        try:
            display_path = str(p.relative_to(root))
        except ValueError:
            display_path = str(p)
        entry = {
            "name": p.name,
            "path": display_path,
            "type": "directory" if p.is_dir() else "file",
            "size": p.stat().st_size if p.is_file() else 0,
        }
        entries.append(entry)

    return {"entries": entries, "count": len(entries)}
