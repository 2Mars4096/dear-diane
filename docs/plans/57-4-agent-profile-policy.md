# 57-4: Agent Profile and Hyperparameter Policy

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** not-started
**Goal:** Turn raw orchestration hyperparameters into a small set of product-level Agent profiles while preserving internal control over worker count, parallelism, validation, repair, and budget.

## Tasks
- [ ] 1. Define product profiles
  - [ ] 1-1. `Fast`: low agent count, low parallelism, minimal validation, short budgets
  - [ ] 1-2. `Balanced`: default agent count, moderate parallelism, one validation pass, bounded repair
  - [ ] 1-3. `Deep`: broader worker pool, stronger validation, richer context/retry behavior
  - [ ] 1-4. `Max`: high budget, high parallelism within safety caps, strict validation and repair
- [ ] 2. Define internal hyperparameter schema
  - [ ] 2-1. `agent_count`
  - [ ] 2-2. `parallelism_cap`
  - [ ] 2-3. `planner_depth`
  - [ ] 2-4. `worker_timeout_seconds`
  - [ ] 2-5. `tool_call_budget`
  - [ ] 2-6. `repair_rounds`
  - [ ] 2-7. `validator_count`
  - [ ] 2-8. `artifact_partitioning_policy`
  - [ ] 2-9. `write_permission` and `approval_policy`
- [ ] 3. Map profiles to backends
  - [ ] 3-1. Map profiles to Super DAN defaults without losing existing CLI defaults
  - [ ] 3-2. Leave room for DAN Code, DAN Research, universal organism, and future operator agents
  - [ ] 3-3. Support per-task overrides only through advanced/debug surfaces, not primary UI
- [ ] 4. Add calibration and observability
  - [ ] 4-1. Log selected profile and resolved hyperparameters on every Agent run
  - [ ] 4-2. Record actual worker count, parallelism, time-to-first-artifact, validation outcome, repair count, and final status
  - [ ] 4-3. Feed these metrics into Plan 55 scheduler replay where possible
- [ ] 5. Define safe bounds
  - [ ] 5-1. Enforce global maximums even when `Max` is selected
  - [ ] 5-2. Keep provider/gateway dispatch limits separate from task scheduling decisions
  - [ ] 5-3. Fail closed on mutation permissions that conflict with the surface approval envelope

## Decisions
- User-facing controls should be profile-first, not raw-agent-count-first.
- Raw hyperparameters are still first-class data for reproducibility, debugging, and experiments.
- Profile defaults should be conservative enough for daily use and explicit enough for eval comparison.

## Notes
- This plan should reuse Plan 55's scheduler vocabulary instead of creating a parallel set of orchestration knobs.
