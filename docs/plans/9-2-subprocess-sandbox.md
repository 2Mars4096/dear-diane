# 9-2: Subprocess Sandbox

**Parent:** [9-extended-capabilities](9-extended-capabilities.md)
**Status:** in-progress
**Goal:** Replace the in-process `exec()` in `CodeExecutor` with a subprocess-based execution model providing operational guardrails: configurable timeouts, resource caps, output limits, and language-agnostic execution. NOT a security sandbox (no OS-level isolation). Harden `shell_command` tool through the same runner.

## Tasks

- [x] 1. SandboxConfig model
  - [x] 1-1. `SandboxConfig` Pydantic model in `src/dan/sandbox/__init__.py`: `mode: Literal["inline", "subprocess"] = "inline"`, `timeout_seconds: int = 30`, `memory_mb: int | None = None`, `language: str = "python"`, `pass_env: list[str] = []` (env var names or prefixes to forward to subprocess, e.g. `["DAN_LLM_API_KEY", "OPENAI_*"]`), `filesystem_paths: list[str] = []` (allowed paths beyond workspace root), `max_output_bytes: int = 1_048_576`
  - [x] 1-2. `SandboxResult` dataclass: `stdout: str`, `stderr: str`, `exit_code: int`, `output_files: list[str]`, `duration_ms: float`, `memory_peak_mb: float | None`, `truncated: bool`
  - [x] 1-3. Migrate `CodeOperator.sandbox_config: dict[str, Any]` field to accept either raw dict (backward compat) or `SandboxConfig` via Pydantic validator. Default remains empty dict (equivalent to `mode="inline"`).
  - [x] 1-4. Default constants: `DEFAULT_TIMEOUT = 30`, `DEFAULT_MAX_OUTPUT = 1_048_576`, `DEFAULT_MEMORY_MB = 512`

- [x] 2. SandboxRunner core
  - [x] 2-1. `SandboxRunner` class in `src/dan/sandbox/runner.py` with `async run(code: str, config: SandboxConfig, inputs: dict) -> tuple[SandboxResult, dict | None]`
  - [x] 2-2. Temp directory lifecycle: create unique temp dir per run, write input data as `_inputs.json`, write code to `_script.{ext}`, clean up after execution
  - [x] 2-3. Subprocess execution via `asyncio.create_subprocess_exec` with the language adapter's command list
  - [x] 2-4. Timeout enforcement: `asyncio.wait_for(proc.communicate(), timeout=config.timeout_seconds)`. On timeout: `proc.kill()`, `await proc.wait()`, return SandboxResult with exit_code=-1 and descriptive stderr
  - [x] 2-5. Output collection: read stdout/stderr from subprocess pipes, read `_result.json` from temp dir if present (structured output)
  - [x] 2-6. Output truncation: cap stdout and stderr at `config.max_output_bytes`, set `truncated=True` if hit
  - [x] 2-7. Input injection: write `inputs` dict as `_inputs.json` in temp dir. Language adapters load this in their bootstrap code.

- [x] 3. Resource limits and operational guardrails
  - [x] 3-1. Memory limit: on Linux/macOS, set `resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))` via `preexec_fn` on subprocess. Best-effort: log warning if `resource` module unavailable (Windows).
  - [x] 3-2. Filesystem sandboxing: subprocess working directory is the temp dir.
  - [x] 3-3. Environment filtering: subprocess inherits `PATH`, `HOME`, `LANG`, `TERM` by default. Additional vars forwarded via `SandboxConfig.pass_env` (supports exact names and glob prefixes like `"DAN_*"` via `fnmatch`). `DAN_*` env vars are NOT inherited unless explicitly listed in `pass_env`.
  - [ ] 3-4. Output size cap already in 2-6. Additionally: kill subprocess if output pipe exceeds 10x max_output_bytes (prevents memory exhaustion in parent process).
  - [x] 3-5. Network isolation: explicitly out of scope for v1.

- [x] 4. Language adapters
  - [x] 4-1. `LanguageAdapter` protocol in `src/dan/sandbox/adapters.py`: `prepare(code: str, config: SandboxConfig, temp_dir: Path) -> tuple[list[str], str]` returning (command args, script filename)
  - [x] 4-2. `PythonAdapter`: writes code to `_script.py` with bootstrap preamble (load `_inputs.json`, write `_result.json` from `result` variable). Command: `[sys.executable, "-u", script_path]`
  - [x] 4-3. `ShellAdapter`: writes code to `_script.sh`, makes executable. Command: `["/bin/sh", script_path]`. Input available via env vars.
  - [x] 4-4. Adapter registry: `{"python": PythonAdapter(), "shell": ShellAdapter()}`. Extensible for future languages (Node.js, R) without changing runner core.

- [x] 5. CodeExecutor upgrade
  - [x] 5-1. Parse `SandboxConfig` from `CodeOperator.sandbox_config` field (dict or model)
  - [x] 5-2. Route: `mode="inline"` or absent config -> existing `exec()` path unchanged. `mode="subprocess"` -> `SandboxRunner.run()`
  - [x] 5-3. Map `SandboxResult` to `NodeResult`: structured output from `_result.json` -> `outputs`, stdout/stderr -> emit `code_output` event, timeout/error -> FAILED status
  - [x] 5-4. on_failure handling: `skip` returns empty SKIPPED, `halt` returns FAILED with `metadata.halt=True` (same as current)
  - [x] 5-5. Language routing: `CodeOperator.language` field maps to adapter key. Default `"python"` for both inline and subprocess modes.
  - [x] 5-6. Backward compat test: existing code nodes with no sandbox_config produce identical behavior to current

