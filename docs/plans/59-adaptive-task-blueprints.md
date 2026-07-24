# 59: Adaptive Task Blueprints

**Status:** completed
**Goal:** Make one canonical Task Blueprint the durable semantic source of truth for a task—combining its protected goal contract with task-native work, decisions, artifacts, dependencies, and gates—while compiling separate, immutable Execution Attempts for Super DAN and projecting the same state into the Work GUI.

## Tasks

- [x] 1. Define the canonical blueprint model via [59-1-task-blueprint-contract-and-revisions](59-1-task-blueprint-contract-and-revisions.md)
- [x] 2. Integrate Super DAN and execution attempts via [59-2-super-dan-blueprint-runtime](59-2-super-dan-blueprint-runtime.md)
- [x] 3. Project task-native blueprints into the Work GUI via [59-3-work-gui-task-blueprints](59-3-work-gui-task-blueprints.md)
- [x] 4. Validate cross-layer behavior
  - [x] 4-1. Prove direct, debugging, research, design, meeting, and manufacturing topology fixtures
  - [x] 4-2. Prove stale revisions, permission widening, criterion weakening, unbounded loops, and active-task rewrites fail closed
  - [x] 4-3. Prove legacy `task_graph_state` events remain compatible during rollout
  - [x] 4-4. Prove one blueprint can retain multiple distinct execution attempts
- [x] 5. Reconcile architecture, API, roadmap, and user documentation

## Decisions

- Persist two primary concepts: `TaskBlueprint` and `ExecutionAttempt`.
- `TaskBlueprint` contains a protected contract section plus a semantic task graph; contract and graph share one revision lineage but have different mutation authority.
- `ExecutionAttempt` is bound to exactly one blueprint revision and owns workers, models, tools, retries, scheduling, runtime state, and results.
- Saved `dan_graph_v1` workflows remain reusable execution assets; the blueprint may bind a semantic node to one without becoming a compute graph itself.
- Task families are topology priors composed from a small grammar, not hardcoded product modes.
- Dynamic revisions are append-only, base-revision checked transactions. Pending work may change; running work is pinned to an execution attempt; completed work is superseded rather than erased.
- Plan-execute-validate is a reusable topology pattern with no privileged runtime status.
- Meeting and manufacturing are supported semantic task families. Supplier integration, purchasing, and autonomous manufacturing remain outside this horizontal implementation.

## Notes

- Reuse the current Super DAN `task_graph_state`, universal-organism scheduler, Chat V2 task board, graph-mutator transaction patterns, context capsules, and Work Panel projection instead of creating another execution runtime.
- Preserve the current `live.task_graph.updated` event during migration; canonical blueprint payloads are additive.
