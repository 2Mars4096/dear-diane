"""Built-in tool: targeted line-based edits to an existing file."""

from __future__ import annotations

import os

from dan.tools._workspace import validate_path

TOOL_METADATA = {
    "tool_id": "file_edit",
    "description": (
        "Edit an existing text file by line range. Relative paths resolve against the "
        "workspace root; absolute and ~/ paths are allowed. Supports replacing, deleting, "
        "or inserting content relative to 1-indexed line numbers, and can batch multiple "
        "non-overlapping edits to the same file in one call."
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
                "description": "Anchor or first target line (1-indexed). Required for all edit modes.",
            },
            "end_line": {
                "type": "integer",
                "description": "Last target line for replace/delete (1-indexed, inclusive). Defaults to start_line.",
            },
            "content": {
                "type": "string",
                "description": "Replacement or inserted content. Required for replace/insert modes.",
            },
            "mode": {
                "type": "string",
                "enum": ["replace", "insert_before", "insert_after", "delete"],
                "description": "Edit mode. 'replace' swaps the line range, 'delete' removes it, and insert modes add content around start_line.",
                "default": "replace",
            },
            "encoding": {
                "type": "string",
                "description": "File encoding.",
                "default": "utf-8",
            },
            "edits": {
                "type": "array",
                "description": (
                    "Optional batch form for multiple non-overlapping edits to the same file. "
                    "Each item uses the same fields as a single edit: start_line, optional "
                    "end_line, optional content, and optional mode."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "start_line": {
                            "type": "integer",
                            "description": "Anchor or first target line (1-indexed).",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Last target line for replace/delete (1-indexed, inclusive).",
                        },
                        "content": {
                            "type": "string",
                            "description": "Replacement or inserted content.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["replace", "insert_before", "insert_after", "delete"],
                            "description": "Edit mode for this batch item.",
                            "default": "replace",
                        },
                    },
                    "required": ["start_line"],
                },
            },
        },
        "required": ["path"],
        "anyOf": [
            {"required": ["start_line"]},
            {"required": ["edits"]},
        ],
    },
    "examples": [
        {
            "input": {
                "path": "src/main.py",
                "start_line": 10,
                "end_line": 12,
                "content": "print('patched')\n",
                "mode": "replace",
            },
            "output": {
                "path": "src/main.py",
                "mode": "replace",
                "start_line": 10,
                "end_line": 12,
                "bytes_written": 1234,
            },
        },
        {
            "input": {
                "path": "README.md",
                "start_line": 3,
                "content": "- inserted line\n",
                "mode": "insert_before",
            },
            "output": {
                "path": "README.md",
                "mode": "insert_before",
                "start_line": 3,
                "end_line": 3,
                "bytes_written": 456,
            },
        },
        {
            "input": {
                "path": "src/main.py",
                "edits": [
                    {"start_line": 10, "end_line": 10, "content": "renamed = True\n", "mode": "replace"},
                    {"start_line": 30, "content": "# trailing note\n", "mode": "insert_after"},
                ],
            },
            "output": {
                "path": "src/main.py",
                "mode": "batch",
                "edit_count": 2,
                "bytes_written": 1400,
            },
        },
    ],
    "category": "file",
    "returns": "dict with path, mode, edited line bounds, and final byte count",
}

_EDIT_MODES = frozenset({"replace", "insert_before", "insert_after", "delete"})


def _require_existing_file(path: str, resolved: str) -> None:
    if not os.path.isfile(resolved):
        raise FileNotFoundError(
            f"File not found: '{path}'. Use file_write to create a new file first."
        )


def _normalize_line_number(value: int | None, *, name: str) -> int:
    if value is None:
        raise TypeError(f"file_edit() missing 1 required positional argument: '{name}'")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None
    if parsed < 1:
        raise ValueError(f"{name} must be >= 1")
    return parsed


def _replacement_lines(content: str) -> list[str]:
    return content.splitlines(keepends=True)


def _normalize_mode(value: str | None, *, name: str = "mode") -> str:
    normalized = str(value or "replace").strip()
    if normalized not in _EDIT_MODES:
        raise ValueError(
            f"Invalid {name} '{value}'. Use 'replace', 'insert_before', 'insert_after', or 'delete'."
        )
    return normalized


def _build_edit_spec(
    *,
    start_line: int | None,
    end_line: int | None,
    content: str | None,
    mode: str | None,
    label: str,
) -> dict[str, object]:
    field_prefix = "" if label == "edit" else f"{label}."
    normalized_mode = _normalize_mode(mode, name=f"{field_prefix}mode")
    anchor_line = _normalize_line_number(start_line, name=f"{field_prefix}start_line")
    replacement_text = "" if content is None else str(content)
    if normalized_mode != "delete" and content is None:
        raise TypeError(
            f"file_edit() missing 1 required positional argument: '{field_prefix}content'"
        )

    normalized_end: int
    if normalized_mode in {"replace", "delete"}:
        normalized_end = _normalize_line_number(
            end_line if end_line is not None else anchor_line,
            name=f"{field_prefix}end_line",
        )
        if normalized_end < anchor_line:
            raise ValueError(f"{field_prefix}end_line must be >= {field_prefix}start_line")
    else:
        normalized_end = anchor_line

    return {
        "mode": normalized_mode,
        "start_line": anchor_line,
        "end_line": normalized_end,
        "replacement_lines": _replacement_lines(replacement_text),
    }