- [x] 6. shell_command tool upgrade
  - [x] 6-1. Refactor `shell_command` to use `SandboxRunner` with `ShellAdapter` when `DAN_SANDBOX_SHELL=true` env var is set
  - [x] 6-2. Default behavior unchanged: raw `create_subprocess_shell` with existing allowlist
  - [x] 6-3. When sandbox mode enabled: inherit resource limits from `DAN_SANDBOX_TIMEOUT`, `DAN_SANDBOX_MEMORY_MB` env vars
  - [x] 6-4. Preserve existing `DAN_SHELL_ALLOW` prefix allowlist enforcement regardless of sandbox mode
  - [x] 6-5. Backward compat: no behavioral change for users who do not set `DAN_SANDBOX_SHELL`

- [x] 7. Event emission
  - [x] 7-1. Register `SANDBOX_STARTED` and `SANDBOX_COMPLETED` event types in `src/dan/engine/events.py` (already done by shared foundation)
  - [x] 7-2. `SANDBOX_STARTED` event data: `language`, `mode`, `timeout_seconds`, `memory_mb`
  - [x] 7-3. `SANDBOX_COMPLETED` event data: `exit_code`, `duration_ms`, `memory_peak_mb`, `truncated`, `output_size_bytes`
  - [x] 7-4. CodeExecutor emits sandbox events around SandboxRunner.run() call (only in subprocess mode)

- [ ] 8. Visual editor integration
  - [ ] 8-1. TypeScript `SandboxConfig` interface in `types/graph.ts`
  - [ ] 8-2. ConfigPanel: sandbox section for `code_operator` nodes. Mode toggle (inline/subprocess), timeout slider (1-300s), memory limit input (MB), language dropdown (python/shell)
  - [ ] 8-3. DanNode: show sandbox mode indicator icon on code nodes when mode=subprocess
  - [ ] 8-4. LogPanel: render SANDBOX_STARTED/COMPLETED events with appropriate icons and data preview

- [ ] 9. Builder DSL and decompiler
  - [ ] 9-1. `wf.code(node_id, ..., sandbox=SandboxConfig(...))` in builder. Compiler maps SandboxConfig to `sandbox_config` dict field.
  - [ ] 9-2. Decompiler: if `sandbox_config` is non-empty, emit `sandbox=SandboxConfig(...)` kwarg with non-default fields only

- [x] 10. Tests
  - [x] 10-1. `SandboxRunner` unit: successful Python execution, timeout kill, large output truncation, input injection via _inputs.json, structured output via _result.json
  - [x] 10-2. `PythonAdapter`: bootstrap preamble correctness, result variable capture, missing result variable handling
  - [x] 10-3. `ShellAdapter`: script execution, env var input, exit code propagation
  - [x] 10-4. Resource limits: memory limit enforcement (platform-dependent, skip on unsupported OS)
  - [x] 10-5. `CodeExecutor` inline mode: identical behavior to current (regression)
  - [x] 10-6. `CodeExecutor` subprocess mode: Python code, shell code, timeout, error handling
  - [x] 10-7. `shell_command` tool: backward compat (no sandbox env), sandbox mode (with env vars)
  - [x] 10-8. Event emission: SANDBOX_STARTED/COMPLETED events with correct data fields
  - [x] 10-9. Backward compat: existing code nodes with empty sandbox_config behave identically
  - [ ] 10-10. Update `test_builtins_registered` count if applicable

- [ ] 11. Docs sync
  - [x] 11-1. `architecture.md`: add `src/dan/sandbox/` package to directory tree, document SandboxRunner, SandboxConfig, adapters
  - [ ] 11-2. `llm-api-guide.md`: CodeOperator `sandbox_config` field reference, SandboxConfig parameters
  - [ ] 11-3. `README.md`: subprocess sandbox capability in feature summary
  - [x] 11-4. `pyproject.toml`: no new deps required (asyncio subprocess is stdlib)

## Decisions

- `SandboxRunner.run()` returns `tuple[SandboxResult, dict | None]` — the second element is the parsed `_result.json` (structured output). Read before temp dir cleanup.
- `fnmatch.fnmatch()` used for glob matching in `pass_env` patterns (e.g. `"OPENAI_*"` matches all OPENAI_-prefixed vars).
- `preexec_fn` with `resource.setrlimit(RLIMIT_AS)` — best-effort on macOS ARM (RLIMIT_AS may not be enforced for all allocation patterns), reliable on Linux.
- Invalid `sandbox_config` dict falls back to inline mode with a warning — no crash on malformed config.

## Notes

- `CodeOperator.sandbox_config` already exists as `dict[str, Any]`. This plan formalizes it without breaking existing serialized graphs.
- `shell_command` tool already implements the timeout + subprocess pattern via `asyncio.create_subprocess_shell`. SandboxRunner generalizes this.
- The inline `exec()` path is intentionally preserved: it is faster (no subprocess overhead), simpler (no temp dir), and sufficient for development. Subprocess mode provides operational guardrails (timeouts, memory caps, output limits) — it is NOT a security sandbox and should not be relied upon for running untrusted code.
- Memory limits via `resource.setrlimit` are best-effort on macOS (RLIMIT_AS may not be enforced for all allocation patterns). Linux enforcement is reliable.
- Network isolation requires OS-level mechanisms (network namespaces, seccomp, or Docker). Documented as out-of-scope for v1; can be added as a SandboxRunner backend later.
- The adapter pattern is designed for extensibility: adding Node.js support later requires only a new adapter class and registry entry.
