"""Configuration for the direct execution architecture (plan 32-7)."""

from __future__ import annotations

import os

# Telemetry fields for direct execution (plan 32-7):
# - metadata.execution_path: "inline" | "codegen" | "codegen_fallback"
# - metadata.graph_build_ms: time to construct Graph in-process
# - metadata.inline_build_success: bool
# - metadata.inline_execution_success: bool
# - metadata.total_ms: total execution time


def is_direct_build_enabled() -> bool:
    """Check if direct build execution is enabled.

    Reads DAN_DIRECT_BUILD env var. Values: "on" (default), "off", "only".
    Returns False only when set to "off".
    """
    return os.environ.get("DAN_DIRECT_BUILD", "on").lower() != "off"


def is_direct_build_only() -> bool:
    """Check if ONLY direct build is allowed (no codegen fallback).

    Reads DAN_DIRECT_BUILD env var. Returns True when set to "only".
    """
    return os.environ.get("DAN_DIRECT_BUILD", "on").lower() == "only"


def get_materialize_threshold() -> int:
    """Minimum node count for post-hoc graph materialization.

    Reads DAN_MATERIALIZE_THRESHOLD env var. Default: 3.
    """
    try:
        return int(os.environ.get("DAN_MATERIALIZE_THRESHOLD", "3"))
    except ValueError:
        return 3


def get_eval_execution_path() -> str:
    """Get the execution path for eval runs. Values: 'inline', 'codegen', 'auto'."""
    return os.environ.get("DAN_EVAL_EXECUTION_PATH", "auto")
