# 7-1: Reduced Universal Cell Contract

**Parent:** [7-blended-universal-cell-config](7-blended-universal-cell-config.md)
**Status:** completed
**Goal:** Reduce the canonical cell to context plus a universal contract, move executor selection into invocation, and add an extensible runtime-authored record ledger for sources, modifications, artifacts, deliverables, and future evidence kinds.

## Tasks

- [x] Define `CellSpec(context, contract)` with allowed tools, dos, don'ts, preferences, limits, and acceptance.
- [x] Move model/runtime selection into a separate `CellInvocation` executor binding.
- [x] Move per-fork concurrency out of cell constraints and into topology/runtime.
- [x] Define extensible record and record-requirement contracts.
- [x] Reduce reports to outcome, result, context delta, records, and error.
- [x] Refactor compilation, execution, sequence inheritance, child preparation, collapse, and handoff.
- [x] Add record collection and acceptance checks for sources, modifications, artifacts, and deliverables.
- [x] Update public exports, focused tests, and regression coverage.
- [x] Reconcile architecture, API, README, plans, changelog, and known-environment notes.

## Decisions

- Tool capability is represented only by a closed `allowed_tools` allowlist at cell level.
- Platform/runtime denials remain outside the cell and participate in effective tool resolution.
- High-level behavior uses `dos`, `donts`, and `preferences`, not generic allow/deny terminology.
- `limits` is the universal resource-ceiling map; fork concurrency belongs to the fork relation and shared runtime.
- `acceptance` owns output shape, validation checks, and typed required-record predicates.
- Runtime-observed facts use an open `CellRecord.kind` string plus stable common provenance fields, so adding record kinds does not change the cell schema.
- The cell definition has no identity, model, topology position, status, or execution history.

## Notes

- This is a cell-layer refinement only; it does not introduce an organism scheduler.
- The focused reduced-cell suite passes 26 tests; all 137 retained worker tests pass.
- The repository gate passes 691 tests with 4 skipped and the two documented worktree-path assertions deselected.
