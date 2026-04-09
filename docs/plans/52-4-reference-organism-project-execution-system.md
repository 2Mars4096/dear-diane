# 52-4: Reference Organism Project Execution System

**Parent:** [52-multicellular-composition](52-multicellular-composition.md)
**Status:** completed
**Goal:** Assemble one bounded end-to-end organism that proves the hardened cell and the tissue/organ layers can work together coherently on a real user task.

## Dependencies

- `52-3` should define the first organ contracts before this plan locks the first organism around them.
- The proving task should be chosen to exercise deep research, validation, and bounded build/synthesis together without forcing a huge product surface.

## Tasks
- [x] 1. Choose the first organism shape
  - [x] 1-1. One concierge/lead brain
  - [x] 1-2. One deep researcher organ
  - [x] 1-3. One universal validator organ
  - [x] 1-4. One coding/build organ
  - [x] 1-5. One synthesis/reporting organ
- [x] 2. Choose the first bounded task class
  - [x] 2-1. Prefer a project-execution task that is rich enough to require coordination but narrow enough to validate well
  - [x] 2-2. Favor a code-change-style or structured research-to-delivery task with explicit deep research, execution, validation, and final reporting rather than a vague “general assistant” target
  - [x] 2-3. Lock one exact first proving benchmark: a bounded repo-change request with explicit acceptance criteria, focused validation/tests, and a final evidence-backed delivery summary
- [x] 3. Define organism-level orchestration rules
  - [x] 3-1. Decomposition
  - [x] 3-2. Routing
  - [x] 3-3. Validation and score reporting
  - [x] 3-4. Repair / retry / compare loop
  - [x] 3-5. Final synthesis
- [x] 4. Add organism-level observability
  - [x] 4-1. Make cross-organ handoffs and final accountability inspectable
- [x] 5. Prove the “biology stack” end to end
  - [x] 5-1. Show cells -> tissues -> organs -> organism on one real workflow instead of only in design notes
  - [x] 5-2. Show that the organism can improve itself through a validator-driven score/repair loop instead of only executing once
- [x] 6. Define the later self-rebuilding extension
  - [x] 6-1. Keep the meta workflow builder out of the first organism baseline
  - [x] 6-2. Define how a proven [45-4-meta-workflow-builder-eval-harness](45-4-meta-workflow-builder-eval-harness.md) path could later be promoted into a bounded builder/rebuilder capability for the organism

## Primary Files

- `src/dan/worker/organs/__init__.py`
- `src/dan/worker/organ.py`
- `src/dan/worker/organisms/project_execution.py`
- `src/dan/worker/organisms/__init__.py`
- `src/dan/worker/__init__.py`
- `tests/eval/reference_organism_acceptance.py`
- `tests/eval/test_bounded_organ_patterns.py`
- `tests/eval/test_reference_organism_acceptance.py`
- `tests/test_worker/test_reference_organism.py`

## Success Criteria

- the first organism has one exact benchmark task, not just a category of tasks
- the benchmark flow is explicitly `research -> validate/score -> build -> synthesize`
- the organism can surface why it trusts the result: evidence refs, validator scores, missing-risk notes, and final accountability
- the validator-driven repair loop is bounded and inspectable rather than open-ended
- the self-rebuilding extension is intentionally deferred behind a proven baseline organism

## Decisions
- The first organism should be bounded and inspectable, not maximally broad.
- The reference organism exists to prove the architecture, not to be the final product surface.
- The first organism should prove deep research plus universal validation before it tries to rebuild itself.
- The first organism must choose one exact proving benchmark before implementation starts; “code-change-style task” is still too broad by itself.
- The exact proving benchmark is now a bounded repo-change request with explicit acceptance criteria, one focused validation command, a validator-driven repair loop, and a final evidence-backed delivery summary.
- The top-level organism keeps one planner brain above four bounded organs: `deep_research`, `coding_build`, `universal_validator`, and `synthesis`.
- The validator loop is intentionally score-driven and bounded: build attempt 1 is allowed to fail the bar, the validator emits a repair brief plus score breakdown, the builder retries once, and the organism compares scores before choosing the final candidate.
- Organ-level observability lives in explicit stage records plus the underlying cross-cell trace log; the organism does not hide accountability inside a single merged prompt transcript.
- The later self-rebuilding path remains documentation-only in this baseline. A future builder/rebuilder promotion must consume the same bounded validator and research outputs after `45-4` proves it deserves elevation.

## Notes
- This is the proof that the cell work was worth doing.
- The first organism should look more like a bounded project-execution system than a general-purpose omniscient swarm.
- The self-rebuilding idea is important, but it should arrive as a gated extension after the organism can already research, build, validate, score, and synthesize coherently.
- A good first proving benchmark is a bounded repo-change request with explicit acceptance criteria, targeted tests, and a final evidence-backed delivery note.
- 2026-04-08: the landed organism runtime and preset surface is:
  - `project_execution_reference_organism(...)`
  - `execute_project_execution_organism(...)`
- 2026-04-08: the landed organ runtime and presets used by the organism are:
  - `execute_organ_pattern(...)`
  - `deep_research_organ(...)`
  - `coding_build_organ(...)`
  - `universal_validator_organ(...)`
  - `synthesis_organ(...)`
- 2026-04-08: the deterministic acceptance harness now proves the full stack on one exact workflow:
  - planner brain decomposes the repo-change request
  - deep research organ grounds the candidate in supplied evidence
  - coding/build organ emits candidate 1
  - universal validator scores candidate 1 at `0.74` and emits a repair brief
  - coding/build organ emits candidate 2
  - universal validator scores candidate 2 at `0.96`
  - synthesis organ emits the final delivery summary and accountability map
- Focused validation for the landed slice:
  - `PYTHONPATH=src pytest -q tests/test_worker/test_model.py tests/test_worker/test_reference_organism.py tests/eval/test_bounded_organ_patterns.py tests/eval/test_reference_organism_acceptance.py`
  - `PYTHONPATH=src python - <<'PY' ... run_reference_organism_acceptance(...) ... PY`
