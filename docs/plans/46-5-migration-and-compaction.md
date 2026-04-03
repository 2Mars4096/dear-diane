# 46-5: Migration, Deprecation & Compaction

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** completed
**Goal:** Migrate existing workflows toward Worker-first compute surfaces, deprecate redundant compute node types, and compact the codebase — while keeping specialized control/runtime primitives where they remain the clearer execution model — and establish the Worker-first contract surfaces that plan 47 will target.

## The Compaction Target

Before (current):
- 21 node types in `Node` union
- 20 executor registrations in `executor_defaults.py`
- 15+ builder methods in `builder.py`
- 10 composite-contract fields copy-pasted across 7 types

After:
- 1 primary compute node type (`Worker`) + legacy aliases for deserialization
- 1 primary compute executor (`WorkerExecutor`) delegating to internal execution paths
- 1 primary compute builder method (`worker()`) + convenience aliases
- 1 copy of the composite contract
- retained specialized control/runtime primitives only where they still represent real scheduler semantics rather than redundant compute node variants

That's the tightening.

## Tasks

- [x] 1. Conversion utilities in `src/dan/worker/presets.py` (extends 46-3 work)
  - [x] 1-1. `convert_graph(graph: Graph) -> Graph` — convert all nodes in a graph (including sub-graphs) to Workers
  - [x] 1-2. `validate_conversion(original: Graph, converted: Graph)` — structural comparison (ports, edges, sub-graphs preserved)
  - [x] 1-3. Preserve edge connectivity: edge `source_node_id`/`target_node_id`/port names unchanged
- [x] 2. Workflow generation migration
  - [x] 2-1. Update `GENERATE_SPEC_NODE_TYPES` to prefer `"worker"` over old types
  - [x] 2-2. Update `intent_compiler.py` to emit Worker nodes with appropriate role config
  - [x] 2-3. Update `generation_defaults.py` to produce Worker configs
  - [x] 2-4. Update `node_worker.py` (structured generation) to output Workers
  - [x] 2-5. Gate behind `DAN_WORKER_GENERATION` env var (disabled/canary/enabled)
  - [x] 2-6. Old-type generation continues working — this is additive
  - [x] 2-7. Ensure generated Worker nodes populate enough contract metadata (`description`, `persona`, `role`, port descriptions/schemas) for downstream lint-config autogen
- [x] 3. Builder alias refactoring
  - [x] 3-1. `llm()` → internally constructs Worker with `model` + `llm` hints
  - [x] 3-2. `tool()` → internally constructs Worker with `tool_ids`
  - [x] 3-3. `code()` → internally constructs Worker with `code` + `language`
  - [x] 3-4. Other convenience methods follow only where they represent redundant compute wrappers; retained control/runtime primitives do not need fake Worker delegation
    - [x] 3-4-1. `input_node()` now materializes Worker-shaped metadata internally while preserving the legacy `InputNode` public graph shape and aggregate `input` contract
    - [x] 3-4-2. `router()` now materializes Worker-shaped routing config internally while preserving the specialized `RouterNode` public graph shape and `route`/`result` ports
    - [x] 3-4-3. `validator()` and `reflection()` now materialize Worker-shaped metadata internally while preserving their specialized public graph shapes and canonical default ports
    - [x] 3-4-4. `rag()` now materializes Worker-shaped retrieval metadata internally while preserving the legacy `RAGOperator` public graph shape and canonical `query`/`chunks` ports
    - [x] 3-4-5. `human()` and `human_in_the_loop()` now materialize Worker-shaped interaction metadata internally while preserving the specialized human node graph shapes and canonical `input`/`response` ports
    - [x] 3-4-6. `vote()` / `ensemble()` now materialize Worker-shaped voting metadata internally while preserving the specialized `VoteNode` public graph shape and canonical `input`/`winner` ports
    - [x] 3-4-7. `reduce()` now materializes Worker-shaped aggregation metadata internally while preserving the specialized `ReduceNode` public graph shape and canonical `result` port
  - [x] 3-5. Keep all method signatures stable — zero breaking changes
  - [x] 3-6. Only after 46-3 equivalence proof passes
- [x] 4. Executor compaction
  - [x] 4-1. `WorkerExecutor` becomes the primary dispatch for Worker-native compute execution
  - [x] 4-2. Old compute executor classes (`LLMExecutor`, `ToolExecutor`, `CodeExecutor`, etc.) become internal implementation details of `WorkerExecutor`
  - [x] 4-3. `executor_defaults.py` still registers old types for legacy graph compat; compute families may point to `WorkerExecutor` adapters while specialized control/runtime families may remain directly registered
