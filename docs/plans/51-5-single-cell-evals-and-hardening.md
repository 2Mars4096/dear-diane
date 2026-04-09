# 51-5: Single-Cell Evals And Hardening

**Parent:** [51-universal-worker-hardening-and-standalone-cell](51-universal-worker-hardening-and-standalone-cell.md)
**Status:** completed
**Goal:** Define and enforce the acceptance bar that proves the worker is a strong independent cell rather than only a promising internal primitive.

## Tasks
- [x] 1. Choose the canonical single-cell tasks
  - [x] 1-1. Coding task
  - [x] 1-2. Research task
  - [x] 1-3. Structured review/validation task
- [x] 2. Define the evaluation dimensions
  - [x] 2-1. Task completion quality
  - [x] 2-2. Contract adherence
  - [x] 2-3. Controllability and budget discipline
  - [x] 2-4. Recoverability after interruption or failure
- [x] 3. Add targeted regressions
  - [x] 3-1. Progressive acquisition stays stepwise
  - [x] 3-2. Capability selection stays selective
  - [x] 3-3. Memory/continuation stays resumable and compact
- [x] 4. Add at least one baseline comparison
  - [x] 4-1. Compare DAN’s standalone cell against a simpler Pi-like baseline on the same task class, using DAN’s target criteria rather than generic popularity criteria
- [x] 5. Define the promotion gate into `52-*`
  - [x] 5-1. Do not begin multicellular composition until the cell acceptance bar is explicit and met

## Decisions
- A strong cell is measured by clarity and recoverability as much as raw task success.
- `52-*` starts only after there is a real bar for single-cell viability.
- The promotion gate is an explicit pytest basket, not a narrative checklist. `52-*` stays blocked unless the basket covering the canonical tasks and focused worker regressions remains green.
- The Pi-like baseline comparison is judged on DAN-relevant cell criteria: selective context loading, explicit contracts, and resumable recovery rather than general ecosystem breadth.

## Notes
- This is the proof plan that prevents the project from skipping from “interesting primitive” to “premature organism.”
- 2026-04-08: Added `tests/eval/test_single_cell_acceptance_gate.py` as the explicit `51-5` promotion gate. It exercises one coding task, one research task, and one structured review task through `StandaloneWorkerRunner`, checks the four required dimensions (`quality`, `contract_adherence`, `budget_discipline`, `recoverability`), and compares the coding task against a simpler Pi-like baseline that lacks selective acquisition and continuation state.
- 2026-04-08: Tightened the standalone runner recoverability proof in `tests/test_worker/test_core_executor.py` with a total-runtime budget regression, then revalidated the full `51-5` basket with `PYTHONPATH=src pytest -q tests/eval/test_single_cell_acceptance_gate.py tests/test_worker/test_core_executor.py tests/test_worker/test_capability_manifest.py tests/test_worker/test_memory_evidence_lifecycle.py` (`15 passed`).
- 2026-04-08: The concrete gate for starting `52-*` is now:
  - the canonical coding/research/structured-review basket passes
  - output contracts remain explicit and parseable in each task
  - selection stays within the staged-acquisition budgets instead of dumping full context
  - the review task resumes from memory/continuation without re-discovering the workspace
