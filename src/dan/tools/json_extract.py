"""Built-in tool: extract a value from JSON data using dot-notation path."""

from __future__ import annotations

import json
from typing import Any

TOOL_METADATA = {
    "tool_id": "json_extract",
    "description": (
        "Extract a value from a JSON string or dictionary using dot-notation "
        "path (e.g. 'a.b.c'). Supports nested objects and array indexing "
        "via numeric keys (e.g. 'items.0.name')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "data": {
                "description": "JSON string or dict to extract from.",
            },
            "path": {
                "type": "string",
                "description": "Dot-notation path to the value (e.g. 'user.address.city').",
            },
        },
        "required": ["data", "path"],
    },
    "examples": [
        {
            "input": {"data": '{"user": {"name": "Alice"}}', "path": "user.name"},
            "output": {"value": "Alice", "path": "user.name", "found": True},
        },
        {
            "input": {"data": {"items": [1, 2, 3]}, "path": "items.1"},
            "output": {"value": 2, "path": "items.1", "found": True},
        },
    ],
    "category": "utility",
    "returns": "dict with value, path, and found (bool)",
}


async def json_extract(data: Any, path: str, **_kwargs) -> dict:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid JSON string: {e}. Provide valid JSON or a dict."
            )

    keys = path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict):
            if key not in current:
                return {"value": None, "path": path, "found": False}
            current = current[key]
        elif isinstance(current, (list, tuple)):
            try:
                idx = int(key)
            except ValueError:
                return {"value": None, "path": path, "found": False}
            if idx < 0 or idx >= len(current):
                return {"value": None, "path": path, "found": False}
            current = current[idx]
        else:
            return {"value": None, "path": path, "found": False}

    return {"value": current, "path": path, "found": True}
