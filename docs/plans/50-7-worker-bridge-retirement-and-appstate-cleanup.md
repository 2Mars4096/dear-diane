# 50-7: Worker Bridge Retirement and AppState Cleanup

**Parent:** [50-structural-consolidation-and-module-reduction](50-structural-consolidation-and-module-reduction.md)
**Status:** completed
**Goal:** Remove compatibility delegation from the default runtime-core path where native Worker execution is ready, and finish the migration from globals-backed service access to typed `AppState`.

## Dependencies

- **50-1** should identify the remaining bridge/default-owner hotspots and the compute-family audit (task 1-4 of 50-1).
- Coordinate with **50-6** because `GraphMutator` and workflow-authoring seams currently participate in Worker/legacy projection.

## Tasks

- [x] 1. Classify bridge debt
  - [x] 1-1. Reference the 50-1 inventory (task 1-4) for which compute families still depend on `LegacyWorkerAdapterExecutor` in the default path — do not repeat the audit.
  - [x] 1-2. Separate acceptable edge compatibility from runtime-core containment failures.
- [x] 2. Make optional-dependency imports lazy and usage-triggered
  - [x] 2-1. Fix `src/dan/executors/llm.py:12`: move the top-level `from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError` inside the methods that use them, or behind a `TYPE_CHECKING` guard with a runtime lazy-import helper. This is the root cause of worker test collection failures in environments without `openai` installed.
  - [x] 2-2. Fix `src/dan/executor_defaults.py:21-67`: `register_default_executors()` unconditionally imports and constructs `LLMExecutor()`. Make the LLM executor import/construction conditional so code-only workflow runs do not require `openai`. Guard with `try/except ImportError` or defer construction to first LLM-type node dispatch.
  - [x] 2-3. Fix `src/dan/server/run_manager.py:1265-1268`: this path calls `register_default_executors()` even for runs that should not need LLMs, which pulls in the `openai` import chain. Make the LLM executor path lazy or skip registration for non-LLM run types.
  - [x] 2-4. Fix worker test modules: `tests/test_worker/test_executor.py` and `tests/test_worker/test_workflow.py` import `LLMExecutor` at module load time, causing collection failure without `openai`. Move to local imports or `pytest.importorskip("openai")`.
- [x] 3. Enforce Worker import ceilings
  - [x] 3-1. Worker core files (`executor.py`, `model.py`, `presets.py`) currently import directly from `dan.executors.*` (9 composed executor instances), `dan.models.legacy` (`CodeOperator`, `LLMOperator`, `ToolOperator`), `dan.models.context`/`control_flow`/`nodes`/`ports`, and `dan.tools.get_all_tools`. Define an import ceiling: Worker core should not import `dan.server.*`, and imports from `dan.executors.*` / `dan.models.legacy` should move to an adapter layer or behind lazy/conditional imports.
  - [x] 3-2. Move DAN-specific legacy bridging imports outward from Worker core into `dan/worker/adapters/` or equivalent, so the Worker compute contract becomes separable from the DAN runtime dependency surface.
- [x] 4. Retire default Worker/legacy bridge paths for ready families
  - [x] 4-1. Move supported compute families to direct Worker execution.
  - [x] 4-2. Remove default registry routing through `legacy_worker_adapter` for those families.
- [x] 5. Delete runtime-core projection glue as families move
  - [x] 5-1. Remove Worker→legacy or legacy→Worker projections from builder/runtime/mutation paths once no longer needed in the default flow.
- [x] 6. Finish `AppState` service migration
  - [x] 6-1. Replace globals-backed service access with typed accessors off `request.app.state.dan` / `AppState`.
  - [x] 6-2. Before deleting `_mirror_state_to_globals()`, run a codebase-wide audit (`grep -rn` for each mirrored global name) to confirm no runtime code path, CLI entry point, or test fixture still reads from the globals surface.
  - [x] 6-3. Delete `_mirror_state_to_globals()` and the globals-backed dependency surface once the audit confirms all callers are migrated.
