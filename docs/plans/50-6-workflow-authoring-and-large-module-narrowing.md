# 50-6: Workflow Authoring and Large-Module Narrowing

**Parent:** [50-structural-consolidation-and-module-reduction](50-structural-consolidation-and-module-reduction.md)
**Status:** completed
**Goal:** Split the workflow-authoring sinks along real phase boundaries and use the same pass to narrow one additional oversized backend module that the inventory confirms is a true structural hotspot.

## Dependencies

- **50-1** sets the hotspot map and split criteria.
- **50-3** should land first so workflow-generation/chat ownership moves in the same direction and the facade is already retired.
- **50-5** should land first so concierge runtime boundaries are settled before adapter/scheduler seams that touch workflow-generation entrypaths are moved.

## Tasks

- [x] 1. Split workflow generation by phase
  - [x] 1-1. Break `WorkflowGenerationRuntime.generate()` into explicit phases: intent extraction, candidate construction, candidate acceptance, diagnosis/repair, and automatic recovery. Each phase becomes a separate file in a new `src/dan/server/agent_runtime/workflow_generation/` package (e.g., `intent.py`, `candidate.py`, `acceptance.py`, `diagnosis.py`, `recovery.py`).
  - [x] 1-2. Keep `generate()` as a thin coordinator over those phases in `__init__.py`.
- [x] 2. Narrow `GraphMutator`
  - [x] 2-1. Keep normalized operation application and transaction semantics in the mutator core.
  - [x] 2-2. Move macro/pattern authoring helpers and Worker/legacy migration policy out to more natural owners.
  - [x] 2-3. Keep compatibility auto-repair only where it is genuinely part of mutation application.
- [x] 3. Split one additional oversized backend sink
  - [x] 3-1. Use the 50-1 inventory to choose the next-best split candidate among `src/dan/engine/scheduler.py` (4154 lines), `src/dan/builder/builder.py` (3053 lines), and `src/dan/server/routers/adapters.py` (2459 lines). Selection criteria: (a) number of distinct responsibilities mixed in one file, (b) frequency of merge conflicts or cross-cutting edits, (c) number of distinct callers/entrypoints. Note: `builder/builder.py` is the 4th-largest Python file and mixes DSL construction, validation, serialization, and Worker-projection; it is also a Primary File in 50-7, so splitting here may reduce 50-7 scope.
  - [x] 3-2. Split that file by a real responsibility boundary rather than arbitrary line count. **If `engine/scheduler.py` is chosen:** this is the second-largest file in the repo and the split will likely need its own detailed sub-task tree (e.g., separating bootstrap/init, execution/dispatch, lint/retry, telemetry/mutation hooks). Acknowledge this scale and expand task 3 accordingly at execution time.
- [x] 4. Prune redundant helpers in touched areas
  - [x] 4-1. Delete dead phase glue, repeated wrapper functions, or migrated compatibility code as each split lands.
- [x] 5. Regressions and performance
  - [x] 5-1. Revalidate workflow generation, mutation application/repair, and the touched backend sink's critical path.
  - [x] 5-2. Watch for generation-latency or adapter/runtime regression caused by the split.

## Primary Files

- `src/dan/server/agent_runtime/workflow_generation.py` → new `src/dan/server/agent_runtime/workflow_generation/` package
- `src/dan/server/agent_runtime/workflow_generation/intent.py`
- `src/dan/server/agent_runtime/workflow_generation/structured.py`
- `src/dan/server/agent_runtime/workflow_generation/recovery.py`
- `src/dan/server/graph_mutator.py`
- `src/dan/server/graph_mutator_helpers.py`
- `src/dan/server/graph_mutator_patterns.py`
- one of:
  - `src/dan/engine/scheduler.py`
  - `src/dan/builder/builder.py`
  - `src/dan/server/routers/adapters.py`

## Success Criteria

- `WorkflowGenerationRuntime.generate()` is a thin public coordinator over explicit internal phases in separate files rather than a moved monolith
- `GraphMutator` focuses on applying normalized operations, not macro authoring and migration policy
- the GraphMutator helper buckets now live in `graph_mutator_helpers.py` and `graph_mutator_patterns.py`, leaving the core module focused on transactional application
- at least one additional oversized backend file is narrowed by a real split
- touched areas lose code as well as gain structure

## Decisions

- The third split target should be chosen by structural payoff (number of mixed responsibilities, merge-conflict frequency, caller count), not purely by line count.
- Do not split `engine/scheduler.py` or `routers/adapters.py` unless the resulting boundary is clearly stronger than the current one.
- Workflow generation phases become separate files in a package, not just internal functions in one file, so each phase can be reasoned about and tested independently.

## Notes

- This plan is where "split a couple of too long scripts" becomes real backend work rather than just a wishlist.
- This plan must follow 50-3 and 50-5 (not run in parallel with them) because workflow-generation and graph-mutator splits depend on chat/capability ownership and concierge runtime boundaries being settled first.
- 2026-04-07: the first 50-6 slice landed by extracting GraphMutator helper buckets into `graph_mutator_helpers.py` and `graph_mutator_patterns.py`. The mutator core keeps transactional apply semantics; the remaining 50-6 work is the workflow-generation package split and the secondary backend sink.
- 2026-04-07: the workflow-generation split landed as a package under `src/dan/server/agent_runtime/workflow_generation/`. The public `__init__.py` is now a thin wrapper, while the implementation uses explicit `intent.py`, `structured.py`, and `recovery.py` phase helpers and preserves the monkeypatchable `request_builder_code` / `accept_candidate_graph` surface for the structured-generation tests.
- 2026-04-07: the builder hotspot slice landed by extracting private alias projection helpers into `src/dan/builder/_aliases.py` and Worker/composite/team scope helpers into `src/dan/builder/_scopes.py`. `WorkflowBuilder` kept the same public DSL surface, and the helper extraction preserved monkeypatchable `worker_to_legacy` behavior for the legacy-alias tests.
- 2026-04-07: the GraphMutator compatibility-repair slice narrowed a bit further. `_op_add_edge(...)` now delegates source-port compatibility repair and alias normalization to `_resolve_source_output_port(...)` in `graph_mutator_helpers.py`, which keeps the mutator core closer to transactional apply semantics while preserving the existing diagnostics under the focused mutator baskets.
- 2026-04-07: the last GraphMutator tail landed in the helper bucket. `_op_add_edge(...)` now also delegates target-port auto-create/strict validation to `graph_mutator_helpers.py`, and the apply path owns the stale `response` / `output` source-port compatibility aliases that show up in workflow-authoring previews. Focused mutator baskets plus `tests/test_meta/test_structured_generation_runtime.py` revalidated green after the narrowing.
