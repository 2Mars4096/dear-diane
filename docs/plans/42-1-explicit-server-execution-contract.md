# 42-1: Explicit Server Execution Contract

**Parent:** [42-benchmark-execution-trustworthiness](42-benchmark-execution-trustworthiness.md)
**Status:** completed
**Goal:** Make `--server` mean server, fail fast when the backend is unavailable, and expose the real execution mode clearly enough that benchmark runs cannot accidentally degrade into local mode.

## Problem

Current CLI behavior is too forgiving:

- `dan-run` pings `/health`, then silently falls back to local mode if the server is unavailable.
- The CLI only prints the fallback mode in non-quiet paths, which makes it easy to miss during testing.
- `--server` is currently a **URL argument** (`--server http://...`), not a boolean "strict mode" flag — passing it only changes the base URL but still auto-detects and falls back to local if the ping fails.
- That behavior is acceptable for convenience, but not for benchmark trustworthiness.

For benchmark-grade runs, explicit server requests should be strict. If the user asked for server mode and the server is not healthy, the run should fail rather than pretending to honor the request.

## Tasks

- [ ] 1. Make explicit server mode strict
  - [ ] 1-1. Either repurpose `--server` (currently a URL arg) to also imply strict mode, or add a dedicated `--strict-server` / `--require-server` flag. Do not assume `--server` is already a boolean.
  - [ ] 1-2. In `DanClientOrLocal.detect_mode()`, when strict server mode is active, raise instead of returning `"local"` when `ping()` returns `False`.
  - [ ] 1-3. In `run_workflow()`, propagate the strict flag and return a clear error message that the server was requested but unavailable.

- [ ] 2. Surface execution mode clearly
  - [ ] 2-1. Print the chosen execution mode in non-quiet runs (already done for stderr: "Connected to dan-serve…" / "dan-serve not detected — running in local mode." — verify this is sufficient).
  - [ ] 2-2. Include the execution mode in machine-readable CLI output (currently no `execution_mode` field exists in structured output — needs new work).
  - [ ] 2-3. Distinguish "server requested" from "server actually active" (currently indistinguishable when `--quiet`).

- [ ] 3. Add regression coverage
  - [ ] 3-1. Test the hard-fail path when health is unavailable.
  - [ ] 3-2. Test the happy path when the server is healthy.
  - [ ] 3-3. Test that quiet mode does not hide the mode selection in structured outputs.

## Likely Files

| File | Why |
|------|-----|
| `src/dan/client/local.py` | `detect_mode()` owns the server-vs-local decision and fallback behavior — primary change target |
| `src/dan/cli/run.py` | `run_workflow()` selects mode based on `detect_mode()`, prints mode to stderr; needs strict-flag plumbing and structured output |
| `src/dan/client/client.py` | `ping()` / `is_server_available()` — health check only, no mode logic here |
| `tests/test_client/` or new test file | Regression coverage for strict mode — note: `tests/test_cli/` does not currently exist as a directory |

## Notes

- The goal is not to eliminate local mode. The goal is to stop pretending local mode satisfies an explicit server request.
- If a compatibility fallback remains, it should require an opt-in flag and be obvious in logs.
- This change is a prerequisite for credible benchmark execution.

## Audit Notes (2026-03-25)

- **`--server` is a URL argument** (`type=str`), not a boolean strict-mode flag. Tasks 1-1 through 1-3 updated to reflect this — implementer must decide flag semantics.
- **Mode selection is in `local.py` + `run.py`**, not `client.py`. `client.py` only provides `ping()`.
- **`tests/test_cli/` does not exist** — tests likely go in `tests/test_client/` or a new file.
- **Non-quiet stderr already prints mode** ("Connected to dan-serve…" / "running in local mode.") — task 2-1 is partially done.
- **No `execution_mode` field in structured output** — task 2-2 needs new work.
- **`detect_mode()` swallows all exceptions** via a bare `except Exception` and returns `"local"` — strict mode must change this behavior.

## Completion Notes (2026-03-26)

- Explicit `--server <url>` now implies strict server mode in `[src/dan/cli/run.py](/Volumes/data/Dropbox/Projects/deep-agent-network/src/dan/cli/run.py)`.
- `[DanClientOrLocal.detect_mode()](/Volumes/data/Dropbox/Projects/deep-agent-network/src/dan/client/local.py)` now raises on failed health checks when strict server mode is active.
- Structured CLI output now includes `execution_mode`, `server_requested`, and `server_url`.
- Server-mode runs now fetch the final `/api/runs/{run_id}` snapshot before rendering final output so machine-readable output is available in both local and server paths.
