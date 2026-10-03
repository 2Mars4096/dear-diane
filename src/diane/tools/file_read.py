"""Built-in tool: read file contents with optional line range."""

from __future__ import annotations

import os

from diane.tools._workspace import validate_path

TOOL_METADATA = {
    "tool_id": "file_read",
    "description": (
        "Read the contents of a file. Relative paths resolve against the workspace root; "
        "absolute and ~/ paths are allowed. Supports optional "
        "line-range selection (1-indexed) and configurable encoding. Prefer "
        "line windows over shell pattern matching for code inspection. "
        "Files larger than max_size bytes are rejected to prevent memory issues."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path. Relative paths resolve against the workspace root; absolute and ~/ paths are allowed.",
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
            "output": {
                "content": "print('hello')\n",
                "line_count": 1,
                "returned_line_count": 1,
                "total_line_count": 1,
                "size": 15,
                "file_size": 15,
                "path": "src/main.py",
            },
        },
        {
            "input": {"path": "README.md", "start_line": 1, "end_line": 5},
            "output": {
                "content": "# Project\n\nA short readme.\n",
                "line_count": 3,
                "returned_line_count": 3,
                "total_line_count": 3,
                "line_start": 1,
                "line_end": 3,
                "size": 27,
                "file_size": 27,
                "path": "README.md",
            },
        },
    ],
    "category": "file",
    "returns": "dict with content, returned/total line counts, byte counts, selected line range, and resolved path",
}

MAX_FILE_SIZE = 4_194_304  # 4 MB


def _normalize_line_number(value: int | None, *, name: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None
    if parsed < 1:
        raise ValueError(f"{name} must be >= 1")
    return parsed


async def file_read(
    path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    encoding: str = "utf-8",
    file_path: str | None = None,
    **_kwargs,
) -> dict:
    effective_path = str(path or file_path or "").strip()
    if not effective_path:
        raise TypeError("file_read() missing 1 required positional argument: 'path'")

    resolved = validate_path(effective_path)

    if not os.path.isfile(resolved):
        raise FileNotFoundError(
            f"File not found: '{effective_path}'. Check the path and try again."
        )

    normalized_start = _normalize_line_number(start_line, name="start_line")
    normalized_end = _normalize_line_number(end_line, name="end_line")
    if (
        normalized_start is not None
        and normalized_end is not None
        and normalized_end < normalized_start
    ):
        raise ValueError("end_line must be >= start_line")

    size = os.path.getsize(resolved)
    reading_range = normalized_start is not None or normalized_end is not None
    if size > MAX_FILE_SIZE and not reading_range:
        raise ValueError(
            f"File '{effective_path}' is {size:,} bytes (limit {MAX_FILE_SIZE:,}). "
            "Use start_line/end_line to read a portion."
        )

    if reading_range:
        first_line = normalized_start or 1
        last_line = normalized_end
        selected_lines: list[str] = []
        total_line_count = 0
        with open(resolved, encoding=encoding) as f:
            for line_number, line in enumerate(f, start=1):
                total_line_count = line_number
                if line_number < first_line:
                    continue
                if last_line is not None and line_number > last_line:
                    continue
                selected_lines.append(line)
        content = "".join(selected_lines)
        returned_line_count = len(selected_lines)
        line_start = first_line
        line_end = first_line + returned_line_count - 1 if returned_line_count else first_line - 1
    else:
        with open(resolved, encoding=encoding) as f:
            content = f.read()
        returned_line_count = len(content.splitlines())
        total_line_count = returned_line_count
        line_start = 1
        line_end = returned_line_count

    return {
        "content": content,
        "line_count": returned_line_count,
        "returned_line_count": returned_line_count,
        "total_line_count": total_line_count,
        "line_start": line_start,
        "line_end": line_end,
        "size": len(content.encode(encoding)),
        "file_size": size,
        "range_requested": reading_range,
        "path": effective_path,
    }