- [x] 5. Model compaction
  - [x] 5-1. Move old node types from `nodes.py`/`control_flow.py` into `models/legacy.py`
  - [x] 5-2. Keep them in the `Node` union (must deserialize forever)
  - [x] 5-3. New graph creation uses `Worker` for compute stages and explicit specialized primitives for retained control/runtime stages
  - [x] 5-4. `node_taxonomy.py` distinguishes canonical compute types (`worker`) from retained control/runtime primitives and legacy compatibility types
- [x] 6. Documentation
  - [x] 6-1. `docs/llm-api-guide.md` — Worker-first examples, role system
  - [x] 6-2. `docs/architecture.md` — update node inventory, explain Worker module structure
  - [x] 6-3. `README.md` — if Worker changes user-facing features
  - [x] 6-4. Builder DSL examples — show `worker()` as primary, old methods as aliases
- [x] 7. Validation gate
  - [x] 7-1. Focused Worker-first validation suite passes across builder, mutation, generation, and taxonomy surfaces
  - [x] 7-2. Paper-writing workflow runs identically with Workers
  - [x] 7-3. Workflow generation produces Worker graphs that pass quality/contract checks
  - [x] 7-4. No performance regression from WorkerExecutor dispatch overhead

## Exit Gate for Plan 47

Plan 47 should start only after this plan has produced a stable Worker-first contract surface:

- `Worker` is in the runtime union and executor registry, and Worker-native graphs run end to end
- Builder/compiler/decompiler preserve Worker contract metadata without lossy fallback
- Generation/authoring surfaces can emit Worker-native graphs with meaningful descriptions, roles/personas, and port schemas/descriptions
- Legacy graphs still deserialize and execute unchanged
- The remaining compaction tail may continue later, but the above gate must be true before the linter plan begins

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
- Plan 47 consumes the Worker-first contract surface produced here. Auto-generated lint config should prefer Worker metadata and only fall back to legacy heuristics when necessary.
- Compaction is not the same thing as erasing every non-Worker primitive. If a node represents real scheduler semantics rather than redundant compute configuration, retaining it is acceptable.

## Notes

