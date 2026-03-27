# 44-3: Section Assembly and Parallel Validation

**Parent:** [44-structured-workflow-generation](44-structured-workflow-generation.md)
**Status:** completed
**Goal:** Assemble validated node specs into parallelizable sections, run section-local integration tests and repairs, and only promote a candidate graph once its sections pass acceptance.

## Problem

The current graph-generation path tends to treat the workflow as one large authoring problem. That makes it harder to debug, slower to recover from failures, and too easy to persist a graph before the structure is actually trustworthy.

A section-based assembly layer should narrow the failure surface:

- section-local issues should be repaired without redoing the whole workflow
- section tests should validate input/output contracts before cross-section linking
- the builder should keep the graph in candidate/staging form until acceptance passes
- parallel section workers should keep latency bounded even when the full workflow is large

## Tasks

- [x] 1. Define the section assembly contract
  - [x] 1-1. Specify the inputs a section assembler receives from the high-level spec and node-worker stages
  - [x] 1-2. Specify the section artifact it returns: nodes, internal edges, boundary ports, test summary, and repair notes
  - [x] 1-3. Define what it means for a section to be accepted, deferred, or rejected
- [x] 2. Implement parallel section assembly
  - [x] 2-1. Launch section assemblers in parallel with a configurable cap via `DAN_STRUCTURED_SECTION_ASSEMBLER_CAP` (default derived from `DAN_STRUCTURED_WORKER_POOL_CAP`, not hardcoded inline)
  - [x] 2-2. Assemble section-local graphs without linking to other sections yet
  - [x] 2-3. Preserve section boundaries explicitly so later debugging can isolate failures by chapter or segment
- [x] 3. Add section-local validation including semantic coherence
  - [x] 3-1. Validate section input and output contracts before whole-graph linking
  - [x] 3-2. Run section-level integration tests for each assembled section
  - [x] 3-3. Surface contract mismatches with enough detail to repair only the affected section
  - [x] 3-4. Verify cross-node semantic coherence within the section: if node A declares output "earnings_data" and node B consumes it, check that A's executor kind and tool bindings can plausibly produce that data — a `tool_operator` calling `web_search` can produce search results but not structured financial data unless downstream processing exists
  - [x] 3-5. Verify that section-level data flow is end-to-end grounded: every section entry port has a producer (upstream section or global input) and every section exit port has a consumer (downstream section or global output); orphaned ports are a spec-level error, not a section-assembly problem
- [x] 4. Add within-section repair loops
  - [x] 4-1. Repair missing ports, bad node wiring, and incompatible outputs inside a section first
  - [x] 4-2. Re-run the section test after each repair attempt
  - [x] 4-3. Stop after bounded retries (`DAN_STRUCTURED_SECTION_REPAIR_MAX`, default `2`) and return a structured failure if the section still does not pass — not a hardcoded retry count
- [x] 5. Gate whole-graph progression on section acceptance
  - [x] 5-1. Prevent whole-graph linking until all required sections pass section-level acceptance
  - [x] 5-2. Keep the workflow as a candidate graph until section validation and acceptance are complete
  - [x] 5-3. Only allow persistence once the full candidate graph has passed the section gate and downstream acceptance checks
  - [x] 5-4. Emit section-level runnable fixtures or expected boundary artifacts so whole-graph execution smoke can verify more than structural validity in 44-4

## Likely Files

New:
- `src/dan/server/agent_runtime/section_assembly.py` — new section assembler and section-local repair loop
- `tests/test_meta/test_section_assembly.py` — new section assembly and repair tests

Existing (modify or extend):
- `src/dan/server/agent_runtime/workflow_generation.py` — integrate section-assembly stage into the generation pipeline
- `src/dan/server/agent_runtime/workflow_generation_acceptance.py` — existing `accept_candidate_graph()` pattern; extend for section-level acceptance
- `src/dan/meta/workflow_contract.py` — existing `validate_workflow_build_contract(apply_repairs=True)` already does mechanical fixes, graph validation, and run-readiness checks; reuse at section scope
- `src/dan/validation/graph.py` — existing `validate_graph()` used inside `validate_workflow_build_contract`; may need section-scoped variant
- `src/dan/meta/graph_quality.py` — existing quality dimensions; may need section-level quality checks
- `src/dan/server/agent_runtime/workflow_outcomes.py` — existing `PreparedWorkflowSave` / `prepare_validated_workflow_save()` for the persistence gate
- `tests/test_meta/test_validation_pipeline.py`

## Decisions

- Sections should be treated as first-class intermediate artifacts, not just an internal implementation detail.
- Section assembly should optimize for both debugging and throughput, so bounded parallelism matters.
- Section-assembler concurrency should be configurable, not hardcoded, and should default coherently relative to the node-worker cap.
- Persistence must wait for acceptance; candidate graphs should not be saved as final graphs before section validation passes. The existing `PreparedWorkflowSave` / `prepare_validated_workflow_save()` pattern should be reused for the final persistence gate.
- Repair should stay local to the section that failed unless the failure is explicitly a boundary-linking problem. The existing `validate_workflow_build_contract(apply_repairs=True)` already performs mechanical fixes and could be invoked per-section.
- The section boundary itself is part of the contract and should remain visible in diagnostics.

## Notes

- This subplan depends on `44-1` for section definitions and `44-2` for node-level readiness.
- The whole-graph linker should not be responsible for fixing section-local problems.
- If section tests become too coarse, split them before widening the scope of the linker.
- The existing validation stack is: `validate_graph()` (structural) → `validate_workflow_build_contract()` (structural + mechanical repairs + run-readiness) → `compute_quality_report()` (semantic quality). Section validation should compose with these layers, not duplicate them.
- The engine already skips downstream nodes when required upstream data edges are missing due to failure (landed in 43-2). Section validation should complement this runtime safety net by catching semantic gaps at generation time before the graph ever runs.
- Section-local subgraphs may need temporary `entry_points` / `exit_points` to pass validation in isolation, then be stitched into the full graph during boundary linking.
- Section acceptance should emit enough artifact and fixture metadata that 44-4 can run a bounded end-to-end smoke instead of trusting `run_ready` alone.
- Landed in `src/dan/server/agent_runtime/section_assembly.py` and `tests/test_meta/test_section_assembly.py`: parallel section assembly, explicit boundary artifacts, section-local validation issues, bounded repair loops, and runnable test summaries that feed the whole-graph smoke gate.
