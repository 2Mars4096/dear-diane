# 44-5: Routing, Rollout, and Evals

**Parent:** [44-structured-workflow-generation](44-structured-workflow-generation.md)
**Status:** not-started
**Goal:** Route structured workflow generation behind a safe rollout path, compare it against the current intent-compiler/codegen pipeline, and measure whether it improves latency, robustness, and semantic quality on realistic workflow-authoring prompts.

## Problem

The new structured-generation pipeline is only useful if it is:

- routed deliberately instead of becoming another silent fallback
- benchmarked against the current build path on the same prompts
- guarded so concierge instability is measured separately from graph-generation quality
- rolled out with a clear fallback path when it regresses

Without that, we will not know whether the structured pipeline is actually better or just differently broken.

## Tasks

- [ ] 1. Define routing rules for the structured-generation path
  - [ ] 1-1. Add a feature flag via `DAN_STRUCTURED_GENERATION` (`disabled` / `canary` / `enabled`, default `disabled`) and route before the current path has already spent most of the generation budget; `CoverageChecker` in `intent_compiler.py` can be an advisory signal, but it should not be the only routing gate because it sits downstream of intent extraction
  - [ ] 1-2. Define which prompt classes are eligible for structured generation first — eligibility criteria should be configurable, not a hardcoded list, and should cover continuation-style build turns plus schedule-bearing workflow prompts
  - [ ] 1-3. Keep concierge and triage routing diagnostics separate from graph-generation diagnostics
- [ ] 2. Add rollout guardrails and fallback behavior
  - [ ] 2-1. Ensure the current path remains the default until the structured path proves stable
  - [ ] 2-2. Add explicit fallback to the existing generation path when structured generation fails validation
  - [ ] 2-3. Prevent partially built candidate graphs from being persisted before acceptance
- [ ] 3. Build a comparison benchmark suite
  - [ ] 3-1. Add realistic conversation-style workflow prompts, including the equity-research example
  - [ ] 3-2. Include prompts of simple, medium, and moderately long workflow length
  - [ ] 3-3. Compare structured generation vs intent-compiler/codegen on the same prompt set
  - [ ] 3-4. Add end-to-end fixtures that continue past generation: save -> one-shot run -> scheduled dispatch -> result assertion for workflows whose prompts ask for recurring execution
- [ ] 4. Define acceptance metrics
  - [ ] 4-1. Measure end-to-end latency from user message to runnable workflow
  - [ ] 4-2. Measure `graph_created`, `run_ready`, and validation success rates
  - [ ] 4-3. Measure semantic quality, not just structural validity
  - [ ] 4-4. Track concierge parsing and routing stability separately from graph-generation success
  - [ ] 4-5. Measure one-shot run success, scheduled-dispatch success, and result-delivery success separately from generation success
- [ ] 5. Add rollout and regression evals
  - [ ] 5-1. Run the benchmark suite in both control and structured modes
  - [ ] 5-2. Record latency distributions and failure modes by stage
  - [ ] 5-3. Add regression coverage for known failure classes: empty graph, boundary mismatch, timeout fallback, and path variability
  - [ ] 5-4. Add a canary rollout path for opt-in structured generation
  - [ ] 5-5. Add a scheduled-dispatch smoke path that uses the existing `/schedule workflow` or scheduler-store path, triggers an immediate or overdue run, and asserts that a run starts and a result event or artifact is emitted
- [ ] 6. Document operator guidance
  - [ ] 6-1. Record when to enable structured generation
  - [ ] 6-2. Record how to interpret benchmark results
  - [ ] 6-3. Record rollback criteria when structured generation regresses

## Likely Files

Existing (modify or extend):
- `src/dan/server/agent_runtime/workflow_generation.py` — wire the structured path alongside the current path
- `src/dan/server/chat_manager.py` — routing decision point
- `src/dan/meta/intent_compiler.py` — existing `CoverageChecker` shim is the natural routing gate; `IntentCompiler` carries the current build path
- `src/dan/meta/graph_quality.py` — quality comparison metrics
- `src/dan/meta/workflow_contract.py` — shared validation for both paths
- `src/dan/server/agent_runtime/workflow_generation_stats.py` — existing `record_generation_outcome()` / `get_generation_stats_hint()` for recording per-path outcomes
- `src/dan/engine/generation_stats.py` — underlying generation stats store
- `src/dan/server/concierge/scheduler.py` — existing `/schedule workflow` path and schedule store
- `src/dan/server/startup.py` — existing scheduled workflow dispatch into `RunManager.start_run()`
- `src/dan/server/run_manager.py` — run start/completion success metrics
- `tests/eval/__main__.py` — existing eval runner with lane/tag/run-tag infrastructure
- `tests/eval/workflow_contract_comparison_prompts.json` — existing prompt fixtures (already has chatty/realistic prompts)
- `tests/test_meta/test_workflow_generation_pipeline.py`

## Decisions

- Roll out behind an explicit feature flag (`DAN_STRUCTURED_GENERATION`) rather than replacing the current path outright. The flag should have three states (`disabled` / `canary` / `enabled`) so operators can opt in gradually.
- Compare against the current intent-compiler/codegen pipeline on the same prompts and the same acceptance criteria.
- Treat concierge instability as a separate metric stream from graph-generation quality.
- Use realistic workflow-authoring prompts, not synthetic toy prompts, as the main benchmark set.
- Block persistence until the structured candidate graph passes acceptance.
- All acceptance thresholds (quality score minimums, latency budgets, success rate targets) must be `DAN_*` env-var configurable so operators can tune rollout aggressiveness without code changes.
- The structured path should be judged on the whole chain, not just graph creation: authored graph, one-shot run, scheduled dispatch, and result artifact delivery.

## Notes

- The benchmark should emphasize time-to-runnable-workflow, not just final graph shape.
- The comparison suite should include the equity-research prompt plus nearby real-world variants. The existing `workflow_contract_comparison_prompts.json` already has `contract-10-chatty-watchlist-brief`, `contract-11-chatty-folder-digest`, and `contract-12-chatty-earnings-note` as realistic fixtures.
- If the structured pipeline becomes slower but more reliable, that tradeoff should be visible in the metrics instead of hidden.
- This plan should stay generic so it can cover future workflow families, not just the current equity-research case.
- The existing eval runner (`tests/eval/__main__.py`) already supports `--lane build`, `--workflow-contract enabled`, `--tag`, and `--run-tag` for comparison runs. The structured path should get its own lane or tag rather than a separate harness.
- The existing `record_generation_outcome()` in `workflow_generation_stats.py` already records `success_method` (e.g. `intent_compiler`, `sandbox`, `automatic_recovery`). The structured path should register as a distinct `success_method` for comparison.
- The repo already has a real scheduling surface: `src/dan/server/concierge/scheduler.py` stores workflow schedules, and `src/dan/server/startup.py` dispatches scheduled workflows through `RunManager.start_run()`. The rollout plan should reuse that path rather than inventing a second scheduling harness.
