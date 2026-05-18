"""Built-in tool: deterministic read-only checks over workspace files."""

from __future__ import annotations

import ast
from html.parser import HTMLParser
import json
import os
import re
from typing import Any

from dan.tools._source_structure import (
    source_shape_profile_for_path,
    suspicious_source_structure_issues,
)
from dan.tools._workspace import validate_path

MAX_CHECK_FILE_SIZE = 1_048_576
_SUPPORTED_CHECKS = {"exists", "literal_count", "regex_count", "html_tags", "syntax"}
_VOID_HTML_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_DEFAULT_HTML_TAGS = ["html", "head", "body", "main", "section", "style", "script"]

TOOL_METADATA = {
    "tool_id": "workspace_check",
    "description": (
        "Run deterministic read-only checks against workspace files. Use this before "
        "shell_command for file existence, literal or regex counts, HTML tag balance, "
        "and lightweight Python/JSON/HTML or registered source-profile syntax checks."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "check": {
                "type": "string",
                "enum": ["exists", "literal_count", "regex_count", "html_tags", "syntax"],
                "description": "Check type to run.",
            },
            "path": {
                "type": "string",
                "description": "Single file or directory path for the check.",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Paths for exists checks.",
            },
            "patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Literal or regex patterns to count in the target file.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "HTML tag names to count and balance.",
            },
            "syntax": {
                "type": "string",
                "enum": ["auto", "python", "json", "html", "gdscript"],
                "description": "Syntax flavor for syntax checks.",
                "default": "auto",
            },
            "encoding": {
                "type": "string",
                "description": "Text encoding for file checks.",
                "default": "utf-8",
            },
            "max_size": {
                "type": "integer",
                "description": "Maximum bytes to read from one file.",
                "default": MAX_CHECK_FILE_SIZE,
            },
        },
        "required": ["check"],
    },
    "examples": [
        {
            "input": {"check": "exists", "paths": ["index.html", "styles.css"]},
            "output": {
                "check": "exists",
                "passed": True,
                "results": [{"path": "index.html", "exists": True, "is_file": True}],
            },
        },
        {
            "input": {"check": "html_tags", "path": "index.html", "tags": ["html", "head", "body"]},
            "output": {
                "check": "html_tags",
                "path": "index.html",
                "passed": True,
                "tags": {"html": {"start": 1, "end": 1, "balanced": True}},
            },
        },
    ],
    "category": "file",
    "returns": "dict with check, passed, and structured check-specific results",
}


class _TagCounter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.starts: dict[str, int] = {}
        self.ends: dict[str, int] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.lower()
        self.starts[normalized] = self.starts.get(normalized, 0) + 1

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        self.ends[normalized] = self.ends.get(normalized, 0) + 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.lower()
        self.starts[normalized] = self.starts.get(normalized, 0) + 1
        if normalized not in _VOID_HTML_TAGS:
            self.ends[normalized] = self.ends.get(normalized, 0) + 1


def _normalize_check(check: str | None) -> str:
    normalized = str(check or "").strip().lower().replace("-", "_")
    if normalized not in _SUPPORTED_CHECKS:
        allowed = ", ".join(sorted(_SUPPORTED_CHECKS))
        raise ValueError(f"Unsupported workspace_check check '{check}'. Expected one of: {allowed}.")
    return normalized


def _normalize_string_list(value: Any, *, name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise ValueError(f"{name} must be a string or list of strings.")
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _target_paths(path: str | None, paths: Any) -> list[str]:
    result = _normalize_string_list(paths, name="paths")
    if path:
        text = str(path).strip()
        if text and text not in result:
            result.insert(0, text)
    if not result:
        raise ValueError("workspace_check requires path or paths for this check.")
    return result


def _read_text(path: str, *, encoding: str, max_size: int) -> tuple[str, int]:
    resolved = validate_path(path)
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"File not found: '{path}'.")
    size = os.path.getsize(resolved)
    if size > max_size:
        raise ValueError(
            f"File '{path}' is {size:,} bytes (limit {max_size:,}). "
            "Use a narrower tool or raise max_size deliberately."
        )
    with open(resolved, encoding=encoding) as handle:
        return handle.read(), size


