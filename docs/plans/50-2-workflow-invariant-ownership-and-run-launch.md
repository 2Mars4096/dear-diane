# 50-2: Workflow Invariant Ownership and Run Launch

**Parent:** [50-structural-consolidation-and-module-reduction](50-structural-consolidation-and-module-reduction.md)
**Status:** completed
**Goal:** Make workflow apply/run invariants and run-launch behavior authoritative at the callee boundary instead of reimplemented across callers.

## Dependencies

- **50-1** should land first so the key run/apply boundaries, callers, and validation basket are explicit.

## Tasks

- [x] 1. Make run-readiness callee-owned
  - [x] 1-1. Update `RunManager.start_run()` and `resume_run()` to enforce the shared run-readiness contract by default.
  - [x] 1-2. Add a narrowly scoped explicit bypass only for synthetic/reflection/test paths that truly require it.
  - [x] 1-3. Remove or simplify caller-side guard duplication once the callee owns the invariant.
- [x] 2. Consolidate authoritative run launch
  - [x] 2-1. Create one authoritative launch helper on `RunManager` — expected shape: `launch(workflow_id, *, relay=True, bus=None) -> RunHandle` — that starts a run and optionally attaches relay/bus plumbing in one call.
  - [x] 2-2. Move repeated relay glue out of routes/gateway/startup helpers so launch behavior stays consistent.
- [x] 3. Consolidate apply-ready mutation save
  - [x] 3-1. Create one shared flow that applies the stronger apply-readiness check, saves the graph, and returns consistent revision/result metadata.
  - [x] 3-2. Route router, capability, and chat auto-apply paths through that shared flow.
- [x] 4. Tighten workflow reference ownership
  - [x] 4-1. Use the shared workflow-identity resolution/stale-revision model from `workflow_identity.py` (shipped in 48-1) by default for user-facing workflow references.
  - [x] 4-2. Leave raw exact-ID APIs intentionally minimal only where that lower-level contract is explicit.
- [x] 5. Regressions and compatibility cleanup
  - [x] 5-1. Add focused coverage for guarded run start, relay hookup, apply-ready mutation save, and stale-resolution behavior across all major surfaces.
  - [x] 5-2. Delete superseded caller-side guard/launch helpers in the same PR that lands the callee-owned replacement — not deferred to a later patch series.

## Primary Files

- `src/dan/server/workflow_guards.py`
- `src/dan/server/run_manager.py`
- `src/dan/server/run_relay.py`
- `src/dan/server/routers/runs.py`
- `src/dan/server/routers/chat.py`
- `src/dan/server/capabilities/runs.py`
- `src/dan/server/gateway/router.py`
- `src/dan/server/app.py`
- `src/dan/server/chat_manager.py`
- `src/dan/server/cli/chat_local.py`
- `src/dan/server/workflow_identity.py`

## Success Criteria

- run-readiness is enforced by `RunManager` rather than remembered by individual callers
- run launch + relay hookup is implemented once and reused by all normal run entrypoints
- workflow mutation apply/save uses one stronger contract path across router, capability, and chat surfaces
- repeated caller-side workflow/run guard code is measurably reduced
- superseded helpers are deleted in the same PR, not deferred

## Decisions

- The authoritative callee should own invariants; routes and adapters may still shape user-facing errors, but not decide whether the invariant exists.
- Apply-readiness and run-readiness share the same contract family but should stay separate entrypoints because they differ in repair policy.

## Notes

- This plan directly addresses the review finding that the strongest workflow contract exists, but its ownership is still too caller-heavy.
- `workflow_identity.py` already ships revision-aware resolution and stale-revision context packs from the 48-1 plan; task 4 finishes the migration to that helper rather than building a new one.
