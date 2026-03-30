"""Workspace root resolution and path sandboxing for file-system tools."""

from __future__ import annotations

import logging
import os
from typing import Literal

logger = logging.getLogger(__name__)


def _workspace_root() -> str:
    explicit = os.environ.get("DAN_WORKSPACE_ROOT")
    if explicit:
        return explicit
    from dan.server.paths import resolve_workspace_root

    return resolve_workspace_root()


def _is_within_workspace(resolved: str, root: str) -> bool:
    return resolved == root or resolved.startswith(root + os.sep)


def strict_sandbox_enabled() -> bool:
    raw = os.environ.get("DAN_STRICT_SANDBOX", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def validate_path(path: str, *, operation: Literal["read", "write", "delete"] = "read") -> str:
    """Resolve user-provided paths safely.

    - Relative paths stay rooted inside the workspace.
    - Explicit absolute paths (including ``~/...``) are allowed as-is for reads.
    - In strict sandbox mode, absolute write/delete paths must remain inside the workspace.
    """
    expanded = os.path.expanduser(path)
    root = os.path.realpath(_workspace_root())
    if os.path.isabs(expanded):
        resolved = os.path.realpath(expanded)
        inside_workspace = _is_within_workspace(resolved, root)
        if not inside_workspace:
            if strict_sandbox_enabled() and operation in {"write", "delete"}:
                raise PermissionError(
                    f"{operation.title()} path '{path}' resolves outside the workspace root "
                    f"'{root}' and DAN_STRICT_SANDBOX=1 is enabled."
                )
            logger.info("Operating outside workspace root (%s): %s", operation, resolved)
        return resolved

    resolved = os.path.realpath(os.path.join(root, expanded))
    if not _is_within_workspace(resolved, root):
        raise ValueError(
            f"Relative path '{path}' resolves outside the workspace root '{root}'. "
            "Use an explicit absolute path if that is intentional."
        )
    return resolved
