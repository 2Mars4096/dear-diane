"""Built-in tool: copy a file or directory."""

from __future__ import annotations

import os
import shutil

from dan.tools._workspace import validate_path

TOOL_METADATA = {
    "tool_id": "file_copy",
    "description": (
        "Copy a file or directory within the workspace. "
        "Use recursive=true for directories. "
        "Both source and destination must be within the workspace root."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Path to the source file or directory.",
            },
            "destination": {
                "type": "string",
                "description": "Path for the copy.",
            },
            "recursive": {
                "type": "boolean",
                "description": "If true, copy directories recursively.",
                "default": False,
            },
        },
        "required": ["source", "destination"],
    },
    "examples": [
        {
            "input": {"source": "report.md", "destination": "backup/report.md"},
            "output": {"copied": True, "source": "report.md", "destination": "backup/report.md"},
        },
    ],
    "category": "file",
    "returns": "dict with copied (bool), source, destination",
}


async def file_copy(source: str, destination: str, recursive: bool = False, **_kwargs) -> dict:
    src = validate_path(source)
    dst = validate_path(destination)

    if not os.path.exists(src):
        raise FileNotFoundError(f"Source not found: '{source}'")

    dst_dir = os.path.dirname(dst)
    if dst_dir:
        os.makedirs(dst_dir, exist_ok=True)

    if os.path.isdir(src):
        if not recursive:
            raise ValueError(
                f"'{source}' is a directory. Set recursive=true to copy directories."
            )
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)

    return {"copied": True, "source": source, "destination": destination}
