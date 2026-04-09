# 51-1: Progressive Context Acquisition And Continuation Hooks

**Parent:** [51-universal-worker-hardening-and-standalone-cell](51-universal-worker-hardening-and-standalone-cell.md)
**Status:** completed
**Goal:** Make the worker acquire only necessary context step by step while leaving durable hooks that allow later steps to continue without rescanning the world.

## Dependencies

- This should be the first active `51-*` slice because it sets the acquisition pattern that later capability, memory, and runner work should reuse.
- The worker-boundary cleanup in the tail of `50-*` should continue moving toward smaller, typed contracts so `51-1` does not need to harden around temporary compatibility seams.

## Tasks
- [x] 1. Define staged acquisition as a first-class worker behavior
  - [x] 1-1. Specify a `discover -> select -> expand -> act` contract for tools, files, and other evidence sources
  - [x] 1-2. Keep discovery responses compact and cheap so the worker sees catalogs before details
- [x] 2. Add durable continuation hooks
  - [x] 2-1. Define stable refs/ids for discovered tools, files, evidence blocks, and partial task state
  - [x] 2-2. Define the minimum checkpoint/cursor payload needed to resume without rescanning prior state
- [x] 3. Make stepwise acquisition observable and governable
  - [x] 3-1. Surface which discovery outputs were selected and why
  - [x] 3-2. Add explicit budget and policy controls for when a worker is allowed to expand from catalog-level context into full detail
- [x] 4. Prove the pattern on the first evidence families
  - [x] 4-1. Tool catalogs first, tool details second
  - [x] 4-2. Path/file inventory first, file reads second
- [x] 5. Add focused regressions and eval hooks
  - [x] 5-1. Lock the worker into targeted reads instead of eager full-context acquisition on representative tasks

## Primary Files

- `src/dan/worker/core/contracts.py`
- `src/dan/worker/core/interfaces.py`
- `src/dan/worker/model.py`
- `src/dan/worker/adapters.py`
- new discovery/catalog helper surfaces under `src/dan/worker/core/` as needed

## Success Criteria

- the worker can discover tools, paths, and evidence sources through compact list/catalog responses before reading full detail
- the worker can select a subset and then request expansion only for that subset
- continuation hooks survive across later steps so resumed work can reuse refs, cursors, and compact summaries instead of rescanning everything
- focused regressions prove that representative tasks stay on the staged `discover -> select -> expand -> act` path rather than collapsing back into eager full-context loading

## Decisions
- Progressive disclosure is part of the cell membrane, not just a runtime optimization.
- Later organism layers should consume refs and summaries whenever possible, and only rehydrate detail on demand.
- The worker-core membrane now carries explicit acquisition state in the normalized request/result contract: compact source specs, discovered catalogs, selection records, expanded context, and a resumable continuation payload.
- Worker-level acquisition policy is the governing surface for expansion budgets; request-level policy can tighten or shape a run, but the core always records which refs were selected and expanded.

## Notes
- This slice hardens the “only pick up what is necessary” principle directly into the worker contract.
- Landed in `src/dan/worker/core/` via typed acquisition contracts, an acquisition provider interface, executor-level `discover -> select -> expand -> act` orchestration, and a standalone local acquisition helper for tool catalogs plus file inventories.
- The worker now preserves compact catalog cursors plus expanded refs in `ContinuationPayload`, and resume paths can reuse prior catalogs/expanded context without rediscovery when the checkpoint already contains what the next step needs.
- Focused proof command: `pytest -q tests/test_worker/test_core_executor.py tests/test_worker/test_model.py`
