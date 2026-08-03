# 7: Blended Universal Cell Configuration

**Status:** completed
**Goal:** Establish the initial three-input Structured Cell authoring configuration while retaining the original typed brief, execution request, worker core, provider, tool, memory, acquisition, workspace-instruction, and structured-output machinery as compiled runtime infrastructure; the final reduction is recorded in sub-plan 7-1.

## Tasks

- [x] Define canonical `model + context + constraints` cell specs.
- [x] Move sequence and fork/join relationships into a separate topology contract.
- [x] Add nested context views and bounded model projections while retaining full logical context.
- [x] Move output and validation specifications into constraints.
- [x] Compile cell specs into internal `WorkerBrief` and `ExecutionRequest` contracts.
- [x] Enforce authority-monotone child delegation and bounded parallelism.
- [x] Add typed all-settled/all-success/quorum/at-least-one join policies and next-cell gates.
- [x] Add focused and regression tests.
- [x] Reconcile architecture, API, tracking, and failure documentation.

## Decisions

- The initial `StructuredCell` had three behavioral inputs: `model`, `context`, and `constraints`; sub-plan 7-1 supersedes this with `CellSpec(context, contract)` plus external invocation binding.
- Topology is external. Cells do not own mutable previous/next/parent/children pointers.
- Horizontal handoff preserves full logical context, but compilation renders a bounded projection for the model.
- Vertical delegation uses explicit nested context views; unselected context is absent from child state.
- Child constraints are computed as an authority-monotone narrowing of the parent contract.
- Child collapse stores compact reports, evidence/artifact references, and context deltas—not complete child contexts or transcripts.
- All declared children must settle before a join is evaluated; join policy then decides whether advancement is accepted.
- The original `WorkerBrief` becomes an internal compiled representation, not the caller-facing cell mental model.
- The existing `WorkerCoreExecutor` and lower provider/tool/memory/acquisition layers remain unchanged.

## Notes

- This work remains isolated on `codex/simplified-universal-cell` until the blended contract is reviewed and explicitly selected for product integration.
- [7-1](7-1-reduced-cell-contract.md) supersedes the initial three-input authoring surface: the canonical `CellSpec` now contains only context plus contract, while identity and executor selection belong to `CellInvocation`.
- The focused reduced-cell suite passes 26 tests; the complete retained worker suite passes 137 tests.
- The repository gate passes 691 tests with 4 skipped; two known server assertions remain deselected because they derive the expected primary workspace from the nested worktree test-file path.
