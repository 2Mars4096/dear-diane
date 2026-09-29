"""Crash-safe local file replacement helpers shared by tools and durable server state."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat


def atomic_write_bytes(path: str | Path, content: bytes, *, mode: int | None = None) -> None:
    """Replace ``path`` atomically after fully writing and syncing a sibling temp file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_mode: int | None = None
    try:
        existing_mode = stat.S_IMODE(target.stat().st_mode)
    except OSError:
        pass

    replacement_mode = mode if mode is not None else existing_mode
    descriptor, temporary = _open_sibling_temporary(target, mode=replacement_mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if replacement_mode is not None:
            os.chmod(temporary, replacement_mode)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_write_text(
    path: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
    atomic_write_bytes(path, content.encode(encoding), mode=mode)


def _fsync_directory(path: Path) -> None:
    """Best-effort directory sync so the rename itself survives a crash."""

    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        # Some platforms/filesystems do not permit syncing directory handles.
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _open_sibling_temporary(target: Path, *, mode: int | None = None) -> tuple[int, Path]:
    """Create a private-name sibling while honoring the process umask.

    ``tempfile.mkstemp`` always creates mode ``0600``. That is appropriate for
    generic temporary data but would silently change Diane's newly created
    workspace files from normal ``open(..., "w")`` permissions (typically
    ``0644``) to ``0600`` after the atomic-write migration.
    """

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    for _attempt in range(100):
        temporary = target.parent / (f".{target.name}.dan-tmp-{secrets.token_hex(12)}")
        try:
            descriptor = os.open(temporary, flags, 0o666 if mode is None else mode)
        except FileExistsError:
            continue
        return descriptor, temporary
    raise FileExistsError(
        f"Could not reserve an atomic-write temporary beside {target}"
    )
