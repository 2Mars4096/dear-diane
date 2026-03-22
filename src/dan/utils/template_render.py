"""Runtime template rendering helpers."""

from __future__ import annotations

import re
from typing import Any

_SIMPLE_PLACEHOLDER_RE = re.compile(
    r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}|\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}"
)


def render_runtime_template(template: str, variables: dict[str, Any]) -> str:
    """Render simple runtime placeholders without breaking on JSON braces.

    Supports both the canonical ``{name}`` syntax and legacy ``{{name}}``.
    Unknown placeholders are left intact as ``{name}``.
    """

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2) or ""
        if key not in variables:
            return "{" + key + "}"
        value = variables[key]
        return "" if value is None else str(value)

    return _SIMPLE_PLACEHOLDER_RE.sub(_replace, template)
