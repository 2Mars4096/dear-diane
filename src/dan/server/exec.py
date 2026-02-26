"""Shared Python execution — used by run_python tool and run_strategy_script.

Provides execute_python(code, namespace, ...) that runs code in a controlled
namespace with stdout/stderr capture. Reuses the inline path from CodeExecutor
(allowed builtins, no subprocess) for determinism and low latency.
Subprocess/SandboxRunner path can be added later for timeout/memory limits.
"""

from __future__ import annotations

import contextlib
import io
import logging
from typing import Any

from dan.executors.code import _ALLOWED_BUILTINS

logger = logging.getLogger(__name__)


def execute_python(
    code: str,
    namespace: dict[str, Any],
    timeout: int = 60,
    memory_mb: int = 512,
    *,
    include_namespace: bool = False,
) -> dict[str, Any]:
    """Execute Python code in a controlled namespace.

    Args:
        code: Python code to execute. Should assign to `result` or `output` for
            structured return.
        namespace: Variables to inject (e.g. pd, np, item, out_dir). Will be
            merged with allowed builtins.
        timeout: Reserved for future subprocess path (currently inline, no limit).
        memory_mb: Reserved for future subprocess path.
        include_namespace: If True, include the post-exec namespace in the
            result for callers that need to extract callables (e.g. build_factor).
            Not exposed by run_python tool.

    Returns:
        Dict with:
        - result: value of `namespace["result"]` or `namespace["output"]`
        - stdout: captured stdout
        - stderr: captured stderr
        - error: exception message if execution failed
        - namespace: (if include_namespace) the namespace after exec
    """
    merged: dict[str, Any] = {"__builtins__": _ALLOWED_BUILTINS}
    merged.update(namespace)

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()

    try:
        with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(
            stderr_capture
        ):
            exec(code, merged)  # noqa: S102
    except Exception as exc:
        stdout_str = stdout_capture.getvalue()
        stderr_str = stderr_capture.getvalue()
        logger.debug("execute_python failed: %s", exc)
        out: dict[str, Any] = {
            "result": None,
            "stdout": stdout_str,
            "stderr": stderr_str,
            "error": str(exc),
        }
        if include_namespace:
            out["namespace"] = merged
        return out

    stdout_str = stdout_capture.getvalue()
    stderr_str = stderr_capture.getvalue()
    result = merged.get("result")
    if result is None and "output" in merged:
        result = merged["output"]

    out = {
        "result": result,
        "stdout": stdout_str,
        "stderr": stderr_str,
        "error": None,
    }
    if include_namespace:
        out["namespace"] = merged
    return out
