# 38-3: Startup & Config Hardening

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** completed
**Goal:** Make server startup resilient when `~/.dan` is not writable, and eliminate import-time config freezing for telemetry.

## Context

Two related issues:
1. The block registry eagerly writes `~/.dan/blocks/_index.json` during startup, and the Furnace session store eagerly `mkdir`s `~/.dan/furnace/sessions`. If `~/.dan` is not writable, the server fails before startup completes — causing 85 downstream test errors.
2. Telemetry DB path (`DAN_TELEMETRY_DB`) is read at module import time and never re-evaluated, so tests and embedded runtimes that set the env var after import silently use the wrong path.

Relevant code:
- `src/dan/blocks/registry.py`: `scan()`, `_write_index()`
- `src/dan/server/startup.py`: `init_stores()`
- `src/dan/engine/recipe/session_store.py`: `FurnaceSessionStore.__init__()`
- `src/dan/server/telemetry.py`: `_TELEMETRY_DB` (L28, module-level), `SQLiteTelemetryStore.__init__()` (L384–392, falls back to `_TELEMETRY_DB` when `db_path=None`), `get_telemetry_store()` (L434–441, passes `db_path` through to the store)

## Tasks

- [x] 1. Make block registry write best-effort
  - [x] 1-1. Wrap `_write_index()` in a try/except that logs a warning instead of crashing
  - [x] 1-2. If the index file is not writable, the registry should still function in read-only mode (in-memory index from scan results)
- [x] 2. Make Furnace session store startup lazy
  - [x] 2-1. Defer `mkdir` in `FurnaceSessionStore.__init__()` to first actual use (first session create/load)
  - [x] 2-2. If `DAN_FURNACE_API_ENABLED=0`, skip store initialization entirely
- [x] 3. Add safe-mode startup path
  - [x] 3-1. When any home-directory write fails during startup, log a warning and continue with degraded capabilities
  - [x] 3-2. Surface the degraded state via a status endpoint or log message (not a hard crash)
- [x] 4. Make telemetry DB path dynamic
  - [x] 4-1. Remove module-level `_TELEMETRY_DB = os.environ.get(...)` (L28)
  - [x] 4-2. Read `DAN_TELEMETRY_DB` at call time inside `SQLiteTelemetryStore.__init__()` (where the fallback to `_TELEMETRY_DB` actually happens at L386, not in `get_telemetry_store()` which just passes `db_path` through)
  - [x] 4-3. Also check `_RETENTION_DAYS` (L29) which is similarly frozen at import time
  - [x] 4-4. Verify that the existing `get_telemetry_store()` caching still works correctly with dynamic resolution
- [x] 5. Add tests
  - [x] 5-1. Test that server starts cleanly with a read-only `~/.dan` (block registry degrades, doesn't crash)
  - [x] 5-2. Test that `FurnaceSessionStore` does not write to disk until a session is actually created
  - [x] 5-3. Test that setting `DAN_TELEMETRY_DB` after import is respected by `get_telemetry_store()`
  - [x] 5-4. Test startup under a simulated home-write failure matching the restricted-`HOME` cascade scenario

## Decisions

- Treat startup-time home-directory writes as best-effort so the server can continue booting with degraded capabilities instead of crashing.
- Make Furnace session persistence lazy rather than eager; session directories are created on the first actual write.
- Resolve telemetry env configuration at call time to keep tests and embedded runtimes deterministic even when env vars are set after import.

## Notes

- The 85 cascading test errors in the full suite all trace back to the `PermissionError` in `_write_index()`. Fixing task 1 alone should eliminate most of them.
- `remove_block()` also calls `_write_index(_USER_BLOCKS_ROOT)`, so wrapping `_write_index()` itself covers both `scan()` and `remove_block()`.
- The module audit recommends preferring explicit config objects over module-level env lookups as a general pattern.
- Validation landed in `tests/test_server/test_startup.py` and `tests/test_server/test_telemetry.py`.
