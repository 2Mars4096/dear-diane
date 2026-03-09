"""Built-in tool: show the working-tree status of a git repository."""

from __future__ import annotations

from dan.tools._git_helpers import _find_repo, _run_git

TOOL_METADATA = {
    "tool_id": "git_status",
    "description": (
        "Return the working-tree status of a git repository: current branch, "
        "modified/staged/untracked files, and ahead/behind counts relative to "
        "the upstream branch. Read-only — safe on any surface."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the git repository (defaults to current directory).",
                "default": ".",
            },
        },
        "required": [],
    },
    "examples": [
        {
            "input": {"path": "."},
            "output": {
                "branch": "main",
                "modified": ["src/app.py"],
                "staged": [],
                "untracked": ["notes.txt"],
                "ahead": 0,
                "behind": 0,
            },
        },
    ],
    "category": "git",
    "returns": "dict with branch, modified, staged, untracked, ahead, behind",
}


def _parse_porcelain_v2(output: str) -> dict:
    branch = ""
    ahead = 0
    behind = 0
    modified: list[str] = []
    staged: list[str] = []
    untracked: list[str] = []

    for line in output.splitlines():
        if line.startswith("# branch.head "):
            branch = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab "):
            parts = line.split()
            for part in parts[2:]:
                if part.startswith("+"):
                    ahead = int(part[1:])
                elif part.startswith("-"):
                    behind = int(part[1:])
        elif line.startswith("1 ") or line.startswith("2 "):
            # "1 XY ..." or "2 XY ..."  — ordinary/rename entries
            xy = line.split(" ", 2)[1]
            filename = line.rsplit(" ", 1)[-1]
            if xy[0] != ".":
                staged.append(filename)
            if xy[1] != ".":
                modified.append(filename)
        elif line.startswith("? "):
            untracked.append(line[2:])

    return {
        "branch": branch,
        "modified": modified,
        "staged": staged,
        "untracked": untracked,
        "ahead": ahead,
        "behind": behind,
    }


async def _fallback_status(repo: str) -> dict:
    """Simpler parse when --porcelain=v2 is unavailable (old git)."""
    branch_out, _, _ = await _run_git(repo, "branch", "--show-current")
    branch = branch_out.strip()

    status_out, _, _ = await _run_git(repo, "status", "--porcelain")

    modified: list[str] = []
    staged: list[str] = []
    untracked: list[str] = []

    for line in status_out.splitlines():
        if len(line) < 4:
            continue
        x, y = line[0], line[1]
        filename = line[3:]
        if x == "?":
            untracked.append(filename)
        else:
            if x not in (" ", "?"):
                staged.append(filename)
            if y not in (" ", "?"):
                modified.append(filename)

    return {
        "branch": branch,
        "modified": modified,
        "staged": staged,
        "untracked": untracked,
        "ahead": 0,
        "behind": 0,
    }


async def git_status(path: str = ".", **_kwargs) -> dict:
    repo = _find_repo(path)

    stdout, stderr, rc = await _run_git(repo, "status", "--porcelain=v2", "--branch")

    if rc != 0:
        # Fallback for older git without porcelain v2
        if "unknown option" in stderr.lower() or "unrecognized" in stderr.lower():
            return await _fallback_status(repo)
        raise RuntimeError(f"git status failed (exit {rc}): {stderr.strip()}")

    return _parse_porcelain_v2(stdout)
