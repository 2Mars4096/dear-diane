# 38-7: Chat Dispatch & Workflow Generation Fixes

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** in-progress
**Goal:** Fix confirmed bugs and correctness gaps in the workflow generation pipeline and chat dispatch routing identified in the 2026-03-19 chat-dispatch and workflow-generation reviews.

## Context

- The chat-dispatch review found that `workflow_query` routes are silently broadened into `workflow_build`, giving mutation tools to users who only asked about a workflow.
- The workflow-generation review found 8 P1 issues: a vestigial `CoverageChecker`, a bad tool-ID fallback, a fragile `locals()` check, a dict-vs-list bug in the defaults enricher, a silent single-node fallback, missing quality gates on the mutation path, and incomplete mutation schema exposure.
- The `prompts.py` import-order regression (Chat Dispatch P1) was already fixed — variables are now in correct definition order.

## Tasks

### 1. Fix `_ensure_validation_gate()` edge access bug
- [x] 1-1. Fix `generation_defaults.py:211` — `graph_dict.get("edges", {}).get("data", [])` treats edges as a dict, but serialized `Graph.edges` is a flat list. Replace with correct list iteration filtering by edge type. *(fixed 2026-03-19: both `_ensure_validation_gate` and `_ensure_review_on_content` now handle flat list and nested dict formats)*
- [ ] 1-2. Add a test exercising the `robust` profile end-to-end to prevent this from being dead code again.

### 2. Replace `locals()` sentinel with explicit variable
- [x] 2-1. Replace `'first_entry_ref' in locals()` at `intent_compiler.py:908` with an explicit `first_entry_ref = None` sentinel before the loop and `if first_entry_ref is not None` check. *(fixed 2026-03-19)*
- [x] 2-2. Audit for any other `in locals()` patterns in the file. *(fixed 2026-03-19: found and fixed `first_entry_var` at line 1074 — same pattern in `_compile_segment_code`)*

### 3. Fix or remove vestigial CoverageChecker
- [ ] 3-1. Either implement real coverage checking (check stage types against supported compiler patterns) or remove `CoverageChecker` entirely and document that `IntentCompiler.compile()` is the real gate.
- [ ] 3-2. If removing, clean up any call sites in `chat_manager.py` that reference `CoverageChecker.check()`.

### 4. Fix `_infer_tool_id` web_search fallback
- [x] 4-1. Change the fallback from `"web_search"` to a neutral default (e.g., raise a warning and use `"llm_operator"` or return `None` so the caller handles unknown tools explicitly). *(fixed 2026-03-19: changed to `"llm_operator"` with existing warning log)*
- [ ] 4-2. Add test cases for stages with no keyword match to verify the new behavior.

### 5. Fix legacy fallback single-node silent degradation
- [ ] 5-1. When `_execute_generate_code()` fails in `planner.py`, surface a clear error to the user instead of silently creating a single `llm_operator` node.
- [ ] 5-2. At minimum, emit a warning event and include a note in the chat response that generation fell back to a simplified workflow.

### 6. Separate `workflow_query` from `workflow_build` routing
- [x] 6-1. In `tier_executors.py:488-489`, check `action_hints` for `workflow_query` vs `workflow_edit`/`workflow_build` instead of treating all `route_target == "workflow"` as `workflow_build`. *(fixed 2026-03-19: `workflow_query`-only turns now stay on the conversation/read path while explicit build/edit hints still route to `workflow_build`)*
- [x] 6-2. When `action_hints` contains only `workflow_query`, set `allow_mutation_tool=False` and route through the normal read/query path. *(fixed 2026-03-19 with focused regression coverage in `tests/test_concierge/test_tiered_dispatch.py`)*
- [ ] 6-3. Update existing tests that lock in the broadening behavior.

### 7. Add quality gate on chat-mutation path
- [ ] 7-1. After `GraphMutator.apply()` succeeds on the chat-mutation path, invoke `compute_quality_report()` as a post-check (same as the intent path).
- [ ] 7-2. If quality is below threshold, surface a warning but still allow the mutation (don't block — just inform).

### 8. Document or expose missing mutation operations
- [ ] 8-1. The mutation schema exposes 9 of 14 operation types. Document why `rename_node`, `set_metadata`, `reorder_edges`, `batch_set_positions`, `duplicate_node` are hidden, or expose them.
- [ ] 8-2. If intentionally hidden, add a code comment explaining the rationale.

## Primary Files

- `src/dan/meta/generation_defaults.py`
- `src/dan/meta/intent_compiler.py`
- `src/dan/meta/planner.py`
- `src/dan/server/chat/prompts.py`
- `src/dan/server/chat_manager.py`
- `src/dan/server/graph_mutator.py`
- `src/dan/server/concierge/tier_executors.py`
- `src/dan/meta/graph_quality.py`

## Decisions

- CoverageChecker: prefer removal over incomplete implementation — the compiler itself is the real gate.
- Quality gate on mutation path: warn, don't block. Users should see quality feedback but not be prevented from iterating.
- Legacy fallback: make it visible, not silent. The user should know their complex request was reduced.

## Estimate

~2 days
