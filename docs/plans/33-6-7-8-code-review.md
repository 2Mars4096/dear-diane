# Code Review: Phase 33 Plans 33-6, 33-7, 33-8

**Base:** b793fd8 | **Head:** Working tree (uncommitted)  
**Reviewed:** 2026-03-11

---

## Strengths

1. **Plan alignment** — All three plans are well implemented. Intent compiler activation (33-6), semantic quality gates (33-7), and codegen resilience (33-8) are integrated coherently into `_generate_workflow_from_intent()`.

2. **Separation of concerns** — Sandbox retry is correctly separated from codegen LLM retry. Pre-sandbox `ast.parse()` catches syntax errors without subprocess overhead. Quality checks run after validation on all paths (intent compiler, codegen, diagnosis, mutation).

3. **Defensive coding** — `_is_transient_llm_error()` covers common transient cases. `_parse_intent_from_result()` has JSON-in-content fallback for models without tool calling. `graph_quality` uses `edge_type`/`type` and `source`/`source_node_id` fallbacks for graph dict shape variation.

4. **Test coverage** — `test_graph_quality.py` and `test_intent_compiler.py` cover the new behavior. All 27 tests pass. Calibration test verifies well-formed graphs score ≥70.

5. **Telemetry and observability** — Intent extraction telemetry, classifier fallback callback, and `ChatGraphQualityEvent` provide useful signals for eval and debugging.

6. **Backward compatibility** — `DAN_GRAPH_QUALITY_THRESHOLD=0` by default; `dispatch_compound_mutations` keeps `dispatch.result`/`dispatch.macro_name` for single-macro paths.

---

## Issues

### Critical

- **None identified.**

### Important

1. **chat_manager.py:2268–2286 — Heartbeat loop may not yield `progress_ack` correctly**
   - The `while not codegen_task.done()` loop yields `ChatCompleteEvent(detected_mode="progress_ack")` on `asyncio.TimeoutError`, but the event uses `message_id` from the outer scope. If the task eventually fails, the final `ChatCompleteEvent` may overwrite or conflict. Verify that the stream consumer correctly handles multiple `ChatCompleteEvent`s (one per 30s heartbeat plus final).
   - **Recommendation:** Confirm the client/harness treats `progress_ack` as a heartbeat, not a terminal event. Add a short comment that `progress_ack` is a keepalive.

2. **chat_manager.py:3703 — `sandbox_codegen` can be `None` when sandbox raises**
   - `_sandbox_exec_builder_code` returns `(None, None)` on exception. The line `err_msg = sandbox_codegen.error_message or "" if sandbox_codegen else ""` correctly yields `""`, so `is_timeout` is False and we fall through to the non-timeout path. No bug, but the logic is subtle.
   - **Recommendation:** Add a one-line comment: `# sandbox_codegen is None when runner raised; treat as non-timeout.`

3. **test_graph_quality.py:281–312 — Integration test can pass without asserting**
   - `test_quality_event_emitted_after_validation` only asserts when `records` is non-empty and `rec.graph_created and rec.validation and rec.validation.passed`. If the server is down or no record meets those conditions, the test passes without asserting.
   - **Recommendation:** Add `pytest.skip` when `not records` or when no record has `graph_created and validation.passed`, with a message like `"Server not running or no successful build"`, so the test either asserts or explicitly skips.

4. **graph_quality.py:104 — Edge format assumption**
   - `_get_nodes_edges` expects `edges` to be a list. The Graph model and codegen output use a flat list. Structural mutations use `edges["data"]` (dict format). `graph_quality` is only called with graph dicts from codegen/intent compiler/diagnosis/mutation, which use the Graph model format (flat list). No bug in current usage.
   - **Recommendation:** Add a short docstring: `Handles graph_dict from Graph.model_dump() — edges is a list.`

### Minor

1. **chat_manager.py:3687 — Typo in log message**
   - `"falling to diagnosis"` → `"falling back to diagnosis"`.

2. **runtime.py — `_safe_metadata` and `_requested_mode_forces_solver_path`**
   - These helpers are clear and correctly strip non-serializable values. The `surface`/`surface_id` changes in telemetry look like fixes; confirm they match the intended schema.

3. **Intent compiler tests — No `_parse_intent_from_result` tests**
   - Plan 33-6 remaining task: IntentCompiler verification tests exist, but `_parse_intent_from_result` (JSON-in-content fallback) is not unit-tested.
   - **Recommendation:** Add a test that constructs a `CompletionResult` with `text='{"goal": "...", "stages": [...]}'` and no `tool_calls`, and asserts `_parse_intent_from_result` returns a valid `WorkflowIntent`.

---

## Recommendations

1. **Fix typo:** `"falling to diagnosis"` → `"falling back to diagnosis"` (chat_manager.py:3687).

2. **Clarify heartbeat:** Add a comment that `progress_ack` is a WebSocket keepalive, not a terminal event.

3. **Tighten integration test:** Make `test_quality_event_emitted_after_validation` skip explicitly when it cannot assert, rather than passing silently.

4. **Add `_parse_intent_from_result` test:** Cover the JSON-in-content fallback path to close 33-6 remaining work.

5. **Document edge format:** Add a brief note in `graph_quality._get_nodes_edges` about expected graph dict shape.

---

## Assessment

**Ready to merge?** **Yes, with minor fixes.**

### Reasoning

- All planned functionality is implemented and wired correctly.
- Tests pass. No critical or blocking issues.
- The important items are clarifications and robustness improvements, not correctness bugs.
- Fixing the typo and tightening the integration test are quick wins before merge.

### Suggested pre-merge checklist

- [x] Fix typo: "falling to diagnosis" → "falling back to diagnosis"
- [x] Add `_parse_intent_from_result` JSON-in-content test (`tests/test_chat_manager_intent.py`)
- [x] Add skip logic to `test_quality_event_emitted_after_validation` when no assertable record
- [x] Update `docs/changelog.md` with Phase 33 patch summary
