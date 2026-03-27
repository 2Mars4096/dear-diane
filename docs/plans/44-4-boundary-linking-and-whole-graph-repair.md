# 44-4: Boundary Linking and Whole-Graph Repair

**Parent:** [44-structured-workflow-generation](44-structured-workflow-generation.md)
**Status:** completed
**Goal:** Link validated sections into a full candidate workflow, repair section-boundary port and contract mismatches without redoing completed sections, and persist only after whole-graph acceptance passes.

## Problem

Section-local generation is useful only if the handoff between sections is reliable. The main failure mode for larger workflows is not usually a broken node inside a section, but a mismatch at the boundary between section output ports and the next section's expected inputs.

This subplan defines the final assembly layer:

- diagnose boundary failures separately from within-section failures
- repair only the minimal affected boundary, not the whole workflow
- run whole-graph tests only after section-local validation succeeds
- persist a candidate graph only after it passes acceptance

## Tasks

- [x] 1. Define a boundary-failure report that distinguishes
  - [x] 1-1. section-internal node failures
  - [x] 1-2. section-end to section-start port mismatches
  - [x] 1-3. missing or renamed contract fields
- [x] 2. Build the graph linker that stitches validated sections into a candidate workflow
  - [x] 2-1. Preserve section artifact IDs and boundary metadata
  - [x] 2-2. Attach explicit expected-input and produced-output contracts at joins
  - [x] 2-3. Keep section-local artifacts reusable across repair attempts
- [x] 3. Implement a boundary-only repair loop
  - [x] 3-1. Patch port mappings, aliases, or adapter nodes at section joins
  - [x] 3-2. Re-run only the two adjacent sections involved in the mismatch
  - [x] 3-3. Avoid regenerating unrelated sections when boundary repair succeeds
  - [x] 3-4. Bound repair attempts via `DAN_STRUCTURED_BOUNDARY_REPAIR_MAX` (default `2`) — not hardcoded
- [x] 4. Add whole-graph acceptance checks after section-local validation
  - [x] 4-1. Verify end-to-end data flow across section joins
  - [x] 4-2. Run a full candidate graph test only after all sections pass locally
  - [x] 4-3. Report whole-graph failure separately from boundary repair failure
  - [x] 4-4. Run a bounded one-shot execution smoke on the accepted candidate via `RunManager.start_run()` or direct engine execution when required inputs can be satisfied from defaults, fixtures, or declared global inputs; assert the run reaches a terminal state and produces expected top-level artifacts or output ports
- [x] 5. Gate persistence on candidate acceptance
  - [x] 5-1. Do not persist incomplete or partially repaired graphs
  - [x] 5-2. Persist only the accepted candidate graph and its boundary metadata
  - [x] 5-3. Retain rejected candidate diagnostics for debugging and evals
  - [x] 5-4. If the prompt carried schedule intent, persist the accepted graph first and only then emit a schedule-ready sidecar for rollout/testing rather than trying to embed schedule state into the graph itself
- [x] 6. Add regressions for boundary repair efficiency
  - [x] 6-1. Repair a single join without rebuilding all sections
  - [x] 6-2. Fail whole-graph acceptance when a boundary contract is wrong
  - [x] 6-3. Prove persistence does not happen before acceptance
  - [x] 6-4. Prove execution smoke catches graphs that pass structural validation but still fail immediately at runtime

## Likely Files

New:
- `src/dan/server/agent_runtime/workflow_boundary_linking.py` — new boundary linker, join diagnosis, and boundary-only repair loop

Existing (modify or extend):
- `src/dan/server/agent_runtime/workflow_generation_acceptance.py` — existing `accept_candidate_graph()` for whole-graph acceptance; extend to distinguish boundary vs section failures
- `src/dan/server/agent_runtime/workflow_generation.py` — integrate boundary linking into the generation pipeline
- `src/dan/meta/workflow_contract.py` — existing `validate_workflow_build_contract(apply_repairs=True)` for whole-graph validation and mechanical repair
- `src/dan/meta/diagnosis.py` — existing `DiagnosisLoop.diagnose_and_repair()` that already does LLM-guided graph repair; the boundary repair loop should reuse or compose with this
- `src/dan/meta/graph_quality.py` — existing `compute_quality_report()` for whole-graph semantic quality gating
- `src/dan/server/run_manager.py` — existing `RunManager.start_run()` entry point for one-shot execution smoke
- `tests/test_meta/test_workflow_contract.py`
- `tests/test_meta/test_validation_pipeline.py`
- `tests/test_meta/test_planner_codegen_integration.py`

## Decisions

- Boundary repair must be cheaper than full regeneration, otherwise the sectioning architecture loses its speed advantage.
- A section can be considered locally valid even if the final join fails, but only the join should be repaired.
- Whole-graph acceptance is a separate gate from section validation and should never be conflated with it.
- Persistence must happen only after the candidate graph passes acceptance, not when linking begins.
- Repair artifacts should preserve enough metadata to explain which join failed and why.

## Notes

- This subplan is the final assembly layer for structured workflow generation.
- The repair strategy should prefer minimal contract adapters over reauthoring sections. The existing `DiagnosisLoop.diagnose_and_repair()` in `dan.meta.diagnosis` already does LLM-guided graph repair with `_diagnosis_graph_validator` — boundary repair should compose with this rather than reimplementing diagnosis from scratch.
- The existing `validate_workflow_build_contract(apply_repairs=True)` already performs mechanical fixes (normalize metadata, fix legacy edges, deduplicate IDs, fix entry/exit points). Boundary linking should invoke this on the stitched graph before triggering LLM-guided repair.
- `accept_candidate_graph()` and `validate_workflow_build_contract()` are necessary but not sufficient. This plan should add an execution-smoke layer so "accepted" means "validated and at least minimally runnable," not just "run_ready on paper."
- The main metric is latency to an accepted graph, not just the number of successful intermediate validations.
- If boundary mismatches dominate, the sectioning heuristic likely needs tuning in [44-1](44-1-high-level-spec-and-sectioning.md).
- The existing `accept_candidate_graph()` validates, emits `ChatValidationResultEvent`, applies the quality gate, and records generation outcomes — whole-graph acceptance should flow through this same function.
- Landed in `src/dan/server/agent_runtime/workflow_boundary_linking.py`, `src/dan/server/agent_runtime/workflow_generation.py`, and `src/dan/server/chat_manager.py`: diagnostics-only boundary reports, bounded alias repair at section joins, candidate-graph materialization with boundary metadata, safe-subset execution smoke through `RunManager`, and regressions for join repair plus no-persist-before-acceptance behavior.
