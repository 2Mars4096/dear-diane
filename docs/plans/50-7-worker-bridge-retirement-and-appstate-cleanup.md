# 50-7: Worker Bridge Retirement and AppState Cleanup

**Parent:** [50-structural-consolidation-and-module-reduction](50-structural-consolidation-and-module-reduction.md)
**Status:** in-progress
**Goal:** Remove compatibility delegation from the default runtime-core path where native Worker execution is ready, and finish the migration from globals-backed service access to typed `AppState`.

## Dependencies

- **50-1** should identify the remaining bridge/default-owner hotspots and the compute-family audit (task 1-4 of 50-1).
- Coordinate with **50-6** because `GraphMutator` and workflow-authoring seams currently participate in Worker/legacy projection.

## Tasks

- [ ] 1. Classify bridge debt
  - [ ] 1-1. Reference the 50-1 inventory (task 1-4) for which compute families still depend on `LegacyWorkerAdapterExecutor` in the default path — do not repeat the audit.
  - [ ] 1-2. Separate acceptable edge compatibility from runtime-core containment failures.
- [ ] 2. Make optional-dependency imports lazy and usage-triggered
  - [x] 2-1. Fix `src/dan/executors/llm.py:12`: move the top-level `from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError` inside the methods that use them, or behind a `TYPE_CHECKING` guard with a runtime lazy-import helper. This is the root cause of worker test collection failures in environments without `openai` installed.
  - [x] 2-2. Fix `src/dan/executor_defaults.py:21-67`: `register_default_executors()` unconditionally imports and constructs `LLMExecutor()`. Make the LLM executor import/construction conditional so code-only workflow runs do not require `openai`. Guard with `try/except ImportError` or defer construction to first LLM-type node dispatch.
  - [x] 2-3. Fix `src/dan/server/run_manager.py:1265-1268`: this path calls `register_default_executors()` even for runs that should not need LLMs, which pulls in the `openai` import chain. Make the LLM executor path lazy or skip registration for non-LLM run types.
  - [x] 2-4. Fix worker test modules: `tests/test_worker/test_executor.py` and `tests/test_worker/test_workflow.py` import `LLMExecutor` at module load time, causing collection failure without `openai`. Move to local imports or `pytest.importorskip("openai")`.
- [ ] 3. Enforce Worker import ceilings
  - [ ] 3-1. Worker core files (`executor.py`, `model.py`, `presets.py`) currently import directly from `dan.executors.*` (9 composed executor instances), `dan.models.legacy` (`CodeOperator`, `LLMOperator`, `ToolOperator`), `dan.models.context`/`control_flow`/`nodes`/`ports`, and `dan.tools.get_all_tools`. Define an import ceiling: Worker core should not import `dan.server.*`, and imports from `dan.executors.*` / `dan.models.legacy` should move to an adapter layer or behind lazy/conditional imports.
  - [ ] 3-2. Move DAN-specific legacy bridging imports outward from Worker core into `dan/worker/adapters/` or equivalent, so the Worker compute contract becomes separable from the DAN runtime dependency surface.
- [ ] 4. Retire default Worker/legacy bridge paths for ready families
  - [ ] 4-1. Move supported compute families to direct Worker execution.
  - [ ] 4-2. Remove default registry routing through `legacy_worker_adapter` for those families.
- [ ] 5. Delete runtime-core projection glue as families move
  - [ ] 5-1. Remove Worker→legacy or legacy→Worker projections from builder/runtime/mutation paths once no longer needed in the default flow.
- [ ] 6. Finish `AppState` service migration
  - [ ] 6-1. Replace globals-backed service access with typed accessors off `request.app.state.dan` / `AppState`.
  - [ ] 6-2. Before deleting `_mirror_state_to_globals()`, run a codebase-wide audit (`grep -rn` for each mirrored global name) to confirm no runtime code path, CLI entry point, or test fixture still reads from the globals surface.
  - [ ] 6-3. Delete `_mirror_state_to_globals()` and the globals-backed dependency surface once the audit confirms all callers are migrated.
