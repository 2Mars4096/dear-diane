"""Shared git helpers for built-in git tools."""

from __future__ import annotations

import asyncio
import os


def _git_binary() -> str:
    system_git = "/usr/bin/git"
    if os.path.exists(system_git):
        return system_git
    return "git"


def _find_repo(start: str = ".") -> str:
    """Walk up from *start* to find the git repo root."""
    current = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    raise FileNotFoundError(
        "Not inside a git repository. Ensure you are in a git working tree."
    )


async def _run_git(repo: str, *args: str) -> tuple[str, str, int]:
    """Run git in *repo* with *args*. Returns (stdout, stderr, returncode)."""
    proc = await asyncio.create_subprocess_exec(
        _git_binary(),
        "-C",
        repo,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
        proc.returncode or 0,
    )
