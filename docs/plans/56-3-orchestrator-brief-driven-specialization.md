# 56-3: Orchestrator Brief-Driven Specialization

**Parent:** [56-universal-organism-and-cell-restructure](56-universal-organism-and-cell-restructure.md)
**Status:** not-started
**Goal:** Migrate every place that previously specialized a cell by building a custom `WorkerDefinition` to instead emit an orchestrator brief composed from the `56-2` template library, so 100% of task-specific behavior lives in declarative brief data instead of imperative builder code.

## Tasks

- [ ] 1. Define the brief contract
  - [ ] 1-1. Add `src/dan/worker/brief.py` with a typed `WorkerBrief` carrying `task`, `scope`, `hard_constraints`, `soft_constraints`, `tool_clamp`, `contract_snippets`, `fail_predicates`, `recovery_hints`, `output_contract`, `sampling_preset`, `evidence`, `metadata`
  - [ ] 1-2. Add `request_from_brief(brief) -> ExecutionRequest` to convert briefs into the existing `WorkerCoreExecutor` input
  - [ ] 1-3. Confirm `OutputContract` from `46-7` accommodates brief-supplied `expected_return_shape` + JSON-Schema backstop without changes
- [ ] 2. Migrate orchestrators to emit briefs
  - [ ] 2-1. `coding_execution_organism` produces `WorkerBrief` instances for orchestrator / worker / aggregator / validator stages instead of inline `WorkerDefinition` construction
  - [ ] 2-2. `super_organism` live lanes (website, generic) produce `WorkerBrief` instances, with website-specificity (required files, anti-template phrases, preferred tools) living entirely in the brief
  - [ ] 2-3. `project_execution` and research-reader paths produce `WorkerBrief` instances
  - [ ] 2-4. `incident_execution` paths produce `WorkerBrief` instances
- [ ] 3. Validators stop being a code concept
  - [ ] 3-1. Replace `_build_live_*_validator(...)` functions with `templates.validator_brief(...)` invocations at the orchestrator level
  - [ ] 3-2. Confirm read-only enforcement still happens via `tool_clamp` + the existing role-based runtime classifier in `local_runtime.py`, not via per-builder identity
  - [ ] 3-3. Confirm the validator stays at `sampling_preset="deterministic"` purely through brief assembly
- [ ] 4. Lock the migration with regressions
  - [ ] 4-1. Add a regression that runs the same task through (a) the legacy per-variant builder path and (b) the new brief-driven path and asserts equivalent organism-log event sequences for representative coding, validation, and research-reader cases
  - [ ] 4-2. After legacy paths are removed in `56-5`, the regression collapses to brief-driven only

## Decisions

- The brief is the only specialization channel between orchestrator and cell. Not `WorkerDefinition` fields, not custom system prompts, not custom instruction strings.
- A brief is always composable from snippets + small task-specific text. Orchestrators that only know one task family can use a single template helper (e.g. `templates.coding_worker_brief(...)`) and forget the snippet primitives exist.
- The `OutputContract` substrate from `46-7` is preserved unchanged; the brief just builds it more uniformly.
- Brief construction is the new copy-paste risk surface. Mitigation: keep snippet vocabulary small and orthogonal so common patterns do not need new templates.

## Notes

- This is where the duplicated `cli/super_organism.py` builders actually disappear. After 56-3, those ~70-line `_build_live_*_worker` / `_build_live_*_validator` functions become 5–15 line brief composers.
- Brief composers can be unit-tested as pure data factories — no LLM, no runtime, no fixtures — which is a real reliability win over today's mostly-untested cell builders.
- `WorkerBrief` is a control-plane object, not a wire-format object. It does not need to round-trip through any persisted store; it is built per dispatch.
- Important migration discipline: do not invent a parallel runtime. `WorkerCoreExecutor` continues to be the only execution path; `request_from_brief(...)` is just a thin adapter.
