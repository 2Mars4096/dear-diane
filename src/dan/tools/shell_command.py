"""Built-in tool: run a shell command as a subprocess."""

from __future__ import annotations

import asyncio
import os

TOOL_METADATA = {
    "tool_id": "shell_command",
    "description": (
        "Execute a shell command and capture its output. "
        "Runs as a subprocess with configurable timeout, working directory, "
        "and environment variables. Supports an allowlist via DAN_SHELL_ALLOW "
        "for security-sensitive deployments."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "working_directory": {
                "type": "string",
                "description": "Working directory for the command (defaults to workspace root).",
            },
            "timeout": {
                "type": "integer",
                "description": "Maximum execution time in seconds.",
                "default": 30,
            },
            "env": {
                "type": "object",
                "description": "Additional environment variables (merged with current env).",
            },
        },
        "required": ["command"],
    },
    "examples": [
        {
            "input": {"command": "echo hello"},
            "output": {"exit_code": 0, "stdout": "hello\n", "stderr": ""},
        },
        {
            "input": {"command": "ls -la", "working_directory": "src"},
            "output": {"exit_code": 0, "stdout": "total 8\n...", "stderr": ""},
        },
    ],
    "category": "system",
    "returns": "dict with exit_code, stdout, and stderr",
}

MAX_OUTPUT_SIZE = 1_048_576  # 1 MB


def _check_allowlist(command: str) -> None:
    """Enforce DAN_SHELL_ALLOW prefix allowlist when set."""
    raw = os.environ.get("DAN_SHELL_ALLOW", "").strip()
    if not raw:
        return
    allowed = [prefix.strip() for prefix in raw.split(",") if prefix.strip()]
    if not any(command.startswith(prefix) for prefix in allowed):
        raise PermissionError(
            f"Command '{command}' is not in the allowed command list. "
            f"Allowed prefixes: {allowed}"
        )


async def shell_command(
    command: str,
    working_directory: str | None = None,
    timeout: int = 30,
    env: dict | None = None,
    **_kwargs,
) -> dict:
    _check_allowlist(command)

    run_env = {**os.environ, **(env or {})}
    cwd = working_directory or None

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=run_env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout} seconds.",
        }

    return {
        "exit_code": proc.returncode,
        "stdout": (stdout_bytes or b"").decode("utf-8", errors="replace")[:MAX_OUTPUT_SIZE],
        "stderr": (stderr_bytes or b"").decode("utf-8", errors="replace")[:MAX_OUTPUT_SIZE],
    }
