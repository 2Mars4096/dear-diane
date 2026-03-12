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

**Prior baseline (Cycle 2):** 54.9% pass (51 records), 20/23 failures were `no_graph_created`. Intent compiler 29.4% activation. 72% of failures were `timeout_planning`.

- [ ] 10. Generate post-33-9/33-10 report from re-run JSONL
  - [ ] 10-1. Overall pass rate (expected: lower than 54.9% due to fixture enforcement)
  - [ ] 10-2. Per-tier pass rates with comparison to Cycle 2
  - [ ] 10-3. Failure mode distribution — specifically track new `expectation_mismatch` category
  - [ ] 10-4. Intent compiler activation rate per tier (target: T1 >80%, T2 >60%)
  - [ ] 10-5. Tool_id correctness rate for tool-node prompts
- [ ] 11. Triage new failure modes
  - [ ] 11-1. Separate `expectation_mismatch` into "fixture too strict" vs "generation genuinely wrong"
  - [ ] 11-2. Widen fixture ranges for reasonable alternative topologies
  - [ ] 11-3. Identify any remaining `no_graph_created` that should be sub-classified
- [ ] 12. Compare honest baseline to prior "passing" rate
  - [ ] 12-1. Document the gap between structural-only validation and expectation-fit validation
  - [ ] 12-2. Identify which "previously passing" graphs now fail — are they genuine quality issues?
- [ ] 13. Assess 33-10 A/D/E impact
  - [ ] 13-1. How many graphs have review-loop condition polarity bugs? (informs 33-10 A priority)
  - [ ] 13-2. How many graphs have wrong tool_ids despite keyword map? (informs 33-10 D — codegen tool catalog)
  - [ ] 13-3. Would LLM-as-judge (33-10 E) catch issues that fixture expectations miss?
- [ ] 14. Produce Cycle 3 comparison report
  - [ ] 14-1. Cycle 2 vs Cycle 3 comparison table
  - [ ] 14-2. Updated failure mode histogram
  - [ ] 14-3. Recommendations for 33-10 remaining patches (A, D, E)
  - [ ] 14-4. Decision: is generation quality trending toward daily usability?
- [ ] 15. Update docs: bugs.md, todo.md, changelog.md

## Files

| File | Action |
|------|--------|
| `tests/eval/results/` | Read — baseline JSONL logs |
| Various source files | Fix — depending on failure modes |
| `docs/bugs.md` | Update — root causes and failed approaches |
| `docs/changelog.md` | Update — fixes applied |
| `docs/todo.md` | Update — remaining generation quality work |

## Decisions

- **Granular failure modes (33-8 Task 7):** Added `routing_blocked` to `_determine_status()` when events contain "please confirm" or "meta session started" — distinguishes confirmation-blocked builds from generic `no_graph_created`.
- **Full battery timing:** A full 44-prompt battery was initially deferred on 2026-03-11 because of LLM API reliability and server stability under load, then completed on 2026-03-12 once the evaluation pass was retried. Treat the earlier deferment as historical context, not current status.

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