def _exists_result(paths: list[str]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for item in paths:
        resolved = validate_path(item)
        exists = os.path.exists(resolved)
        result: dict[str, Any] = {
            "path": item,
            "exists": exists,
            "is_file": os.path.isfile(resolved) if exists else False,
            "is_dir": os.path.isdir(resolved) if exists else False,
        }
        if exists and os.path.isfile(resolved):
            result["size"] = os.path.getsize(resolved)
        results.append(result)
    return {
        "check": "exists",
        "passed": all(item["exists"] for item in results),
        "results": results,
    }


def _count_literals(content: str, patterns: list[str]) -> list[dict[str, Any]]:
    return [{"pattern": pattern, "count": content.count(pattern)} for pattern in patterns]


def _count_regexes(content: str, patterns: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for pattern in patterns:
        try:
            compiled = re.compile(pattern, flags=re.MULTILINE)
        except re.error as exc:
            raise ValueError(f"Invalid regex pattern '{pattern}': {exc}.") from None
        results.append({"pattern": pattern, "count": len(list(compiled.finditer(content)))})
    return results


def _count_result(
    *,
    check: str,
    path: str,
    content: str,
    size: int,
    patterns: list[str],
) -> dict[str, Any]:
    if not patterns:
        raise ValueError(f"workspace_check {check} requires at least one pattern.")
    counts = _count_literals(content, patterns) if check == "literal_count" else _count_regexes(content, patterns)
    return {
        "check": check,
        "path": path,
        "passed": True,
        "size": size,
        "counts": counts,
    }


def _html_tag_result(path: str, content: str, size: int, tags: list[str]) -> dict[str, Any]:
    requested = [tag.lower() for tag in (tags or _DEFAULT_HTML_TAGS)]
    parser = _TagCounter()
    parser.feed(content)
    parser.close()

    tag_results: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for tag in requested:
        start = parser.starts.get(tag, 0)
        end = parser.ends.get(tag, 0)
        balance_required = tag not in _VOID_HTML_TAGS
        balanced = (not balance_required) or start == end
        if balance_required and not balanced:
            issues.append(f"{tag}: start={start}, end={end}")
        if tag in {"html", "head", "body"} and (start > 1 or end > 1):
            issues.append(f"{tag}: duplicate document tag start={start}, end={end}")
        tag_results[tag] = {
            "start": start,
            "end": end,
            "balanced": balanced,
        }

    return {
        "check": "html_tags",
        "path": path,
        "passed": not issues,
        "size": size,
        "tags": tag_results,
        "issues": issues,
    }


def _infer_syntax(path: str, syntax: str | None) -> str:
    requested = str(syntax or "auto").strip().lower()
    if requested != "auto":
        return requested
    lower = path.lower()
    if lower.endswith(".py"):
        return "python"
    if lower.endswith(".json"):
        return "json"
    if lower.endswith((".html", ".htm")):
        return "html"
    source_profile = source_shape_profile_for_path(path)
    if source_profile:
        return source_profile
    raise ValueError(
        "workspace_check syntax=auto only supports .py, .json, .html, .htm, "
        "or files with a registered source-shape profile."
    )


def _syntax_result(path: str, content: str, size: int, syntax: str | None) -> dict[str, Any]:
    kind = _infer_syntax(path, syntax)
    error = ""
    if kind == "python":
        try:
            ast.parse(content, filename=path)
        except SyntaxError as exc:
            error = f"{exc.msg} at line {exc.lineno}, column {exc.offset}"
    elif kind == "json":
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            error = f"{exc.msg} at line {exc.lineno}, column {exc.colno}"
    elif kind == "html":
        html_result = _html_tag_result(path, content, size, _DEFAULT_HTML_TAGS)
        return {
            "check": "syntax",
            "path": path,
            "syntax": kind,
            "passed": bool(html_result["passed"]),
            "size": size,
            "html": html_result,
            "error": "" if html_result["passed"] else "; ".join(html_result["issues"]),
        }
    elif kind in {"gdscript", "gd"}:
        issues = suspicious_source_structure_issues(path, content, profile="gdscript")
        error = "; ".join(issues)
        return {
            "check": "syntax",
            "path": path,
            "syntax": "gdscript",
            "passed": not issues,
            "size": size,
            "error": error,
            "issues": issues,
        }
    else:
        raise ValueError(
            "workspace_check syntax supports only auto, python, json, html, "
            "or a registered source-shape profile."
        )
    return {
        "check": "syntax",
        "path": path,
        "syntax": kind,
        "passed": not error,
        "size": size,
        "error": error,
    }


async def workspace_check(
    check: str | None = None,
    path: str | None = None,
    paths: Any = None,
    patterns: Any = None,
    tags: Any = None,
    syntax: str = "auto",
    encoding: str = "utf-8",
    max_size: int = MAX_CHECK_FILE_SIZE,
    **_kwargs,
) -> dict[str, Any]:
    normalized_check = _normalize_check(check)
    try:
        normalized_max_size = int(max_size)
    except (TypeError, ValueError):
        raise ValueError("max_size must be an integer.") from None
    if normalized_max_size < 1:
        raise ValueError("max_size must be >= 1.")

    if normalized_check == "exists":
        return _exists_result(_target_paths(path, paths))

    target = _target_paths(path, None)[0]
    content, size = _read_text(target, encoding=encoding, max_size=normalized_max_size)
    if normalized_check in {"literal_count", "regex_count"}:
        return _count_result(
            check=normalized_check,
            path=target,
            content=content,
            size=size,
            patterns=_normalize_string_list(patterns, name="patterns"),
        )
    if normalized_check == "html_tags":
        return _html_tag_result(
            target,
            content,
            size,
            _normalize_string_list(tags, name="tags"),
        )
    return _syntax_result(target, content, size, syntax)
