"""Diane-owned Codex state, separate from the selected account's desktop threads."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile

SCOPE = "diane-v1"


def continuation_account(requested: str, saved_home: str, session: str, profiles: dict) -> tuple[str, str]:
    """Pin a durable thread to its configured account, including legacy default aliases."""
    homes = {name: str(Path(p.get("env", {}).get("CODEX_HOME", "")).expanduser().resolve())
             for name, p in profiles.items() if p.get("env", {}).get("CODEX_HOME")}
    selected = homes.get(requested, "")
    if saved_home:
        target = str(Path(saved_home).expanduser().resolve())
        matches = [name for name, home in homes.items() if home == target]
        if not matches:
            raise ValueError("The original Codex account for this conversation is no longer configured")
        return (requested if requested in matches else matches[0]), target
    if session and requested == "default":
        # Older state stored only the alias. Recover solely from configured,
        # Diane-owned account homes; never copy rollouts between accounts.
        from uuid import UUID
        try:
            UUID(session)
        except ValueError:
            return requested, selected
        found = set()
        for home in set(homes.values()):
            private = private_home(home)
            if any(any((private / folder).glob(f"**/*-{session}.jsonl"))
                   for folder in ("sessions", "archived_sessions")):
                found.add(home)
        if selected in found:
            return requested, selected
        if len(found) > 1:
            raise ValueError("This Codex thread exists in multiple accounts; select its original account")
        if found:
            target = found.pop()
            return next(name for name, home in homes.items() if home == target), target
    return requested, selected


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
