# 38-14: Adapter Snapshot and CLI Shutdown Hardening

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** completed
**Goal:** Close the next live-debugging follow-ups: make adapter status polling accept both sync and async connection snapshots, and stop `dan-chat` from dumping a traceback on normal `Ctrl-C` exits.

## Tasks
- [x] 1. Normalize adapter snapshot polling across adapter types.
 - [x] 1-1. Add one router helper that accepts sync or async `get_connection_snapshot()` implementations.
 - [x] 1-2. Reuse it in both the adapter heartbeat loop and the `/api/adapters/status` / config-summary paths.
 - [x] 1-3. Add regression coverage proving async Telegram snapshots and sync WhatsApp snapshots both work.
- [x] 2. Make `dan-chat` exit cleanly on `Ctrl-C`.
 - [x] 2-1. Make top-level client shutdown best-effort when the loop is already being cancelled.
 - [x] 2-2. Swallow the final top-level `KeyboardInterrupt` so REPL exits do not print a traceback.
 - [x] 2-3. Add focused CLI tests for the quiet close / quiet main-exit behavior.

## Decisions
- Keep the adapter contract flexible instead of forcing every adapter onto an async snapshot API; the router now normalizes either sync or async implementations.
- Treat `dan-chat` shutdown as a UX path, not an error path: closing the HTTP client during `Ctrl-C` is best-effort and should never produce a second traceback.

## Notes
- Root cause: Telegram exposes `async def get_connection_snapshot(...)`, while WhatsApp Web exposes a synchronous method. The router had split expectations in opposite directions, so one path leaked un-awaited coroutines and another silently failed for sync adapters.
- Validation: `python -m pytest tests/test_server/test_adapters_api.py tests/test_cli/test_chat.py -q`.
