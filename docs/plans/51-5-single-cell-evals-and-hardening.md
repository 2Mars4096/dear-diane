# 51-5: Single-Cell Evals And Hardening

**Parent:** [51-universal-worker-hardening-and-standalone-cell](51-universal-worker-hardening-and-standalone-cell.md)
**Status:** not-started
**Goal:** Define and enforce the acceptance bar that proves the worker is a strong independent cell rather than only a promising internal primitive.

## Tasks
- [ ] 1. Choose the canonical single-cell tasks
  - [ ] 1-1. Coding task
  - [ ] 1-2. Research task
  - [ ] 1-3. Structured review/validation task
- [ ] 2. Define the evaluation dimensions
  - [ ] 2-1. Task completion quality
  - [ ] 2-2. Contract adherence
  - [ ] 2-3. Controllability and budget discipline
  - [ ] 2-4. Recoverability after interruption or failure
- [ ] 3. Add targeted regressions
  - [ ] 3-1. Progressive acquisition stays stepwise
  - [ ] 3-2. Capability selection stays selective
  - [ ] 3-3. Memory/continuation stays resumable and compact
- [ ] 4. Add at least one baseline comparison
  - [ ] 4-1. Compare DAN’s standalone cell against a simpler Pi-like baseline on the same task class, using DAN’s target criteria rather than generic popularity criteria
- [ ] 5. Define the promotion gate into `52-*`
  - [ ] 5-1. Do not begin multicellular composition until the cell acceptance bar is explicit and met

## Decisions
- A strong cell is measured by clarity and recoverability as much as raw task success.
- `52-*` starts only after there is a real bar for single-cell viability.

## Notes
- This is the proof plan that prevents the project from skipping from “interesting primitive” to “premature organism.”