- [ ] 7. Regressions
  - [ ] 7-1. Revalidate Worker execution parity, executor defaults, builder/graph mutation compatibility, startup, and router dependency injection.
  - [ ] 7-2. Audit and update test expectations that assume the legacy bridge path is the default — bridge retirement will change default executor resolution and may break tests that assert legacy behavior.
  - [ ] 7-3. Confirm worker test modules collect successfully without `openai` installed after lazy-import fixes land.

## Primary Files

- `src/dan/worker/executor.py` (1138 lines — 9 composed executor imports from `dan.executors.*`, legacy bridge dispatch)
- `src/dan/worker/model.py` (imports `dan.models.legacy.ValidationRule`)
- `src/dan/worker/presets.py` (515 lines — wide legacy node type imports from `dan.models.legacy`)
- `src/dan/executors/llm.py` (top-level `openai` import at line 12)
- `src/dan/executor_defaults.py` (unconditional `LLMExecutor()` construction)
- `src/dan/server/run_manager.py` (calls `register_default_executors()` for all runs)
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
- `openai` is not required at import time for Worker, executor-defaults, or run-manager paths that do not use LLM functionality
- worker test modules collect successfully without `openai` installed

## Decisions

- Bridge retirement is incremental; do not attempt to flip every node family at once.
- `AppState` cleanup is migration completion, not a new DI framework.
- The compute-family audit comes from 50-1, not repeated here.

## Notes

- This plan should not be allowed to expand Worker surface area before the default bridge debt shrinks.
- `builder/builder.py` (3053 lines) is a Primary File here and also a candidate for structural splitting in 50-6. If 50-6 splits it first, this plan's Worker-projection cleanup becomes scoped to the resulting sub-modules rather than the monolith.
- 46-7 (Worker bundle extraction) is now deferred until an external consumer exists. This plan still has value for internal cleanliness (typed service access, fewer bridge defaults), but is no longer on the critical path to a "reusable bundle." Execute when the bridge debt or globals access actively blocks product work.
- 2026-04-06: the optional-dependency lazy-import slice is landed. The import-time `openai` pull is removed from `executors/llm.py`, default worker bundle construction defers `LLMExecutor` creation until first LLM dispatch, subprocess regressions cover `LLMExecutor`, `register_default_executors()`, and `RunManager._make_executor_registry()` under an `openai` blocker, and the remaining worker tests no longer import `LLMExecutor` at module load time. The broader bridge/AppState cleanup is still open.
- 2026-04-06: the typed AppState migration now covers the main graph/run/chat/publish/block/RAG/misc HTTP entry points plus the run-events websocket. `routers/dependencies.py` now accepts any connection object with `.app.state.dan` so both `Request` and `WebSocket` callers can resolve typed services. `_mirror_state_to_globals()` and the remaining fallback imports stay in place because meta/adapters/experience helpers and some tests still rely on the globals surface.
- 2026-04-07: request-backed dependency resolution now prefers typed `AppState` outright when a `Request`/`WebSocket` is available instead of consulting mirrored `dan.server.app` globals first. The migration now also covers request-backed `experiences.py`, `meta.py` discovery, `misc.py` health, and the Furnace HTTP/SSE entry points. No-request compatibility paths still fall back to `dan.server.app`, so `_mirror_state_to_globals()` cannot be deleted yet.
- 2026-04-07: the trace-distillation/meta surface now also keeps the typed AppState path honest without breaking focused direct-call tests. `promote_trace_draft(...)` accepts direct no-request calls again for patched graph-store tests, while request-backed `/api/experiences/trace-draft/promote` still resolves storage through the shared typed dependency layer. The no-request compatibility surface remains, so task 6-2 / 6-3 are still open.
