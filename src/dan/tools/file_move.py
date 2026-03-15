"""Built-in tool: move or rename a file or directory."""

from __future__ import annotations

import os
import shutil

from dan.tools._workspace import validate_path

TOOL_METADATA = {
    "tool_id": "file_move",
    "description": (
        "Move or rename a file or directory. Relative paths resolve against the workspace root; "
        "absolute and ~/ paths are allowed for both source and destination."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Source path. Relative paths resolve against the workspace root; absolute and ~/ paths are allowed.",
            },
            "destination": {
                "type": "string",
                "description": "Destination path. Relative paths resolve against the workspace root; absolute and ~/ paths are allowed.",
            },
        },
        "required": ["source", "destination"],
    },
    "examples": [
        {
            "input": {"source": "draft.md", "destination": "archive/draft.md"},
            "output": {"moved": True, "source": "draft.md", "destination": "archive/draft.md"},
        },
    ],
    "category": "file",
    "returns": "dict with moved (bool), source, destination",
}


async def file_move(source: str, destination: str, **_kwargs) -> dict:
    src = validate_path(source)
    dst = validate_path(destination)

    if not os.path.exists(src):
        raise FileNotFoundError(f"Source not found: '{source}'")

    dst_dir = os.path.dirname(dst)
    if dst_dir:
        os.makedirs(dst_dir, exist_ok=True)

    shutil.move(src, dst)
    return {"moved": True, "source": source, "destination": destination}
