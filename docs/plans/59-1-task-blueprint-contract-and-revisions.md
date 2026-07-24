# 59-1: Task Blueprint Contract and Revisions

**Parent:** [59-adaptive-task-blueprints](59-adaptive-task-blueprints.md)
**Status:** completed
**Goal:** Define a typed, replayable, revision-safe semantic blueprint that combines the task contract and task graph without allowing planners to weaken goals, criteria, or permissions.

## Tasks

- [x] 1. Add typed models for the protected contract, semantic nodes/edges, acceptance criteria, loop policies, artifacts/evidence references, and execution bindings
- [x] 2. Add task-family topology compilation for direct, debugging, research, design, meeting, and manufacturing requests
- [x] 3. Add immutable revision and event models plus deterministic replay/reduction
- [x] 4. Add atomic patch operations for add, edit, supersede, split, merge, dependency revision, criterion/evidence attachment, branch changes, and reopening
- [x] 5. Enforce invariants for stable IDs, dangling edges, bounded loops, permission monotonicity, criterion preservation, and active/completed task immutability
- [x] 6. Add focused unit and replay tests

## Decisions

- Ready/active/completed lists are derived projections, not independently authored truth.
- Removal creates a tombstone/supersession record.
- Goal, non-goal, permission expansion, meaningful budget expansion, and acceptance weakening require explicit contract amendments rather than ordinary model patches.
- Artifact payloads remain in the artifact/evidence stores; blueprint nodes keep immutable references.

## Notes

- The initial implementation should be provider- and surface-neutral.
