"""Built-in tool: manage git worktrees."""

from __future__ import annotations

from diane.tools._git_helpers import _find_repo, _run_git

TOOL_METADATA = {
    "tool_id": "git_worktree",
    "description": (
        "List, add, or remove git worktrees. 'list' is read-only; "
        "'add' and 'remove' are WRITE operations that create or delete "
        "working-tree directories on disk."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "add", "remove"],
                "description": "The worktree operation to perform.",
            },
            "path": {
                "type": "string",
                "description": "Filesystem path for the worktree (required for 'add' and 'remove').",
            },
            "branch": {
                "type": "string",
                "description": "Branch to check out in the new worktree (optional for 'add').",
            },
        },
        "required": ["action"],
    },
    "examples": [
        {
            "input": {"action": "list"},
            "output": {
                "worktrees": [
                    {"path": "/repo", "branch": "refs/heads/main", "head": "abc1234"},
                ],
            },
        },
        {
            "input": {"action": "add", "path": "../repo-feature", "branch": "feature/x"},
            "output": {"created": "../repo-feature", "branch": "feature/x"},
        },
        {
            "input": {"action": "remove", "path": "../repo-feature"},
            "output": {"removed": "../repo-feature"},
        },
    ],
    "category": "git",
    "returns": "dict — shape depends on action (see examples)",
}


def _parse_worktree_list(output: str) -> list[dict]:
    """Parse `git worktree list --porcelain` output into structured dicts."""
    worktrees: list[dict] = []
    current: dict[str, str] = {}

    for line in output.splitlines():
        if not line.strip():
            if current:
                worktrees.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line.split(" ", 1)[1]
        elif line.startswith("HEAD "):
            current["head"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1]
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True

    if current:
        worktrees.append(current)

    return worktrees


async def git_worktree(
    action: str,
    path: str | None = None,
    branch: str | None = None,
    **_kwargs,
) -> dict:
    repo = _find_repo()

    if action == "list":
        stdout, stderr, rc = await _run_git(repo, "worktree", "list", "--porcelain")
        if rc != 0:
            raise RuntimeError(
                f"git worktree list failed (exit {rc}): {stderr.strip()}"
            )
        return {"worktrees": _parse_worktree_list(stdout)}

    if action == "add":
        if not path:
            raise ValueError("'path' is required for action 'add'.")
        add_args: list[str] = ["worktree", "add", path]
        if branch:
            add_args.append(branch)
        _, stderr, rc = await _run_git(repo, *add_args)
        if rc != 0:
            raise RuntimeError(
                f"git worktree add failed (exit {rc}): {stderr.strip()}"
            )
        result: dict = {"created": path}
        if branch:
            result["branch"] = branch
        return result

    if action == "remove":
        if not path:
            raise ValueError("'path' is required for action 'remove'.")
        _, stderr, rc = await _run_git(repo, "worktree", "remove", path)
        if rc != 0:
            raise RuntimeError(
                f"git worktree remove failed (exit {rc}): {stderr.strip()}"
            )
        return {"removed": path}

    raise ValueError(
        f"Unknown action '{action}'. Must be one of: list, add, remove."
    )
