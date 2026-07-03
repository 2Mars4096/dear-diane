"""Lightweight source-shape guards shared by mutation tools.

The public entry point is intentionally profile-based instead of project-based:
new languages can be registered here without adding workflow-specific commands
or objective routing to DAN surfaces.
"""

from __future__ import annotations

import os
import re
from collections import Counter

_CSS_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_GDSCRIPT_FUNCTION_RE = re.compile(
    r"^[ \t]*(?:static[ \t]+)?func[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(",
    re.MULTILINE,
)
_GDSCRIPT_CLASS_NAME_RE = re.compile(
    r"^[ \t]*class_name[ \t]+([A-Za-z_][A-Za-z0-9_]*)\b",
    re.MULTILINE,
)
_GDSCRIPT_FUNCTION_LINE_RE = re.compile(
    r"^[ \t]*(?:static[ \t]+)?func[ \t]+[A-Za-z_][A-Za-z0-9_]*[ \t]*\("
)
_GDSCRIPT_MALFORMED_FUNCTION_PREFIX_RE = re.compile(
    r"^[ \t]*(?:static[ \t]+)?func(?:[ \t]+|$)"
)
_GDSCRIPT_VAR_RE = re.compile(
    r"^[ \t]*(?:@[A-Za-z_][A-Za-z0-9_]*(?:\([^)]*\))?[ \t]+)*(?:static[ \t]+)?var[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*(?::|:=|=)"
)
_GDSCRIPT_BLOCK_PREFIXES = ("if", "elif", "else", "for", "while", "match")
_SOURCE_SHAPE_PROFILE_BY_EXTENSION = {
    ".css": "css",
    ".gd": "gdscript",
}


def source_shape_profile_for_path(path: str) -> str | None:
    """Return the registered source-shape profile for a path, if one exists."""

    return _SOURCE_SHAPE_PROFILE_BY_EXTENSION.get(os.path.splitext(path)[1].lower())


def supported_source_shape_profiles() -> tuple[str, ...]:
    return tuple(sorted(set(_SOURCE_SHAPE_PROFILE_BY_EXTENSION.values())))


def suspicious_source_structure_issues(
    path: str,
    text: str,
    *,
    profile: str | None = None,
) -> list[str]:
    """Return cheap structural parse-risk issues for registered source profiles."""

    normalized_profile = (profile or source_shape_profile_for_path(path) or "").strip().lower()
    if normalized_profile in {"", "auto"}:
        return []
    if normalized_profile == "css":
        return _suspicious_css_structure_issues(text)
    if normalized_profile in {"gd", "gdscript"}:
        return _suspicious_gdscript_structure_issues(text)
    return []


def _suspicious_css_structure_issues(text: str) -> list[str]:
    """Return cheap CSS parse-risk issues without pretending to be a full CSS parser."""

    if not text.strip():
        return []

    issues: list[str] = []
    if text.count("/*") != text.count("*/"):
        issues.append("unterminated CSS block comment [css profile]")

    stripped = _CSS_BLOCK_COMMENT_RE.sub("", text)
    balance = 0
    for index, char in enumerate(stripped):
        if char == "{":
            balance += 1
        elif char == "}":
            balance -= 1
            if balance < 0:
                issues.append(f"unexpected closing brace near character {index + 1} [css profile]")
                balance = 0
    if balance > 0:
        issues.append(f"unclosed CSS rule block count={balance} [css profile]")

    return issues


def _suspicious_gdscript_structure_issues(text: str) -> list[str]:
    """Return parse-risk issues that are cheap to detect before writing .gd files."""

    if not text.strip():
        return []

    issues: list[str] = []
    class_names = _GDSCRIPT_CLASS_NAME_RE.findall(text)
    if len(class_names) > 1:
        issues.append(
            "duplicate class_name declarations [gdscript profile]: "
            + ", ".join(class_names[:5])
        )

    function_names = _GDSCRIPT_FUNCTION_RE.findall(text)
    malformed_function_lines = _malformed_gdscript_function_headers(text.splitlines())
    if malformed_function_lines:
        preview = ", ".join(str(line_number) for line_number in malformed_function_lines[:5])
        issues.append(f"malformed function header at line(s): {preview} [gdscript profile]")

    duplicate_names = sorted(
        name for name, count in Counter(function_names).items() if count > 1
    )
    if duplicate_names:
        issues.append(
            "duplicate function definitions [gdscript profile]: "
            + ", ".join(duplicate_names[:5])
        )

    lines = text.splitlines()
    empty_blocks = _empty_gdscript_control_blocks(lines)
    if empty_blocks:
        preview = ", ".join(str(line_number) for line_number in empty_blocks[:5])
        issues.append(f"empty control block before line(s): {preview} [gdscript profile]")

    unexpected_indents = _unexpected_gdscript_indent_lines(lines)
    if unexpected_indents:
        preview = ", ".join(str(line_number) for line_number in unexpected_indents[:5])
        issues.append(f"unexpected indentation at line(s): {preview} [gdscript profile]")

    duplicate_vars = _duplicate_gdscript_local_vars(lines)
    if duplicate_vars:
        preview = ", ".join(
            f"{name} at line {line_number}" for name, line_number in duplicate_vars[:5]
        )
        issues.append(f"duplicate local variable declarations [gdscript profile]: {preview}")

    duplicate_members = _duplicate_gdscript_member_vars(lines)
    if duplicate_members:
        preview = ", ".join(
            f"{name} at line {line_number}" for name, line_number in duplicate_members[:5]
        )
        issues.append(f"duplicate member variable declarations [gdscript profile]: {preview}")

    unreachable_lines = _unreachable_gdscript_statements_after_return(lines)
    if unreachable_lines:
        preview = ", ".join(str(line_number) for line_number in unreachable_lines[:5])
        issues.append(f"unreachable statement after return at line(s): {preview} [gdscript profile]")

    return issues


