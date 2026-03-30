# 46: Mutation Duplicate-Edge Repair

**Status:** completed
**Goal:** Prevent `plan_graph_mutations` dry runs from failing on exact redundant `add_edge` operations when the edge already exists.

## Tasks
- [x] 1. Confirm current repair boundary
  - [x] 1-1. Verify duplicate `add_node` self-repair exists but duplicate `add_edge` does not.
  - [x] 1-2. Confirm the mutator rejects exact duplicate edge IDs during dry-run.
- [x] 2. Add safe duplicate-edge normalization
  - [x] 2-1. Drop redundant exact `add_edge` ops when the same edge with the same semantics already exists in the graph.
  - [x] 2-2. Drop redundant exact `add_edge` ops repeated earlier in the same plan.
  - [x] 2-3. Preserve intentional remove-then-readd flows and differing edge semantics.
- [x] 3. Add focused regressions
  - [x] 3-1. Existing-edge duplicate becomes a no-op.
  - [x] 3-2. Same-plan duplicate becomes a no-op.
  - [x] 3-3. Remove-then-readd is preserved.
  - [x] 3-4. Same endpoints with different semantics still fail instead of being silently dropped.

## Decisions
- Keep the strict duplicate-edge rejection in `GraphMutator`; repair belongs in chat mutation normalization so only obviously redundant no-ops are removed.
- Only drop duplicates when both the endpoints and semantics (`edge_type`, `spread`) match exactly.

## Notes
- User-visible repro: `Dry run failed: Edge 'process_tickers.results->aggregate_summary.ticker_reports' already exists`.
- Validation: `pytest -q tests/test_mutation_duplicate_add_repair.py` (`10 passed`) and `pytest -q tests/test_post_tool_followup_recovery.py tests/test_chat_manager_build_path.py tests/test_chat_manager_codegen_resilience.py -k 'workflow or mutation'` (`14 passed, 41 deselected`).
