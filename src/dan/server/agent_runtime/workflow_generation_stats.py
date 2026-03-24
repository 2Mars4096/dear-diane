"""Server-side adapters for workflow-generation feedback persistence."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def record_generation_outcome(
    memory_kernel: Any | None,
    *,
    method: str,
    success: bool = True,
    error_type: str = "",
    fix_needed: bool = False,
    pattern: str = "",
    recorder: Callable[..., None] | None = None,
    log: logging.Logger | None = None,
) -> None:
    """Record a workflow-generation outcome when a memory kernel is available."""
    if memory_kernel is None:
        return

    active_logger = log or logger
    try:
        if recorder is None:
            from dan.engine.generation_stats import (
                record_generation_outcome as recorder,
            )
        recorder(
            memory_kernel,
            method=method,
            pattern=pattern,
            success=success,
            error_type=error_type,
            fix_needed=fix_needed,
        )
    except Exception:
        active_logger.debug("Failed to record generation outcome", exc_info=True)


def get_generation_stats_hint(
    memory_kernel: Any | None,
    *,
    load_stats: Callable[[Any], Any] | None = None,
) -> str:
    """Return a prompt hint derived from historical generation outcomes."""
    if memory_kernel is None:
        return ""
    try:
        if load_stats is None:
            from dan.engine.generation_stats import load_generation_stats as load_stats
        stats = load_stats(memory_kernel)
        return stats.format_for_prompt()
    except Exception:
        return ""


__all__ = [
    "get_generation_stats_hint",
    "record_generation_outcome",
]
