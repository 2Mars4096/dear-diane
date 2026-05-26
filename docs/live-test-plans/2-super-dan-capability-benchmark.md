# 2: Super DAN Capability Benchmark

**Status:** completed
**Goal:** Add a rerunnable Super DAN capability-evaluation harness that covers short, medium, and long tasks with token usage, wall-clock usage, validation evidence, and delivered-performance scoring.

## Tasks
- [x] 1. Freeze the capability matrix
  - [x] 1-1. Cover short, medium, and long task lengths
  - [x] 1-2. Cover workspace artifacts, validation-gate checks, website patches, source repairs, research reports, greenfield projects, follow-up adaptation, and cross-surface operator work
  - [x] 1-3. Require token, time, validation/evidence, and delivery scoring dimensions on every case
- [x] 2. Add scoring and telemetry extraction
  - [x] 2-1. Score delivered performance separately from token and time efficiency
  - [x] 2-2. Parse `.dan-super/.../events.jsonl` rows for token usage, elapsed time, validation, changed artifacts, tool calls, tests, blockers, and quality scores
  - [x] 2-3. Summarize pass rate, family/length breakdowns, slowest runs, and largest token consumers
- [x] 3. Add focused pytest coverage
  - [x] 3-1. Lock the frozen matrix shape
  - [x] 3-2. Prove budget waste is penalized even when delivery succeeds
  - [x] 3-3. Prove missing token accounting and validation fail validation-gate cases
  - [x] 3-4. Prove event-log parsing and summary grouping

## Decisions
- Keep this under `tests/eval/` as a deterministic harness plus event-log scorer, not a live-provider test that would make default pytest depend on model credentials.
- Treat short, medium, and long tasks as first-class dimensions so Super DAN quality cannot be claimed from only quick smoke tasks.
- Token and wall-clock budgets are scoring inputs, not mere metadata; a delivered run can still be marked weak if it burns excessive budget.

## Notes
- Added `tests/eval/super_dan_capability_benchmark.py`.
- Added `tests/eval/test_super_dan_capability_benchmark.py`.
- Operator-run traces can be scored with `PYTHONPATH=src:. python -m tests.eval.super_dan_capability_benchmark .dan-super/runs/turn-XX/events.jsonl --case-id <case_id>`.
