# 55-4: Robustness To Misspecification

**Parent:** [55-agent-level-stochastic-orchestration](55-agent-level-stochastic-orchestration.md)
**Status:** in-progress
**Goal:** Make the scheduler stable when estimated runtimes, quality gains, failure risks, uncertainties, and dependency edges are wrong.

## Tasks

- [ ] 1. Define misspecification model
  - [ ] 1-1. Treat duration, quality gain, uncertainty, failure probability, and dependency edges as estimated parameters
  - [ ] 1-2. Track residuals between predicted and observed runtime/quality outcomes
  - [ ] 1-3. Calibrate estimates from live organism logs and replayed traces
  - [ ] 1-4. Include decomposition-model error: a task split can look parallelizable but become slower after handoff, context, or reducer overhead
- [ ] 2. Add conservative pruning rules
  - [ ] 2-1. Prune only when a branch upper confidence bound plus slack is below the incumbent lower confidence bound
  - [ ] 2-2. Keep a bounded exploration reserve for plausible but uncertain branches
  - [ ] 2-3. Record every cancellation decision with estimate inputs and slack margin
- [ ] 3. Add stability controls
  - [ ] 3-1. Add hysteresis so tiny score shifts do not constantly reshuffle the queue
  - [ ] 3-2. Support bounded redundancy for high-impact or high-uncertainty tasks
  - [ ] 3-3. Detect and recover from dependency-graph errors by reopening, adding, demoting, or disproving edges
  - [x] 3-4. Require deterministic guardrails around universal-agent pruning/cancellation proposals so a persuasive but under-evidenced schedule decision cannot overkill a branch
  - [x] 3-5. Add the first deterministic cross-lane contract repair for short website owner lanes so model misspecification in HTML/CSS/JS selector, animation, and structural assumptions does not immediately poison validation
- [ ] 4. Add policy evaluation
  - [ ] 4-1. Compare strict pruning, conservative pruning, no pruning, and redundancy policies on recorded traces
  - [ ] 4-2. Measure time-to-quality, over-pruning failures, noisy extra work, and validator escape failures
  - [ ] 4-3. Keep the first live rollout behind a diagnostic or opt-in scheduler mode

## Decisions

- Robustness is a first-class requirement, not a cleanup after optimization.
- Early estimates should be treated as weak evidence until calibrated.
- The scheduler should prefer leeway over brittle over-pruning, but the leeway must be bounded so the system does not become noisy and useless.
- Robustness applies to both score estimates and graph structure; wrong decomposition can be as harmful as wrong task scoring.
- Universal-agent judgment is itself a misspecified model; scheduler proposals need the same confidence, slack, and audit discipline as runtime/quality estimates.

## Notes

- The pruning rule in the theory note is the intended starting point: cancel a branch only when `UCB(branch) + epsilon < LCB(best)`.
- Dependency uncertainty is as important as parameter uncertainty; wrong dependencies can create false barriers or unsafe parallelization.
- Landed first code slice: `evaluate_scheduler_proposal(...)` now rejects cancellation proposals that lack confidence bounds/slack or fail the pruning-margin condition, so universal-agent schedule moves already pass through one deterministic robustness gate.
- The replay layer now exposes lane-sequence-only serialization and terminal barrier tails from recorded traces, which gives later robustness work concrete evidence for dependency-edge and barrier misspecification without changing live scheduling yet.
- DAN Code short website runs exposed a useful misspecification class: parallel owner lanes can each make reasonable local changes while disagreeing on shared frontend contracts. The short-timeout hygiene pass now repairs common deterministic drift before validation instead of pruning or retrying whole branches.
