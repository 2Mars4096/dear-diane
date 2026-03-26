# 43-3: Structured Degraded Output Contract

**Parent:** [43-paper-review-benchmark-hardening](43-paper-review-benchmark-hardening.md)
**Status:** completed
**Goal:** Replace late crashes in the paper-writing workflow with a structured failed or incomplete artifact that callers and benchmark harnesses can consume.

## Problem

When the workflow loses upstream inputs, downstream nodes run with missing data and produce undefined behavior. That is bad for users and worse for benchmarking because it obscures whether the failure came from literature search, LLM generation, compilation, or artifact assembly.

The benchmark should be able to tell the difference between:

- failed to search papers (Semantic Scholar / OpenRouter unreachable)
- failed to synthesize literature or generate outline
- failed to compile LaTeX
- failed to assemble the final submission bundle
- completed in degraded mode with clear missing sections

### Existing partial-state infrastructure

The engine already has building blocks that this plan can build on:

- `RunResult` carries `run_state` including `partial` (scheduler.py ~3316–3319, ~3452–3478)
- `ChildResultEnvelope.status` allows `"partial"` (control_flow.py ~70)
- `DEAD_EDGE_WARNING` is emitted when a required input port has no data from a failed upstream (scheduler.py ~1959–1986) — but this is a log warning, not a structured artifact
- Runtime repair attempts on `NodeStatus.FAILED` when `runtime_repair_enabled` (scheduler.py ~2356–2459)
- The `save_paper` tool function already writes a `summary.json` with verdict/path metadata — this could be extended for degraded runs

## Tasks

- [ ] 1. Define the degraded-output schema for the paper-writing workflow (extend the existing `summary.json` from `save_paper`)
- [ ] 2. Encode missing prerequisites as structured failure reasons in `RunResult.errors` or a workflow-level artifact
- [ ] 3. Ensure `save_paper` / `package_submission` can emit a failed/incomplete bundle when upstream stages are missing
- [ ] 4. Surface per-node failure reasons in run summaries (leverage `RunResult.node_statuses` and `RunResult.errors`)
- [ ] 5. Add regression tests for at least one degraded-path scenario (e.g., `search_papers` tool fails → downstream produces structured degraded artifact)

## Likely Files

- `examples/paper_writing.py` (especially `save_paper`, `package_submission`, `log_event`)
- `src/dan/engine/scheduler.py` (`_build_result`, `DEAD_EDGE_WARNING`)
- `src/dan/engine/state.py` (`PortDataStore`, `NodeStatus`)
- `src/dan/models/control_flow.py` (`ChildResultEnvelope`)
- `tests/test_loader/test_paper_writing_parity.py`

## Notes

- The contract should prefer explicit machine-readable status over a best-effort human-only message.
- A failed workflow artifact is still useful if it states exactly what is missing.
- The smoke fixture (43-4) should validate this contract, not just the happy path.
- The `save_paper` tool already writes structured JSON — extending it for degraded runs is more pragmatic than introducing a new artifact type.

## Completion Notes (2026-03-26)

- `compile_latex`, `save_paper`, and `package_submission` now emit structured degraded metadata including `artifact_status`, `artifact_stage`, `submission_ready`, `degraded_reasons`, and dependency messages.
- Upstream-blocked runs now still produce artifact manifests instead of ending in late NameErrors or missing-bundle ambiguity.
