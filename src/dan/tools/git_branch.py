"""Built-in tool: list, create, or switch git branches."""

from __future__ import annotations

from dan.tools._git_helpers import _find_repo, _run_git

TOOL_METADATA = {
    "tool_id": "git_branch",
    "description": (
        "List, create, or switch git branches. 'list' is read-only; "
        "'create' and 'switch' are WRITE operations that modify the working tree."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create", "switch"],
                "description": "The branch operation to perform.",
            },
            "name": {
                "type": "string",
                "description": "Branch name (required for 'create' and 'switch').",
            },
        },
        "required": ["action"],
    },
    "examples": [
        {
            "input": {"action": "list"},
            "output": {
                "branches": ["main", "feature/auth", "remotes/origin/main"],
                "current": "main",
            },
        },
        {
            "input": {"action": "create", "name": "feature/new"},
            "output": {"created": "feature/new"},
        },
        {
            "input": {"action": "switch", "name": "feature/new"},
            "output": {"switched": "feature/new"},
        },
    ],
    "category": "git",
    "returns": "dict — shape depends on action (see examples)",
}


async def git_branch(
    action: str,
    name: str | None = None,
    **_kwargs,
) -> dict:
    repo = _find_repo()

    if action == "list":
        stdout, stderr, rc = await _run_git(repo, "branch", "-a")
        if rc != 0:
            raise RuntimeError(f"git branch -a failed (exit {rc}): {stderr.strip()}")

        branches: list[str] = []
        current = ""
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("* "):
                branch_name = line[2:].strip()
                current = branch_name
                branches.append(branch_name)
            else:
                branches.append(line)

        return {"branches": branches, "current": current}

    if action == "create":
        if not name:
            raise ValueError("'name' is required for action 'create'.")
        _, stderr, rc = await _run_git(repo, "branch", name)
        if rc != 0:
            raise RuntimeError(f"git branch '{name}' failed (exit {rc}): {stderr.strip()}")
        return {"created": name}

    if action == "switch":
        if not name:
            raise ValueError("'name' is required for action 'switch'.")
        _, stderr, rc = await _run_git(repo, "switch", name)
        if rc != 0:
            raise RuntimeError(f"git switch '{name}' failed (exit {rc}): {stderr.strip()}")
        return {"switched": name}

    raise ValueError(f"Unknown action '{action}'. Must be one of: list, create, switch.")
