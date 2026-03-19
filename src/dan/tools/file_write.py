"""Built-in tool: write or append content to a file."""

from __future__ import annotations

import os

from dan.tools._workspace import validate_path

TOOL_METADATA = {
    "tool_id": "file_write",
    "description": (
        "Write or append content to a file. Relative paths resolve against the workspace root; "
        "absolute and ~/ paths are allowed. "
        "Automatically creates parent directories if they don't exist. "
        "Use mode='append' to add content to the end of an existing file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path. Relative paths resolve against the workspace root; absolute and ~/ paths are allowed.",
            },
            "content": {
                "type": "string",
                "description": "Content to write to the file.",
            },
            "mode": {
                "type": "string",
                "enum": ["overwrite", "append"],
                "description": "Write mode: 'overwrite' replaces the file, 'append' adds to the end.",
                "default": "overwrite",
            },
            "encoding": {
                "type": "string",
                "description": "File encoding.",
                "default": "utf-8",
            },
        },
        "required": ["path", "content"],
    },
    "examples": [
        {
            "input": {"path": "output/result.txt", "content": "Hello, world!\n"},
            "output": {"bytes_written": 14, "path": "output/result.txt", "mode": "overwrite"},
        },
        {
            "input": {"path": "log.txt", "content": "new entry\n", "mode": "append"},
            "output": {"bytes_written": 10, "path": "log.txt", "mode": "append"},
        },
    ],
    "category": "file",
    "returns": "dict with bytes_written, path, and mode",
}


async def file_write(
    path: str,
    content: str,
    mode: str = "overwrite",
    encoding: str = "utf-8",
    **_kwargs,
) -> dict:
    resolved = validate_path(path, operation="write")

    if mode not in ("overwrite", "append"):
        raise ValueError(
            f"Invalid mode '{mode}'. Use 'overwrite' or 'append'."
        )

    os.makedirs(os.path.dirname(resolved), exist_ok=True)

    file_mode = "a" if mode == "append" else "w"
    with open(resolved, file_mode, encoding=encoding) as f:
        f.write(content)

    return {
        "bytes_written": len(content.encode(encoding)),
        "path": path,
        "mode": mode,
    }
