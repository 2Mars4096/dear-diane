"""Diane-owned Codex state, separate from the selected account's desktop threads."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile

SCOPE = "diane-v1"


def private_home(source: str | Path) -> Path:
    from diane.server.paths import resolve_graphs_dir
    identity = hashlib.sha256(str(Path(source).expanduser().resolve()).encode()).hexdigest()[:24]
    return Path(resolve_graphs_dir()).resolve() / "native_accounts" / "codex" / identity


def _write(path: Path, data: bytes):
    fd, name = tempfile.mkstemp(prefix=".sync-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def prepare_home(source: str | Path) -> Path:
    source = Path(source).expanduser().resolve()
    home = private_home(source)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Copy only account/configuration inputs, never databases, rollouts or locks.
    # A source digest avoids overwriting credentials refreshed by Diane itself.
    for name in ("auth.json", "config.toml", "AGENTS.md"):
        original = source / name
        stamp = home / f".source-{name}.sha256"
        target = home / name
        if original.is_file():
            data = original.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if not target.exists() or not stamp.exists() or stamp.read_text() != digest:
                _write(target, data)
                _write(stamp, digest.encode())
        elif stamp.exists():
            target.unlink(missing_ok=True)
            stamp.unlink()
    # Skills are reusable instructions, not session state.
    skills = home / "skills"
    if (source / "skills").is_dir() and not skills.exists():
        skills.symlink_to(source / "skills", target_is_directory=True)
    return home
