"""Built-in tool: run a shell command as a subprocess."""

from __future__ import annotations

import asyncio
import os
import shlex

_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off"}

TOOL_METADATA = {
    "tool_id": "shell_command",
    "description": (
        "Execute a shell command and capture its output. "
        "Runs as a subprocess with configurable timeout, working directory, "
        "and environment variables. Use for real command-line work such as "
        "running tests/builds/scripts, invoking project CLIs, checking command "
        "availability with command -v, and faithful filesystem operations that "
        "are better handled by the OS than by text reconstruction: mkdir, cp, "
        "mv, rsync, find, du, wc, sha256sum/shasum, tar, gzip, unzip, and similar "
        "standard utilities. Prefer structured file_read/file_write/file_edit "
        "for small precise file inspection or edits; prefer shell_command for "
        "bulk copies, directory transfers, checksums, archive operations, and "
        "commands whose output/result is the artifact being validated. Think of "
        "this as access to the local platform's command-line toolbox: when a task "
        "sounds like something an engineer would do in a terminal, actively choose "
        "the existing CLI, project script, Python one-liner, or POSIX utility that "
        "does the job most faithfully. Supports an allowlist via DAN_SHELL_ALLOW "
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
        {
            "input": {
                "command": "mkdir -p imported && cp -R /path/to/source imported/source",
                "working_directory": "/workspace",
            },
            "output": {"exit_code": 0, "stdout": "", "stderr": ""},
        },
        {
            "input": {"command": "find imported -maxdepth 2 -type f | head -50"},
            "output": {"exit_code": 0, "stdout": "imported/source/file.py\n...", "stderr": ""},
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
    run_env = _prepare_shell_env(working_directory, env)

    if _use_sandbox():
        return await _run_sandboxed(command, working_directory, timeout, run_env)

    return await _run_raw(command, working_directory, timeout, run_env)


def _dedupe_env_paths(parts: list[str]) -> str:
    unique: list[str] = []
    for part in parts:
        normalized = str(part or "").strip()
        if not normalized or normalized in unique:
            continue
        unique.append(normalized)
    return os.pathsep.join(unique)


def _prepare_shell_env(
    working_directory: str | None,
    env: dict | None,
) -> dict:
    prepared = dict(env or {})
    path_parts: list[str] = []
    existing_path = prepared.get("PATH") or os.environ.get("PATH", "")
    if existing_path:
        path_parts.extend(
            str(part).strip()
            for part in str(existing_path).split(os.pathsep)
            if str(part).strip()
        )
    path_parts.extend(
        [
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
    )
    prepared["PATH"] = _dedupe_env_paths(path_parts)

    normalized_working_directory = str(working_directory or "").strip()
    if not normalized_working_directory:
        return prepared

    workspace_root = os.path.abspath(normalized_working_directory)
    pythonpath_parts: list[str] = []
    workspace_src = os.path.join(workspace_root, "src")
    if os.path.isdir(workspace_src):
        pythonpath_parts.append(workspace_src)
    pythonpath_parts.append(workspace_root)

    existing_pythonpath = prepared.get("PYTHONPATH") or os.environ.get("PYTHONPATH", "")
    if existing_pythonpath:
        pythonpath_parts.extend(
            str(part).strip()
            for part in str(existing_pythonpath).split(os.pathsep)
            if str(part).strip()
        )
    prepared["PYTHONPATH"] = _dedupe_env_paths(pythonpath_parts)
    prepared.setdefault("PWD", workspace_root)
    return prepared


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
    working_directory: str | None,
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
    sandbox_command = command
    normalized_working_directory = str(working_directory or "").strip()
    if normalized_working_directory:
        sandbox_command = (
            f"cd {shlex.quote(os.path.abspath(normalized_working_directory))} && {command}"
        )

    runner = SandboxRunner()
    sandbox_result, _structured = await runner.run(sandbox_command, config, inputs)

    return {
        "exit_code": sandbox_result.exit_code,
        "stdout": sandbox_result.stdout[:MAX_OUTPUT_SIZE],
        "stderr": sandbox_result.stderr[:MAX_OUTPUT_SIZE],
    }
