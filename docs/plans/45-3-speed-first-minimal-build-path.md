# 45-3: Speed-First Minimal Build Path

**Parent:** [45-workflow-generation-hardening](45-workflow-generation-hardening.md)
**Status:** not-started
**Goal:** Define and evaluate a stripped-down generation lane that optimizes for speed, determinism, and acceptable quality on a scoped subset of workflow prompts, especially for weaker or open-source models.

## Problem

The current path is powerful, but power carries cost:

- too many branches and fallback layers for simple/common graph shapes
- extra latency before a useful draft appears
- more context and prompt complexity than weaker models can reliably handle

The backlog already suggests a "speed-first rebuild track" if needed. This sub-plan turns that idea into a scoped experiment instead of a vague rewrite threat.

## Tasks

- [ ] 1. Define the minimal supported subset
  - [ ] 1-1. Choose the node/edge families and workflow shapes this path must support first.
  - [ ] 1-2. Exclude shapes that require heavy repair, rich subgraphs, or ambiguous synthesis semantics.
- [ ] 2. Design the fast path
  - [ ] 2-1. Minimize prompt/context payloads and path complexity.
  - [ ] 2-2. Prefer direct build and deterministic transforms where possible.
  - [ ] 2-3. Bound retries and diagnosis more aggressively than the full path.
- [ ] 3. Keep the same acceptance target
  - [ ] 3-1. Even the minimal path must output the same accepted graph contract as the full path.
  - [ ] 3-2. Drafts that do not meet that contract should fail fast, not silently degrade.
- [ ] 4. Benchmark the experiment
  - [ ] 4-1. Compare latency, graph_created, run_ready, and semantic quality against the full path on the scoped prompt set.
  - [ ] 4-2. Include open-source-model-oriented runs where practical.
- [ ] 5. Define a kill-switch
  - [ ] 5-1. If the minimal path is not clearly better on its scoped set, reject it instead of expanding it.

## Decisions

- This is an experiment, not a default-path commitment.
- The minimal path should win by being smaller and clearer, not by weakening the acceptance bar.
- Any improvement claim must be benchmark-backed.

## Notes

- This sub-plan is the structured version of the backlog idea "rebuild the minimal graph-generation path from scratch in 1-2 days if necessary".
- If it works, it can become a dedicated fast lane. If it does not, it should be retired cleanly.
