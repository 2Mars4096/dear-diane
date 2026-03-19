# 37-7: Validated Dynamic Topology & Child Workflow Composition

**Parent:** [37-engine-runtime-parallelism](37-engine-runtime-parallelism.md)
**Status:** completed
**Goal:** Add an engine-owned, constrained runtime expansion model so workflows can spawn validated child branches/workflows with typed input/output handoff, shared budgets, lineage, and safe checkpoint/resume behavior, without permitting arbitrary in-run graph mutation.

## Context

- Tail workflows sometimes need runtime expansion after intermediate results arrive, e.g. "found 4 clusters, run 4 analyses" or "run data workflow, then hand outputs into a report workflow".
- The repo already has orchestrator/team execution, subgraphs, block/workflow imports, async loop design, and meta-controller coordination, but not a first-class engine contract for validated runtime child workflow composition.
- The backlog items for dynamic topology and cross-workflow coordination belong together, but they should land only after the runtime durability and bounded-repair semantics in 37-5 and 37-6 are explicit.
- This slice should not allow arbitrary graph rewrites at runtime. Expansion must come from predeclared subgraphs, registered blocks, or persisted workflow references with typed boundaries.
- Engine-owned semantics matter most here: concierge/runtime consumers can request child workflow execution, but lineage, budgets, limits, and resume behavior should stay defined by the engine contract.
- The 2026-03-17 module audit also warned about giant cross-cutting runtime modules. This slice should prefer dedicated composition primitives/helpers over pushing more orchestration logic into already-large modules such as `scheduler.py`, `run_manager.py`, or `concierge/runtime.py`.

## Tasks

### 1. Define the constrained runtime expansion contract
- [x] 1-1. Introduce explicit runtime models for validated expansion in `models/control_flow.py` / `models/graph.py` (e.g. `DynamicExpansionSpec`, `ChildWorkflowCall`, `SpawnPolicy`) that reference predeclared `sub_graphs`, registered blocks, or persisted workflow IDs instead of raw node/edge patches.
- [x] 1-2. Limit v1 expansion modes to engine-safe patterns: spawn a child subgraph, clone a validated template branch, or invoke a child workflow operator with declared schemas.
- [x] 1-3. Explicitly exclude arbitrary free-form graph mutation and mid-run rewiring of unrelated nodes.
- [x] 1-4. Reuse the existing `BoundaryContract` (`accepts`, `returns`, `signals`, `reads_global`, `writes_global`) rather than inventing a second handoff system.

### 2. Add an engine-owned child workflow / dynamic branch execution primitive
- [x] 2-1. Extend the runtime contract in the engine so executors can request a validated child execution through a first-class API rather than overloading ad hoc subgraph execution paths.
- [x] 2-2. Implement dynamic topology as an execution overlay tracked by the scheduler, not by mutating `Graph.nodes` / `Graph.edges` in place during a run.
- [x] 2-3. Ensure spawned children inherit the ready queue, shared semaphores, and cycle/checkpoint guards from Phase 37 so dynamic work behaves like native engine work.
- [x] 2-4. Add a first-class child workflow operator path for typed workflow-to-workflow composition, not just prompt-only orchestration.

### 3. Enforce typed handoff and cross-workflow coordination
- [x] 3-1. Define a typed child-result envelope (`status`, `outputs`, `signals`, `artifacts`, `run_id`, lineage metadata) so parents consume structured results instead of prompt-only summaries.
- [x] 3-2. Validate parent-to-child inputs and child-to-parent outputs against `BoundaryContract` and the existing boundary-validation path before execution and on return.
- [x] 3-3. Add a clean handoff path into meta-controller / system-plan coordination so upstream outputs become typed downstream workflow inputs.
- [x] 3-4. Keep global/shared-context access bounded through explicit contract fields instead of ad hoc memory coupling.
- [x] 3-5. Ensure boundary mismatch failures surface actionable validation guidance to runtime/status consumers instead of raw structural diagnostics alone.

### 4. Add lineage, limits, and budget accounting
- [x] 4-1. Track parent/child lineage in runtime state and events (`parent_run_id`, `parent_node_id`, `child_run_id`, template/workflow key, spawn index, layer path extension).
- [x] 4-2. Add hard safety limits for dynamic execution: max child depth, max spawns per node, max total dynamic children per run, optional per-child timeout, and budget-share metadata.
- [x] 4-3. Roll child token/cost/concurrency usage into the same run-level accounting used by the engine today so spawned work cannot silently bypass budgets.
- [x] 4-4. Ensure lineage and limits are included in checkpoints and surfaced in run metadata/events for debugging and UX consumers.

### 5. Define checkpoint/resume semantics for spawned children
- [x] 5-1. Extend checkpoint payloads to record dynamic child invocation decisions, completed child results, and in-flight child records needed to rebuild the execution overlay on resume.
- [x] 5-2. Keep the v1 resume boundary simple and safe: a child workflow is resumable from its invocation boundary, not from arbitrary mid-child internal state unless that path is already covered by engine checkpoint semantics.
- [x] 5-3. Guarantee idempotent replay rules so resumed parents do not duplicate already committed child side effects or memory writes.
- [x] 5-4. Make explicit when child workflow replay restarts from the invocation boundary rather than partial nested restore.

