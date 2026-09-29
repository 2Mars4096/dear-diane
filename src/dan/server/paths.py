"""Shared filesystem paths for the Dear Diane server (graphs, chats, runs)."""

from __future__ import annotations

import os
from pathlib import Path

from dan.workspace_roots import canonicalize_workspace_root


def _safe_cwd() -> Path | None:
    """Return the current working directory when it is still addressable."""
    try:
        return Path.cwd()
    except FileNotFoundError:
        return None


def _find_repo_root() -> Path | None:
    """Locate the source checkout root when running from the repo."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return None


def _stabilize_path(raw: str) -> str:
    """Keep normal relative behavior, but anchor paths when cwd disappeared."""
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path)
    if _safe_cwd() is not None:
        return raw
    base = _find_repo_root() or (Path.home() / ".dan")
    return str((base / path).resolve())


def resolve_graphs_dir() -> str:
    """Resolve the graph/chat persistence directory.

    Precedence:

    1. ``DAN_GRAPHS_DIR`` environment variable (after ``load_dotenv()`` in callers).
    2. First line of ``~/.dan/graphs_dir`` if that file exists (absolute path to your
       repo ``graphs`` folder). Keeps **terminal** ``dan-up`` / **Dear Diane Desktop** on the
       same data without rebuilding the app.
    3. ``./graphs`` while the process working directory is valid.
    4. If the working directory is unavailable, anchor the relative fallback to the
       source checkout root when possible, otherwise ``~/.dan/graphs``.
    """
    d = os.environ.get("DAN_GRAPHS_DIR", "").strip()
    if d:
        return _stabilize_path(d)
    marker = Path.home() / ".dan" / "graphs_dir"
    if marker.is_file():
        try:
            line = marker.read_text(encoding="utf-8").strip().split("\n")[0].strip()
            if line and not line.startswith("#"):
                return _stabilize_path(line)
        except OSError:
            pass
    return _stabilize_path("./graphs")


def resolve_workspace_root() -> str:
    """Resolve the workspace root used by server-side relative paths."""
    raw = os.environ.get("DAN_WORKSPACE_ROOT", "").strip()
    if raw:
        stabilized = Path(_stabilize_path(raw)).expanduser().resolve(strict=False)
        return str(canonicalize_workspace_root(stabilized))
    cwd = _safe_cwd()
    if cwd is not None:
        return str(canonicalize_workspace_root(cwd.resolve()))
    base = _find_repo_root() or (Path.home() / ".dan")
    return str(canonicalize_workspace_root(base.resolve()))
