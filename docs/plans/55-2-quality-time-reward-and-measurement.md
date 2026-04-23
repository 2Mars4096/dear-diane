# 55-2: Quality/Time Reward And Measurement

**Parent:** [55-agent-level-stochastic-orchestration](55-agent-level-stochastic-orchestration.md)
**Status:** in-progress
**Goal:** Define measurable quality and time signals so the scheduler can optimize time-to-quality while keeping future reward terms extensible.

## Tasks

- [ ] 1. Define the reward interface
  - [ ] 1-1. Start with `quality_score - lambda * latency`
  - [ ] 1-2. Add optional fields for token cost, rework risk, context loss, and operator intervention without making them required in v1
  - [ ] 1-3. Record the reward components separately so future policies can be re-scored offline
  - [ ] 1-4. Support constrained objectives: maximize quality under a time budget and minimize time to reach a quality threshold
- [ ] 2. Define quality measurement sources
  - [ ] 2-1. Support deterministic tests, validator scores, human/rubric grades, evidence coverage, and artifact completeness
  - [ ] 2-2. Separate internal confidence from validated quality
  - [ ] 2-3. Track quality deltas from each task, not only final-run quality
  - [ ] 2-4. Define `q_target` / acceptance thresholds per task family so time-to-quality is measurable rather than only rhetorical
- [ ] 3. Define time measurement sources
  - [ ] 3-1. Use wall-clock time from organism logs and gateway telemetry
  - [ ] 3-2. Split queue wait, provider latency, tool latency, validation latency, and reducer latency
  - [ ] 3-3. Derive time-to-quality curves for live runs and replayed traces
  - [ ] 3-4. Measure residual barrier time for aggregation, validation, and final delivery separately from parallel worker time
  - [x] 3-5. Add a first replay-only time diagnostic slice from organism logs: makespan, exclusive work, average parallelism, effective lower bound, slack, and terminal barrier tail
  - [x] 3-6. Prove aggregation barrier reduction on DAN Code short website runs: covered exclusive-owner lanes now use deterministic `worker_merge` aggregation with observed 0-1 ms aggregation duration in live traces

## Decisions

- Quality and time are the initial optimization priorities.
- Token cost is diagnostic-only until the quality/time loop is credible.
- The reward schema should be append-only so old traces remain useful for offline policy experiments.
- A scalar reward is an implementation convenience; the product metric should stay time-to-acceptable-quality.

## Notes

- SWE-bench, live coding batteries, DAN Research evals, and Super DAN live traces can all provide different quality signals.
- The reward should not treat confident prose as quality unless it passes a validator or acceptance gate.
- The current seed measurement slice lives in `src/dan/worker/scheduler/replay.py` and `dan-organism-log scheduler-replay`; it is time-only for now and does not yet score quality.
- Latest live short-run traces show the optimization target clearly: aggregation has been reduced to a near-zero deterministic barrier for covered owner lanes, while validation remains the dominant tail. The next reward work should score quality/time separately for worker time, deterministic hygiene, and validator latency.
