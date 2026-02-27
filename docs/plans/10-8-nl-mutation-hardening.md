# 10-8: NL Mutation Hardening

**Parent:** [10-chatbox-nl-workflow](10-chatbox-nl-workflow.md)
**Status:** completed
**Goal:** Make NL→graph authoring reliable enough that a user can stack nodes like Lego blocks via chat without manual JSON fixup. Close the gap between the mutation engine's happy path and real LLM output by adding validation gates, tighter schemas, auto-repair, smarter prompts, and pattern macros.

## Context

Two review rounds identified **7 concrete failure classes** in the current chat→mutate→save pipeline:

1. **No port validation on `AddEdge`** — edges are created with non-existent port names; only node existence is checked.
2. **No `Graph.model_validate` / `validate_graph` on apply** — structurally invalid graphs are persisted directly.
3. **`entry_points`/`exit_points` never updated on `AddNode`** — new entry/exit nodes are silently orphaned.
4. **Tool schema too loose** — only `"op"` is required; no per-op-type required fields, no `node_type` enum, no port name hints.
5. **No auto-retry on dry-run failure** — errors go to the user, never back to the LLM for self-correction.
6. **System prompt lacks port/config reference** — LLM guesses port names; `_default_ports`/`_default_node_config` tables are never injected.
7. **`llm-api-guide.md` stale vs. actual node types** — still documents deprecated `if_else`/`while_loop`; `gate` is missing.

Additionally, plan `10-3-nl-graph-mutation.md` has ~30 unchecked sub-tasks (items 1-*, 2-*, 3-2, 3-4, 4-*, 5-*, 6-3, 6-4, 7-*, 8-*) despite a `completed` status. This plan supersedes those unchecked items and adds new ones discovered during review.

## Tasks

- [x] 1. Validate-before-save gate *(bug fix; blocks downstream reliability work)*
  - [x] 1-1. After `GraphMutator.apply()` succeeds, run `Graph.model_validate(result.new_graph)` in `POST /api/graphs/{graph_id}/apply-mutation`
  - [x] 1-2. If Pydantic parse fails, return `{ success: false, errors: [...] }` and never persist
  - [x] 1-3. If parse succeeds, run `validate_graph(graph)` and partition into fatal errors vs warnings
  - [x] 1-4. Fatal errors reject apply with structured node/edge references; warnings persist and are returned in response payload
  - [x] 1-5. Add the same gate in `GraphMutator.dry_run()` so preview surfaces validation status before apply
  - [x] 1-6. Tests: invalid graph rejected, warning-only graph accepted, no regressions in existing mutator suites

- [x] 2. Port-aware `AddEdge` semantics *(bug fix)*
  - [x] 2-1. In `_op_add_edge`, validate `source_port` against source `output_ports` and `target_port` against target `input_ports`
  - [x] 2-2. If source port exists but target port is missing, auto-create target input port (matching builder `_auto_generate_ports` behavior)
  - [x] 2-3. If source port is missing, return diagnostics with available source ports
  - [x] 2-4. Include suggestions in error text for near-miss names where possible
  - [x] 2-5. Tests: happy path, auto-create path, invalid source port diagnostics

- [x] 3. Entry/exit point recomputation after mutation apply *(bug fix)*
  - [x] 3-1. Recompute `entry_points` and `exit_points` after full operation list applies
  - [x] 3-2. Reuse compiler logic (`_find_entry_points` / `_find_exit_points`) via shared helper to avoid drift
  - [x] 3-3. Tests: isolated node becomes entry+exit, newly connected node removed from entry set

- [x] 4. Typed per-operation mutation tool schema *(primary LLM accuracy lever)*
  - [x] 4-1. Replace flat operation schema with discriminated schemas by `op` (`add_node`, `remove_node`, `edit_node`, `add_edge`, `remove_edge`, `set_position`)
  - [x] 4-2. Enforce per-op required fields (e.g., `add_edge` requires `source_id`, `source_port`, `target_id`, `target_port`)
  - [x] 4-3. Restrict `node_type` to active node types only and exclude deprecated `if_else` / `while_loop`
  - [x] 4-4. Add per-node-type config constraints for `add_node` (`llm_operator`, `code_operator`, `tool_operator`, `gate`, etc.)
  - [x] 4-5. Generate schema from source-of-truth tables (`_default_ports`, `_default_node_config`, Pydantic models) to prevent future drift
  - [x] 4-6. Tests: valid payloads pass, intentionally malformed payloads fail with clear field-level errors

- [x] 5. Port/config reference enrichment in system prompt *(reduce port-name guessing)*
  - [x] 5-1. Build `NODE_TYPE_REFERENCE` from `_default_ports` + `_default_node_config` for all active node types
  - [x] 5-2. Inject reference into `SYSTEM_PROMPT_TEMPLATE` between node-type and edge-type sections
  - [x] 5-3. Add 2-3 canonical operation examples (add node, add edge, gate loop wiring)
  - [x] 5-4. Extend graph summary formatter to include actual ports of existing nodes
  - [x] 5-5. Tests: prompt construction includes reference + examples for both empty and non-empty graphs

