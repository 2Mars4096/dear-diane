"""Built-in tool: show commit history of a git repository."""

from __future__ import annotations

from diane.tools._git_helpers import _find_repo, _run_git

TOOL_METADATA = {
    "tool_id": "git_log",
    "description": (
        "Show the commit history of a git repository. Returns commit hashes, "
        "authors, dates, and messages. Supports limiting by count, date, "
        "and file path. Read-only — safe on any surface."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of commits to return.",
                "default": 10,
            },
            "path": {
                "type": "string",
                "description": "Limit history to commits touching this file or directory.",
            },
            "since": {
                "type": "string",
                "description": "Only show commits after this date (e.g. '2024-01-01', '2 weeks ago').",
            },
        },
        "required": [],
    },
    "examples": [
        {
            "input": {"limit": 3},
            "output": {
                "commits": [
                    {
                        "hash": "abc1234...",
                        "author": "Alice",
                        "date": "2024-06-01T10:00:00+00:00",
                        "message": "feat: add login page",
                    },
                ],
            },
        },
    ],
    "category": "git",
    "returns": "dict with commits list (each has hash, author, date, message)",
}

RECORD_SEP = "\x1e"
FORMAT = f"%H{RECORD_SEP}%an{RECORD_SEP}%aI{RECORD_SEP}%s"


async def git_log(
    limit: int = 10,
    path: str | None = None,
    since: str | None = None,
    **_kwargs,
) -> dict:
    repo = _find_repo(path or ".")

    args: list[str] = ["log", f"--format={FORMAT}", f"-n{limit}"]
    if since:
        args.append(f"--since={since}")
    if path:
        args += ["--", path]

    stdout, stderr, rc = await _run_git(repo, *args)
    if rc:
        raise RuntimeError(
            f"git log failed (exit {rc}): {stderr.strip()}"
        )

    commits: list[dict] = []
    for line in stdout.split("\n"):
        parts = line.split(RECORD_SEP, 3)
        if len(parts) == 4:
            commits.append({
                "hash": parts[0],
                "author": parts[1],
                "date": parts[2],
                "message": parts[3],
            })

    return {"commits": commits}
