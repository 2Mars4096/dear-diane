# 52-2: Tissue Patterns Worker Pools And Quorum

**Parent:** [52-multicellular-composition](52-multicellular-composition.md)
**Status:** completed
**Goal:** Build reusable coordination patterns for groups of similar cells before introducing heterogeneous organ-level modules.

## Tasks
- [x] 1. Define the first tissue patterns
  - [x] 1-1. Parallel worker pool
  - [x] 1-2. Review/quorum pool
  - [x] 1-3. Retrieval/enrichment pool
- [x] 2. Define merge and conflict behavior
  - [x] 2-1. Fan-out/fan-in merge strategy
  - [x] 2-2. Quorum or tie-break rules
  - [x] 2-3. Escalation when the tissue cannot self-resolve
- [x] 3. Add shared limits
  - [x] 3-1. Concurrency, budget, and failure boundaries across a tissue
- [x] 4. Validate that tissues reuse the same cell contract cleanly
  - [x] 4-1. Avoid creating a second incompatible coordination primitive
- [x] 5. Pick one tissue pattern as the first production-quality reusable module
  - [x] 5-1. Choose the smallest high-leverage pattern and harden it first

## Decisions
- Tissues should prove cooperation among similar cells before mixed-role organs are attempted.
- Tissue APIs should remain narrow and reusable.
- Land the tissue layer as a coordinator cell plus child handoff fan-out so the same `CellHandoffPacket`/signal contract remains the only membrane.
- Harden `review_quorum_pool(...)` first because it exercises the generic parallel pool, merge, tie-break, escalation, and shared-limit seams in one bounded reusable primitive.

## Notes
- This slice is where same-type worker pools become a real reusable coordination primitive instead of one-off parallel dispatch.
- 2026-04-08: active child implementation is now in progress on top of the landed `52-1` signaling/handoff layer. The intended first hardening target is a reusable tissue surface for parallel worker pools plus quorum/tie-break behavior without introducing a second incompatible cell contract.
- 2026-04-08: landed `src/dan/worker/tissue.py` with `parallel_worker_pool(...)`, `review_quorum_pool(...)`, `retrieval_enrichment_pool(...)`, shared `TissuePoolLimits`, projected budget-envelope enforcement, bounded concurrency, failure-budget escalation, and coordinator-to-member packet derivation through `build_tissue_member_packet(...)`.
- 2026-04-08: focused validation now covers bounded parallel fan-out, majority quorum resolution, unresolved-tie escalation, pre-dispatch shared-budget refusal, and retrieval-style output-ref aggregation in `tests/test_worker/test_tissue.py`.