- [x] 7. Regressions
  - [x] 7-1. Revalidate Worker execution parity, executor defaults, builder/graph mutation compatibility, startup, and router dependency injection.
  - [x] 7-2. Audit and update test expectations that assume the legacy bridge path is the default — bridge retirement will change default executor resolution and may break tests that assert legacy behavior.
  - [x] 7-3. Confirm worker test modules collect successfully without `openai` installed after lazy-import fixes land.

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
- Ready simple compute families use dedicated worker-backed runtime executors in the default path; broader specialized families stay on explicit legacy executors until their native Worker runtime contracts are equally honest.
- Worker-first authoring is now the default when `DAN_WORKER_BUILDER` is unset, but explicit `canonical_workers=False` or `DAN_WORKER_BUILDER=disabled` remains the compatibility opt-out.

## Notes

- This plan should not be allowed to expand Worker surface area before the default bridge debt shrinks.
- `builder/builder.py` (3053 lines) is a Primary File here and also a candidate for structural splitting in 50-6. If 50-6 splits it first, this plan's Worker-projection cleanup becomes scoped to the resulting sub-modules rather than the monolith.
- 46-7 (Worker bundle extraction) is now deferred until an external consumer exists. This plan still has value for internal cleanliness (typed service access, fewer bridge defaults), but is no longer on the critical path to a "reusable bundle." Execute when the bridge debt or globals access actively blocks product work.
- 2026-04-06: the optional-dependency lazy-import slice is landed. The import-time `openai` pull is removed from `executors/llm.py`, default worker bundle construction defers `LLMExecutor` creation until first LLM dispatch, subprocess regressions cover `LLMExecutor`, `register_default_executors()`, and `RunManager._make_executor_registry()` under an `openai` blocker, and the remaining worker tests no longer import `LLMExecutor` at module load time. The broader bridge/AppState cleanup is still open.
- 2026-04-06: the typed AppState migration now covers the main graph/run/chat/publish/block/RAG/misc HTTP entry points plus the run-events websocket. `routers/dependencies.py` now accepts any connection object with `.app.state.dan` so both `Request` and `WebSocket` callers can resolve typed services. `_mirror_state_to_globals()` and the remaining fallback imports stay in place because meta/adapters/experience helpers and some tests still rely on the globals surface.
- 2026-04-07: request-backed dependency resolution now prefers typed `AppState` outright when a `Request`/`WebSocket` is available instead of consulting mirrored `dan.server.app` globals first. The migration now also covers request-backed `experiences.py`, `meta.py` discovery, `misc.py` health, and the Furnace HTTP/SSE entry points. No-request compatibility paths still fall back to `dan.server.app`, so `_mirror_state_to_globals()` cannot be deleted yet.
- 2026-04-07: the trace-distillation/meta surface now also keeps the typed AppState path honest without breaking focused direct-call tests. `promote_trace_draft(...)` accepts direct no-request calls again for patched graph-store tests, while request-backed `/api/experiences/trace-draft/promote` still resolves storage through the shared typed dependency layer. The no-request compatibility surface remains, so task 6-2 / 6-3 are still open.
- 2026-04-07: referenced the existing 50-1 / `docs/key-scripts.md` inventory rather than repeating the audit. The default-path bridge debt was the documented 12-family set (`llm_operator`, `tool_operator`, `code_operator`, `rag_operator`, `input`, `router`, `human`, `human_in_the_loop`, `validator`, `vote`, `reduce`, `reflection`). This run classifies `LegacyWorkerAdapterExecutor` and `worker.presets` as acceptable edge compatibility, while the previous default registration of those 12 families through the adapter counted as the runtime-core containment failure.
- 2026-04-07: `src/dan/executor_defaults.py` now routes the 12 inventory families through their direct legacy executors in the default registry again, leaving `LegacyWorkerAdapterExecutor` as an explicit compatibility seam instead of the hidden default. `src/dan/worker/executor.py` also no longer imports `dan.executors.*`, `dan.models.legacy`, or `dan.tools.get_all_tools` at module load time; those dependencies are now lazy usage-triggered so the Worker import boundary is narrower even before the broader adapter extraction in task 3-2.
- 2026-04-07: the `_mirror_state_to_globals()` deletion audit is now explicit. Repo-wide `rg` still finds live no-request/global consumers in `src/dan/server/startup/__init__.py` (meta/experience bootstrap still imports `dan.server.app` helpers), `src/dan/server/routers/misc.py` (no-request health fallback), `src/dan/server/routers/adapters.py` (direct compatibility callers via shared dependencies), and the duplicate legacy startup shim at `src/dan/server/startup.py`. Because those runtime paths remain and some are outside this run's write boundary, task 6-3 stays blocked and `_mirror_state_to_globals()` remains in place.
- 2026-04-07: added focused regression files for the missing server-side validation targets (`test_router_dependencies.py`, `test_misc_health.py`, `test_run_manager.py`, `test_run_manager_telemetry.py`) and revalidated the requested `tests/test_executor_defaults.py`, `tests/test_worker/test_executor.py`, `tests/test_worker/test_workflow.py`, `tests/test_server/test_router_dependencies.py`, `tests/test_server/test_misc_health.py`, `tests/test_server/test_run_manager.py`, `tests/test_server/test_run_manager_reflection.py`, and `tests/test_server/test_run_manager_telemetry.py` bundle (`67 passed`).
- 2026-04-08: the remaining AppState/global deletion tail is now landed in the main workspace. `src/dan/server/startup.py` and `src/dan/server/startup/__init__.py` now keep the canonical live `AppState`, expose state-backed helper builders, and no longer mirror runtime services back into `dan.server.app` globals. `src/dan/server/routers/dependencies.py`, `src/dan/server/routers/misc.py`, `src/dan/server/routers/experiences.py`, and `src/dan/server/app.py` now resolve no-request compatibility paths through startup's active `AppState` instead of the old globals surface, `_mirror_state_to_globals()` is deleted from both startup entry points, and the focused bridge/AppState basket now passes in the main workspace (`68 passed, 4 warnings`).
- 2026-04-08: task `3-2` is now landed in the main workspace. `LegacyWorkerAdapterExecutor` moved out of `src/dan/worker/executor.py` and into `src/dan/worker/adapters.py`, so the Worker runtime module no longer owns DAN-specific legacy bridging. Focused regression coverage was updated in `tests/test_executor_defaults.py`, `tests/test_worker/test_workflow.py`, `tests/test_server/test_run_manager.py`, and `tests/test_client/test_local.py`, and the touched basket passed in the main workspace.
- 2026-04-08: task `4-1` is now landed in the main workspace. `src/dan/executor_defaults.py` routes `llm_operator`, `tool_operator`, `code_operator`, and `input` through dedicated `WorkerBackedLegacyComputeExecutor` instances instead of the generic `LegacyWorkerAdapterExecutor`, while the remaining specialized families stay on their explicit legacy executors. The touched runtime/default-path basket passed in the main workspace via `PYTHONPATH=src pytest -q tests/test_executor_defaults.py tests/test_worker/test_workflow.py tests/test_server/test_run_manager.py` (`12 passed, 4 warnings`), `PYTHONPATH=src pytest -q tests/test_worker/test_equivalence.py` (`15 passed, 4 warnings`), and later the combined closeout basket (`281 passed, 2 skipped, 8 warnings`).
- 2026-04-08: task `5-1` is now landed in the main workspace. `resolve_worker_builder_mode()` now defaults unset `DAN_WORKER_BUILDER` to `enabled`, builder/router/validator aliases now follow the same canonical Worker path as the other compute-like aliases, and the default builder/loader/mutator flows now stay Worker-first unless a caller explicitly opts back to `disabled`. Focused authoring/mutation/client coverage passed in the main workspace via `PYTHONPATH=src pytest -q tests/test_meta/test_planner_taxonomy_alignment.py tests/test_worker/test_builder.py tests/test_worker/test_worker_first_validation_gate.py tests/test_graph_mutator_taxonomy.py tests/test_loader/test_compiler.py tests/test_loader/test_decompiler.py` (`105 passed`) and `PYTHONPATH=src pytest -q tests/test_server/test_graph_mutator.py tests/test_server/test_mutation_quality.py tests/test_loader/test_paper_writing_parity.py tests/test_meta/test_intent_compiler_build.py tests/test_worker/test_presets.py tests/test_client/test_local.py` (`149 passed, 2 skipped`).
- 2026-04-08: the late ASGI teardown gap surfaced during the unchanged broad-suite burn-down is now fixed in the same main workspace. `RunManager` owns an explicit async shutdown path that cancels live run tasks and pending approvals during lifespan teardown, and both startup entry points now call it before telemetry/integration shutdown. Focused validation passed with `PYTHONPATH=src pytest -q tests/test_server/test_run_manager.py` (`2 passed`) and `PYTHONPATH=src pytest -q tests/test_server/test_api.py` (`20 passed`).
