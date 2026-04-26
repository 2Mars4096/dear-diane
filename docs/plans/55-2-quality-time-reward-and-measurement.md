# 55-2: Quality/Time Reward And Measurement

**Parent:** [55-agent-level-stochastic-orchestration](55-agent-level-stochastic-orchestration.md)
**Status:** in-progress
**Goal:** Define measurable quality and time signals so the scheduler can optimize time-to-quality while keeping future reward terms extensible.

## Tasks

- [ ] 1. Define the reward interface
  - [x] 1-1. Start with `quality_score - lambda * latency`
  - [x] 1-2. Add optional fields for token cost, rework risk, context loss, and operator intervention without making them required in v1
  - [x] 1-3. Record the reward components separately so future policies can be re-scored offline
  - [ ] 1-4. Support constrained objectives: maximize quality under a time budget and minimize time to reach a quality threshold
  - [x] 1-5. Score scheduler actions by marginal value: expected quality gain, latency added to the critical path, rework risk, validation burden, and probability of material output
- [ ] 2. Define quality measurement sources
  - [ ] 2-1. Support deterministic tests, validator scores, human/rubric grades, evidence coverage, and artifact completeness
  - [ ] 2-2. Separate internal confidence from validated quality
  - [ ] 2-3. Track quality deltas from each task, not only final-run quality
  - [ ] 2-4. Define `q_target` / acceptance thresholds per task family so time-to-quality is measurable rather than only rhetorical
  - [x] 2-5. Track worker material-yield rate separately from final quality so "parallelism" is not rewarded when lanes only read and time out
- [ ] 3. Define time measurement sources
  - [ ] 3-1. Use wall-clock time from organism logs and gateway telemetry
  - [ ] 3-2. Split queue wait, provider latency, tool latency, validation latency, and reducer latency
  - [ ] 3-3. Derive time-to-quality curves for live runs and replayed traces
  - [ ] 3-4. Measure residual barrier time for aggregation, validation, and final delivery separately from parallel worker time
  - [x] 3-5. Add a first replay-only time diagnostic slice from organism logs: makespan, exclusive work, average parallelism, effective lower bound, slack, and terminal barrier tail
  - [x] 3-6. Prove aggregation barrier reduction on DAN Code short website runs: covered exclusive-owner lanes now use deterministic `worker_merge` aggregation with observed 0-1 ms aggregation duration in live traces
  - [x] 3-7. Measure time-to-first-useful-artifact and downstream-unlock latency from capsule/readiness events, not only terminal worker completion
  - [x] 3-8. Measure model-validator tail time separately from deterministic validation time and final delivery time
  - [x] 3-9. Cap DAN Code validation model-call latency separately from the worker/build budget so validators cannot inherit the full long-run timeout by default

## Decisions

- Quality and time are the initial optimization priorities.
- Token cost is diagnostic-only until the quality/time loop is credible.
- The reward schema should be append-only so old traces remain useful for offline policy experiments.
- A scalar reward is an implementation convenience; the product metric should stay time-to-acceptable-quality.
- Reward features must stay task-agnostic. A policy can learn that a class of artifact partitions works well, but it should not hardcode one product family as the reason to parallelize.

## Notes

- SWE-bench, live coding batteries, DAN Research evals, and Super DAN live traces can all provide different quality signals.
- The reward should not treat confident prose as quality unless it passes a validator or acceptance gate.
- The current seed measurement slice lives in `src/dan/worker/scheduler/replay.py` and `dan-organism-log scheduler-replay`; it is time-only for now and does not yet score quality.
- Latest live short-run traces show the optimization target clearly: aggregation has been reduced to a near-zero deterministic barrier for covered owner lanes, while validation remains the dominant tail. The next reward work should score quality/time separately for worker time, deterministic hygiene, and validator latency.
- The measurement harness should make failed parallelism visible: a lane that returns no material artifact has low or negative marginal value even if it ran concurrently.
- DAN Code now emits compact validation policy events: `validation.precheck.completed` separates deterministic static checks from model review, and `validation.policy.selected` records whether the run used the compact one-lead validator or the full quorum validator. Replay/reward scoring can use those events to split deterministic validation time, model-validator tail time, and final delivery time.
- DAN Code validation packets now carry a request-level timeout cap (`30s` when inheriting a longer run budget), and the shared local runtime honors that request-level `completion_timeout_seconds` metadata before applying direct-write soft caps. Worker/build calls can still use the configured long timeout, but validator reviewers should no longer show `timeout=90.00s` from the default DAN Code budget.
- DAN Code now emits `worker.material_yield.measured` after each completed worker pool. The event separates `candidate_material` from `materialized_artifact`, counts matching mutation evidence, records no-material reasons, and stores the same payload in result metadata as `material_yield_history`.
- First action-level reward guard landed for duplicate/hedge proposals: `SchedulingProposal` now carries optional expected value, uncertainty, failure probability, latency cost, token cost, rework risk, and material-yield probability fields; `evaluate_scheduler_proposal(...)` returns hedge value/cost metadata and rejects non-positive or low-yield duplicate actions.
- First generic action selector landed: `select_scheduler_proposal(...)` scores admissible proposals as expected value times material-yield probability minus latency/token/rework costs, records the score components in ranking metadata, and can choose between serial, parallel, duplicate/hedge, context-improve, validate/finalize, wait, and stop proposals through one reward surface. DAN Code now emits those ranking components when artifact partitions are promoted into owner-lane parallelism.
- First readiness timing metrics landed in scheduler replay: `context.capsule.emitted` and `context.readiness.emitted` rows now produce first useful artifact time, first downstream-ready time, downstream unlock latency, ready signal ids, predicates, and blocker-event counts.
