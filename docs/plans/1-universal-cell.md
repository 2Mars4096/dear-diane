# 1: Universal Cell

**Status:** completed
**Goal:** Provide one bounded, reusable execution cell whose behavior is supplied by typed briefs rather than product-specific worker classes.

## Tasks

- [x] Define the fixed Universal Cell system prompt and builder.
- [x] Define typed role, brief, output, sampling, recovery, and prompt-context contracts.
- [x] Keep task/domain policy in the brief and cell infrastructure domain-neutral.
- [x] Add focused prompt, contract, import-boundary, and capability-manifest tests.

## Decisions

- `build_cell(...)` is the only cell-construction primitive.
- A `WorkerBrief` is the executable specialization contract.
- Provider, tool, sandbox, memory, structured-output, and acquisition modules are cell dependencies and remain in the active tree.

## Notes

- Historical builder, graph-node, linter, executor, and product-worker implementations are recoverable from Git before the 2026-08-02 cutover.
