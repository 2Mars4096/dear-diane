"""Built-in tool: compare two files or text strings."""

from __future__ import annotations

import difflib
import os

from diane.tools._workspace import validate_path

TOOL_METADATA = {
    "tool_id": "text_diff",
    "description": (
        "Compare two files or two text strings and produce a unified diff. "
        "Accepts file paths (resolved within workspace) or raw text. "
        "Returns the diff, number of additions, deletions, and whether the inputs are identical."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "a": {
                "type": "string",
                "description": "First input: a file path or raw text.",
            },
            "b": {
                "type": "string",
                "description": "Second input: a file path or raw text.",
            },
            "context_lines": {
                "type": "integer",
                "description": "Number of context lines around changes.",
                "default": 3,
            },
        },
        "required": ["a", "b"],
    },
    "examples": [
        {
            "input": {"a": "hello\nworld\n", "b": "hello\nearth\n"},
            "output": {
                "unified_diff": "--- a\n+++ b\n@@ -1,2 +1,2 @@\n hello\n-world\n+earth\n",
                "additions": 1,
                "deletions": 1,
                "is_identical": False,
            },
        },
    ],
    "category": "text",
    "returns": "dict with unified_diff, additions, deletions, is_identical",
}


def _load_content(input_str: str) -> tuple[list[str], str]:
    """Try to read as file path; fall back to raw text."""
    try:
        resolved = validate_path(input_str)
        if os.path.isfile(resolved):
            with open(resolved, encoding="utf-8") as f:
                return f.readlines(), input_str
    except (ValueError, OSError):
        pass
    return input_str.splitlines(keepends=True), "(text)"


async def text_diff(a: str, b: str, context_lines: int = 3, **_kwargs) -> dict:
    lines_a, name_a = _load_content(a)
    lines_b, name_b = _load_content(b)

    diff_lines = list(difflib.unified_diff(
        lines_a, lines_b,
        fromfile=name_a, tofile=name_b,
        n=context_lines,
    ))

    additions = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    deletions = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

    return {
        "unified_diff": "".join(diff_lines),
        "additions": additions,
        "deletions": deletions,
        "is_identical": len(diff_lines) == 0,
    }
