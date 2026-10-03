"""Built-in tool: show file differences in a git repository."""

from __future__ import annotations

import re

from diane.tools._git_helpers import _find_repo, _run_git

TOOL_METADATA = {
    "tool_id": "git_diff",
    "description": (
        "Show file differences in a git repository. Can diff the working tree, "
        "the staging area (--cached), or against a specific commit. "
        "Read-only — safe on any surface."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Limit diff to a specific file or directory (optional).",
            },
            "staged": {
                "type": "boolean",
                "description": "If true, show staged (cached) changes instead of working-tree changes.",
                "default": False,
            },
            "commit": {
                "type": "string",
                "description": "Compare against a specific commit (e.g. 'HEAD~3', a branch name, or a SHA).",
            },
        },
        "required": [],
    },
    "examples": [
        {
            "input": {"staged": True},
            "output": {
                "diff_text": "diff --git a/file.py ...",
                "files_changed": 1,
                "additions": 5,
                "deletions": 2,
            },
        },
    ],
    "category": "git",
    "returns": "dict with diff_text, files_changed, additions, deletions",
}


def _parse_stat(stat_output: str) -> tuple[int, int, int]:
    """Extract files_changed, additions, deletions from --stat output."""
    files_changed = 0
    additions = 0
    deletions = 0
    for line in stat_output.splitlines():
        m = re.search(
            r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?",
            line,
        )
        if m:
            files_changed = int(m.group(1))
            additions = int(m.group(2) or 0)
            deletions = int(m.group(3) or 0)
            break
    return files_changed, additions, deletions


async def git_diff(
    path: str | None = None,
    staged: bool = False,
    commit: str | None = None,
    **_kwargs,
) -> dict:
    repo = _find_repo(path or ".")

    diff_args: list[str] = ["diff"]
    stat_args: list[str] = ["diff", "--stat"]

    if staged:
        diff_args.append("--cached")
        stat_args.append("--cached")
    if commit:
        diff_args.append(commit)
        stat_args.append(commit)
    if path:
        diff_args += ["--", path]
        stat_args += ["--", path]

    diff_out, diff_err, diff_rc = await _run_git(repo, *diff_args)
    if diff_rc != 0:
        raise RuntimeError(f"git diff failed (exit {diff_rc}): {diff_err.strip()}")

    stat_out, stat_err, stat_rc = await _run_git(repo, *stat_args)
    if stat_rc != 0:
        raise RuntimeError(f"git diff --stat failed (exit {stat_rc}): {stat_err.strip()}")

    files_changed, additions, deletions = _parse_stat(stat_out)

    return {
        "diff_text": diff_out,
        "files_changed": files_changed,
        "additions": additions,
        "deletions": deletions,
    }