def _malformed_gdscript_function_headers(lines: list[str]) -> list[int]:
    malformed: list[int] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        code = stripped.split("#", 1)[0].rstrip()
        if not _GDSCRIPT_MALFORMED_FUNCTION_PREFIX_RE.match(line):
            continue
        if _GDSCRIPT_FUNCTION_LINE_RE.match(line):
            continue
        malformed.append(index + 1)
    return malformed


def _empty_gdscript_control_blocks(lines: list[str]) -> list[int]:
    empty_blocks: list[int] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not _is_gdscript_control_block_header(stripped):
            continue
        indent = _indent_width(line)
        next_content_line = _next_content_line(lines, index + 1)
        if next_content_line is None:
            empty_blocks.append(index + 1)
            continue
        next_index, next_line = next_content_line
        if _indent_width(next_line) <= indent:
            empty_blocks.append(next_index + 1)
    return empty_blocks


def _is_gdscript_control_block_header(stripped: str) -> bool:
    if not stripped or stripped.startswith("#"):
        return False
    code = stripped.split("#", 1)[0].rstrip()
    if not code.endswith(":"):
        return False
    body = code[:-1].strip()
    if not body:
        return False
    for prefix in _GDSCRIPT_BLOCK_PREFIXES:
        if body == prefix or body.startswith(prefix + " ") or body.startswith(prefix + "("):
            return True
    return False


def _unexpected_gdscript_indent_lines(lines: list[str]) -> list[int]:
    unexpected: list[int] = []
    previous_code = ""
    previous_indent = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        code = stripped.split("#", 1)[0].rstrip()
        indent = _indent_width(line)
        if (
            previous_code
            and indent > previous_indent
            and not _gdscript_line_allows_indent(previous_code)
        ):
            unexpected.append(index + 1)
        previous_code = code
        previous_indent = indent
    return unexpected


def _gdscript_line_allows_indent(code: str) -> bool:
    stripped = code.rstrip()
    if not stripped:
        return False
    if stripped.endswith(":"):
        return True
    if stripped.endswith(("[", "(", "{", ",", "\\", "+", "-", "*", "/", "%", "=", "==", "!=", "<", ">", "<=", ">=", "and", "or")):
        return True
    return False


def _duplicate_gdscript_local_vars(lines: list[str]) -> list[tuple[str, int]]:
    duplicates: list[tuple[str, int]] = []
    scope_vars_by_indent: dict[int, set[str]] = {}
    in_function = False
    function_indent = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = _indent_width(line)
        if _GDSCRIPT_FUNCTION_LINE_RE.match(line):
            in_function = True
            function_indent = indent
            scope_vars_by_indent.clear()
            continue
        if not in_function:
            continue
        if indent <= function_indent and not line.startswith((" ", "\t")):
            in_function = False
            scope_vars_by_indent.clear()
            continue
        for known_indent in list(scope_vars_by_indent):
            if known_indent > indent:
                del scope_vars_by_indent[known_indent]
        match = _GDSCRIPT_VAR_RE.match(line)
        if not match:
            continue
        name = match.group(1)
        names = scope_vars_by_indent.setdefault(indent, set())
        if name in names:
            duplicates.append((name, index + 1))
        else:
            names.add(name)
    return duplicates


def _duplicate_gdscript_member_vars(lines: list[str]) -> list[tuple[str, int]]:
    duplicates: list[tuple[str, int]] = []
    names: set[str] = set()
    in_function = False
    function_indent = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = _indent_width(line)
        if _GDSCRIPT_FUNCTION_LINE_RE.match(line):
            in_function = True
            function_indent = indent
            continue
        if in_function:
            if indent <= function_indent and not line.startswith((" ", "\t")):
                in_function = False
            else:
                continue
        if indent != 0:
            continue
        match = _GDSCRIPT_VAR_RE.match(line)
        if not match:
            continue
        name = match.group(1)
        if name in names:
            duplicates.append((name, index + 1))
        else:
            names.add(name)
    return duplicates


def _unreachable_gdscript_statements_after_return(lines: list[str]) -> list[int]:
    unreachable: list[int] = []
    return_indent: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        code = stripped.split("#", 1)[0].rstrip()
        indent = _indent_width(line)
        if return_indent is not None:
            if indent < return_indent:
                return_indent = None
            elif code:
                unreachable.append(index + 1)
                return_indent = None
        if re.match(r"^return(?:\s|$)", code):
            return_indent = indent
    return unreachable


def _next_content_line(lines: list[str], start_index: int) -> tuple[int, str] | None:
    for index in range(start_index, len(lines)):
        if lines[index].strip() and not lines[index].lstrip().startswith("#"):
            return index, lines[index]
    return None


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))
