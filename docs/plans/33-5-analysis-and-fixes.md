# 33-5: Analysis & Fixes

**Parent:** [33-generation-quality-eval](33-generation-quality-eval.md)
**Status:** in-progress
**Goal:** Analyze the baseline results from 33-3 and 33-4, identify the top failure modes, apply targeted fixes, and re-measure to confirm improvement.

## Process

### Step 1: Read the baseline report

After the first full run of the small + complex batteries, the JSONL logs contain every failure with full context. The report generator (33-2) produces:

- Per-tier and per-lane pass rates
- Failure mode distribution (histogram)
- Build-time and run-time token cost distributions
- Build-time and run-time latency distributions
- Top-N worst performers (most tokens, slowest, most repair-heavy)

### Step 2: Failure triage

For each failure mode, categorize by root cause:

| Failure Stage | Meaning | Fix Location |
|---|---|---|
| `misrouted` | Classifier/solver didn't recognize this as a workflow build request | classifier.py, solver.py |
| `routing_blocked` | Confirmation prompt or meta-session prevented build (33-8 granular) | runtime.py CONFIRM bypass |
| `no_graph_created` | Build pipeline ran but produced no graph (legacy/fallback) | chat_manager.py, planner.py |
| `timeout_planning` | Stuck in planning/context, never reached codegen | LLM API, 90s wall timeout |
| `timeout_codegen` | Reached codegen but LLM timed out | LLM API, retry (33-8) |
| `stream_error` | WebSocket/connection failure before events | app.py, client reconnect |
| `llm_error` | LLM returned 500, internal error, empty response | Retry (33-8) |
| `syntax_error` | Generated builder code has Python syntax errors | codegen prompt, few-shot examples |
| `build_error` | Builder code runs but `build()` fails (bad node config, port mismatch) | codegen prompt, builder API |
| `validation_error` | Graph compiles but `validate_graph()` rejects it (unreachable nodes, missing edges, port mismatch) | codegen prompt, graph_mutator.py |
| `wrong_topology` | Graph validates but has wrong structure (chain instead of fan-out, missing review loop) | codegen prompt, intent compiler |
| `execution_error` | Graph validates but fails at runtime (bad prompts, tool config, data flow) | node prompts, tool registry |
| `timeout` | Generation took too long (codegen sandbox timeout, multiple retries) | sandbox timeout, retry budget |
| `guard_short_circuit` | Request guard pipeline (31-19) intervened and prevented the build path | entity_grounding.py, guard functions |
| `wrong_path` | Intent compiler was used but codegen would have been better (or vice versa) — after Phase 22 expansion, check path selection logic | intent_compiler.py, planner.py |
| `missing_defaults` | Graph validates but lacks smart defaults (no retry policy, no validation gates) that Phase 22 (32-3) should have auto-wired | generation_defaults.py |
| `wrong_domain` | Domain detection (31-21) assigned wrong domain or no domain, leading to incorrect profile selection (32-5) | domain_learning.py, domain profiles |
| `expectation_mismatch` | Graph validates but doesn't match fixture expectations (min_nodes, topology, node_types) — 33-10 B | Codegen prompt, intent compiler patterns, fixture ranges |
| `codegen_failed` | Codegen reached, code extracted, but sandbox execution failed | codegen prompt, builder API, sandbox |
| `wrong_tool_id` | Tool node has incorrect `tool_id` (e.g. `web_search` for a CSV-reading task) — 33-10 C | `_TOOL_KEYWORD_MAP`, intent extraction |
| `condition_polarity` | Review-loop condition is backwards (stop-when-satisfied vs continue-while) — 33-10 A | intent_compiler.py, codegen prompt |
| `build_tools_missing` | Mutation tool gated off despite workflow-build intent; LLM says "I don't have the tools" — Cycle 4 | tier_executors.py, triage.py, helpers.py |

