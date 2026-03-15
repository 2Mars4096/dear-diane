"""Workspace root resolution and path sandboxing for file-system tools."""

from __future__ import annotations

import os


def _workspace_root() -> str:
    return os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd())


def validate_path(path: str) -> str:
    """Resolve user-provided paths safely.

    - Relative paths stay rooted inside the workspace.
    - Explicit absolute paths (including ``~/...``) are allowed as-is.
    """
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return os.path.realpath(expanded)

    root = os.path.realpath(_workspace_root())
    resolved = os.path.realpath(os.path.join(root, expanded))
    if resolved != root and not resolved.startswith(root + os.sep):
        raise ValueError(
            f"Relative path '{path}' resolves outside the workspace root '{root}'. "
            "Use an explicit absolute path if that is intentional."
        )
    return resolved
