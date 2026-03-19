"""Built-in tool: delete a file or directory."""

from __future__ import annotations

import os
import shutil

from dan.tools._workspace import validate_path

TOOL_METADATA = {
    "tool_id": "file_delete",
    "description": (
        "Delete a file or directory. Relative paths resolve against the workspace root; "
        "absolute and ~/ paths are allowed. "
        "For directories, set recursive=true (otherwise only empty directories are removed)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file or directory to delete. Relative paths resolve against the workspace root; absolute and ~/ paths are allowed.",
            },
            "recursive": {
                "type": "boolean",
                "description": "If true, delete non-empty directories recursively.",
                "default": False,
            },
        },
        "required": ["path"],
    },
    "examples": [
        {
            "input": {"path": "temp/output.txt"},
            "output": {"deleted": True, "path": "temp/output.txt"},
        },
    ],
    "category": "file",
    "returns": "dict with deleted (bool) and path",
}


async def file_delete(path: str, recursive: bool = False, **_kwargs) -> dict:
    resolved = validate_path(path, operation="delete")

    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Path not found: '{path}'")

    if os.path.isdir(resolved):
        if recursive:
            shutil.rmtree(resolved)
        else:
            try:
                os.rmdir(resolved)
            except OSError:
                raise ValueError(
                    f"Directory '{path}' is not empty. Set recursive=true to delete it and all contents."
                )
    else:
        os.remove(resolved)

    return {"deleted": True, "path": path}