> **Note:** The unified telemetry store (31-20) records exact tokens, cost, duration, model, retry count, and parent-child event correlation for every LLM call. This eliminates the "observability gap" failure category from earlier drafts. If a metric still can't be measured, file a bug against the telemetry emission sites rather than treating it as a test-harness concern.

### Step 3: Fix top 3 failure modes

Pick the 3 most frequent failure modes. For each:

1. Examine 2-3 example failures from the JSONL logs
2. Identify the minimal fix (prompt tweak, default change, validation rule)
3. Apply the fix
4. Re-run just the failed prompts to verify

Priority order: fix the failures that block the most prompts. A port-mismatch bug that fails 5 prompts is higher priority than a timeout that affects 1.

If a key metric is unmeasurable despite the telemetry layer, fix the emission site before drawing strong conclusions from the report.

### Step 4: Re-run full battery

After fixes, re-run the complete battery and compare:

- Did the overall pass rate improve?
- Did the `agent` vs `build` lane gap shrink where routing was the issue?
- Did the fixed failure modes actually decrease?
- Did any new regressions appear?

### Step 5: Document findings

Write up:
- Baseline vs post-fix comparison table
- `agent` vs `build` lane comparison table
- Generation path breakdown: % intent compiler vs % codegen per tier (Phase 22 expanded the intent compiler — is it being used?)
- Domain profile activation rate: which domains were detected, were profiles applied?
- Smart defaults adoption: what % of generated graphs have retry policies and validation gates?
- Root causes found (for bugs.md)
- Recommendations for further improvement (for backlog)
- Decision: is generation quality good enough for daily use, or does it need another fix cycle?

## Tasks

