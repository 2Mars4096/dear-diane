# 6: Structured Universal Cell Prototype

**Status:** completed
**Goal:** Test a smaller Universal Cell contract with three behavioral inputs (`model`, `context`, and `constraints`) plus explicit horizontal inheritance and vertical subsample/fan-out/collapse relationships.

## Tasks

- [x] Define the three-input cell contract and relationship invariants.
- [x] Implement the isolated prototype without changing the Universal Organism.
- [x] Prove full horizontal inheritance from previous to focal to next.
- [x] Prove parent-to-child context subsampling and constraint narrowing.
- [x] Prove parallel child execution collapses completely before the focal cell can advance.
- [x] Run focused and existing Universal Cell regression tests.
- [x] Document the prototype and results.

## Decisions

- The three behavioral inputs are `model`, flexible `context`, and typed `constraints`.
- Task, scope, role, and other task-specific values live inside `context` rather than separate top-level cell fields.
- Horizontal relationships inherit the complete committed context of the previous cell, with the focal cell's local context taking precedence on key collisions.
- Vertical relationships are deliberately lossy: a child receives only explicitly selected context keys plus child-local context.
- Child constraints may only preserve or narrow the parent's authority; they may never widen tools, budgets, or child fan-out.
- Child reports collapse into one namespaced focal-cell context entry. Arbitrary child context is not silently merged into the focal namespace.
- A focal cell with declared children cannot hand off to its next cell until every child has reported; failed children block advancement by default.
- The prototype reuses `WorkerCoreExecutor` through a small adapter and does not introduce a scheduler or organism.

## Notes

- The prototype is additive in `src/dan/worker/structured_cell.py` so it can be evaluated against the current `WorkerBrief` design before any replacement decision.
- Focused prototype/current-cell/import-boundary coverage passes 18 tests; the complete retained worker suite passes 120 tests.
- The prototype remains below organism scope: it supplies only link declaration, inheritance, child preparation, concurrent fan-out, collapse, and next-cell gating primitives.
