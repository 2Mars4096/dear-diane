# 45-5: Benchmark and Rollout Gates

**Parent:** [45-workflow-generation-hardening](45-workflow-generation-hardening.md)
**Status:** not-started
**Goal:** Define the benchmark loop and merge criteria that every plan-45 generation experiment must satisfy before it is allowed onto `main` or promoted to a default path.

## Problem

Without explicit rollout gates, work like this tends to merge because it is interesting, not because it is better. That is exactly the wrong failure mode for workflow generation.

## Tasks

- [ ] 1. Define the benchmark set
  - [ ] 1-1. Reuse representative prompt batteries where possible and add any missing direct-builder fixtures.
  - [ ] 1-2. Ensure prompts cover simple, medium, fan-in, schedule-bearing, and failure-prone workflow shapes.
- [ ] 2. Define the scorecard
  - [ ] 2-1. Track graph_created, validated, run_ready, latency, fallback rate, failure buckets, and semantic quality.
  - [ ] 2-2. Track direct-surface results separately from concierge-mediated results.
- [ ] 3. Define the comparison lanes
  - [ ] 3-1. Baseline current path.
  - [ ] 3-2. Direct builder surface.
  - [ ] 3-3. Speed-first minimal path.
  - [ ] 3-4. Meta-builder harness.
- [ ] 4. Define merge gates
  - [ ] 4-1. No path merges without a written benchmark win or a clearly justified tradeoff.
  - [ ] 4-2. Keep new paths behind flags until the scorecard justifies wider rollout.
- [ ] 5. Define rollback criteria and reporting
  - [ ] 5-1. If a path regresses reliability or quality, disable it quickly.
  - [ ] 5-2. Document operator guidance for when to enable, compare, or retire each path.

## Decisions

- Benchmark evidence is required for merge; intuition is not enough.
- Worktree-first evaluation is the default operating mode for this tranche.
- Paths must compete on the same acceptance bar and prompt set.

## Notes

- This sub-plan is the practical expression of "only merge to main if it works well".
- It should be the last plan in the sequence because it depends on concrete candidate paths to compare.
