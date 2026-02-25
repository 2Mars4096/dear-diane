"""Built-in tool: read file contents with optional line range."""

from __future__ import annotations

import os

from dan.tools._workspace import validate_path

TOOL_METADATA = {
    "tool_id": "file_read",
    "description": (
        "Read the contents of a file within the workspace. Supports optional "
        "line-range selection (1-indexed) and configurable encoding. "
        "Files larger than max_size bytes are rejected to prevent memory issues."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file (resolved against workspace root).",
            },
            "start_line": {
                "type": "integer",
                "description": "First line to include (1-indexed). Omit to start from the beginning.",
            },
            "end_line": {
                "type": "integer",
                "description": "Last line to include (1-indexed, inclusive). Omit to read to the end.",
            },
            "encoding": {
                "type": "string",
                "description": "File encoding.",
                "default": "utf-8",
            },
        },
        "required": ["path"],
    },
    "examples": [
        {
            "input": {"path": "src/main.py"},
            "output": {"content": "print('hello')\n", "line_count": 1, "size": 15, "path": "src/main.py"},
        },
        {
            "input": {"path": "README.md", "start_line": 1, "end_line": 5},
            "output": {"content": "# Project\n\nA short readme.\n", "line_count": 3, "size": 27, "path": "README.md"},
        },
    ],
    "category": "file",
    "returns": "dict with content, line_count, size, and resolved path",
}

MAX_FILE_SIZE = 1_048_576  # 1 MB


async def file_read(
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    encoding: str = "utf-8",
    **_kwargs,
) -> dict:
    resolved = validate_path(path)

    if not os.path.isfile(resolved):
        raise FileNotFoundError(
            f"File not found: '{path}'. Check the path and try again."
        )

    size = os.path.getsize(resolved)
    if size > MAX_FILE_SIZE:
        raise ValueError(
            f"File '{path}' is {size:,} bytes (limit {MAX_FILE_SIZE:,}). "
            "Use start_line/end_line to read a portion."
        )

    with open(resolved, encoding=encoding) as f:
        lines = f.readlines()

    if start_line is not None or end_line is not None:
        s = (start_line or 1) - 1
        e = end_line if end_line is not None else len(lines)
        lines = lines[max(s, 0) : e]

    content = "".join(lines)
    return {
        "content": content,
        "line_count": len(lines),
        "size": len(content.encode(encoding)),
        "path": path,
    }