- [x] 6. Auto-retry with error-feedback loop on failed dry-run *(recoverable mutation path)*
  - [x] 6-1. In `send_message_with_tools`, if `dry_run.success == false`, send structured mutator errors back to model and request corrected plan
  - [x] 6-2. Apply same retry behavior in `_stream_with_json_fallback`
  - [x] 6-3. Cap retry attempts at 1 and log both first-pass and retry plan IDs/results
  - [x] 6-4. Surface actionable error summary in diff preview when retry also fails
  - [x] 6-5. Tests: fail->retry->pass path and fail->retry->fail path

- [x] 7. Pattern macros for high-frequency graph shapes *(user-effort reduction)*
  - [x] 7-1. Add `PATTERN_LIBRARY` (initial set: `chain`, `review_loop`, `fan_out`, `rag_qa`)
  - [x] 7-2. Add pattern expansion op (or equivalent field) in mutator to translate macro call into primitive operations
  - [x] 7-3. Include pattern usage in mutation schema and system prompt guidance
  - [x] 7-4. Ensure expanded operations go through same validation and dry-run flow as manual plans
  - [x] 7-5. Tests: each pattern compiles to valid graph and composes with later manual edits

- [x] 8. Documentation sync and deprecation cleanup *(prevent recurrence)*
  - [x] 8-1. Update `docs/llm-api-guide.md`: replace legacy `if_else` / `while_loop` guidance with `gate` modes
  - [x] 8-2. Update node-type tables/counts and include `input`, `rag_operator`, `validator` where missing
  - [x] 8-3. Update `docs/architecture.md` mutation flow with post-apply validation gate and retry loop
  - [x] 8-4. Mark stale unchecked work in `10-3-nl-graph-mutation.md` as superseded by this plan
  - [x] 8-5. Add changelog entries as each major subtask lands

- [x] 9. Mutation quality CI suite *(regression prevention)*
  - [x] 9-1. Add `tests/test_server/test_mutation_quality.py` with 15-20 deterministic mutation scenarios
  - [x] 9-2. Per scenario assert parse success, dry-run success, post-apply validation pass, and expected graph deltas
  - [x] 9-3. Add pass-rate threshold check for deterministic cases in CI
  - [ ] 9-4. Add optional report-only LLM-in-the-loop variant for future non-deterministic quality tracking

- [x] 10. Stale-plan recovery and apply idempotency *(new hardening from final review)*
  - [x] 10-1. When apply returns `stale_plan: true`, automatically request re-planning against latest graph revision (reusing original user intent)
  - [x] 10-2. Add mutation request idempotency key so duplicate apply clicks/events do not double-apply
  - [x] 10-3. Add stale-revision and duplicate-apply integration tests
  - [x] 10-4. Surface a clear user message when rebase/replan is required

- [x] 11. Acceptance metrics, rollout guardrails, and 2-week validation *(new addition to operationalize success)*
  - [x] 11-1. Instrument and emit `apply_success_rate`, `post_validate_pass_rate`, and `avg_user_turns_to_success`
  - [ ] 11-2. Capture a 3-5 day baseline on current `main` before enabling strict mode globally
  - [ ] 11-3. Set sprint acceptance targets: `apply_success_rate >= 85%`, `post_validate_pass_rate >= 99%`, `avg_user_turns_to_success <= 2.0` on benchmark scenarios
  - [x] 11-4. Add feature flags for strict validation and auto-retry for staged rollout and quick rollback
  - [ ] 11-5. Publish end-of-sprint quality report with baseline vs post-fix deltas

## Execution Order

```
1 -> 2 -> 3
1 -> 4 -> 5 -> 6
4 + 5 -> 7
8 runs in parallel while 1-7 land
9 starts after 1-6 are stable
10 starts after 6 (uses retry and revision handling)
11 starts baseline before 1 and evaluates after 1-10
```

**Tasks 1-3** are independent correctness fixes and should land first.
**Tasks 4-6** are the main reliability tranche.
**Task 7** depends on typed schema plus prompt enrichment.
**Task 8** is continuous docs synchronization.
**Task 9** is the regression gate after the core path stabilizes.
**Tasks 10-11** are operational hardening and success measurement.

## Decisions

- Deprecated `if_else` and `while_loop` node types are excluded from the mutation tool schema. They remain in the engine for backward compat but are not offered to the LLM.
- Auto-retry is capped at 1 attempt per mutation request. More retries risk cost spiral and latency.
- Pattern macros are expansion-based (patterns expand to primitive operations), not a new node type. This keeps the graph model unchanged.
- Entry/exit point recomputation is done after the full plan applies, not per-operation. This avoids intermediate invalid states.
- Stale-plan handling uses explicit re-plan against latest graph revision rather than unsafe best-guess patching.

## Notes

- The mutator now converges with the compiler's safety guarantees — `Graph.model_validate` + `validate_graph` runs on every successful apply/dry_run path.
- `MUTATION_TOOL_SCHEMA` is now generated programmatically from `_default_ports` / `_default_node_config` via `_build_mutation_tool_schema()`. Schema drift is structurally prevented.
- Test coverage: 56 mutator tests, 34 chat manager tests, 18 mutation quality CI tests, 6 metrics tests = **114 total** across 4 test files, all passing (300 across all server+builder+validation suites).
- Remaining items (11-2, 11-3, 11-5) are operational tasks that require deployment time — they cannot be completed in a code session. Item 9-4 (LLM-in-the-loop CI variant) is deferred as non-blocking.
- Feature flags `DAN_STRICT_MUTATION_VALIDATION` and `DAN_MUTATION_AUTO_RETRY` default to `true`. Set to `false` for quick rollback.
