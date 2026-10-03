"""Shared workspace-root normalization for CLI and server surfaces."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

LEGACY_DEEP_AGENT_NETWORK_ROOTS: tuple[Path, ...] = (
    Path("/Volumes/data/Dropbox/Projects/deep-agent-network"),
)


def _safe_current_directory() -> Path | None:
    try:
        return Path.cwd()
    except FileNotFoundError:
        return None


def _expand_home_token(raw: str) -> str:
    text = str(raw or "").strip()
    if text == "$HOME" or text.startswith("$HOME/"):
        return str(Path.home()) + text[len("$HOME") :]
    return text


def _current_project_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if parent.name == "deep-agent-network" and (parent / "pyproject.toml").is_file():
            return parent.resolve(strict=False)
    return None


def _env_workspace_root_aliases() -> Iterable[tuple[Path, Path]]:
    """Read optional root aliases as ``old=new`` entries split by os.pathsep."""

    raw = os.environ.get("DAN_WORKSPACE_ROOT_ALIASES", "").strip()
    if not raw:
        return ()
    pairs: list[tuple[Path, Path]] = []
    for entry in raw.split(os.pathsep):
        if "=" not in entry:
            continue
        old, new = entry.split("=", 1)
        old_text = _expand_home_token(old)
        new_text = _expand_home_token(new)
        if old_text.strip() and new_text.strip():
            pairs.append((Path(old_text).expanduser(), Path(new_text).expanduser()))
    return tuple(pairs)


def _workspace_root_aliases() -> tuple[tuple[Path, Path], ...]:
    aliases = list(_env_workspace_root_aliases())
    current_root = _current_project_root()
    if current_root is not None:
        aliases.extend((legacy, current_root) for legacy in LEGACY_DEEP_AGENT_NETWORK_ROOTS)
    return tuple(aliases)


def canonicalize_workspace_root(path: str | Path) -> Path:
    """Map known stale workspace roots to the current canonical checkout.

    Aliases apply to both the exact root and descendants, so a stale value such as
    ``/Volumes/.../deep-agent-network/website`` resolves to the same subpath under
    the local checkout.
    """

    raw = _expand_home_token(str(path or "."))
    candidate = Path(raw).expanduser()
    try:
        resolved_candidate = candidate.resolve(strict=False)
    except OSError:
        resolved_candidate = candidate

    if not resolved_candidate.is_absolute():
        return resolved_candidate

    for old_root, new_root in _workspace_root_aliases():
        old_resolved = Path(old_root).expanduser().resolve(strict=False)
        new_resolved = Path(new_root).expanduser().resolve(strict=False)
        try:
            suffix = resolved_candidate.relative_to(old_resolved)
        except ValueError:
            continue
        return (new_resolved / suffix).resolve(strict=False)
    return resolved_candidate


def normalize_workspace_root(workspace: str | Path) -> Path:
    """Resolve a workspace root without requiring the process CWD to exist."""

    raw = _expand_home_token(str(workspace or "."))
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return canonicalize_workspace_root(candidate)

    pwd = str(os.environ.get("PWD") or "").strip()
    if pwd:
        return canonicalize_workspace_root(Path(_expand_home_token(pwd)).expanduser() / candidate)

    cwd = _safe_current_directory()
    if cwd is None:
        return Path(os.path.normpath(str(candidate)))
    return canonicalize_workspace_root(cwd / candidate)