def _validate_edit_bounds(spec: dict[str, object], *, total_lines_before: int) -> None:
    mode = str(spec["mode"])
    start_line = int(spec["start_line"])
    end_line = int(spec["end_line"])

    if mode in {"replace", "delete"}:
        if start_line > total_lines_before or end_line > total_lines_before:
            raise ValueError(
                f"Requested line range {start_line}-{end_line} is outside the file bounds 1-{total_lines_before}."
            )
        return

    if mode == "insert_before":
        if start_line > total_lines_before + 1:
            raise ValueError(
                f"Cannot insert before line {start_line}; valid range is 1-{total_lines_before + 1}."
            )
        return

    if start_line > total_lines_before:
        raise ValueError(
            f"Cannot insert after line {start_line}; valid range is 1-{total_lines_before}."
        )


def _occupied_range(spec: dict[str, object]) -> tuple[int, int]:
    return int(spec["start_line"]), int(spec["end_line"])


def _ensure_non_overlapping_edits(specs: list[dict[str, object]]) -> None:
    previous_end: int | None = None
    for spec in sorted(specs, key=_occupied_range):
        start_line, end_line = _occupied_range(spec)
        if previous_end is not None and start_line <= previous_end:
            raise ValueError(
                "Batched file_edit calls require non-overlapping edits with distinct anchor ranges."
            )
        previous_end = end_line


def _apply_edit_to_lines(
    lines: list[str],
    *,
    mode: str,
    start_line: int,
    end_line: int,
    replacement_lines: list[str],
) -> list[str]:
    if mode == "replace":
        return [
            *lines[: start_line - 1],
            *replacement_lines,
            *lines[end_line:],
        ]
    if mode == "delete":
        return [
            *lines[: start_line - 1],
            *lines[end_line:],
        ]
    if mode == "insert_before":
        insert_at = start_line - 1
        return [
            *lines[:insert_at],
            *replacement_lines,
            *lines[insert_at:],
        ]

    insert_at = start_line
    return [
        *lines[:insert_at],
        *replacement_lines,
        *lines[insert_at:],
    ]


async def file_edit(
    path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    content: str | None = None,
    mode: str = "replace",
    encoding: str = "utf-8",
    file_path: str | None = None,
    edits: list[dict[str, object]] | None = None,
    **_kwargs,
) -> dict:
    effective_path = str(path or file_path or "").strip()
    if not effective_path:
        raise TypeError("file_edit() missing 1 required positional argument: 'path'")

    resolved = validate_path(effective_path, operation="write")
    _require_existing_file(effective_path, resolved)

    if edits is not None:
        if any(value is not None for value in (start_line, end_line, content)) or mode != "replace":
            raise ValueError(
                "Use either top-level start_line/end_line/content/mode or batched edits=..., not both."
            )
        if not isinstance(edits, list) or not edits:
            raise ValueError("edits must be a non-empty list when provided.")
        edit_specs = []
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                raise ValueError(f"edits[{index}] must be an object.")
            edit_specs.append(
                _build_edit_spec(
                    start_line=edit.get("start_line"),
                    end_line=edit.get("end_line"),
                    content=edit.get("content"),
                    mode=edit.get("mode"),
                    label=f"edits[{index}]",
                )
            )
    else:
        edit_specs = [
            _build_edit_spec(
                start_line=start_line,
                end_line=end_line,
                content=content,
                mode=mode,
                label="edit",
            )
        ]

    with open(resolved, encoding=encoding) as f:
        original_text = f.read()
    original_lines = original_text.splitlines(keepends=True)
    total_lines_before = len(original_lines)
    if total_lines_before == 0:
        raise ValueError("Cannot apply line-based edits to an empty file. Use file_write instead.")

    for spec in edit_specs:
        _validate_edit_bounds(spec, total_lines_before=total_lines_before)
    if len(edit_specs) > 1:
        _ensure_non_overlapping_edits(edit_specs)

    updated_lines = list(original_lines)
    for spec in sorted(edit_specs, key=lambda spec: _occupied_range(spec), reverse=True):
        updated_lines = _apply_edit_to_lines(
            updated_lines,
            mode=str(spec["mode"]),
            start_line=int(spec["start_line"]),
            end_line=int(spec["end_line"]),
            replacement_lines=list(spec["replacement_lines"]),
        )

    updated_text = "".join(updated_lines)
    with open(resolved, "w", encoding=encoding) as f:
        f.write(updated_text)

    if len(edit_specs) == 1:
        spec = edit_specs[0]
        return {
            "path": effective_path,
            "mode": str(spec["mode"]),
            "start_line": int(spec["start_line"]),
            "end_line": int(spec["end_line"]),
            "total_lines_before": total_lines_before,
            "total_lines_after": len(updated_lines),
            "bytes_written": len(updated_text.encode(encoding)),
        }

    return {
        "path": effective_path,
        "mode": "batch",
        "start_line": min(int(spec["start_line"]) for spec in edit_specs),
        "end_line": max(int(spec["end_line"]) for spec in edit_specs),
        "edit_count": len(edit_specs),
        "applied_edits": [
            {
                "mode": str(spec["mode"]),
                "start_line": int(spec["start_line"]),
                "end_line": int(spec["end_line"]),
            }
            for spec in edit_specs
        ],
        "total_lines_before": total_lines_before,
        "total_lines_after": len(updated_lines),
        "bytes_written": len(updated_text.encode(encoding)),
    }
