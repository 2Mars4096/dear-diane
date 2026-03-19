"""Built-in tool: run a shell command as a subprocess."""

from __future__ import annotations

import asyncio
import os

_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off"}

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


def _use_sandbox() -> bool:
    """Check whether sandbox mode is enabled via env var."""
    raw = os.environ.get("DAN_SANDBOX_SHELL", "").strip().lower()
    if not raw:
        return True
    if raw in _FALSE_VALUES:
        return False
    return True


def shell_sandbox_explicitly_disabled() -> bool:
    raw = os.environ.get("DAN_SANDBOX_SHELL", "").strip().lower()
    return bool(raw) and raw in _FALSE_VALUES


async def shell_command(
    command: str,
    working_directory: str | None = None,
    timeout: int = 30,
    env: dict | None = None,
    **_kwargs,
) -> dict:
    _check_allowlist(command)

    if _use_sandbox():
        return await _run_sandboxed(command, timeout, env)

    return await _run_raw(command, working_directory, timeout, env)


async def _run_raw(
    command: str,
    working_directory: str | None,
    timeout: int,
    env: dict | None,
) -> dict:
    """Original subprocess path — no sandbox wrapper."""
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


async def _run_sandboxed(
    command: str,
    timeout: int,
    env: dict | None,
) -> dict:
    """Run command through SandboxRunner with ShellAdapter."""
    from dan.sandbox import SandboxConfig
    from dan.sandbox.runner import SandboxRunner

    env_timeout = os.environ.get("DAN_SANDBOX_TIMEOUT")
    env_memory = os.environ.get("DAN_SANDBOX_MEMORY_MB")

    config = SandboxConfig(
        mode="subprocess",
        language="shell",
        timeout_seconds=int(env_timeout) if env_timeout else timeout,
        memory_mb=int(env_memory) if env_memory else None,
        pass_env=list((env or {}).keys()),
    )

    inputs = env or {}

    runner = SandboxRunner()
    sandbox_result, _structured = await runner.run(command, config, inputs)

    return {
        "exit_code": sandbox_result.exit_code,
        "stdout": sandbox_result.stdout[:MAX_OUTPUT_SIZE],
        "stderr": sandbox_result.stderr[:MAX_OUTPUT_SIZE],
    }
