# Code Review Report

Date: 2026-03-17

Scope: repository-wide review with emphasis on current backend health, recent server/provider changes, and editor regressions.

## What I Ran

- `pytest -q`
- `pytest -q --maxfail=1`
- targeted failing tests for telemetry, clipboard, Google provider, and preference extraction
- `cd editor && npm test`

Results:

- Python: `6302 passed, 9 failed, 85 errors, 20 skipped, 48 deselected`
- Frontend: `81 passed`

## Summary

The editor test suite is currently healthy. The Python suite is not: most of the error volume collapses into one startup-path failure, and the remaining failures cluster around domain learning, telemetry configuration, Google provider regressions, and environment-sensitive system tooling.

## Findings

### P1: Furnace source IDs are written into artifact paths without sanitization

Impact:

- A caller can escape the session artifact directory and write files elsewhere under the server's writable roots.
- This is a path-safety issue, not just a data-corruption issue.

Evidence:

- `src/dan/server/routers/furnace.py`
  - `add_sources()` accepts arbitrary `source_ids` and stores them unchanged.
  - `_execute_furnace_pipeline()` later writes artifacts to `(artifact_dir / f"{source_id}.txt")` and `(artifact_dir / f"{source_id}.chunks.json")`.

Why this matters:

- `Path("/safe/root") / "../../escape.txt"` resolves outside the artifact directory.
- This means a malicious or malformed `source_id` such as `../../escape` can redirect writes outside the per-session sandbox.

Observed validation:

- Local path check: `Path('/tmp/artifacts/session1') / '../../escape.txt'` resolves to `/private/tmp/escape.txt`.

### P1: Server startup eagerly writes to user-home state and fails in restricted environments

Impact:

- The API can fail before startup completes when `~/.dan` is not writable.
- This is currently large-blast-radius: one write failure caused 85 downstream server test errors.

Evidence:

- `src/dan/blocks/registry.py`
  - `scan()` always calls `_write_index(_USER_BLOCKS_ROOT)`.
  - `_write_index()` always creates and writes `~/.dan/blocks/_index.json`.
- `src/dan/server/startup.py`
  - `init_stores()` calls `state.block_registry.scan()` during lifespan startup.
  - Furnace is enabled by default and constructs `FurnaceSessionStore(...)` at startup.
- `src/dan/engine/recipe/session_store.py`
  - `FurnaceSessionStore.__init__()` immediately calls `mkdir()` on `~/.dan/furnace/sessions`.

Why this matters:

- The block registry failure is the first one currently visible, but Furnace has the same deployment assumption behind it.
- These writes happen eagerly on startup, even when the feature is not used in that process.

Observed failure:

- `PermissionError: [Errno 1] Operation not permitted: '/Users/lizhi/.dan/blocks/_index.json'`

### P2: Furnace `cancel` does not actually stop a running session

Impact:

- A user can cancel a session and still have the background pipeline continue reading, calling models, and writing artifacts.
- Session state can become misleading: the API reports `failed/cancelled` while work is still running.

Evidence:

- `src/dan/server/routers/furnace.py`
  - `cancel_session()` only sets `session.status = "failed"` and `metadata["cancelled"] = True`.
  - `_execute_furnace_pipeline()` only stops when `session is None` or `session.status == "paused"`.
  - There is no check for `failed` or `cancelled` in the read loop or the phase loop.

Why this matters:

- This is especially risky because `delete_session()` only blocks on `status == "active"`, so a just-cancelled session can become deletable while its worker is still alive.

### P2: Furnace `start` / `resume` can queue duplicate runs because the lock is acquired too late

Impact:

- Fast repeated `start` or `resume` requests can enqueue multiple background tasks for the same session.
- That can duplicate cost, duplicate writes, and create hard-to-debug state transitions.

Evidence:

- `src/dan/server/routers/furnace.py`
  - `start_session()` checks `lock.locked()` and `session.status` before scheduling a task.
  - The lock is only acquired inside the background task, after `asyncio.create_task(_run_furnace())`.
  - `resume_session()` does not even check `lock.locked()` or current status before scheduling `_resume()`.

Why this matters:

- There is a real race window between request return and background task lock acquisition.
- A second request arriving in that window can schedule another worker that waits behind the first one and runs later.

### P2: Domain learning is too narrow to capture common user domains, and normalization is lossy for common abbreviations

Impact:

- Concierge fails to store plausible durable preferences for common professional domains.
- Imported profile facts become weaker or lower-fidelity than the user-provided input.

Evidence:

- `src/dan/server/concierge/domain_learning.py`
  - The shared seed keyword map only includes:
    - `paper_rendering`
    - `equity_research`
    - `data_analysis`
    - `literature_review`
    - `code_generation`
    - `workflow_building`
- `src/dan/engine/preference_extractor.py`
  - `_extract_domains()` depends entirely on that keyword map.
- `src/dan/engine/domain_taxonomy.py`
  - Alias coverage does not include abbreviations like `ML` or `NLP`.
- `src/dan/engine/memory_adapters.py`
  - `ProfileAdapter` normalizes `common_domains` through the taxonomy before storing FACT memory.

Observed failures:

- `tests/test_engine/test_consolidation.py::TestPreferenceExtractionWiring::test_try_extract_preferences_stores_domains`
  - `"supply chain logistics"` / `"inventory optimization"` stores no preference at all.
- `tests/test_engine/test_memory_kernel.py::TestAdapterLayer::test_profile_adapter_imports_preferences`
  - profile domains like `ML` / `NLP` do not round-trip with the expected fidelity.

