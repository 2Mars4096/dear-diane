"""Built-in tool: write or append content to a file."""

from __future__ import annotations

import ast
import os

from dan._atomic_file import atomic_write_text
from dan.tools._source_structure import suspicious_source_structure_issues
from dan.tools._workspace import validate_path

_SOURCE_OVERWRITE_GUARD_EXTENSIONS = {
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
            "output": {
                "bytes_written": 14,
                "path": "output/result.txt",
                "mode": "overwrite",
            },
        },
        {
            "input": {"path": "log.txt", "content": "new entry\n", "mode": "append"},
            "output": {"bytes_written": 10, "path": "log.txt", "mode": "append"},
        },
    ],
    "category": "file",
    "returns": "dict with bytes_written, path, and mode",
}


def _tool_argument_error(detail: str) -> ValueError:
    message = str(detail or "").strip() or "invalid arguments for file_write"
    if message.startswith("tool_arguments_invalid:"):
        return ValueError(message)
    return ValueError(f"tool_arguments_invalid: {message}")


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


def _guard_suspicious_python_overwrite(
    *,
    resolved_path: str,
    content: str,
    mode: str,
    encoding: str,
) -> None:
    if (
        mode != "overwrite"
        or not resolved_path.endswith(".py")
        or not os.path.exists(resolved_path)
    ):
        return

    try:
        with open(resolved_path, "r", encoding=encoding) as existing_file:
            original_text = existing_file.read()
    except Exception:
        return
    if not original_text.strip():
        return

    try:
        original_tree = ast.parse(original_text)
    except SyntaxError:
        return

    try:
        updated_tree = ast.parse(content)
    except SyntaxError as exc:
        raise _tool_argument_error(
            "invalid content for file_write: overwriting an existing Python file would leave it syntactically "
            f"invalid ({exc.msg}). Use file_edit for smaller grounded edits or provide the full corrected file content."
        ) from None

    original_blocks = _top_level_named_blocks(original_tree)
    if len(original_blocks) < 4:
        return

    updated_block_names = {name for name, _, _ in _top_level_named_blocks(updated_tree)}
    removed_blocks = [
        name for name, _, _ in original_blocks if name not in updated_block_names
    ]
    if len(removed_blocks) < max(3, len(original_blocks) // 2):
        return

    if len(content.strip()) >= len(original_text.strip()) * 0.75:
        return

    removed_preview = ", ".join(removed_blocks[:3])
    raise _tool_argument_error(
        "invalid content for file_write: suspicious full-file overwrite removes multiple existing Python definitions "
        f"({removed_preview}). Use file_edit for localized edits or provide the complete intended module rewrite."
    )


def _guard_suspicious_source_shrink_overwrite(
    *,
    resolved_path: str,
    content: str,
    mode: str,
    encoding: str,
) -> None:
    if mode != "overwrite" or not os.path.exists(resolved_path):
        return
    ext = os.path.splitext(resolved_path)[1].lower()
    if ext not in _SOURCE_OVERWRITE_GUARD_EXTENSIONS:
        return
    try:
        with open(resolved_path, "r", encoding=encoding) as existing_file:
            original_text = existing_file.read()
    except Exception:
        return
    original_lines = original_text.splitlines()
    updated_lines = content.splitlines()
    if len(original_lines) < 80 or not original_text.strip():
        return
    if len(updated_lines) > max(20, int(len(original_lines) * 0.25)):
        return
    if len(content.strip()) > len(original_text.strip()) * 0.35:
        return
    raise _tool_argument_error(
        "invalid content for file_write: suspicious full-file overwrite would shrink an existing source file "
        f"from {len(original_lines)} lines to {len(updated_lines)} lines. Use file_edit for localized edits, "
        "or provide a complete source-file replacement rather than a stub/truncated module."
    )


def _guard_suspicious_source_structure_write(
    *,
    path: str,
    content: str,
    mode: str,
) -> None:
    if mode != "overwrite":
        return
    issues = suspicious_source_structure_issues(path, content)
    if not issues:
        return
    raise _tool_argument_error(
        "invalid content for file_write: suspicious source structure in overwrite content: "
        + "; ".join(issues)
        + ". Provide syntactically complete source before overwriting the file."
    )


async def file_write(
    path: str | None = None,
    content: str | None = None,
    mode: str = "overwrite",
    encoding: str = "utf-8",
    file_path: str | None = None,
    **_kwargs,
) -> dict:
    effective_path = str(path or file_path or "").strip()
    if not effective_path:
        raise TypeError("file_write() missing 1 required positional argument: 'path'")
    if content is None:
        raise TypeError(
            "file_write() missing 1 required positional argument: 'content'"
        )

    resolved = validate_path(effective_path, operation="write")

    if mode not in ("overwrite", "append"):
        raise ValueError(f"Invalid mode '{mode}'. Use 'overwrite' or 'append'.")

    _guard_suspicious_python_overwrite(
        resolved_path=resolved,
        content=content,
        mode=mode,
        encoding=encoding,
    )
    _guard_suspicious_source_shrink_overwrite(
        resolved_path=resolved,
        content=content,
        mode=mode,
        encoding=encoding,
    )
    _guard_suspicious_source_structure_write(
        path=effective_path,
        content=content,
        mode=mode,
    )

    os.makedirs(os.path.dirname(resolved), exist_ok=True)

    updated_content = content
    if mode == "append" and os.path.exists(resolved):
        with open(resolved, "r", encoding=encoding) as existing_file:
            updated_content = existing_file.read() + content
    atomic_write_text(resolved, updated_content, encoding=encoding)

    return {
        "bytes_written": len(content.encode(encoding)),
        "path": effective_path,
        "mode": mode,
    }