### 6. Integrate with orchestrator and meta-controller consumers
- [x] 6-1. Update `OrchestratorNode` / async-loop execution paths to dispatch via the new engine child-workflow primitive when the work item is a validated child workflow rather than an in-graph team-only subgraph.
- [x] 6-2. Update `MetaController` system execution so `depends_on` / upstream-output coordination can graduate from prompt-only handoffs to typed engine-managed child/run handoff where appropriate.
- [x] 6-3. Keep `server/concierge/runtime.py` as a caller/integrator that consumes typed events and handoffs, not the source of truth for spawn/resume semantics.
- [x] 6-4. Preserve feature-flagged fallbacks so existing orchestrator/meta paths keep working while the engine-owned contract is proved out.

### 7. Backfill tests, feature flagging, and docs
- [x] 7-1. Add engine tests for schema validation, spawn-limit enforcement, lineage emission, budget roll-up, and deterministic resume with dynamic children.
- [x] 7-2. Add integration coverage for async orchestrator redispatch, meta-controller workflow dependencies, and repeated child invocation of the same template/workflow.
- [x] 7-3. Ship behind a feature flag (`DAN_DYNAMIC_TOPOLOGY=1` or equivalent).
- [x] 7-4. Document clear v1 exclusions: no arbitrary graph edits, no partial nested restore, and no editor/frontend-first authoring surface in this slice.

## Primary Files

- `src/dan/engine/scheduler.py`
- `src/dan/engine/executor.py`
- `src/dan/engine/runtime_composition.py`
- `src/dan/engine/checkpoint.py`
- `src/dan/models/graph.py`
- `src/dan/models/control_flow.py`
- `src/dan/models/context.py`
- `src/dan/validation/boundaries.py`
- `src/dan/meta/controller.py`
- `src/dan/meta/architect.py`
- `src/dan/server/concierge/runtime.py`
- `src/dan/server/run_manager.py`
- `tests/test_engine/`
- `tests/test_meta/`

## Dependencies / Sequencing

- This is a tail follow-on to 37-1 through 37-6, not part of the already-landed eager-dispatch core.
- Start with the runtime contract and validation models first; the scheduler overlay should not be designed before the allowed expansion shapes are explicit.
- Reuse the existing typed-boundary semantics and boundary-validator path instead of inventing a parallel handoff contract.
- Wire engine checkpoint/resume semantics before integrating `MetaController` or `OrchestratorNode`, otherwise higher layers will depend on unstable child-run behavior.
- Treat meta-controller and concierge integration as consumers of the engine primitive; avoid a concierge-only workflow-composition path.

## Success Criteria

- [x] Dynamic expansion is only possible through predeclared, validated templates/subgraphs/workflow references; arbitrary runtime graph mutation is impossible through the public contract.
- [x] Spawned child work respects the same global node/LLM concurrency and budget accounting as the parent run.
- [x] Parent-to-child and child-to-parent handoff is schema-validated and returns a typed result envelope with lineage metadata.
- [x] Checkpoint/resume rebuilds dynamic child execution deterministically and does not duplicate committed side effects on replay.
- [x] `OrchestratorNode` and `MetaController` can coordinate validated child workflows through engine-owned APIs instead of prompt-only handoff strings alone.
- [x] The feature ships behind a guarded flag with engine/meta regression coverage for spawn limits, lineage, resume, and budget roll-up.

## Decisions

- Dynamic topology should mean a runtime execution overlay, not mutation of the persisted `Graph` object during execution.
- V1 should allow only validated expansion from `Graph.sub_graphs`, registered blocks, or persisted workflow IDs with declared interfaces.
- Typed boundary contracts remain the central engine contract for accepts/returns/signals; concierge summaries are secondary derived views.
- Child workflows restart from invocation boundaries on resume unless nested checkpoint semantics are explicitly implemented later.
- Prefer an engine-owned composition helper/module over accreting more special cases into `concierge/runtime.py` or `run_manager.py`.

## Notes

- Shared-context access should stay bounded through explicit read/write declarations; prefer explicit typed returns/signals over ad hoc global memory coupling.
- Frontend/editor authoring for dynamic topology stays out of scope here; this plan hardens engine semantics first.
- This slice depends on 37-5 and 37-6 making long-running durability and bounded runtime-repair behavior predictable first.
- Review-driven maintainability constraint: keep the child-workflow execution contract modular enough that future UI/meta integrations consume it rather than duplicating it.
- Review-driven UX requirement: boundary/handoff validation failures should be phrased as actionable mismatch guidance, not only as low-level structural errors.
- V1 exclusions:
  no arbitrary graph edits or unrelated node rewiring during execution.
- V1 exclusions:
  no partial nested restore below the child invocation boundary.
- V1 exclusions:
  no editor/frontend-first authoring surface in this slice; the engine contract lands first.
- Current supported runtime expansion modes:
  `sub_graph`, `template_branch`, and `workflow_ref`, all behind the engine dynamic-topology guard.

## Estimate

~3-4 days
