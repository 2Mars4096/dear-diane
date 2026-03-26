# 43-2: Upstream Prerequisite Gating

**Parent:** [43-paper-review-benchmark-hardening](43-paper-review-benchmark-hardening.md)
**Status:** completed
**Goal:** Prevent downstream paper-writing stages from running when required upstream inputs are missing, invalid, or empty.

## Problem

When an upstream node fails (e.g., `search_papers` tool, `lit_synthesizer` LLM, `compile_initial_latex` tool), the engine scheduler still releases downstream nodes for execution. Downstream nodes then run with missing input data, producing undefined behavior or late crashes.

This happens because `_execute_ready_queue` in `src/dan/engine/scheduler.py` decrements `in_degree` for downstream nodes **regardless of whether the finished node succeeded or failed** — only data-topology dependency is checked, not upstream success.

The existing `_should_skip` logic (scheduler.py ~2542–2578) only skips on:
- Inactive `ControlEdge` branches
- Missing `GateNode` output port data

There is **no** "skip if upstream `FAILED`" rule in the engine.

Note: the original plan referenced `read_pdf` and "referee outputs" — those do not exist in the paper-writing workflow. The actual prerequisite boundaries are around `search_papers`, `lit_synthesizer`, `citation_verifier`, `claim_evidence_gate`, `compile_latex`, etc.

## Tasks

- [ ] 1. Identify the prerequisite boundaries in the paper-writing pipeline
  - Key boundaries: `search_papers` → `survey_aspect` → `lit_synthesizer` → `citation_verifier` → `claim_evidence_gate` → `outline_planner` → section writing → compile → review loop → save → package
- [ ] 2. Choose a gating mechanism:
  - **Option A:** Engine-level: extend `_should_skip` to propagate `FAILED` status to downstream data-edge dependents (generic, but larger change)
  - **Option B:** Workflow-level: insert explicit `GateNode` checks at prerequisite boundaries (local, no engine changes)
  - **Option C:** Use `ControlEdge` with conditions to gate on upstream success flags passed as data
- [ ] 3. Implement chosen gating at the identified boundaries
- [ ] 4. Ensure `DEAD_EDGE_WARNING` (scheduler.py ~1959–1986) behavior is respected — currently warns on missing required inputs but does not prevent execution
- [ ] 5. Add tests that prove downstream nodes are skipped when upstream prerequisites fail

## Likely Files

- `examples/paper_writing.py`
- `src/dan/engine/scheduler.py` (especially `_execute_ready_queue`, `_should_skip`, `DEAD_EDGE_WARNING`)
- `src/dan/engine/state.py` (`PortDataStore.resolve_inputs`, `is_terminal`)
- `src/dan/models/edges.py` (`DataEdge`, `ControlEdge`)
- `src/dan/models/control_flow.py` (`GateNode`)
- `tests/test_loader/test_paper_writing_parity.py`

## Notes

- The gating logic should be simple and explicit. This is not the place for a new generic dependency solver.
- The desired behavior is: fail early, explain clearly, avoid cascading undefined state.
- If a prerequisite is missing, the run should remain inspectable rather than falling through to a late exception.
- Option A (engine-level) would benefit all workflows, not just paper-writing, but is a broader change. Consider whether 43-2 should be narrowed to workflow-level gates with a separate backlog item for engine-level failure propagation.
- `RunResult` already sets `success = not has_failures` so the overall run fails if any node fails, but individual downstream nodes still execute wastefully.

## Completion Notes (2026-03-26)

- `[src/dan/engine/scheduler.py](/Volumes/data/Dropbox/Projects/deep-agent-network/src/dan/engine/scheduler.py)` now skips downstream nodes when a required upstream data edge is missing because the source failed or was skipped, unless the target explicitly opts into degraded-input execution.
- `examples/paper_writing.py` now inserts prerequisite guard nodes (`require_grounded_literature`, `require_drafting_inputs`) so literature-search and evidence failures stop drafting and review work instead of cascading into misleading outputs.
- Artifact writers explicitly opt into degraded-input execution so the workflow still emits a structured failed bundle.
