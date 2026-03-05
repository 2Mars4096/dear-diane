# 23-3: CLI Thin Client

**Parent:** [23-multi-surface-gateway](23-multi-surface-gateway.md)
**Status:** completed
**Goal:** Refactor `dan-run` and related CLI commands to route through `dan-serve` via `DanClient` when the server is available, falling back to direct engine execution when it's not.

## Context

The CLI (built in Phase 12, plan 21-2) currently creates its own Engine instance and runs workflows directly. This plan refactors it to use DanClient, which routes through the server's gateway API. The Rich TUI is unchanged — it just consumes events from DanClient instead of direct engine callbacks. The fallback ensures `dan-run` still works without a running server.

## Tasks

- [ ] 1. **Refactor dan-run to use DanClient**
  - [x] 1-1. Import `DanClientOrLocal` from `dan.client`
  - [x] 1-2. On startup: check if server is available via `client.is_server_available()`
  - [x] 1-3. Server mode: `client.dispatch(workflow_path=path, inputs=inputs, surface_id="cli")` → get run_id → `client.subscribe_run(run_id)` → feed events to Rich TUI
  - [x] 1-4. Local mode (fallback): existing direct Engine.run() path (unchanged from 21-2)
  - [x] 1-5. `--local` flag: force direct engine mode even if server is available
  - [x] 1-6. `--server <url>` flag: override server URL (default: localhost:8000)
  - [ ] 1-7. Refactor `run_background()`: in server mode, `client.dispatch()` + print `run_id` + exit (server handles backgrounding, no subprocess spawn needed). In local mode, keep existing `subprocess.Popen` behavior. Update `dan-status`/`dan-logs` to work with server-managed background runs.
  - [ ] 1-8. `--resume <run_id>` flag: in server mode, calls `client.resume_run(run_id)` → subscribe to events → feed to TUI. In local mode, load checkpoint and resume directly.
- [ ] 2. **Rich TUI event source abstraction**
  - [ ] 2-1. Create `EventSource` protocol: `async events() -> AsyncIterator[EngineEvent]`
  - [ ] 2-2. `ServerEventSource`: wraps `DanClient.subscribe_run()`
  - [ ] 2-3. `LocalEventSource`: wraps direct engine event callback (existing)
  - [ ] 2-4. Rich TUI code accepts `EventSource` — no changes to display logic
- [ ] 3. **HumanNode in server mode**
  - [x] 3-1. In server mode, HumanNode prompts arrive as events from the subscription
  - [x] 3-2. CLI detects `human_input_needed` event (matching `EngineEvent.HUMAN_INPUT_NEEDED`), renders prompt via Rich (same interactive UX as before)
  - [x] 3-3. User response submitted via `client.submit_human_input(run_id, request_id, response)`
  - [ ] 3-4. `--headless` still works: auto-respond or skip HumanNode
  - [ ] 3-5. Graceful shutdown: on SIGINT/SIGTERM in server mode, call `client.cancel_run(run_id)` to cancel the server-side run. Display "Cancelling run {run_id}..." and wait for `run_cancelled` event before exiting.
- [ ] 4. **dan-status refactor**
  - [ ] 4-1. When server available: `client.get_activity()` → show all runs from all surfaces
  - [ ] 4-2. When server unavailable: scan local `~/.dan/runs/` (existing behavior)
  - [ ] 4-3. Output shows surface origin column (cli/telegram/mcp/chat)
  - [ ] 4-4. Show checkpoint info when available: `client.get_checkpoints(run_id)` for resumable runs
- [ ] 5. **dan-logs refactor**
  - [ ] 5-1. When server available: `client.subscribe_run(run_id)` for live streaming, or `client.get_run_events(run_id)` for historical
  - [ ] 5-2. When server unavailable: tail local JSONL files (existing behavior)
- [ ] 6. **Tests**
  - [ ] 6-1. Unit tests for EventSource protocol implementations
  - [ ] 6-2. Unit tests for server mode dispatch → subscribe → TUI flow (mocked DanClient)
  - [ ] 6-3. Unit tests for fallback detection: server up → server mode, server down → local mode
  - [ ] 6-4. Integration test: dan-run with real dan-serve → workflow completes → correct output

## Decisions

- Fallback is automatic and transparent — user doesn't need to know which mode is active (but a startup message says "Connected to dan-serve" or "Running in local mode")
- `--local` flag for explicit direct-engine override (useful for debugging, CI)
- Rich TUI is unchanged — only the event source switches
- Existing CLI flags (`--input`, `--headless`, `--quiet`, etc.) work identically in both modes
- NL goal detection (text → MetaController) works in both modes: server mode dispatches with `text=`, local mode runs MetaController directly

## Notes

- The refactor is primarily in the startup/dispatch path — the presentation layer (Rich TUI) is not touched
- dan-status becomes more useful in server mode: it shows runs from ALL surfaces, not just CLI
- Background mode (`--bg`) in server mode: just dispatch and exit, no need for `subprocess.Popen` — the server handles backgrounding. This is now an explicit task (1-7), not just a note.
- `--resume` reuses the server's existing `POST /api/runs/{id}/resume` endpoint — no new server-side work needed.
- Graceful shutdown in server mode requires `cancel_run()` — without it, Ctrl+C would disconnect the client but leave the server-side run hanging.
- When server is not running and `--local` is not set, CLI prints: "dan-serve not detected — running in local mode. Start dan-serve for cross-surface features." This guides users toward the gateway without blocking them.