Why this matters:

- Domain-aware routing and profile learning are user-facing features, so false negatives here directly reduce product usefulness.

### P2: Telemetry DB configuration is frozen at import time

Impact:

- `DAN_TELEMETRY_DB` changes after module import are ignored.
- Tests and embedded runtimes can silently use the wrong database path.

Evidence:

- `src/dan/server/telemetry.py`
  - `_TELEMETRY_DB = os.environ.get("DAN_TELEMETRY_DB", "")` is evaluated at import time.
  - `SQLiteTelemetryStore.__init__()` falls back to `_TELEMETRY_DB` rather than rereading the environment.
  - `get_telemetry_store()` looks dynamic from the call site, but it is not for DB path resolution.

Observed failure:

- `tests/test_server/test_telemetry.py::TestFactory::test_enabled_default`
  - setting `DAN_TELEMETRY_DB` in the test does not take effect; the code falls back to the default location and fails opening the DB.

Why this matters:

- This is a real config regression, not just a test issue.
- It can also make telemetry silently unavailable in environments that rely on late configuration.

### P3: Furnace progress streaming only supports one live subscriber per session

Impact:

- Opening the same session in two clients or tabs causes one subscriber to overwrite the other.
- Disconnecting one subscriber can also clear the queue for the remaining subscriber.

Evidence:

- `src/dan/server/routers/furnace.py`
  - `_session_progress` stores exactly one queue per `session_id`.
  - `session_events()` unconditionally assigns `_session_progress[session_id] = queue`.
  - The generator cleanup unconditionally calls `_session_progress.pop(session_id, None)`.

Why this matters:

- This is not a pure observability issue: progress delivery becomes nondeterministic in multi-client usage.
- The editor work in this repo increasingly supports multiple views/tabs, so single-subscriber assumptions are brittle.

### P3: Google provider internals and tests have drifted apart

Impact:

- Regression coverage around Gemini message conversion and timeout behavior is currently broken.
- The provider is harder to unit test safely after the refactor.

Evidence:

- `src/dan/providers/google_provider.py`
  - the class now exposes `_to_gemini_contents()` instead of the previously-tested `_to_gemini_messages()` helper.
  - `complete()` now reaches `_build_text_part()` early and requires `_protos` to exist before timeout behavior can be exercised.

Observed failures:

- `tests/test_providers/test_google_provider.py`
  - both `_to_gemini_messages` tests fail because the helper no longer exists.
- `tests/test_provider_timeouts.py::test_google_provider_complete_times_out`
  - the test now fails with `AttributeError: 'GoogleProvider' object has no attribute '_protos'` before the timeout path is reached.

Why this matters:

- The implementation may still work against the live SDK, but the project has lost regression protection in a provider layer that is already integration-fragile.

### P3: Furnace source identity can collide and silently merge unrelated inputs

Impact:

- Distinct sources can collapse onto the same `source_id`, causing dropped queue entries or overwritten artifacts.

Evidence:

- `src/dan/server/routers/furnace.py`
  - PDF source IDs are derived from `Path(expanded).stem`.
  - URL source IDs are derived from the last URL path segment via `_url_to_source_id()`.
- `src/dan/engine/recipe/session_store.py`
  - session queues are keyed by `paper_id`, so collisions overwrite logically distinct sources.

Why this matters:

- Two different files named `paper.pdf` in different directories collapse to the same source.
- Two different URLs ending in the same slug also collapse.
- This is a correctness issue for any serious corpus-building workflow.

### P3: Clipboard capability detection is too optimistic and the failure mode is opaque

Impact:

- On headless or non-GUI sessions, clipboard operations fail with an empty error message.
- The system appears flaky instead of correctly reporting that clipboard access is unavailable.

Evidence:

- `src/dan/tools/clipboard.py`
  - `_find_clipboard_cmd()` treats `pbcopy` presence as sufficient.
  - `clipboard()` raises `RuntimeError(f"Clipboard command failed: {stderr.decode().strip()}")`, which can be blank.

Observed failure:

- `tests/test_tools/test_builtin_tools.py::TestClipboard::test_copy_text`

Why this matters:

- This is lower severity than the startup and configuration issues, but it is a poor UX path in exactly the environments where automation often runs.

## Additional Notes

- The frontend suite passed cleanly under `editor/`, which lowers concern around the most recent chat/editor changes.
- I did not find direct API/router tests for the Furnace endpoints themselves; current furnace-related tests are concentrated in concierge triage behavior rather than the router/runtime lifecycle.
- I saw one early full-suite failure in `tests/test_cli/test_power_user.py::TestPrepTimeout::test_prep_timeout_does_not_block`, but I could not reproduce it directly or under `--maxfail=1`. I would treat that as probable flakiness/shared-state leakage until reproduced again.

## Suggested Prioritization

1. Fix Furnace path safety for `source_id` before it can write outside the session artifact directory.
2. Remove eager home-directory writes from server startup, or make them best-effort/configurable.
3. Make Furnace cancellation and start/resume lifecycle stateful enough to prevent runaway or duplicate workers.
4. Expand domain taxonomy and domain keyword seeds, especially for practical business/technical domains and common abbreviations.
5. Make telemetry path resolution dynamic at call time.
6. Realign Google provider tests with the current adapter API and restore timeout-path coverage.
7. Improve Furnace subscriber fan-out and source identity handling.
8. Improve clipboard availability detection and error messaging.
