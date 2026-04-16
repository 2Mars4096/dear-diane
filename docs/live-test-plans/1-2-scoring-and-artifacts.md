# 1-2: Scoring And Artifacts

**Parent:** [1-dan-code-live-capability-battery](1-dan-code-live-capability-battery.md)
**Status:** completed
**Goal:** Define the reviewer scorecard and saved run artifacts for DAN Code live capability tests so runs are comparable and debuggable.

## Tasks
- [x] 1. Define the scoring axes
  - [x] 1-1. Correctness against explicit acceptance checks
  - [x] 1-2. Test quality and whether DAN Code adds meaningful regression coverage
  - [x] 1-3. Edit precision and boundedness of the final diff
  - [x] 1-4. Recovery quality after a failed first pass or reviewer follow-up
- [x] 2. Define the required saved artifacts per run
  - [x] 2-1. Prompt brief, repo fixture, and environment metadata
  - [x] 2-2. DAN Code transcript plus `.dan-code/runs/*/events.jsonl`
  - [x] 2-3. Final diff, tests run, and reviewer notes
- [x] 3. Decide the score aggregation model
  - [x] 3-1. Per-scenario pass/fail gates
  - [x] 3-2. Cross-project summary roll-up for the whole battery
- [x] 4. Define the minimum artifact bundle needed to compare two runs honestly

## Decisions
- Live-test scoring should prioritize correctness and adaptation quality over speed or presentation.
- Keep the first battery's roll-up coarse (`pass` / `partial` / `fail`, then `strong` / `mixed` / `blocked`) so manual reviews stay comparable without pretending to be benchmark-precise.

## Notes
- The saved artifact shape should be lightweight enough to capture manually at first, but structured enough to automate later under `tests/eval/`.
- The frozen reviewer contract lives in [reviewer-scorecard.md](reviewer-scorecard.md).
- `recovery_quality` is scored now so the same rubric can carry into the later `1-4` follow-up battery, even though the first rerun loop is still build-only.
