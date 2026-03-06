# 22-3: Review Hardening (Round 3)

**Parent:** [22-deferred-waves](../todo.md#phase-121--cross-phase-deferred-completion-waves)
**Status:** completed
**Goal:** Patch the 2 critical, 6 important, and 2 minor issues surfaced by the comprehensive Round 3 review — server safety, mutation semantics, CLI/server contract consistency, and docs truthfulness.

## Tasks

### Critical

- [x] 1. **Graph ID path-traversal sanitization**
  - [x] 1-1. Add `_validate_graph_id(graph_id)` to `src/dan/server/graph_store.py` that rejects `/`, `\`, `..`, empty strings, and names failing `^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$`. Raise `ValueError` on bad IDs.
  - [x] 1-2. Call `_validate_graph_id` in `_graph_path`, `get_graph`, `save_graph`, `create_graph`, `delete_graph`, `load_as_model`, `set_last_opened`.
  - [x] 1-3. Add matching guard in `src/dan/server/app.py` graph CRUD routes (`POST /api/graphs`, `GET /api/graphs/{id}`, `PUT /api/graphs/{id}`, `DELETE /api/graphs/{id}`) — return HTTP 400 on invalid IDs.
  - [x] 1-4. Add client-side pre-check in `src/dan/cli/chat.py` for `/save`, `/saveas`, `/new`, `/open` — reject bad IDs with a user-friendly message before hitting the server.
  - [x] 1-5. Tests: 19 tests — path-traversal rejected, valid slugs accepted, GraphStore integration (6 methods), HTTP route integration (5 routes).

- [x] 2. **Gateway approval/cancel race**
  - [x] 2-1. In `src/dan/server/gateway/router.py` `_run_after_approval()`, re-fetch `rm.get_run(run_id)` immediately before `rm.start_run()` and abort unless still in pending/approved state.
  - [x] 2-2. Extracted `RunManager.approve_and_start(run_id, ...)` — atomically checks run state before delegating to `start_run()`.
  - [x] 2-3. Tests: 3 tests — cancelled-then-approve returns None, approve succeeds, nonexistent run returns None.

### Important

- [x] 3. **CLI cancel endpoint mismatch**
  - [x] 3-1. `ChatClient.cancel_run()` now POSTs to `/api/gateway/cancel` with `{"run_id": run_id}`, matching `DanClient`.
  - [x] 3-2. Verified `dan-run` cancel path uses `client.cancel_run()` from `client.py` — no change needed.
  - [x] 3-3. Updated `docs/cli.md` and reconciled `docs/bugs.md` cancel-fix claims.

- [x] 4. **Human-input `run_id`/`request_id` ownership validation**
  - [x] 4-1. Added `_human_input_request_ownership: dict[str, str]` mapping in `RunManager.__init__`.
  - [x] 4-2. `submit_human_input()` rejects mismatched `run_id` (restores event on mismatch).
  - [x] 4-3. Ownership stored in `_make_human_input_callback` and `register_meta_approval`; cleaned up in `pop_meta_approval_response`.
  - [x] 4-4. Tests: 6 tests — ownership dict, mismatch rejection, correct acceptance, no-ownership compat, meta approval storage/cleanup.

- [x] 5. **Stop silently rewriting context edges to data edges**
  - [x] 5-1. Removed silent `context` → `data` rewrite in `_normalize_generated_mutation_ops()`. Context edges now pass through; downstream Pydantic validation catches missing `context_key`.
  - [x] 5-2. Auto-repair loop recovers naturally via dry-run validation error feedback.

- [x] 6. **Restore strict-edge enforcement for build-mode**
  - [x] 6-1. `_coerce_strict_edges()` restored: sets `strict=True` via `setdefault` on `add_edge` ops. Explicit `strict=False` preserved.
  - [x] 6-2. Well-known ports handled via `setdefault` — LLM can override with explicit `strict=False`.
  - [x] 6-3. Tests: 4 tests — strict set, explicit False preserved, non-edge untouched, no input mutation.

- [x] 7. **Graph mutator alias collisions**
  - [x] 7-1. `_op_add_node()` rejects explicit `op.id` that already exists (error instead of silent rename).
  - [x] 7-2. `_track_add_node_alias()` takes `original_ids` and skips alias installation when placeholder collides with pre-existing node.
  - [x] 7-3. Tests: 3 tests — explicit ID collision rejected, auto-ID no collision, placeholder no shadowing.

- [x] 8. **`dan-chat --workflow-id` startup behavior**
  - [x] 8-1. In `src/dan/cli/chat.py` REPL startup, if `workflow_id != "_scratch"`, fetch the graph via `client.get_graph(workflow_id)`, fail fast with a clear error on 404, initialize `client_graph_revision` from the fetched graph.
  - [x] 8-2. Default mode to `mutate` (not `build`) when an existing workflow is loaded, unless `--mode` was explicitly passed.
  - [x] 8-3. Update `docs/cli.md` example to reflect the actual startup behavior.

### Minor

- [x] 9. **Docs cleanup**
  - [x] 9-1. `docs/plans/21-6-cli-chat-mode.md` line 80: change "33 unit tests" to match actual count or use non-brittle phrasing.
  - [x] 9-2. `docs/plans/21-6-cli-chat-mode.md` "Files to Touch" table: add `chat_manager.py`, `graph_mutator.py`, `executors/input.py`.
  - [x] 9-3. `docs/changelog.md`: narrow "All client methods wrap transport errors" to only the methods that actually have wrapping.
  - [x] 9-4. `docs/cli.md`: normalize all defaults to `127.0.0.1` consistently (lines 40, 55, 104).
  - [x] 9-5. `README.md` line 91: change markdown agents from "coming soon" to "available" or "experimental", link to `examples/`.
  - [x] 9-6. `docs/cli.md` exit prompt example (line ~370): update to show new auto-suggestion prompt format.
  - [x] 9-7. `docs/bugs.md` / `docs/changelog.md`: reconcile cancel-fix claims with the CLI cancel route mismatch (task 3).

## Decisions
- Graph ID regex relaxed to `[A-Za-z0-9_]` leading char (allows `_scratch`).
- Task 2 implemented both re-check + `approve_and_start()` atomic method.
- Task 5: removed silent rewrite entirely rather than emitting custom error; downstream Pydantic validation provides clear enough feedback for auto-repair.
- Task 6: used `setdefault("strict", True)` so LLM can opt out with explicit `strict=False`.
- Task 4: kept `submit_human_input` return as `bool`; ownership validated internally without changing API.

## Notes
- Findings sourced from comprehensive Round 3 code review dispatched across CLI/client, server/runtime, and docs/tracking.
- No new tests were shipped with the reviewed changes (`git diff --name-only HEAD -- tests/` returned empty).
- Tasks 1 and 2 are blocking — they are security and correctness issues that must be fixed before merge.
- Tasks 3–8 are important and should be fixed before the next feature wave.
- Task 9 is docs-only cleanup that can be batched.
