"""Built-in tool: targeted line-based edits to an existing file."""

from __future__ import annotations

import ast
import os

from dan.tools._atomic_file import atomic_write_text
from dan.tools._source_structure import suspicious_source_structure_issues
from dan.tools._workspace import validate_path

_SOURCE_SHRINK_GUARD_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".gd",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
}

TOOL_METADATA = {
    "tool_id": "file_edit",
    "description": (
        "Edit an existing text file by line range. Relative paths resolve against the "
        "workspace root; absolute and ~/ paths are allowed. Supports replacing, deleting, "
        "or inserting content relative to 1-indexed line numbers, and can batch multiple "
        "non-overlapping edits to the same file in one call. Prefer explicit line ranges "
        "derived from prior reads over regex or exact-text matching."
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
                "description": (
                    "Last target line for replace/delete (1-indexed, inclusive). "
                    "Defaults to start_line for single-line edits. Multi-line replace "
                    "calls can infer a wider range from old_string or content length "
                    "when end_line is omitted."
                ),
            },
            "content": {
                "type": "string",
                "description": "Replacement or inserted content. Required for replace/insert modes; invalid with delete mode.",
            },
            "old_string": {
                "type": "string",
                "description": (
                    "Compatibility replace form: exact existing text to replace. "
                    "Use only when copied from a recent file_read and the text appears once. "
                    "This implies replace mode; do not combine it with delete mode."
                ),
            },
            "new_string": {
                "type": "string",
                "description": "Compatibility replace form: replacement text for old_string. Implies replace mode.",
            },
            "mode": {
                "type": "string",
                "enum": ["replace", "insert_before", "insert_after", "delete"],
                "description": (
                    "Edit mode. 'replace' swaps the line range using content, insert modes add content around "
                    "start_line, and 'delete' removes text only. Delete mode must not include content or new_string."
                ),
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
                    "Each item uses either line-based fields (start_line, optional end_line, "
                    "content/mode) or compatibility replace fields (old_string plus new_string)."
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
                            "description": "Replacement or inserted content. Required for replace/insert modes; invalid with delete mode.",
                        },
                        "old_string": {
                            "type": "string",
                            "description": (
                                "Compatibility replace form: exact existing text to replace. "
                                "Use only when copied from a recent file_read and unique in the file. "
                                "This implies replace mode; do not combine it with delete mode."
                            ),
                        },
                        "new_string": {
                            "type": "string",
                            "description": "Compatibility replace form: replacement text for old_string. Implies replace mode.",
                        },
                        "replace": {
                            "type": "string",
                            "description": "Alias for content in batched replace edits.",
                        },
                        "replacement": {
                            "type": "string",
                            "description": "Alias for content in batched replace edits.",
                        },
                        "new_content": {
                            "type": "string",
                            "description": "Alias for content in batched replace edits.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": [
                                "replace",
                                "insert_before",
                                "insert_after",
                                "delete",
                            ],
                            "description": "Edit mode for this batch item. Delete mode removes text only and must not include replacement fields.",
                            "default": "replace",
                        },
                    },
                    "anyOf": [
                        {"required": ["start_line"]},
                        {"required": ["old_string", "new_string"]},
                        {"required": ["old_string", "content"]},
                    ],
                },
            },
        },
        "required": ["path"],
        "anyOf": [
            {"required": ["start_line"]},
            {"required": ["edits"]},
            {"required": ["old_string", "new_string"]},
            {"required": ["old_string", "content"]},
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
                    {
                        "start_line": 10,
                        "end_line": 10,
                        "content": "renamed = True\n",
                        "mode": "replace",
                    },
                    {
                        "start_line": 30,
                        "content": "# trailing note\n",
                        "mode": "insert_after",
                    },
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
_REPLACEMENT_FIELD_NAMES = (
    "content",
    "new_string",
    "replace",
    "replacement",
    "new_content",
)


def _tool_argument_error(detail: str) -> ValueError:
    message = str(detail or "").strip() or "invalid arguments for file_edit"
    if message.startswith("tool_arguments_invalid:"):
        return ValueError(message)
    return ValueError(f"tool_arguments_invalid: {message}")


def _require_existing_file(path: str, resolved: str) -> None:
    if not os.path.isfile(resolved):
        raise FileNotFoundError(
            f"File not found: '{path}'. Use file_write to create a new file first."
        )


def _normalize_line_number(value: int | None, *, name: str) -> int:
    if value is None:
        raise _tool_argument_error(f"missing required arguments for file_edit: {name}")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise _tool_argument_error(
            f"invalid arguments for file_edit: {name} must be an integer"
        ) from None
    if parsed < 1:
        raise _tool_argument_error(
            f"invalid arguments for file_edit: {name} must be >= 1"
        )
    return parsed


def _replacement_lines(content: str) -> list[str]:
    return content.splitlines(keepends=True)


def _line_text(line: str) -> str:
    return line.rstrip("\r\n")


def _line_count(text: str) -> int:
    return len(text.splitlines())


def _find_unique_span(text: str, needle: str) -> tuple[int, int]:
    if not needle:
        raise _tool_argument_error(
            "invalid arguments for file_edit: old_string must not be empty when used with file_edit compatibility mode."
        )
    start_index = text.find(needle)
    if start_index < 0:
        raise _tool_argument_error(
            "invalid arguments for file_edit: old_string was not found in the target file. Provide start_line/end_line explicitly."
        )
    duplicate_index = text.find(needle, start_index + 1)
    if duplicate_index >= 0:
        raise _tool_argument_error(
            "invalid arguments for file_edit: old_string matched multiple locations in the target file. Provide start_line/end_line explicitly."
        )
    return start_index, start_index + len(needle)


def _line_range_from_span(
    text: str, start_index: int, end_index: int
) -> tuple[int, int]:
    start_line = len(text[:start_index].splitlines()) + 1
    end_line = max(start_line, len(text[:end_index].splitlines()))
    return start_line, end_line


def _has_replacement_field(container: dict[str, object], *field_names: str) -> str:
    for field_name in field_names:
        if field_name in container and container.get(field_name) is not None:
            return field_name
    return ""


def _normalize_replace_compatibility_args(
    *,
    original_text: str,
    start_line: int | None,
    end_line: int | None,
    content: str | None,
    mode: str,
    old_string: object | None,
    new_string: object | None,
) -> tuple[int | None, int | None, str | None]:
    normalized_mode = str(mode or "replace").strip()
    normalized_content = content if content is not None else None
    if normalized_content is None and new_string is not None:
        normalized_content = str(new_string)
    old_text = str(old_string) if old_string is not None else ""
    if old_text and normalized_content is not None and normalized_mode != "replace":
        raise _tool_argument_error(
            "invalid argument combination for file_edit: old_string plus new_string/content is a replacement "
            f"compatibility form and cannot be combined with mode '{normalized_mode}'. "
            "Retry with mode 'replace' or omit mode; use delete mode only when removing text without replacement fields."
        )
    if normalized_mode != "replace":
        return start_line, end_line, normalized_content

    normalized_start = (
        _normalize_line_number(start_line, name="start_line")
        if start_line is not None
        else None
    )
    normalized_end = (
        _normalize_line_number(end_line, name="end_line")
        if end_line is not None
        else None
    )

    if old_text:
        if normalized_start is None:
            match_start, match_end = _find_unique_span(original_text, old_text)
            normalized_start, inferred_end = _line_range_from_span(
                original_text,
                match_start,
                match_end,
            )
            if normalized_end is None:
                normalized_end = inferred_end
        elif normalized_end is None:
            normalized_end = normalized_start + max(_line_count(old_text), 1) - 1

    if (
        normalized_start is not None
        and normalized_end is None
        and normalized_content is not None
    ):
        replacement_line_count = _line_count(normalized_content)
        if replacement_line_count > 1:
            normalized_end = normalized_start + replacement_line_count - 1

    return normalized_start, normalized_end, normalized_content


def _normalize_mode(value: str | None, *, name: str = "mode") -> str:
    normalized = str(value or "replace").strip()
    if normalized not in _EDIT_MODES:
        raise _tool_argument_error(
            f"invalid arguments for file_edit: invalid {name} '{value}'. Use 'replace', 'insert_before', 'insert_after', or 'delete'."
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
    if start_line is None:
        if label == "edit":
            raise _tool_argument_error(
                "missing required arguments for file_edit: start_line or old_string"
            )
        raise _tool_argument_error(
            f"missing required arguments for file_edit: {label} must include start_line or old_string"
        )
    anchor_line = _normalize_line_number(start_line, name=f"{field_prefix}start_line")
    replacement_text = "" if content is None else str(content)
    if normalized_mode == "delete" and content is not None:
        raise _tool_argument_error(
            "invalid argument combination for file_edit: delete mode removes text only and must not include "
            "content/new_string/replace/replacement/new_content. If you intend to swap text, retry with mode 'replace'."
        )
    if normalized_mode != "delete" and content is None:
        raise _tool_argument_error(
            f"missing required arguments for file_edit: {field_prefix}content"
        )

    normalized_end: int
    if normalized_mode in {"replace", "delete"}:
        normalized_end = _normalize_line_number(
            end_line if end_line is not None else anchor_line,
            name=f"{field_prefix}end_line",
        )
        if normalized_end < anchor_line:
            raise _tool_argument_error(
                f"invalid arguments for file_edit: {field_prefix}end_line must be >= {field_prefix}start_line"
            )
    else:
        normalized_end = anchor_line

    return {
        "mode": normalized_mode,
        "start_line": anchor_line,
        "end_line": normalized_end,
        "replacement_lines": _replacement_lines(replacement_text),
    }


def _edit_content_argument(edit: dict[str, object]) -> str | None:
    if "content" in edit:
        value = edit.get("content")
        return None if value is None else str(value)
    for alias in ("replace", "replacement", "new_content", "new_string"):
        if alias in edit:
            value = edit.get(alias)
            return None if value is None else str(value)
    return None


def _validate_edit_bounds(spec: dict[str, object], *, total_lines_before: int) -> None:
    mode = str(spec["mode"])
    start_line = int(spec["start_line"])
    end_line = int(spec["end_line"])

    if mode in {"replace", "delete"}:
        if start_line > total_lines_before or end_line > total_lines_before:
            raise _tool_argument_error(
                "invalid arguments for file_edit: "
                f"requested line range {start_line}-{end_line} is outside the file bounds 1-{total_lines_before}."
            )
        return

    if mode == "insert_before":
        if start_line > total_lines_before + 1:
            raise _tool_argument_error(
                "invalid arguments for file_edit: "
                f"cannot insert before line {start_line}; valid range is 1-{total_lines_before + 1}."
            )
        return

    if start_line > total_lines_before:
        raise _tool_argument_error(
            "invalid arguments for file_edit: "
            f"cannot insert after line {start_line}; valid range is 1-{total_lines_before}."
        )


def _clamp_replace_delete_end_at_eof(
    spec: dict[str, object],
    *,
    total_lines_before: int,
) -> dict[str, object]:
    if str(spec["mode"]) not in {"replace", "delete"}:
        return spec
    start_line = int(spec["start_line"])
    end_line = int(spec["end_line"])
    if start_line > total_lines_before or end_line <= total_lines_before:
        return spec
    return {**spec, "end_line": total_lines_before}


def _preserve_line_boundary(
    spec: dict[str, object],
    *,
    total_lines_before: int,
) -> dict[str, object]:
    replacement_lines = list(spec["replacement_lines"])
    if not replacement_lines or replacement_lines[-1].endswith("\n"):
        return spec

    mode = str(spec["mode"])
    start_line = int(spec["start_line"])
    end_line = int(spec["end_line"])
    needs_trailing_newline = (
        (mode == "replace" and end_line < total_lines_before)
        or (mode == "insert_before" and start_line <= total_lines_before)
        or (mode == "insert_after" and start_line < total_lines_before)
    )
    if not needs_trailing_newline:
        return spec

    return {
        **spec,
        "replacement_lines": [
            *replacement_lines[:-1],
            replacement_lines[-1] + "\n",
        ],
    }


def _reinterpret_anchor_heavy_replace(
    spec: dict[str, object],
    *,
    original_lines: list[str],
) -> dict[str, object]:
    if str(spec["mode"]) != "replace":
        return spec

    start_line = int(spec["start_line"])
    end_line = int(spec["end_line"])
    replacement_lines = list(spec["replacement_lines"])
    target_lines = original_lines[start_line - 1 : end_line]
    target_line_count = end_line - start_line + 1

    if (
        not replacement_lines
        or not target_lines
        or len(replacement_lines) <= target_line_count
    ):
        return spec

    first_target_line = target_lines[0]
    if start_line == end_line and _line_text(replacement_lines[0]) == _line_text(
        first_target_line
    ):
        return {
            **spec,
            "mode": "insert_after",
            "replacement_lines": replacement_lines[1:],
        }

    if start_line <= 1 or len(replacement_lines) < 3:
        return spec

    previous_line = original_lines[start_line - 2]
    if _line_text(replacement_lines[0]) == _line_text(previous_line):
        trailing_lines = replacement_lines[1:]
        for offset in range(1, len(trailing_lines)):
            candidate_suffix = trailing_lines[offset:]
            shared_prefix_length = 0
            for replacement_line, target_line in zip(candidate_suffix, target_lines):
                if _line_text(replacement_line) != _line_text(target_line):
                    break
                shared_prefix_length += 1
            if shared_prefix_length >= 2 and shared_prefix_length == len(
                candidate_suffix
            ):
                return {
                    **spec,
                    "mode": "insert_after",
                    "start_line": start_line - 1,
                    "end_line": start_line - 1,
                    "replacement_lines": trailing_lines[:offset],
                }

    if _line_text(replacement_lines[0]) == _line_text(previous_line) and _line_text(
        replacement_lines[-1]
    ) == _line_text(first_target_line):
        return {
            **spec,
            "mode": "insert_after",
            "start_line": start_line - 1,
            "end_line": start_line - 1,
            "replacement_lines": replacement_lines[1:-1],
        }

    return spec


def _occupied_range(spec: dict[str, object]) -> tuple[int, int]:
    return int(spec["start_line"]), int(spec["end_line"])


def _ensure_non_overlapping_edits(specs: list[dict[str, object]]) -> None:
    previous_end: int | None = None
    for spec in sorted(specs, key=_occupied_range):
        start_line, end_line = _occupied_range(spec)
        if previous_end is not None and start_line <= previous_end:
            raise _tool_argument_error(
                "invalid argument combination for file_edit: batched file_edit calls require non-overlapping edits with distinct anchor ranges."
            )
        previous_end = end_line


def _guard_suspicious_bulk_replace(
    spec: dict[str, object],
    *,
    path: str,
    original_text: str,
    original_lines: list[str],
) -> None:
    if str(spec["mode"]) != "replace":
        return

    start_line = int(spec["start_line"])
    end_line = int(spec["end_line"])
    target_lines = original_lines[start_line - 1 : end_line]
    target_line_count = len(target_lines)
    replacement_text = "".join(list(spec["replacement_lines"])).strip()
    replacement_line_count = len(replacement_text.splitlines())
    replacement_char_count = len(replacement_text)
    target_char_count = max(len("".join(target_lines).strip()), 1)
    char_ratio = replacement_char_count / target_char_count
    original_prefix = original_text.strip()[:160]

    if (
        len(original_lines) >= 40
        and target_line_count <= max(10, int(len(original_lines) * 0.1))
        and replacement_line_count >= int(len(original_lines) * 0.75)
        and len(original_prefix) >= 40
        and replacement_text.startswith(original_prefix)
    ):
        raise _tool_argument_error(
            "invalid edit shape for file_edit: suspicious whole-file content in a narrow "
            f"replacement range ({start_line}-{end_line}). This would duplicate the "
            "unchanged suffix. Use file_write for an intentional whole-file replacement "
            "or apply a smaller targeted edit."
        )

    if target_line_count < 20:
        return

    if (
        replacement_line_count <= 3
        and replacement_char_count <= 120
        and char_ratio <= 0.1
    ):
        raise _tool_argument_error(
            "invalid edit shape for file_edit: Suspicious bulk replace: the requested replacement collapses a large line range "
            f"({start_line}-{end_line}) into very little content. Use smaller targeted edits, "
            "or use delete mode plus a separate insert when intentionally removing a large block."
        )

    if not path.endswith(".py"):
        return

    if target_line_count < 20:
        return

    updated_lines = _apply_edit_to_lines(
        list(original_lines),
        mode="replace",
        start_line=start_line,
        end_line=end_line,
        replacement_lines=list(spec["replacement_lines"]),
    )

    try:
        original_tree = ast.parse(original_text)
    except SyntaxError:
        return

    def _top_level_named_blocks(tree: ast.Module) -> list[tuple[str, int, int]]:
        blocks: list[tuple[str, int, int]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
            elif isinstance(node, ast.Assign):
                if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                    continue
                name = node.targets[0].id
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id
            else:
                continue

            node_start = getattr(node, "lineno", None)
            node_end = getattr(node, "end_lineno", None)
            if node_start is None or node_end is None:
                continue
            blocks.append((name, node_start, node_end))
        return blocks

    original_blocks = _top_level_named_blocks(original_tree)
    intersecting_blocks = [
        name
        for name, block_start, block_end in original_blocks
        if not (block_end < start_line or block_start > end_line)
    ]
    if len(intersecting_blocks) < 2:
        return

    updated_text = "".join(updated_lines)
    try:
        updated_tree = ast.parse(updated_text)
    except SyntaxError:
        raise _tool_argument_error(
            "invalid edit shape for file_edit: Suspicious structural replace: the requested replacement spans multiple "
            "top-level Python blocks and leaves the file syntactically invalid. Use smaller targeted edits so adjacent "
            "definitions remain intact."
        ) from None

    updated_block_names = {name for name, _, _ in _top_level_named_blocks(updated_tree)}
    removed_blocks = [
        name for name in intersecting_blocks if name not in updated_block_names
    ]
    if removed_blocks:
        removed_preview = ", ".join(removed_blocks[:3])
        raise _tool_argument_error(
            "invalid edit shape for file_edit: Suspicious structural replace: the requested replacement spans multiple "
            f"top-level Python blocks and removes {removed_preview}. Use smaller targeted "
            "edits so adjacent definitions remain intact."
        )


def _guard_placeholder_style_python_replace(
    spec: dict[str, object],
    *,
    path: str,
) -> None:
    if not path.endswith(".py"):
        return
    if str(spec["mode"]) not in {"replace", "insert_before", "insert_after"}:
        return

    replacement_text = "".join(list(spec["replacement_lines"])).strip()
    if not replacement_text:
        return

    normalized = replacement_text.lower()
    if (
        "shell command output placeholder" in normalized
        or "git restore placeholder" in normalized
    ):
        raise _tool_argument_error(
            "invalid edit shape for file_edit: Suspicious placeholder-style content for Python source. "
            "Apply the real code change instead of shell-note or restore-placeholder text."
        )

    nonempty_lines = [
        line.strip() for line in replacement_text.splitlines() if line.strip()
    ]
    if not nonempty_lines:
        return
    if any(line == "# placeholder" for line in nonempty_lines) or (
        "placeholder" in normalized
        and all(line.startswith("#") for line in nonempty_lines)
        and len(nonempty_lines) <= 3
    ):
        raise _tool_argument_error(
            "invalid edit shape for file_edit: Suspicious placeholder-style content for Python source. "
            "Apply the real code change instead of placeholder comments."
        )


def _guard_placeholder_style_replace(
    spec: dict[str, object],
    *,
    path: str,
    total_lines_before: int,
) -> None:
    if str(spec["mode"]) not in {"replace", "insert_before", "insert_after"}:
        return

    replacement_text = "".join(list(spec["replacement_lines"])).strip()
    if not replacement_text:
        return

    normalized = replacement_text.lower()
    placeholder_markers = (
        "read current content",
        "existing content here",
        "existing file content",
        "placeholder",
    )
    if not any(marker in normalized for marker in placeholder_markers):
        return

    nonempty_lines = [
        line.strip() for line in replacement_text.splitlines() if line.strip()
    ]
    if len(nonempty_lines) > 3:
        return

    comment_prefixes = ("#", "//", "/*", "*", "<!--")
    if not all(line.startswith(comment_prefixes) for line in nonempty_lines):
        return

    start_line = int(spec["start_line"])
    end_line = int(spec["end_line"])
    whole_fileish_replace = start_line == 1 and end_line >= max(
        1, total_lines_before - 2
    )
    placeholder_only_comment = any(
        "read current content" in line.lower() for line in nonempty_lines
    )
    if not whole_fileish_replace and not placeholder_only_comment:
        return

    raise _tool_argument_error(
        "invalid edit shape for file_edit: Suspicious placeholder-style content. "
        f"Apply the real change to {path} instead of comment-only placeholder text."
    )


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


def _guard_suspicious_source_shrink_edit(
    *,
    path: str,
    original_lines: list[str],
    updated_lines: list[str],
) -> None:
    ext = os.path.splitext(path)[1].lower()
    if ext not in _SOURCE_SHRINK_GUARD_EXTENSIONS:
        return
    original_count = len(original_lines)
    updated_count = len(updated_lines)
    if original_count < 80:
        return
    if updated_count > max(20, int(original_count * 0.25)):
        return
    original_chars = max(len("".join(original_lines).strip()), 1)
    updated_chars = len("".join(updated_lines).strip())
    if updated_chars > original_chars * 0.35:
        return
    raise _tool_argument_error(
        "invalid edit shape for file_edit: suspicious source shrink would reduce an existing source file "
        f"from {original_count} lines to {updated_count} lines. Use smaller targeted edits, "
        "or provide a complete source-file replacement rather than a stub/truncated module."
    )


def _guard_suspicious_source_structure_edit(*, path: str, updated_text: str) -> None:
    issues = suspicious_source_structure_issues(path, updated_text)
    if not issues:
        return
    raise _tool_argument_error(
        "invalid edit shape for file_edit: suspicious source structure after edit: "
        + "; ".join(issues)
        + ". Re-read the focused range and apply a smaller syntactically complete edit."
    )


def _edit_spec_is_noop(
    spec: dict[str, object],
    *,
    current_lines: list[str],
) -> bool:
    if str(spec["mode"]) != "replace":
        return False
    start_line = int(spec["start_line"])
    end_line = int(spec["end_line"])
    return current_lines[start_line - 1 : end_line] == list(spec["replacement_lines"])


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
        raise _tool_argument_error("missing required arguments for file_edit: path")

    resolved = validate_path(effective_path, operation="write")
    _require_existing_file(effective_path, resolved)

    with open(resolved, encoding=encoding) as f:
        original_text = f.read()
    original_lines = original_text.splitlines(keepends=True)
    total_lines_before = len(original_lines)
    if total_lines_before == 0:
        raise _tool_argument_error(
            "invalid arguments for file_edit: cannot apply line-based edits to an empty file. Use file_write instead."
        )

    if edits is not None:
        if (
            any(value is not None for value in (start_line, end_line, content))
            or mode != "replace"
        ):
            raise _tool_argument_error(
                "invalid argument combination for file_edit: use either top-level start_line/end_line/content/mode or batched edits=..., not both."
            )
        if not isinstance(edits, list) or not edits:
            raise _tool_argument_error(
                "invalid arguments for file_edit: edits must be a non-empty list when provided."
            )
        edit_specs = []
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                raise _tool_argument_error(
                    f"invalid arguments for file_edit: edits[{index}] must be an object."
                )
            edit_mode = edit.get("mode")
            if str(edit_mode or "replace").strip() == "delete":
                replacement_field = _has_replacement_field(
                    edit, *_REPLACEMENT_FIELD_NAMES
                )
                if replacement_field:
                    raise _tool_argument_error(
                        "invalid argument combination for file_edit: "
                        f"edits[{index}] uses delete mode with {replacement_field}. Delete mode removes text only; "
                        "retry with mode 'replace' if you intend to swap text."
                    )
            edit_start_line, edit_end_line, edit_content = (
                _normalize_replace_compatibility_args(
                    original_text=original_text,
                    start_line=edit.get("start_line"),
                    end_line=edit.get("end_line"),
                    content=_edit_content_argument(edit),
                    mode=str(edit_mode or "replace"),
                    old_string=edit.get("old_string"),
                    new_string=edit.get("new_string"),
                )
            )
            edit_specs.append(
                _build_edit_spec(
                    start_line=edit_start_line,
                    end_line=edit_end_line,
                    content=edit_content,
                    mode=edit_mode,
                    label=f"edits[{index}]",
                )
            )
    else:
        if str(mode or "replace").strip() == "delete":
            top_level_args = {
                "content": content,
                "new_string": _kwargs.get("new_string"),
                "replace": _kwargs.get("replace"),
                "replacement": _kwargs.get("replacement"),
                "new_content": _kwargs.get("new_content"),
            }
            replacement_field = _has_replacement_field(
                top_level_args, *_REPLACEMENT_FIELD_NAMES
            )
            if replacement_field:
                raise _tool_argument_error(
                    "invalid argument combination for file_edit: "
                    f"delete mode was combined with {replacement_field}. Delete mode removes text only; "
                    "retry with mode 'replace' if you intend to swap text."
                )
        start_line, end_line, content = _normalize_replace_compatibility_args(
            original_text=original_text,
            start_line=start_line,
            end_line=end_line,
            content=content,
            mode=mode,
            old_string=_kwargs.get("old_string"),
            new_string=_kwargs.get("new_string"),
        )
        edit_specs = [
            _build_edit_spec(
                start_line=start_line,
                end_line=end_line,
                content=content,
                mode=mode,
                label="edit",
            )
        ]

    edit_specs = [
        _clamp_replace_delete_end_at_eof(
            spec,
            total_lines_before=total_lines_before,
        )
        for spec in edit_specs
    ]
    edit_specs = [
        _reinterpret_anchor_heavy_replace(spec, original_lines=original_lines)
        for spec in edit_specs
    ]
    for spec in edit_specs:
        _validate_edit_bounds(spec, total_lines_before=total_lines_before)
    edit_specs = [
        _preserve_line_boundary(spec, total_lines_before=total_lines_before)
        for spec in edit_specs
    ]
    if len(edit_specs) > 1:
        _ensure_non_overlapping_edits(edit_specs)
    for spec in edit_specs:
        _guard_placeholder_style_replace(
            spec,
            path=effective_path,
            total_lines_before=total_lines_before,
        )
        _guard_placeholder_style_python_replace(
            spec,
            path=effective_path,
        )
        _guard_suspicious_bulk_replace(
            spec,
            path=effective_path,
            original_text=original_text,
            original_lines=original_lines,
        )

    updated_lines = list(original_lines)
    changed = False
    for spec in sorted(
        edit_specs, key=lambda spec: _occupied_range(spec), reverse=True
    ):
        if _edit_spec_is_noop(spec, current_lines=updated_lines):
            continue
        updated_lines = _apply_edit_to_lines(
            updated_lines,
            mode=str(spec["mode"]),
            start_line=int(spec["start_line"]),
            end_line=int(spec["end_line"]),
            replacement_lines=list(spec["replacement_lines"]),
        )
        changed = True

    updated_text = "".join(updated_lines)
    if changed:
        _guard_suspicious_source_shrink_edit(
            path=effective_path,
            original_lines=original_lines,
            updated_lines=updated_lines,
        )
        _guard_suspicious_source_structure_edit(
            path=effective_path,
            updated_text=updated_text,
        )
        atomic_write_text(resolved, updated_text, encoding=encoding)

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
            "changed": changed,
            "no_op": not changed,
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
        "changed": changed,
        "no_op": not changed,
    }
