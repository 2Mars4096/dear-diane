"""Built-in tool: stage files and create a git commit."""

from __future__ import annotations

import re

from dan.tools._git_helpers import _find_repo, _run_git

TOOL_METADATA = {
    "tool_id": "git_commit",
    "description": (
        "Stage files and create a git commit. If specific files are given they "
        "are staged individually; otherwise all changes are staged (git add -A). "
        "This is a WRITE operation — it modifies repository history."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The commit message.",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of file paths to stage. Omit to stage all changes.",
            },
            "amend": {
                "type": "boolean",
                "description": "If true, amend the previous commit instead of creating a new one.",
                "default": False,
            },
        },
        "required": ["message"],
    },
    "examples": [
        {
            "input": {"message": "fix: correct off-by-one in parser", "files": ["src/parser.py"]},
            "output": {
                "commit_hash": "abc1234",
                "files_committed": 1,
                "message": "fix: correct off-by-one in parser",
            },
        },
    ],
    "category": "git",
    "returns": "dict with commit_hash, files_committed, message",
}


async def git_commit(
    message: str,
    files: list[str] | None = None,
    amend: bool = False,
    **_kwargs,
) -> dict:
    repo = _find_repo()

    # Stage
    if files:
        for f in files:
            _, err, rc = await _run_git(repo, "add", f)
            if rc != 0:
                raise RuntimeError(f"git add '{f}' failed (exit {rc}): {err.strip()}")
    else:
        _, err, rc = await _run_git(repo, "add", "-A")
        if rc != 0:
            raise RuntimeError(f"git add -A failed (exit {rc}): {err.strip()}")

    # Commit
    commit_args: list[str] = ["commit", "-m", message]
    if amend:
        commit_args.append("--amend")

    stdout, stderr, rc = await _run_git(repo, *commit_args)
    if rc != 0:
        raise RuntimeError(f"git commit failed (exit {rc}): {stderr.strip()}")

    # Extract short hash from commit output
    hash_out, _, _ = await _run_git(repo, "rev-parse", "--short", "HEAD")
    commit_hash = hash_out.strip()

    # Count committed files from the output
    files_committed = 0
    m = re.search(r"(\d+) files? changed", stdout)
    if m:
        files_committed = int(m.group(1))

    return {
        "commit_hash": commit_hash,
        "files_committed": files_committed,
        "message": message,
    }
