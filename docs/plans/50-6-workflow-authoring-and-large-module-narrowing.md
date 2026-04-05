# 50-6: Workflow Authoring and Large-Module Narrowing

**Parent:** [50-structural-consolidation-and-module-reduction](50-structural-consolidation-and-module-reduction.md)
**Status:** not-started
**Goal:** Split the workflow-authoring sinks along real phase boundaries and use the same pass to narrow one additional oversized backend module that the inventory confirms is a true structural hotspot.

## Dependencies

- **50-1** sets the hotspot map and split criteria.
- **50-3** should land first so workflow-generation/chat ownership moves in the same direction and the facade is already retired.
- **50-5** should land first so concierge runtime boundaries are settled before adapter/scheduler seams that touch workflow-generation entrypaths are moved.

## Tasks

- [ ] 1. Split workflow generation by phase
  - [ ] 1-1. Break `WorkflowGenerationRuntime.generate()` into explicit phases: intent extraction, candidate construction, candidate acceptance, diagnosis/repair, and automatic recovery. Each phase becomes a separate file in a new `src/dan/server/agent_runtime/workflow_generation/` package (e.g., `intent.py`, `candidate.py`, `acceptance.py`, `diagnosis.py`, `recovery.py`).
  - [ ] 1-2. Keep `generate()` as a thin coordinator over those phases in `__init__.py`.
- [ ] 2. Narrow `GraphMutator`
  - [ ] 2-1. Keep normalized operation application and transaction semantics in the mutator core.
  - [ ] 2-2. Move macro/pattern authoring helpers and Worker/legacy migration policy out to more natural owners.
  - [ ] 2-3. Keep compatibility auto-repair only where it is genuinely part of mutation application.
- [ ] 3. Split one additional oversized backend sink
  - [ ] 3-1. Use the 50-1 inventory to choose the next-best split candidate among `src/dan/engine/scheduler.py` (4154 lines), `src/dan/builder/builder.py` (3053 lines), and `src/dan/server/routers/adapters.py` (2459 lines). Selection criteria: (a) number of distinct responsibilities mixed in one file, (b) frequency of merge conflicts or cross-cutting edits, (c) number of distinct callers/entrypoints. Note: `builder/builder.py` is the 4th-largest Python file and mixes DSL construction, validation, serialization, and Worker-projection; it is also a Primary File in 50-7, so splitting here may reduce 50-7 scope.
  - [ ] 3-2. Split that file by a real responsibility boundary rather than arbitrary line count. **If `engine/scheduler.py` is chosen:** this is the second-largest file in the repo and the split will likely need its own detailed sub-task tree (e.g., separating bootstrap/init, execution/dispatch, lint/retry, telemetry/mutation hooks). Acknowledge this scale and expand task 3 accordingly at execution time.
- [ ] 4. Prune redundant helpers in touched areas
  - [ ] 4-1. Delete dead phase glue, repeated wrapper functions, or migrated compatibility code as each split lands.
- [ ] 5. Regressions and performance
  - [ ] 5-1. Revalidate workflow generation, mutation application/repair, and the touched backend sink's critical path.
  - [ ] 5-2. Watch for generation-latency or adapter/runtime regression caused by the split.

## Primary Files

- `src/dan/server/agent_runtime/workflow_generation.py` → new `src/dan/server/agent_runtime/workflow_generation/` package
- `src/dan/server/graph_mutator.py`
- one of:
  - `src/dan/engine/scheduler.py`
  - `src/dan/builder/builder.py`
  - `src/dan/server/routers/adapters.py`

## Success Criteria

- `WorkflowGenerationRuntime.generate()` is a coordinator over explicit internal phases in separate files rather than a moved monolith
- `GraphMutator` focuses on applying normalized operations, not macro authoring and migration policy
- at least one additional oversized backend file is narrowed by a real split
- touched areas lose code as well as gain structure

## Decisions

- The third split target should be chosen by structural payoff (number of mixed responsibilities, merge-conflict frequency, caller count), not purely by line count.
- Do not split `engine/scheduler.py` or `routers/adapters.py` unless the resulting boundary is clearly stronger than the current one.
- Workflow generation phases become separate files in a package, not just internal functions in one file, so each phase can be reasoned about and tested independently.

## Notes

- This plan is where "split a couple of too long scripts" becomes real backend work rather than just a wishlist.
- This plan must follow 50-3 and 50-5 (not run in parallel with them) because workflow-generation and graph-mutator splits depend on chat/capability ownership and concierge runtime boundaries being settled first.
