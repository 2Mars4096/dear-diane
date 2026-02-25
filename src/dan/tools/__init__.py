"""dan.tools — batteries-included tool library with auto-discovery.

Each module in this package exports an async tool function and a ``TOOL_METADATA``
dict.  ``get_all_tools()`` scans the package and returns every successfully
loadable tool, gracefully skipping modules whose optional dependencies are
missing.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

ToolFunction = Callable[..., Awaitable[Any]]

_TOOL_MODULES = [
    "file_read",
    "file_write",
    "list_directory",
    "web_search",
    "web_fetch",
    "http_request",
    "shell_command",
    "pdf_read",
    "text_chunk",
    "json_extract",
    "regex_match",
]

_REQUIRED_METADATA_KEYS = {"tool_id", "description", "parameters", "examples", "category", "returns"}


def get_all_tools() -> dict[str, tuple[ToolFunction, dict]]:
    """Discover all tool modules and return ``{tool_id: (function, metadata)}``."""
    tools: dict[str, tuple[ToolFunction, dict]] = {}

    for module_name in _TOOL_MODULES:
        fqn = f"dan.tools.{module_name}"
        try:
            mod = importlib.import_module(fqn)
        except ImportError:
            logger.warning("Skipping tool module '%s' — import failed (missing optional dependency?)", fqn)
            continue
        except Exception:
            logger.warning("Skipping tool module '%s' — unexpected error during import", fqn, exc_info=True)
            continue

        metadata = getattr(mod, "TOOL_METADATA", None)
        if not isinstance(metadata, dict):
            logger.warning("Skipping '%s' — no TOOL_METADATA dict found", fqn)
            continue

        missing = _REQUIRED_METADATA_KEYS - set(metadata)
        if missing:
            logger.warning("Skipping '%s' — TOOL_METADATA missing keys: %s", fqn, missing)
            continue

        tool_id = metadata["tool_id"]
        fn = getattr(mod, tool_id, None)
        if fn is None or not callable(fn):
            logger.warning(
                "Skipping '%s' — no callable named '%s' found in module",
                fqn, tool_id,
            )
            continue

        tools[tool_id] = (fn, metadata)

    return tools