- [x] 1. Generate baseline report from first full run *(pilot 10 prompts, Run 1: 60% pass)*
- [x] 2. Triage failures into root-cause categories *(see [33-pilot-findings](33-pilot-findings-2026-03-11.md) and parent plan Measure-Fix-Measure)*
- [x] 3. Verify telemetry data completeness (tokens, cost, duration present for each turn) *(report now includes telemetry_completeness: with_tokens, with_cost, tokens_complete_rate)*
- [x] 4. Select top 3 failure modes for fixing *(routing_blocked, timeout, LLM flakiness)*
- [x] 5. Apply targeted fixes *(CONFIRM bypass, META_GOAL override, clarification auto-reply, 33-6/7/8 patches)*
- [x] 6. Re-run failed prompts to verify fixes *(Run 2: p04 fixed)*
- [x] 7. Re-run full battery for regression check *(2026-03-12: 51 records, 54.9% pass, durability D1-D4 run)*
- [x] 7b. Post-patch full battery re-run *(2026-03-12: 51 records, 54.9% pass — T2 +50pp, T3 +40pp, T4 +50pp vs pre-patch; intent compiler 100% pass when activated; 20/23 failures are no_graph_created; results: 2026-03-12_133646_run.jsonl)*
- [x] 8. Produce comparison report *(baseline vs post-fix in [33-generation-quality-eval](33-generation-quality-eval.md#measure-fix-measure-cycle-2026-03-11))*
- [x] 9. Update docs: bugs.md (root causes found), todo.md (remaining work), changelog.md *(bugs.md: Phase 33 root causes triage section added)*

### Cycle 3: Post-33-9/33-10 analysis (2026-03-12)

**Context:** Pipeline has received 33-6 (intent compiler activation), 33-7 (semantic quality gates), 33-8 (codegen resilience), 33-9 (build path trustworthiness), and 33-10 partial (semantic correctness — B, C, F done; A, D, E pending). Fixture expectation enforcement (33-10 B) is now active, producing an honest baseline. Tool keyword inference (33-10 C) and tool-aware extraction (33-10 F) should improve tool-node quality.

**Prior baseline (Cycle 2):** 33.3% pass (60 records, both lanes), build-only 46.7% (14/30). Intent compiler 18.3%. 72% of failures `timeout_planning`.

**Cycle 3 results (2026-03-13):** `tests/eval/results/2026-03-13_002753_run.jsonl` — 56 records, build lane only.

| Metric | Cycle 2 (build) | Cycle 3 (build) | Delta |
|---|---|---|---|
| Overall pass rate | 46.7% (14/30) | 25.0% (14/56) | -21.7pp (expected: fixture enforcement) |
| T1 | 33.3% (4/12) | 54.5% (6/11) | +21.2pp |
| T2 | 25.0% (3/12) | 37.5% (3/8) | +12.5pp |
| T2R | — | 0.0% (0/3) | — |
| T3 | 16.7% (2/12) | 0.0% (0/18) | -16.7pp (multi-turn all fail) |
| T4 | 16.7% (2/12) | 40.0% (4/10) | +23.3pp |
| T5 | 75.0% (9/12) | 16.7% (1/6) | -58.3pp (misrouted in build lane) |
| Intent compiler activation | 18.3% | 30.4% | +12.1pp |
| T1 intent compiler | 33.3% | 63.6% | +30.3pp |
| T2 intent compiler | 25.0% | 75.0% | +50.0pp |

**Failure mode distribution (42 failures):**

| Mode | Count | % | Description |
|---|---|---|---|
| `expectation_mismatch` | 23 | 54.8% | Graph valid but doesn't match fixture expectations |
| `timeout_planning` | 8 | 19.0% | LLM API never reached codegen |
| `misrouted` | 5 | 11.9% | T5 edge cases built graphs when shouldn't |
| `timeout_codegen` | 4 | 9.5% | Codegen timed out |
| `no_graph_created` | 2 | 4.8% | Pipeline ran but no graph |

**Top quality concerns from LLM-as-judge:**
- 9x single node with no edges for multi-step prompts
- 5x node count below fixture min
- 4x "review loop" prompt but no loop edges
- 4x "parallel" prompt but no fan-out/for_each nodes

- [x] 10. Generate post-33-9/33-10 report *(2026-03-13: 56 records, 25.0% honest baseline)*
  - [x] 10-1. Overall pass rate: 25.0% (down from 46.7% due to fixture enforcement)
  - [x] 10-2. Per-tier: T1 54.5%, T2 37.5%, T2R 0%, T3 0%, T4 40%, T5 16.7%
  - [x] 10-3. expectation_mismatch is dominant: 23/42 failures (54.8%)
  - [x] 10-4. Intent compiler: T1=63.6% (target 80% miss), T2=75.0% (target 60% HIT)
  - [x] 10-5. Tool_id: most graphs are LLM-only; tool/code/gate nodes rarely generated
- [ ] 11. Triage expectation_mismatch failures
  - [ ] 11-1. **Under-noding (genuine):** p02, t1-03, t1-06, t2-02 produce 1 node for multi-step prompts — intent compiler collapses to single node
  - [ ] 11-2. **Fixture too strict?** t1-07 (single-node summarizer fails "chain" topology check — could relax); t3-03 (5 nodes vs min 6 — borderline)
  - [ ] 11-3. **Missing node types (genuine):** t3-01 (no tool), t3-02 (no gate), t2-06 (no for_each) — LLM-only chains for prompts requiring tools
  - [ ] 11-4. **Multi-turn stagnation (genuine):** m1/m2/m3 never grow; follow-ups don't modify graphs
- [ ] 12. Compare honest vs prior pass rate
  - [x] 12-1. Gap: 46.7% (Cycle 2 structural) vs 25.0% (Cycle 3 honest) = 21.7pp of false-positive passes removed
  - [x] 12-2. Genuine improvements: T1 +21.2pp, T2 +12.5pp, T4 +23.3pp (more graphs, better quality)
- [ ] 13. Prioritize next fixes
  - [ ] 13-1. **P0: Intent compiler under-noding** — single biggest issue. 9 single-node graphs for multi-step prompts. Fix: intent compiler patterns must expand to multi-node; or fall back to codegen when prompt complexity exceeds intent compiler capability.
  - [ ] 13-2. **P1: Multi-turn mutation stagnation** — follow-ups don't modify graphs. ~~Fix: structural mutation macros not activating.~~ *(2026-03-16: root cause identified and fixed — mutation macros assumed legacy dict edge format `edges.data/control` but real graphs use flat `edges: list[Edge]`. `_get_edges_by_type()` helper now handles both formats. Re-run needed to verify multi-turn follow-ups now modify graphs correctly.)*
  - [ ] 13-3. **P1: T5 edge case detection in build lane** — 5/6 T5 prompts misrouted. Fix: build lane should still detect non-workflow requests before building. Or: accept that build lane always builds and only test T5 in agent lane.
  - [ ] 13-4. **P2: Missing non-LLM node types** — tool, code, gate, for_each nodes rarely generated. Fix: intent compiler patterns should produce tool/code nodes when prompt mentions file/web/code/email; codegen prompt should use tool catalog (33-10 D). *(2026-03-16: codegen tool catalog already landed (33-10 D); live intent extraction now also includes tool catalog. Re-run needed to measure impact.)*
- [ ] 14. Produce summary for next fix cycle
- [ ] 15. Update docs: bugs.md, todo.md, changelog.md

### Cycle 4: Build-capability gating fix (2026-03-17)

**Problem:** In agent mode, the chat assistant reports "I don't have the tools to build or modify workflows" and only exposes catalog/run tools (`list_graphs`, `fork_workflow`, `start_run`, `get_run_status`). The `plan_graph_mutations` tool and the codegen fast path are both gated behind `allow_mutation_tool`, which is only set when the triage produces a `workflow_edit` action hint. Three code-level mismatches cause this:

1. **`allow_mutation_tool` is too narrowly gated.** `_extract_chat_params()` in `tier_executors.py` only sets it when `"workflow_edit" in required_action_hints`. But `_determine_stage()` considers the turn a `workflow_build` more broadly (`mode == "build"` OR `route_target == "workflow"`). A turn can be routed as a workflow build but still lack the mutation tool.
2. **`_ACTION_HINT_TOOL_MAP` has no `workflow_edit` entry.** `_tool_choice_for_action_hints()` in `chat/helpers.py` cannot force the model toward `plan_graph_mutations` because the hint is not mapped to any tool. So even when the hint is present, `tool_choice` stays `"auto"` and the model can answer in prose instead of calling the mutation tool.
3. **Triage heuristic fallback is too narrow for build/retry phrasing.** `_WORKFLOW_EDIT_RE` in `triage.py` requires both a workflow-entity word AND an edit verb. Phrases like "can you learn from previous failures and retry to build?" have no workflow-entity word, so the fallback never produces `workflow_edit`.
4. **Stale empty-graph prompt.** `EMPTY_GRAPH_SUMMARY_PLACEHOLDER` still says "Create from scratch using plan_graph_mutations" but the runtime now prefers the codegen path for empty graphs.

- [x] 16. **Fix `allow_mutation_tool` gating in `tier_executors.py`**
  - [x] 16-1. In `_extract_chat_params()`, after the existing `allow_mutation_tool` inference from `required_action_hints`, add a second gate: if `mode == "build"` or `route_target == "workflow"`, force `allow_mutation_tool = True`. This aligns mutation-tool availability with the same predicate `_determine_stage()` uses.
  - [x] 16-2. Preserve the explicit metadata override: if the caller set `metadata["allow_mutation_tool"]` to a bool, that still takes precedence.
  - [x] 16-3. Add test in `test_tiered_dispatch.py`: a session with `mode="agent"` and `route_target="workflow"` (but no `workflow_edit` hint) should still produce `allow_mutation_tool=True`.
  - [x] 16-4. Add test: a session with `mode="build"` and no route/hints should produce `allow_mutation_tool=True`.

- [x] 17. **Add `workflow_edit` to `_ACTION_HINT_TOOL_MAP` in `chat/helpers.py`**
  - [x] 17-1. Add `"workflow_edit": frozenset({"plan_graph_mutations"})` to `_ACTION_HINT_TOOL_MAP`. This makes `_tool_choice_for_action_hints()` return `"required"` (or exact choice) when `workflow_edit` is unsatisfied, forcing the model to call the mutation tool instead of answering in prose.
  - [x] 17-2. Verified: existing mutation-handling code in `chat_manager.py` processes `plan_graph_mutations` calls specially (applies mutations and breaks out of the tool loop), so double-calling is not a risk. No code change needed.
  - [x] 17-3. Added tests in `test_tiered_dispatch.py`: `_missing_action_hints(["workflow_edit"], set())` returns `["workflow_edit"]`; `_missing_action_hints(["workflow_edit"], {"plan_graph_mutations"})` returns `[]`.

- [x] 18. **Broaden triage heuristic fallback for build/retry phrasing in `triage.py`**
  - [x] 18-1. Added `build|rebuild|retry|regenerate|redo` to `_WORKFLOW_EDIT_RE`. Added a separate `_WORKFLOW_BUILD_VERB_RE` (rebuild|retry|regenerate|redo|try again) and bridging condition: when the project has linked workflows and a build/retry verb is present, `workflow_context_like` is set True. This is more conservative than adding generic "build" to `_WORKFLOW_ENTITY_RE` (avoids false positives like "build a grocery list").
  - [x] 18-2. (merged into 18-1)
  - [x] 18-3. Added test in `test_triage.py`: `_infer_fallback_action_hints("can you learn from previous failures and retry to build?", context)` returns `["workflow_edit"]` when the context has a linked workflow.
  - [x] 18-4. Added test: `_infer_fallback_action_hints("rebuild the daily equity watchlist workflow", context)` returns `["workflow_edit"]`. Also added negative test: "build a grocery list" with no linked workflows does NOT produce `workflow_edit`.
  - [x] 18-5. **Review fix:** Added `try\s+again` to `_WORKFLOW_EDIT_RE`. Without this, the bridging condition (18-1) sets `workflow_context_like = True` for "try again" but the guard at line 888 fails because `_WORKFLOW_EDIT_RE` has no multi-word `try\s+again` alternative. Added test in `test_triage.py`.
  - [x] 18-6. **Review fix:** Successful embedding/LLM triage results now pass through workflow-edit post-processing. Without this, short agent-mode follow-ups like "can you retry and fix this?" could still be parsed as generic `ask/general` or `agent/general` turns, bypass the fallback heuristic entirely, and end as prose-only repair promises instead of `workflow_edit` routes. Added regression tests covering both LLM-parsed and embedding-primary misroutes.

- [x] 19. **Update stale empty-graph prompt in `chat/prompts.py`**
  - [x] 19-1. Changed `EMPTY_GRAPH_SUMMARY_PLACEHOLDER` to `"Workflow is empty (0 nodes, 0 edges). Build from scratch."` — removed `plan_graph_mutations` reference.
  - [x] 19-2. Softened `BUILD_FROM_INTENT_PROMPT` rule from `"Produce a complete runnable workflow in one plan_graph_mutations call."` to `"Produce a complete runnable workflow."`
- [x] 20. **Add direct simple-build chat regression coverage**
  - [x] 20-1. Added `tests/test_chat_manager_build_path.py` covering `ChatManager.send_message_with_tools()` in `agent` mode on an empty graph. The test stubs `_generate_workflow_from_intent()` to return a tiny valid `input -> llm_operator` workflow and asserts `ChatGraphCreatedEvent`, the standard completion copy, and persisted graph state.
  - [x] 20-2. Ran `python -m pytest tests/test_chat_manager_build_path.py tests/test_model_override.py -q` (12 passed).
- [x] 21. **Preserve explicit build-mode overrides through the live concierge executor seam**
  - [x] 21-1. Added `_request_mode()` in `tier_executors.py` so explicit `requested_mode="build"` / `"mutate"` survives the router’s normalized `mode="agent"` alias when determining the stage and extracting chat params.
  - [x] 21-2. Added regressions in `tests/test_concierge/test_tiered_dispatch.py` for both `_determine_stage()` and `_extract_chat_params()` when `requested_mode="build"` is present.
  - [x] 21-3. Live-smoke-tested the real `/api/chat/message` path on a temporary server: `/build` created a workflow, `/api/graphs/{id}/validate` returned no errors, `/run` completed successfully, and `/schedule add "/run" every 6h` plus `/schedule remove` both worked.
- [x] 22. **Wire live dispatcher resource budgets and soften Telegram overload copy**
  - [x] 22-1. Updated `build_concierge()` in `runtime.py` to instantiate `ResourceTracker(ResourceBudget.from_env())` and pass it into `ConcurrentDispatcher`, then updated `dispatcher.py` so live chat tasks consume both the run/project slot and the LLM slot. This makes the existing `DAN_MAX_CONCURRENT_RUNS` / `DAN_MAX_CONCURRENT_LLM` knobs actually constrain live queueing.
  - [x] 22-2. Added `_format_telegram_stream_error()` in `telegram_fleet.py` so upstream 429 / overload errors render as a temporary-overload message instead of raw backend/provider text.
  - [x] 22-3. Added regressions in `tests/test_concierge/test_resources.py` and `tests/test_adapters/test_telegram.py`, then ran `python -m pytest tests/test_chat_manager_build_path.py tests/test_concierge/test_tiered_dispatch.py tests/test_concierge/test_resources.py tests/test_concierge/test_scheduler.py tests/test_adapters/test_telegram.py -q` (`270` passed).

### Cycle 5: Workflow retry routing guardrails (2026-03-18)

**Problem:** Generic retry language like `"try again"` or `"retry"` over-routes into workflow editing whenever the project has any linked workflow, even when the user's message has no workflow-specific intent. The bridging condition in `_infer_fallback_action_hints()` (lines 940-943) treated `_WORKFLOW_BUILD_VERB_RE` + `linked_workflow_ids` as sufficient evidence. This was identified in the 2026-03-18 review (`docs/reviews/2026-03-18-workflow-generation-review.md`).

- [x] 23. **Add `_has_recent_workflow_activity()` guard to bridging condition in `triage.py`**
  - [x] 23-1. Added `_has_recent_workflow_activity()` that scans the last 4 task turns for workflow-related evidence: `workflow_edit`/`workflow_build`/`build`/`mutate` intents, `route_target="workflow"` metadata, `allow_mutation_tool` metadata, or assistant messages mentioning workflow/graph/build/node.
  - [x] 23-2. Updated the bridging condition so `_WORKFLOW_BUILD_VERB_RE` + linked workflow only sets `workflow_context_like = True` when `_has_recent_workflow_activity()` returns True.
  - [x] 23-3. Updated existing test `test_fallback_infers_workflow_edit_for_try_again_with_linked_workflow` → renamed to `test_fallback_no_workflow_edit_for_try_again_without_workflow_activity` (asserts `workflow_edit` NOT in hints).
  - [x] 23-4. Added `test_fallback_infers_workflow_edit_for_try_again_with_workflow_activity` (with recent turns → `workflow_edit` IS in hints).
  - [x] 23-5. Added `test_fallback_no_workflow_edit_for_plain_retry_without_workflow_activity`.
  - [x] 23-6. Updated `test_fallback_infers_workflow_edit_for_retry_build_with_linked_workflow` and both async triage upgrade tests to include `_workflow_activity_turns()`.
  - [x] 23-7. Verified: `rebuild the ... workflow` still works without activity turns (uses `_WORKFLOW_ENTITY_RE` path, not the bridging path).
  - [x] 23-8. 21 triage tests pass, 872 concierge tests pass (13 skipped), 0 regressions.

## Files

| File | Action |
|------|--------|
| `tests/eval/results/` | Read — baseline JSONL logs |
| Various source files | Fix — depending on failure modes |
| `src/dan/server/concierge/tier_executors.py` | Fix — widen `allow_mutation_tool` gate (task 16) |
| `src/dan/server/chat/helpers.py` | Fix — add `workflow_edit` to `_ACTION_HINT_TOOL_MAP` (task 17) |
| `src/dan/server/concierge/triage.py` | Fix — broaden heuristic fallback regexes (task 18) |
| `src/dan/server/chat/prompts.py` | Fix — update stale empty-graph and build-intent prompt text (task 19) |
| `src/dan/server/agent_runtime/workflow_generation.py` | Fix — deterministic intent tool choice and invalid-payload retry for empty-graph builds (task 27) |
| `src/dan/agent_runtime/mutation_preview.py` | Fix — fallback-only empty-graph auto-apply resolution helper (task 27) |
| `src/dan/server/chat_manager.py` | Fix — auto-apply recovered empty-graph fallback previews after fast-path failure (task 27) |
| `src/dan/server/concierge/runtime.py` | Fix — wire live dispatcher resource tracker (task 22) |
| `src/dan/adapters/telegram_fleet.py` | Fix — friendly overload messaging on Telegram (task 22) |
| `tests/test_concierge/test_tiered_dispatch.py` | Test — tasks 16-3, 16-4 |
| `tests/test_concierge/test_resources.py` | Test — task 22 |
| `tests/test_concierge/test_triage.py` | Test — tasks 18-3, 18-4 |
| `tests/test_chat_manager_build_path.py` | Test — task 20 |
| `tests/test_chat_manager_codegen_resilience.py` | Test — task 27 |
| `tests/test_agent_runtime/test_mutation_preview.py` | Test — task 27 |
| `tests/test_adapters/test_telegram.py` | Test — task 22 |
| `docs/bugs.md` | Update — root causes and failed approaches |
| `docs/changelog.md` | Update — fixes applied |
| `docs/todo.md` | Update — remaining generation quality work |

### Cycle 6: Generation pipeline correctness from 2026-03-19 workflow-generation review

> These tasks address structural bugs in the generation pipeline identified during the 2026-03-19 deep review. They are pre-existing code bugs, not findings from eval reruns.

- [ ] 24. **End-to-end integration tests** — add 3-5 representative prompts exercising: NL → intent extraction → compilation → validation → enrichment → execution readiness. Each pipeline stage has unit coverage but seams between components are untested.
- [ ] 25. **Model tiering abstraction** — replace hardcoded `gpt-4o` / `gpt-4o-mini` in `generation_defaults.py` model tiering with abstract tier labels that map to provider-specific models via config, so non-OpenAI users get correct assignments.
- [ ] 26. **Automated roundtrip test suite** — decompile reference graphs, recompile, and assert structural equivalence to lock down the lossless round-trip claim.
- [x] 27. **Empty-graph build fallback reliability**
  - [x] 27-1. Prefer exact or required `emit_workflow_intent` tool choice when the active provider/model supports it instead of defaulting to `tool_choice="auto"` for structured extraction.
  - [x] 27-2. Retry intent extraction once when a tool call is present but the payload does not parse into a valid `WorkflowIntent`.
  - [x] 27-3. Auto-apply recovered empty-graph mutation previews only when the fast build path already failed and the dry-run result is clean.
  - [x] 27-4. Keep normal mutation previews opt-in so non-fallback edits still require explicit `auto_apply`.
  - [x] 27-5. Added regressions in `tests/test_chat_manager_codegen_resilience.py`, `tests/test_chat_manager_build_path.py`, and `tests/test_agent_runtime/test_mutation_preview.py`, then re-ran the focused auto-apply compatibility subset in `tests/test_post_tool_followup_recovery.py`.

## Decisions

- **Granular failure modes (33-8 Task 7):** Added `routing_blocked` to `_determine_status()` when events contain "please confirm" or "meta session started" — distinguishes confirmation-blocked builds from generic `no_graph_created`.
- **Full battery timing:** A full 44-prompt battery was initially deferred on 2026-03-11 because of LLM API reliability and server stability under load, then completed on 2026-03-12 once the evaluation pass was retried. Treat the earlier deferment as historical context, not current status.
- **Empty-graph structured extraction:** Intent extraction should bias toward deterministic tool emission when the provider can honor it, rather than relying on `tool_choice="auto"` for a structured extraction task.
- **Fallback preview auto-apply boundary:** Empty-graph fallback auto-apply is only enabled for recovered build-lane previews after the fast path already failed; ordinary mutation previews remain opt-in.

## Notes

- "Top 3" is a guideline, not a hard rule. If one fix addresses 80% of failures, do that one fix and re-measure.
- Prompt tweaks (changing the codegen system prompt, adding few-shot examples) are the lowest-risk, highest-impact fixes. Code changes to the builder or validator should only happen if the prompt fix can't address the issue.
- The unified telemetry store (31-20) should provide exact build-phase tokens, cost, and duration via `chat_turn` events. If data gaps exist, the fix belongs in the telemetry emission sites (concierge, run_manager), not in the test harness.
- This plan is explicitly time-boxed: 1 day for analysis + fixes + re-run. If generation quality needs more than 1 day of fixes, that becomes a separate plan.
- **Cycle 3 framing:** The key question for Cycle 3 is NOT "did pass rate go up" — fixture enforcement (33-10 B) will likely lower the headline number. The key question is: "of the graphs that pass the honest baseline, are they semantically correct?" The gap between Cycle 2 pass rate (54.9%) and Cycle 3 honest pass rate shows how many false-positive passes existed before.
- **Expectation mismatch triage:** Not all `expectation_mismatch` failures are generation bugs. Some fixture ranges may be too narrow for valid alternative topologies. The analysis step should separate "fixture needs widening" from "generation genuinely wrong." Widen ranges and re-run if needed.
- **33-10 remaining patches:** The re-run will reveal how much value the remaining 33-10 patches (A: condition polarity, D: codegen tool catalog, E: LLM-as-judge) would add. If most failures are `expectation_mismatch` from fixture strictness, the fixes are different than if most are `wrong_tool_id` or `condition_polarity`.
- **Phase 22 feedback loop:** If convenience layer methods aren't being used, or the intent compiler isn't activating on expected patterns, or smart defaults aren't being applied — file as bugs against the relevant 32-X component.
- **Remaining verbosity signal:** If builder codegen still frequently emits manual `NodeRef(...)` recovery, that is likely the next post-33 ergonomics slice.
- **Guard pipeline (31-19) false positives:** If `guard_short_circuit` appears for legitimate build requests, tune guard thresholds.
- **Self-adaptive behavior (31-22):** If `DAN_BEHAVIOR_TIER >= 1`, log whether behavior proposals were generated during the battery run.
- **File reference note (2026-03-16):** The failure-triage table above references pre-rewrite filenames (`classifier.py`, `solver.py`, `entity_grounding.py`, `runtime.py`). These were replaced by `triage.py`, `tiered_dispatch.py`, `tier_executors.py` during the Plan 34 concierge rewrite. The fixes referenced by checked-off tasks were applied to the correct current files.
- **2026-03-27 benchmark-prep follow-up:** The LR2 benchmark-prep correctness seam is no longer blocked on missing tool args or stale report catalogs. `planner.py` now binds explicit local paths into generated tool configs, `workflow_contract.py` treats required tool args as part of run-readiness, `tests/eval/__main__.py` carries custom prompt-pack paths into report generation, and `executors/code.py` / `executors/llm.py` now mirror bare outputs onto a single declared port for generated nodes. Focused regressions landed, and the isolated rerun on `127.0.0.1:8010` passed (`tests/eval/results/2026-03-27_004702_phase33_lr2_dedicated_rerun_8010_r2.report.json`). The next analysis pass should focus on coverage breadth (LR1/LR3 + broader honest-baseline reruns), not the earlier LR2 plumbing bugs.
- **Meta-workflow scope:** Keep the meta-workflow builder backlogged for product work for now; use it first as a testing and evaluation flow rather than as a new authoring-layer dependency.
