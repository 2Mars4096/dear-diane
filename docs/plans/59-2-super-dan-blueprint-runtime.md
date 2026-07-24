# 59-2: Super DAN Blueprint Runtime

**Parent:** [59-adaptive-task-blueprints](59-adaptive-task-blueprints.md)
**Status:** completed
**Goal:** Make Super DAN author, execute, and safely revise canonical Task Blueprints while preserving separate immutable Execution Attempts and existing live-task-graph compatibility.

## Tasks

- [x] 1. Convert request understanding and planner task graphs into canonical Task Blueprints
- [x] 2. Compile the ready semantic frontier into `OrganismPlan` / `WorkerBrief` execution work
- [x] 3. Record an `ExecutionAttempt` bound to the exact blueprint revision, policy snapshot, and run identifiers
- [x] 4. Emit canonical blueprint snapshots/revision events alongside legacy `live.task_graph.updated` events
- [x] 5. Admit dynamic revision proposals only at safe boundaries and preserve running/completed work semantics
- [x] 6. Add Super DAN and Chat V2 compatibility tests

## Decisions

- Super DAN may revise topology and strengthen criteria, but may not silently amend the protected contract.
- Model output proposes graph changes; deterministic policy owns admission.
- Runtime retries, tool swaps, and model changes produce new attempt data rather than semantic revisions unless the intended work changes.

## Notes

- Existing `super_dan_task_graph_v1` payloads remain accepted during migration.