- This sub-plan has the longest tail. Task 2 (generation migration) and task 3 (builder aliases) are immediate wins. Tasks 4–5 (executor/model compaction) happen gradually. Task 6 (docs) is continuous.
- The real payoff is not just fewer files — it's conceptual simplicity. One primary compute primitive, one primary compute executor, one primary compute builder method. Control/runtime semantics can still stay honest and explicit instead of being forced into a misleading one-size-fits-all wrapper.
- The first migration primitive is now live in this tree: `convert_graph()` recursively Workerizes convertible legacy nodes while preserving graph topology, sub-graph keys, and shared resources, and `validate_conversion()` reports structural drift without pretending to be a semantic equivalence proof. `tests/test_worker/test_presets.py` covers the bridge and the graph-level conversion seam.
- The next migration primitive is now live too: `DAN_WORKER_GENERATION=disabled|canary|enabled` controls whether lightweight generation surfaces prefer legacy compute nodes or Worker-first compute nodes. The planner prompt, lightweight `GENERATE` compiler, `workflow_spec_from_intent()`, `IntentCompiler`, `DefaultsEnricher`, staged `node_worker` planning, and structured candidate-graph materialization now all understand Worker-native simple compute stages while leaving `gate`/`for_each`/loop primitives specialized.
- Worker-generation remains additive, not destructive. The prompt contract prefers `worker` when the gate is on, but the compiler still accepts legacy `llm_operator` / `tool_operator` / `code_operator` specs, and the surrounding regression bundle explicitly revalidates both paths.
- The Worker-generation validation gate is now explicit too. `tests/test_meta/test_workflow_generation_pipeline.py` carries a direct planner path under `DAN_WORKER_GENERATION=enabled` that produces Worker-native compute nodes, round-trips the graph through `decompile(...)`, and then proves the rebuilt graph passes `validate_workflow_build_contract(...)` with no contract or run-readiness issues.
- The dispatch-overhead gate is now explicit too. `tests/eval/worker_dispatch_benchmark.py` compares legacy compute nodes against `convert_graph(...)` Workerized equivalents for `code_only`, `tool_only`, `llm_only`, a `code -> tool -> llm` chain, a `body_graph` mapping case, and a sub-worker merge/orchestration case using an in-process deterministic engine. The harness now alternates legacy and Worker runs in one interleaved timing loop to reduce phase-order noise, and the strict local gate currently passes at `--max-overhead-ratio 1.25` with the report saved at `tests/eval/results/20260403T095041Z_worker_dispatch_benchmark.json`.
- The first public alias-compaction slice is now live too: `wf.llm(...)`, `wf.tool(...)`, and `wf.code(...)` keep their legacy public graph shapes and signatures, but they now materialize Worker-shaped config internally and then project back through `worker_to_legacy(...)`. This keeps user-facing compatibility stable while forcing simple compute aliases through the same Worker contract surface that generation and direct `wf.worker(...)` authoring now use.
- That simple compute alias bucket now also has an additive Worker-native authoring gate: `workflow(..., canonical_workers=True)` and `DAN_WORKER_BUILDER=canary|enabled` let `wf.llm(...)`, `wf.tool(...)`, and `wf.code(...)` emit canonical `worker` nodes directly while preserving their familiar default ports (`text` / `result`) and prompt-marker auto-wiring behavior. The default remains conservative (`disabled`) so legacy public graph shapes still win unless a caller opts in.
- That Worker-native alias gate now covers a broader compute-like bucket too: `wf.rag(...)`, `wf.input_node(...)`, `wf.reduce(...)`, `wf.reflection(...)`, `wf.human(...)`, `wf.human_in_the_loop(...)`, `wf.vote(...)`, and `wf.ensemble(...)` can also emit canonical `worker` nodes when the gate is on, while preserving their old chaining defaults (`input -> query`, `result`, `chunks`, `principles`, `response`, `winner`) and keeping router/validator/control primitives explicit. The builder regression suite now covers both explicit `canonical_workers=True` and env-gated `DAN_WORKER_BUILDER=enabled` emission for that broader alias surface.
- The next safe alias-compaction slice is live too: `wf.input_node(...)`, `wf.router(...)`, `wf.validator(...)`, and `wf.reflection(...)` now also materialize Worker-shaped metadata internally before projecting back to their specialized legacy graph shapes. This advances Worker-first compaction without pretending those public graph shapes should disappear or that loop/control primitives need fake Worker absorption.
- `wf.rag(...)` is now in that same Worker-first alias bucket too. It materializes retrieval metadata through Worker-shaped config before projecting back to `RAGOperator`, keeping the public graph type stable while preserving the canonical `query` / `chunks` ports for that compute surface.
- `wf.human(...)` and `wf.human_in_the_loop(...)` now follow the same Worker-first internal path as well. Interaction metadata is authored through Worker-shaped config internally and then projected back to the specialized human node graph shapes, which keeps the public forms explicit while sharing the common contract surface.
- `wf.vote(...)` and therefore `wf.ensemble(...)` are now in that same Worker-first internal bucket too. Voting strategy/config metadata is authored through Worker-shaped config internally and then projected back to `VoteNode`, which keeps the public ensemble graph shape stable while consolidating another compute-like alias family onto the Worker contract path.
- `wf.reduce(...)` now follows that same Worker-first alias path too. Aggregation metadata is authored through Worker-shaped config internally and then projected back to `ReduceNode`, and the readability-oriented decompiler can now lower simple reducer Workers back to `wf.reduce(...)` when `use_convenience_aliases=True`.
- The same additive Worker authoring gate now reaches chat/editor mutation add-node too. `src/dan/server/graph_mutator.py` consults `DAN_WORKER_BUILDER` and now Workerizes the full safe compute-like mutation bucket (`llm_operator`, `tool_operator`, `code_operator`, `rag_operator`, `input`, `router`, `validator`, `reflection`, `human`, `human_in_the_loop`, `vote`, `reduce`) while preserving their familiar default ports and specialized metadata mappings. Retained control/runtime primitives such as gate, loops, and orchestration nodes still stay explicit.
- Mutation-time Worker creation no longer creates fake empty subgraphs for plain leaf Workers. `GraphMutator` now only auto-scaffolds a worker `body_graph` when a Worker actually declares one, which keeps Workerized add-node graphs run-ready under the contract validator instead of inventing empty `worker__body` stubs.
- Migration proof is no longer just structural. The dedicated consolidated 46/47 worktree now also carries `tests/test_worker/test_equivalence.py`, which runs Tier 1 legacy `llm/tool/code` nodes and their Workerized equivalents through the real engine and compares outputs plus normalized node event streams, and also checks a small migrated `code -> tool -> llm` chain after `convert_graph()`. Broader workflow parity remains follow-up work.
- Default executor compaction has started for the already-bridged legacy compute families too. `src/dan/executor_defaults.py` now builds one shared `WorkerExecutor` and registers `LegacyWorkerAdapterExecutor` for `llm_operator`, `tool_operator`, `code_operator`, `input`, `router`, `validator`, `reflection`, `rag_operator`, `human`, `human_in_the_loop`, and `vote`, while retained control/runtime primitives and still-unbridged families keep their explicit legacy registrations. This makes Worker the primary default dispatch path for the bridged compute surface without forcing broader runtime erasure.
- `tests/test_executor_defaults.py` now locks that registry boundary explicitly: the legacy compute families share the same `LegacyWorkerAdapterExecutor`/`WorkerExecutor` compaction path, while builtin tool coverage is asserted through the shared Worker-owned `ToolExecutor` rather than through the old pre-compaction expectation that `tool_operator` registered a raw `ToolExecutor`.
- That executor-compaction boundary now covers the remaining default/local/server seams too. `reduce` now routes through the same `LegacyWorkerAdapterExecutor`, and both `src/dan/client/local.py` and `src/dan/server/run_manager.py` now call `register_default_executors(..., tool_registry=...)` instead of hand-registering raw `tool_operator` executors. That leaves the legacy compute executors instantiated only inside `WorkerExecutor` itself and the shared default-registry factory that feeds it.
- The runtime taxonomy now carries the compaction boundary explicitly in code too. `src/dan/models/node_taxonomy.py` exposes `CANONICAL_COMPUTE_NODE_TYPES`, `RETAINED_RUNTIME_NODE_TYPES`, `LEGACY_COMPATIBILITY_NODE_TYPES`, and Worker-first `CANONICAL_AUTHORING_NODE_TYPES`, with `tests/test_runtime_taxonomy_alignment.py` asserting the partition so "new compute uses Worker, real scheduler semantics stay explicit, old wrappers remain compatibility types" is enforced instead of living only in docs.
- The legacy-model move is now real, not just a routing seam. `src/dan/models/legacy.py` now owns the class bodies for the legacy compute/compatibility node families (`LLMOperator`, `ToolOperator`, `CodeOperator`, `RAGOperator`, `ReflectionNode`, `InputNode`, `ReduceNode`, `RouterNode`, `HumanNode`, `HumanInTheLoopNode`, `ValidatorNode`, `VoteNode`, and their support models), while `nodes.py` and `control_flow.py` re-export them for compatibility. The runtime union plus core Worker/builder/loader/validation/executor/runtime paths now import those classes through `legacy.py`, so the compaction boundary is enforced in code rather than only in docs.
- Markdown authoring now shares the same additive Worker authoring gate as the Python builder. `src/dan/loader/compiler.py` honors `DAN_WORKER_BUILDER=canary|enabled` for compute-like agent specs, Workerizes compatible loaded nodes through `legacy_to_worker(...)`, preserves loader-specific chaining defaults via stamped default-port metadata, and still leaves gates / loops / team primitives explicit. `tests/test_loader/test_compiler.py` now covers both Workerized simple markdown workflows and retained specialized primitives under that gate.
- Internal helper-graph synthesis is now following that same boundary too. `src/dan/server/scoped_run.py` builds the scoped entry helper as a typed `Worker` with `input_variables` metadata instead of minting a legacy `InputNode`, `src/dan/server/run_manager.py` now schedules automatic post-run reflection through a `role="reflection"` Worker with explicit runtime payload ports instead of a synthetic legacy `ReflectionNode`, and `src/dan/validation/boundaries.py` now auto-inserts validator-shaped Workers at composite boundaries instead of synthesizing legacy `ValidatorNode`s. The surrounding server/runtime regressions lock that in without forcing explicit boundary/control primitives to disappear as public graph shapes.
- After that boundary-helper pass, the remaining direct legacy compute-node instantiation sites are now down to the honest compatibility surfaces: the public builder/compiler and markdown loader/compiler paths that still materialize legacy graph shapes when compatibility mode is requested, plus `WorkerExecutor`'s internal cached legacy templates that reuse existing executor internals. There are no other runtime helper-graph seams still minting legacy compute nodes directly.
- The final 46-5 validation gate is now explicit and rerunnable. The focused Worker-first suite `DAN_WORKER_BUILDER=enabled DAN_WORKER_GENERATION=enabled PYTHONPATH=src pytest -q tests/test_worker/test_worker_first_validation_gate.py tests/test_graph_mutator_taxonomy.py tests/test_runtime_taxonomy_alignment.py tests/test_meta/test_intent_compiler_build.py::TestBuildGraphSingleStage::test_build_graph_prefers_workers_for_simple_compute_when_enabled tests/test_meta/test_workflow_generation_pipeline.py::test_direct_worker_generation_pipeline_passes_contract_validation` now passes cleanly (`26 passed`) and now includes a scoped Worker composition case with `input_mappings`, `output_mappings`, `parallelism`, `spawn_policy`, and first-class `validation_rules`. That gate covers builder, scoped Worker composition, GraphMutator, direct generation, taxonomy partitioning, and contract/run-readiness validation under the Worker-first env gates.
