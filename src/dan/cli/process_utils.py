"""Shared CLI helpers for checking process liveness."""
from __future__ import annotations

import os
import subprocess


def _unix_process_state(pid: int) -> str | None:
    """Return the compact process state on Unix, or ``None`` if unavailable."""
    if os.name == "nt":
        return None
    try:
        output = subprocess.check_output(
            ["ps", "-o", "stat=", "-p", str(pid)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    state = output.strip().split(None, 1)[0] if output.strip() else ""
    return state or None


def is_process_alive(pid: int) -> bool:
    """Check whether *pid* refers to a live, non-zombie process."""
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False

    state = _unix_process_state(pid)
    if state is not None and "Z" in state.upper():
        return False
    return True
