# 50-7: Worker Bridge Retirement and AppState Cleanup

**Parent:** [50-structural-consolidation-and-module-reduction](50-structural-consolidation-and-module-reduction.md)
**Status:** not-started
**Goal:** Remove compatibility delegation from the default runtime-core path where native Worker execution is ready, and finish the migration from globals-backed service access to typed `AppState`.

## Dependencies

- **50-1** should identify the remaining bridge/default-owner hotspots and the compute-family audit (task 1-4 of 50-1).
- Coordinate with **50-6** because `GraphMutator` and workflow-authoring seams currently participate in Worker/legacy projection.

## Tasks

- [ ] 1. Classify bridge debt
  - [ ] 1-1. Reference the 50-1 inventory (task 1-4) for which compute families still depend on `LegacyWorkerAdapterExecutor` in the default path — do not repeat the audit.
  - [ ] 1-2. Separate acceptable edge compatibility from runtime-core containment failures.
- [ ] 2. Retire default Worker/legacy bridge paths for ready families
  - [ ] 2-1. Move supported compute families to direct Worker execution.
  - [ ] 2-2. Remove default registry routing through `legacy_worker_adapter` for those families.
- [ ] 3. Delete runtime-core projection glue as families move
  - [ ] 3-1. Remove Worker→legacy or legacy→Worker projections from builder/runtime/mutation paths once no longer needed in the default flow.
- [ ] 4. Finish `AppState` service migration
  - [ ] 4-1. Replace globals-backed service access with typed accessors off `request.app.state.dan` / `AppState`.
  - [ ] 4-2. Before deleting `_mirror_state_to_globals()`, run a codebase-wide audit (`grep -rn` for each mirrored global name) to confirm no runtime code path, CLI entry point, or test fixture still reads from the globals surface.
  - [ ] 4-3. Delete `_mirror_state_to_globals()` and the globals-backed dependency surface once the audit confirms all callers are migrated.
- [ ] 5. Regressions
  - [ ] 5-1. Revalidate Worker execution parity, executor defaults, builder/graph mutation compatibility, startup, and router dependency injection.
  - [ ] 5-2. Audit and update test expectations that assume the legacy bridge path is the default — bridge retirement will change default executor resolution and may break tests that assert legacy behavior.

## Primary Files

- `src/dan/worker/executor.py`
- `src/dan/executor_defaults.py`
- `src/dan/builder/builder.py`
- `src/dan/server/graph_mutator.py`
- `src/dan/server/startup/__init__.py`
- `src/dan/server/routers/dependencies.py`
- `src/dan/server/app.py`

## Success Criteria

- default runtime-core execution uses native Worker paths for the compute families that are ready
- runtime-core bridge debt is more contained and visibly reduced
- router/service access no longer depends on globals mirrored out of `app.py`
- compatibility layers that remain are honest edge adapters rather than hidden core defaults
- `_mirror_state_to_globals()` deletion is backed by a verified codebase audit, not just "it compiles"
- the remaining Worker core boundary is clean enough that 46-7 can extract a reusable bundle from adapters instead of exporting DAN-specific runtime coupling

## Decisions

- Bridge retirement is incremental; do not attempt to flip every node family at once.
- `AppState` cleanup is migration completion, not a new DI framework.
- The compute-family audit comes from 50-1, not repeated here.

## Notes

- This plan should not be allowed to expand Worker surface area before the default bridge debt shrinks.
- `builder/builder.py` (3053 lines) is a Primary File here and also a candidate for structural splitting in 50-6. If 50-6 splits it first, this plan's Worker-projection cleanup becomes scoped to the resulting sub-modules rather than the monolith.
- 46-7 (Worker bundle extraction) is now deferred until an external consumer exists. This plan still has value for internal cleanliness (typed service access, fewer bridge defaults), but is no longer on the critical path to a "reusable bundle." Execute when the bridge debt or globals access actively blocks product work.
