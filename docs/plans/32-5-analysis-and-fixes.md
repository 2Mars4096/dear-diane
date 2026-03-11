# 32-5: Analysis & Fixes

**Parent:** [32-generation-quality-eval](32-generation-quality-eval.md)
**Status:** not-started
**Goal:** Analyze the baseline results from 32-3 and 32-4, identify the top failure modes, apply targeted fixes, and re-measure to confirm improvement.

## Process

### Step 1: Read the baseline report

After the first full run of the small + complex batteries, the JSONL logs contain every failure with full context. The report generator (32-2) produces:

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
| `no_graph_created` | Build pipeline ran but produced no graph (codegen silently failed, or mutation wasn't applied) | chat_manager.py, planner.py |
| `syntax_error` | Generated builder code has Python syntax errors | codegen prompt, few-shot examples |
| `build_error` | Builder code runs but `build()` fails (bad node config, port mismatch) | codegen prompt, builder API |
| `validation_error` | Graph compiles but `validate_graph()` rejects it (unreachable nodes, missing edges, port mismatch) | codegen prompt, graph_mutator.py |
| `wrong_topology` | Graph validates but has wrong structure (chain instead of fan-out, missing review loop) | codegen prompt, intent compiler |
| `execution_error` | Graph validates but fails at runtime (bad prompts, tool config, data flow) | node prompts, tool registry |
| `timeout` | Generation took too long (codegen sandbox timeout, multiple retries) | sandbox timeout, retry budget |

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
- Root causes found (for bugs.md)
- Recommendations for further improvement (for backlog)
- Decision: is generation quality good enough for daily use, or does it need another fix cycle?

## Tasks

- [ ] 1. Generate baseline report from first full run
- [ ] 2. Triage failures into root-cause categories
- [ ] 3. Verify telemetry data completeness (tokens, cost, duration present for each turn)
- [ ] 4. Select top 3 failure modes for fixing
- [ ] 5. Apply targeted fixes (prompt, config, telemetry, or code)
- [ ] 6. Re-run failed prompts to verify fixes
- [ ] 7. Re-run full battery for regression check
- [ ] 8. Produce comparison report (baseline vs post-fix, plus `agent` vs `build`)
- [ ] 9. Update docs: bugs.md (root causes found), todo.md (remaining work), changelog.md

## Files

| File | Action |
|------|--------|
| `tests/eval/results/` | Read — baseline JSONL logs |
| Various source files | Fix — depending on failure modes |
| `docs/bugs.md` | Update — root causes and failed approaches |
| `docs/changelog.md` | Update — fixes applied |
| `docs/todo.md` | Update — remaining generation quality work |

## Decisions

- (filled in during execution)

## Notes

- "Top 3" is a guideline, not a hard rule. If one fix addresses 80% of failures, do that one fix and re-measure.
- Prompt tweaks (changing the codegen system prompt, adding few-shot examples) are the lowest-risk, highest-impact fixes. Code changes to the builder or validator should only happen if the prompt fix can't address the issue.
- The unified telemetry store (31-20) should provide exact build-phase tokens, cost, and duration via `chat_turn` events. If data gaps exist, the fix belongs in the telemetry emission sites (concierge, run_manager), not in the test harness.
- This plan is explicitly time-boxed: 1 day for analysis + fixes + re-run. If generation quality needs more than 1 day of fixes, that becomes a separate plan.
