"""Port type inference for the markdown agent format."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DEFAULT_LLM_OUTPUT_SCHEMA: dict[str, Any] = {"type": "string"}


class SchemaLoadError(Exception):
    """Raised when a linked schema file cannot be loaded."""


_EXPLICIT_TYPES: dict[str, dict[str, Any]] = {
    "string": {"type": "string"},
    "str": {"type": "string"},
    "integer": {"type": "integer"},
    "int": {"type": "integer"},
    "number": {"type": "number"},
    "float": {"type": "number"},
    "boolean": {"type": "boolean"},
    "bool": {"type": "boolean"},
    "array": {"type": "array"},
    "list": {"type": "array"},
    "object": {"type": "object"},
    "dict": {"type": "object"},
}

_NAME_RULES: list[tuple[re.Pattern[str], dict[str, Any]]] = [
    (re.compile(r"^count$"), {"type": "integer"}),
    (re.compile(r"^num_.*$"), {"type": "integer"}),
    (re.compile(r".*_count$"), {"type": "integer"}),
    (re.compile(r".*_number$"), {"type": "integer"}),
    (re.compile(r"^is_.*$"), {"type": "boolean"}),
    (re.compile(r"^has_.*$"), {"type": "boolean"}),
    (re.compile(r".*_flag$"), {"type": "boolean"}),
    (re.compile(r".*_enabled$"), {"type": "boolean"}),
    (re.compile(r"^score$"), {"type": "number"}),
    (re.compile(r".*_score$"), {"type": "number"}),
    (re.compile(r"^temperature$"), {"type": "number"}),
    (re.compile(r".*_rate$"), {"type": "number"}),
    (re.compile(r".*_ratio$"), {"type": "number"}),
]

_TYPED_ARRAY_RE = re.compile(r"^\w+\[\]$")


def infer_port_schema(name: str, type_annotation: str = "string") -> dict[str, Any]:
    if type_annotation and type_annotation.strip().lower() not in ("", "string"):
        ann = type_annotation.strip().lower()
        if ann in _EXPLICIT_TYPES:
            return _EXPLICIT_TYPES[ann].copy()
        if _TYPED_ARRAY_RE.match(type_annotation.strip()):
            return {"type": "array", "items": {"type": "object"}}
        return {"type": "object", "description": f"Custom type: {type_annotation}"}
    return infer_schema_from_name(name)


def infer_schema_from_name(name: str) -> dict[str, Any]:
    if name.endswith("[]"):
        return {"type": "array"}
    for pattern, schema in _NAME_RULES:
        if pattern.search(name):
            return schema.copy()
    return {"type": "string"}


def load_linked_schema(schema_path: str, base_dir: Path) -> dict[str, Any]:
    resolved = (base_dir / schema_path).resolve()
    try:
        content = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise SchemaLoadError(f"Schema file not found: {resolved}") from e
    except OSError as e:
        raise SchemaLoadError(f"Cannot read schema file: {resolved}") from e
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise SchemaLoadError(f"Invalid JSON in schema file: {resolved}") from e
    if not isinstance(data, dict):
        raise SchemaLoadError(f"Schema file must contain a JSON object, got {type(data).__name__}")
    return data
