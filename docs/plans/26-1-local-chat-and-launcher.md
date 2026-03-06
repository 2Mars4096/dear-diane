# 26-1: Local Chat and Launcher

**Parent:** [26-always-on-service](26-always-on-service.md)
**Status:** completed
**Goal:** `dan-chat` works without `dan-serve` via in-process ChatManager; `dan up` / `dan down` provide single-command zero-to-chat with background server management.

## Context

Phase 15 (Plan 25) makes chat the unified control plane. Phase 16 makes it always available. This plan **promotes and supersedes** the deferred [21-8-local-cli-chat-fallback](21-8-local-cli-chat-fallback.md).

Currently `dan-chat` (in `src/dan/cli/chat.py`) requires a running `dan-serve` server. It uses `ChatClient` with httpx/websockets to connect to `http://127.0.0.1:8000`. When the server is unavailable, it exits with an error. `dan-run` already has a local fallback via `DanClientOrLocal` in `src/dan/client/local.py` — it detects server availability and runs the engine in-process when unreachable. This plan brings the same pattern to chat: local mode when server is down, plus `dan up` / `dan down` for zero-friction entry.

## Tasks

- [x] 1. **LocalChatRuntime**
  - [x] 1-1. Extract shared chat dependencies from `app.py` lifespan into a reusable factory (`build_chat_services()` in `src/dan/server/chat_factory.py`).
  - [x] 1-2. Create `LocalChatRuntime` class in `src/dan/cli/chat_local.py` that mirrors `ChatClient` interface.
  - [x] 1-3. Local storage at `~/.dan/local/` with paths that match existing store layouts.
  - [x] 1-4. Bootstrap `_scratch` graph on first use.

- [x] 2. **dan-chat local fallback**
  - [x] 2-1. On startup in `main()`: if `ChatClient.ping()` fails and `--local` not set, switch to `LocalChatRuntime` instead of exiting. If `--local` is set, skip ping and use local immediately.
  - [x] 2-2. Print "Local mode (no server)" banner when running locally so user knows the mode.
  - [x] 2-3. Add `--local` flag to force local mode even if server is available.
  - [x] 2-4. All REPL commands work in both modes via the unified client interface.

- [x] 3. **Local mutation apply**
  - [x] 3-1. In `LocalChatRuntime.apply_mutation()`: call `GraphMutator().apply()` in-process.
  - [x] 3-2. Dry-run validation parity with server mode.

- [x] 4. **Local run from chat**
  - [x] 4-1. `/run` reuses `parse_run_command()` and `build_scoped_graph()` from `scoped_run.py`.
  - [x] 4-2. Back local chat runs with a local `RunManager` + `RunStore`.
  - [x] 4-3. Stream events via internal queues with `chat_run_event` envelope.
  - [x] 4-4. `submit_human_input()` delegates to local `RunManager`.

- [x] 5. **`dan up` / `dan down` commands**
  - [x] 5-1. New entry points `dan-up` and `dan-down` in `pyproject.toml`.
  - [x] 5-2. `dan up`: check PID, start server, health poll, drop into chat.
  - [x] 5-3. `dan down`: read PID, SIGTERM, timeout+SIGKILL, cleanup.
  - [x] 5-4. Entry points registered.

- [x] 6. **PID file management**
  - [x] 6-1. PID file at `~/.dan/server.pid`. Format: `PID\nPORT`.
  - [x] 6-2. Stale PID detection via `os.kill(pid, 0)`.
  - [x] 6-3. Lock file to prevent concurrent `dan up` starts.

- [x] 7. **Tests** — 40 tests in `tests/test_cli/test_chat_local.py`
  - [x] 7-1. Unit tests for `LocalChatRuntime`: ping, graph ops (get/list/create/save), mutation apply (success + missing graph), chat streaming, run command routing, bootstrap, close, submit_human_input, cancel_run.
  - [x] 7-2. Unit tests for PID management: write/read, stale detection, corrupt file handling, default port, remove (existing + missing).
  - [x] 7-3. Unit tests for `dan down`: no PID file, invalid PID file, stale PID.
  - [x] 7-4. Unit tests for chat.py parser: `--local` flag, `--confirm` flag.
  - [x] 7-5. Unit tests for `chat_factory`: `build_chat_services`, `_build_engine_config`, `_build_tool_registry`.
  - [x] 7-6. Unit tests for `dan up` helpers: `check_health` (no server), `wait_for_health` (timeout), startup lock acquire/release, and lock-contention exit path.
  - [ ] 7-7. Integration test (deferred — requires LLM).

## Files to Touch

| File | Changes |
|------|---------|
| `src/dan/cli/chat_local.py` | New: `LocalChatRuntime` class mirroring `ChatClient` interface |
| `src/dan/cli/chat.py` | Fallback detection, `--local` flag, use `LocalChatRuntime` when server unavailable |
| `src/dan/server/chat_factory.py` | New shared dependency factory for chat/runtime services extracted from `app.py` |
| `src/dan/server/app.py` | Reuse shared chat dependency factory in lifespan |
| `src/dan/server/scoped_run.py` | Reuse existing run-command parsing and run-event mapping helpers for local mode parity |
| `src/dan/cli/up.py` | New: `dan up` — PID check, start server, health poll, spawn `dan-chat` |
| `src/dan/cli/down.py` | New: `dan down` — read PID, SIGTERM, cleanup |
| `src/dan/cli/__init__.py` | Ensure `DAN_DIR`, `ensure_dan_dir()` support `~/.dan/local/` if needed |
| `pyproject.toml` | Add `dan-up`, `dan-down` entry points |
| `tests/test_cli/test_chat_local.py` | New: unit + integration tests for LocalChatRuntime, fallback, PID, dan-up flow |

## Decisions

- Used lazy import in `_ensure_init()` to avoid pulling server-weight dependencies until first method call.
- `chat_factory.py` duplicates `_get_engine_config()` / `_build_chat_provider_registry()` logic from `app.py` rather than importing from it, to avoid circular imports and FastAPI dependency pull-in for CLI-only usage.
- `LocalChatRuntime` uses `asyncio.Queue` per stream channel for internal event streaming, matching the WebSocket-based server flow.
- Dry-run validation runs before apply in local mode for parity with server's apply-mutation endpoint.
- `dan up` uses `os.execvp` to replace the process with `dan-chat` after server is ready, keeping the UX simple.
- `dan up` now holds a non-blocking lock on `~/.dan/server.lock` during startup checks to prevent concurrent launches from racing PID/health bookkeeping.

## Notes

- `ChatManager` constructor is currently small (`provider_registry`, `graph_store`, `mention_resolver`), but local mode also needs the surrounding chat/runtime services currently assembled in `app.py` lifespan.
- `GraphMutator.apply()` returns `MutationResult` with `success`, `new_graph`, `errors`. Server apply-mutation in `app.py` (around line 1525) uses `GraphMutator().apply(data, plan, current_revision=revision)`.
- `DanClientOrLocal._dispatch_local()` is useful as a local/server detection reference, but it is not sufficient for chat parity because local `submit_human_input()` currently returns `False` and its bare-`Engine` path does not preserve chat run semantics.
- `dan.server.__main__:main` starts uvicorn with `dan.server.app:app`. Default host `127.0.0.1`, port `8000`. `dan up` should pass `--port` if configurable.
- Effort estimate: ~3 days.
