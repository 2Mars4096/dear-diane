"""SandboxRunner — subprocess execution with operational guardrails."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from dan.sandbox import DEFAULT_MEMORY_MB, SandboxConfig, SandboxResult
from dan.sandbox.adapters import ADAPTERS

logger = logging.getLogger(__name__)

_BASE_ENV_KEYS = {"PATH", "HOME", "LANG", "TERM"}


def _filter_env(pass_env: list[str]) -> dict[str, str]:
    """Build a minimal environment from current env.

    Always includes PATH, HOME, LANG, TERM.  Additional variables are
    forwarded only if they match an entry in *pass_env* (exact name or
    glob pattern via ``fnmatch``).
    """
    env: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val

    if not pass_env:
        return env

    for key, val in os.environ.items():
        if key in _BASE_ENV_KEYS:
            continue
        for pattern in pass_env:
            if fnmatch.fnmatch(key, pattern):
                env[key] = val
                break
    return env


def _make_preexec(memory_mb: int | None) -> Any:
    """Return a preexec_fn that sets RLIMIT_AS, or None if unsupported."""
    if memory_mb is None:
        return None
    try:
        import resource  # noqa: F811
    except ImportError:
        logger.warning("resource module unavailable — memory limit not enforced")
        return None

    memory_bytes = memory_mb * 1024 * 1024

    def _set_limits() -> None:
        import resource as _res

        try:
            _res.setrlimit(_res.RLIMIT_AS, (memory_bytes, memory_bytes))
        except (ValueError, OSError) as exc:
            import sys

            print(f"Warning: could not set memory limit: {exc}", file=sys.stderr)

    return _set_limits


class SandboxRunner:
    """Executes code in a subprocess with configurable guardrails."""

    async def run(
        self,
        code: str,
        config: SandboxConfig,
        inputs: dict[str, Any],
    ) -> tuple[SandboxResult, dict[str, Any] | None]:
        """Execute code and return ``(result, structured_output)``.

        *structured_output* is the parsed ``_result.json`` if the script
        wrote one, otherwise ``None``.
        """
        language = config.language
        adapter = ADAPTERS.get(language)
        if adapter is None:
            return SandboxResult(
                stderr=f"Unsupported language: '{language}'. Available: {sorted(ADAPTERS)}",
                exit_code=-1,
            ), None

        temp_dir = Path(tempfile.mkdtemp(prefix="dan_sandbox_"))
        try:
            result = await self._execute(code, config, inputs, adapter, temp_dir)
            structured = self._read_result_json(temp_dir)
            return result, structured
        finally:
            self._cleanup(temp_dir)

    async def _execute(
        self,
        code: str,
        config: SandboxConfig,
        inputs: dict[str, Any],
        adapter: Any,
        temp_dir: Path,
    ) -> SandboxResult:
        inputs_path = temp_dir / "_inputs.json"
        inputs_path.write_text(json.dumps(inputs, default=str), encoding="utf-8")

        cmd, _script_name = adapter.prepare(code, config, temp_dir)

        env = _filter_env(config.pass_env)
        if config.language == "shell":
            for k, v in inputs.items():
                if isinstance(v, str):
                    env[k] = v
                else:
                    env[k] = json.dumps(v, default=str)

        preexec = _make_preexec(config.memory_mb)

        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(temp_dir),
                env=env,
                preexec_fn=preexec,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()  # type: ignore[union-attr]
            await proc.wait()  # type: ignore[union-attr]
            duration_ms = (time.monotonic() - t0) * 1000
            return SandboxResult(
                stdout="",
                stderr=f"Execution timed out after {config.timeout_seconds} seconds.",
                exit_code=-1,
                duration_ms=duration_ms,
            )

        duration_ms = (time.monotonic() - t0) * 1000

        stdout_str = (stdout_bytes or b"").decode("utf-8", errors="replace")
        stderr_str = (stderr_bytes or b"").decode("utf-8", errors="replace")

        truncated = False
        if len(stdout_str) > config.max_output_bytes:
            stdout_str = stdout_str[: config.max_output_bytes]
            truncated = True
        if len(stderr_str) > config.max_output_bytes:
            stderr_str = stderr_str[: config.max_output_bytes]
            truncated = True

        output_files: list[str] = []
        result_path = temp_dir / "_result.json"
        if result_path.exists():
            output_files.append("_result.json")

        return SandboxResult(
            stdout=stdout_str,
            stderr=stderr_str,
            exit_code=proc.returncode or 0,
            output_files=output_files,
            duration_ms=duration_ms,
            truncated=truncated,
        )

    @staticmethod
    def _cleanup(temp_dir: Path) -> None:
        """Best-effort cleanup of the temporary directory."""
        import shutil

        try:
            shutil.rmtree(temp_dir)
        except OSError:
            logger.debug("Failed to clean up temp dir: %s", temp_dir)

    @staticmethod
    def _read_result_json(temp_dir: Path) -> dict[str, Any] | None:
        """Read structured output from ``_result.json`` if present."""
        result_path = temp_dir / "_result.json"
        if not result_path.exists():
            return None
        try:
            return json.loads(result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
