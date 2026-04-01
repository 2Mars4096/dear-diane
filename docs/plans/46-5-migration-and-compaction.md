# 46-5: Migration, Deprecation & Compaction

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** not-started
**Goal:** Migrate existing workflows to Workers, deprecate old node types, and compact the codebase — delivering the clean, tight architecture that the Worker primitive makes possible.

## The Compaction Target

Before (current):
- 21 node types in `Node` union
- 20 executor registrations in `executor_defaults.py`
- 15+ builder methods in `builder.py`
- 10 composite-contract fields copy-pasted across 7 types

After:
- 1 primary node type (`Worker`) + legacy aliases for deserialization
- 1 primary executor (`WorkerExecutor`) delegating to internal execution paths
- 1 primary builder method (`worker()`) + convenience aliases
- 1 copy of the composite contract

That's the tightening.

## Tasks

- [ ] 1. Conversion utilities in `src/dan/worker/presets.py` (extends 46-3 work)
  - [ ] 1-1. `convert_graph(graph: Graph) -> Graph` — convert all nodes in a graph (including sub-graphs) to Workers
  - [ ] 1-2. `validate_conversion(original: Graph, converted: Graph)` — structural comparison (ports, edges, sub-graphs preserved)
  - [ ] 1-3. Preserve edge connectivity: edge `source_node_id`/`target_node_id`/port names unchanged
- [ ] 2. Workflow generation migration
  - [ ] 2-1. Update `GENERATE_SPEC_NODE_TYPES` to prefer `"worker"` over old types
  - [ ] 2-2. Update `intent_compiler.py` to emit Worker nodes with appropriate role config
  - [ ] 2-3. Update `generation_defaults.py` to produce Worker configs
  - [ ] 2-4. Update `node_worker.py` (structured generation) to output Workers
  - [ ] 2-5. Gate behind `DAN_WORKER_GENERATION` env var (disabled/canary/enabled)
  - [ ] 2-6. Old-type generation continues working — this is additive
- [ ] 3. Builder alias refactoring
  - [ ] 3-1. `llm()` → internally constructs Worker with `model` + `llm` hints
  - [ ] 3-2. `tool()` → internally constructs Worker with `tool_ids`
  - [ ] 3-3. `code()` → internally constructs Worker with `code` + `language`
  - [ ] 3-4. All other methods → similar internal delegation
  - [ ] 3-5. Keep all method signatures stable — zero breaking changes
  - [ ] 3-6. Only after 46-3 equivalence proof passes
- [ ] 4. Executor compaction
  - [ ] 4-1. `WorkerExecutor` becomes the primary dispatch for all execution
  - [ ] 4-2. Old executor classes (`LLMExecutor`, `ToolExecutor`, `CodeExecutor`, etc.) become internal implementation details of `WorkerExecutor`
  - [ ] 4-3. `executor_defaults.py` still registers old types for legacy graph compat, but all point to `WorkerExecutor` adapters
- [ ] 5. Model compaction
  - [ ] 5-1. Move old node types from `nodes.py`/`control_flow.py` into `models/legacy.py`
  - [ ] 5-2. Keep them in the `Node` union (must deserialize forever)
  - [ ] 5-3. All new graph creation uses `Worker`
  - [ ] 5-4. `node_taxonomy.py` distinguishes canonical types (`worker`) from legacy
- [ ] 6. Documentation
  - [ ] 6-1. `docs/llm-api-guide.md` — Worker-first examples, role system
  - [ ] 6-2. `docs/architecture.md` — update node inventory, explain Worker module structure
  - [ ] 6-3. `README.md` — if Worker changes user-facing features
  - [ ] 6-4. Builder DSL examples — show `worker()` as primary, old methods as aliases
- [ ] 7. Validation gate
  - [ ] 7-1. Full test suite passes with Worker-only graphs
  - [ ] 7-2. Paper-writing workflow runs identically with Workers
  - [ ] 7-3. Workflow generation produces Worker graphs that pass quality/contract checks
  - [ ] 7-4. No performance regression from WorkerExecutor dispatch overhead

## Likely Files

**Modified:**
- `src/dan/executor_defaults.py` — compaction
- `src/dan/models/nodes.py` → `src/dan/models/legacy.py`
- `src/dan/models/control_flow.py` → partial move to `legacy.py`
- `src/dan/builder/builder.py` — alias refactoring
- `src/dan/models/node_taxonomy.py` — canonical vs. legacy sets
- `src/dan/meta/intent_compiler.py` — Worker generation
- `src/dan/meta/generation_defaults.py` — Worker generation
- `src/dan/worker/presets.py` — graph conversion utilities

**Test:**
- `tests/test_worker/test_migration.py` (new)

## Decisions

- Old node types are **never removed** from the `Node` union. Saved graph JSON must always deserialize. They move to `legacy.py` but stay importable and functional.
- Builder convenience methods (`llm()`, `tool()`, `code()`) are **never removed**. They become thin wrappers over `worker()`. Existing user code keeps working unchanged.
- Workflow generation migration is the highest-impact change. Once the NL→workflow pipeline generates Workers instead of old types, all new workflows are Worker-native. Old workflows can be migrated at leisure.
- Graph-store migration CLI (`dan migrate-to-workers`) is optional. The `convert_graph()` function exists for programmatic use, but most users won't need a CLI tool.

## Notes

- This sub-plan has the longest tail. Task 2 (generation migration) and task 3 (builder aliases) are immediate wins. Tasks 4–5 (executor/model compaction) happen gradually. Task 6 (docs) is continuous.
- The real payoff is not just fewer files — it's conceptual simplicity. One primitive, one executor, one builder method. Everything else is a named configuration. That's the architecture that scales.
