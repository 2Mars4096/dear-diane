"""Workspace root resolution and path sandboxing for file-system tools."""

from __future__ import annotations

import os


def _workspace_root() -> str:
    return os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd())


def validate_path(path: str) -> str:
    """Resolve *path* relative to workspace root and reject escapes.

    Raises ``ValueError`` if the resolved path falls outside the workspace.
    """
    root = os.path.realpath(_workspace_root())
    resolved = os.path.realpath(os.path.join(root, path))
    if resolved != root and not resolved.startswith(root + os.sep):
        raise ValueError(
            f"Path '{path}' resolves outside the workspace root. "
            f"All file operations are sandboxed to '{root}'."
        )
    return resolved
