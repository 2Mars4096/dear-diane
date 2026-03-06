# 22-3: Review Hardening (Round 3)

**Parent:** [22-deferred-waves](../todo.md#phase-121--cross-phase-deferred-completion-waves)
**Status:** not-started
**Goal:** Patch the 2 critical, 6 important, and 2 minor issues surfaced by the comprehensive Round 3 review — server safety, mutation semantics, CLI/server contract consistency, and docs truthfulness.

## Tasks

### Critical

- [ ] 1. **Graph ID path-traversal sanitization**
  - [ ] 1-1. Add `_validate_graph_id(graph_id)` to `src/dan/server/graph_store.py` that rejects `/`, `\`, `..`, empty strings, and names failing `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`. Raise `ValueError` on bad IDs.
  - [ ] 1-2. Call `_validate_graph_id` in `_graph_path`, `save_graph`, `create_graph`, `delete_graph`, `load_as_model`, `set_last_opened`.
  - [ ] 1-3. Add matching guard in `src/dan/server/app.py` graph CRUD routes (`POST /api/graphs`, `GET /api/graphs/{id}`, `PUT /api/graphs/{id}`, `DELETE /api/graphs/{id}`) — return HTTP 400 on invalid IDs.
  - [ ] 1-4. Add client-side pre-check in `src/dan/cli/chat.py` for `/save`, `/saveas`, `/new`, `/open`, `/rename` — reject bad IDs with a user-friendly message before hitting the server.
  - [ ] 1-5. Tests: path-traversal IDs rejected, valid slugs accepted, CRUD routes return 400 on bad IDs.

- [ ] 2. **Gateway approval/cancel race**
  - [ ] 2-1. In `src/dan/server/gateway/router.py` `_run_after_approval()`, re-fetch `rm.get_run(run_id)` immediately before `rm.start_run()` and abort unless the record is still in a pending/approved state.
  - [ ] 2-2. Consider extracting approval resolution + start into a single `RunManager.approve_and_start(run_id, ...)` method guarded by per-run state to make the window impossible.
  - [ ] 2-3. Test: approve then immediately cancel — verify run does not start.

### Important

- [ ] 3. **CLI cancel endpoint mismatch**
  - [ ] 3-1. In `src/dan/cli/chat.py` `ChatClient.cancel_run()`, change from `POST /api/runs/{run_id}/cancel` to `POST /api/gateway/cancel` with body `{"run_id": run_id}`, matching `src/dan/client/client.py`.
  - [ ] 3-2. Verify `dan-run` cancel path in `src/dan/cli/run.py` — it uses `client.cancel_run()` from `src/dan/client/client.py` (gateway route), so no change needed there.
  - [ ] 3-3. Update `docs/cli.md` and `docs/plans/21-6-cli-chat-mode.md` to reference the correct cancel route.

- [ ] 4. **Human-input `run_id`/`request_id` ownership validation**
  - [ ] 4-1. In `RunManager._make_human_input_callback`, store `request_id -> run_id` in a new `_human_input_request_ownership: dict[str, str]` mapping.
  - [ ] 4-2. In `RunManager.submit_human_input()`, reject if the supplied `run_id` does not match the canonical ownership mapping.
  - [ ] 4-3. In `src/dan/server/gateway/router.py` `submit_input()`, use the canonical run ID from the ownership mapping for the broadcast event, not the caller-supplied one.
  - [ ] 4-4. Test: submit with correct pair succeeds; submit with mismatched run_id fails; broadcast uses canonical run_id.

- [ ] 5. **Stop silently rewriting context edges to data edges**
  - [ ] 5-1. In `src/dan/server/chat_manager.py` `_normalize_generated_mutation_ops()`, instead of rewriting `edge_type="context"` to `"data"`, emit a dry-run error message: `"Context edges require context_key and mode fields — use edge_type='data' or provide full context edge fields"`.
  - [ ] 5-2. Verify the auto-repair loop can recover by re-prompting the model with the error.

- [ ] 6. **Restore strict-edge enforcement for build-mode**
  - [ ] 6-1. In `src/dan/server/chat_manager.py` `_coerce_strict_edges()`, restore `strict=True` on `add_edge` ops for empty-graph / build-mode flows. The current implementation is a shallow copy no-op.
  - [ ] 6-2. Carve out a narrow allowlist for intentional auto-created ports (e.g., well-known port names from node templates), rather than making all ports auto-create by default.
  - [ ] 6-3. Test: build-mode mutation with misspelled port name fails dry-run instead of silently creating the port.

- [ ] 7. **Graph mutator alias collisions**
  - [ ] 7-1. In `src/dan/server/graph_mutator.py` `_op_add_node()`, if `op.id` is explicitly provided and already exists in the graph, return an operation error instead of silently renaming.
  - [ ] 7-2. In `_track_add_node_alias()`, only install synthetic `node_N` aliases when that placeholder was absent from the original graph before mutation began.
  - [ ] 7-3. Test: explicit `add_node(id="existing_id")` when `existing_id` is taken → operation error; placeholder `node_1` does not shadow existing `node_1`.

- [ ] 8. **`dan-chat --workflow-id` startup behavior**
  - [ ] 8-1. In `src/dan/cli/chat.py` REPL startup, if `workflow_id != "_scratch"`, fetch the graph via `client.get_graph(workflow_id)`, fail fast with a clear error on 404, initialize `client_graph_revision` from the fetched graph.
  - [ ] 8-2. Default mode to `mutate` (not `build`) when an existing workflow is loaded, unless `--mode` was explicitly passed.
  - [ ] 8-3. Update `docs/cli.md` example to reflect the actual startup behavior.

### Minor

- [ ] 9. **Docs cleanup**
  - [ ] 9-1. `docs/plans/21-6-cli-chat-mode.md` line 80: change "33 unit tests" to match actual count or use non-brittle phrasing.
  - [ ] 9-2. `docs/plans/21-6-cli-chat-mode.md` "Files to Touch" table: add `chat_manager.py`, `graph_mutator.py`, `executors/input.py`.
  - [ ] 9-3. `docs/changelog.md`: narrow "All client methods wrap transport errors" to only the methods that actually have wrapping.
  - [ ] 9-4. `docs/cli.md`: normalize all defaults to `127.0.0.1` consistently (lines 40, 55, 104).
  - [ ] 9-5. `README.md` line 91: change markdown agents from "coming soon" to "available" or "experimental", link to `examples/`.
  - [ ] 9-6. `docs/cli.md` exit prompt example (line ~370): update to show new auto-suggestion prompt format.
  - [ ] 9-7. `docs/bugs.md` / `docs/changelog.md`: reconcile cancel-fix claims with the CLI cancel route mismatch (task 3).

## Decisions
- (filled in during execution)

## Notes
- Findings sourced from comprehensive Round 3 code review dispatched across CLI/client, server/runtime, and docs/tracking.
- No new tests were shipped with the reviewed changes (`git diff --name-only HEAD -- tests/` returned empty).
- Tasks 1 and 2 are blocking — they are security and correctness issues that must be fixed before merge.
- Tasks 3–8 are important and should be fixed before the next feature wave.
- Task 9 is docs-only cleanup that can be batched.
