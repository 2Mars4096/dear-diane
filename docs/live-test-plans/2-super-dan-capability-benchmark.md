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
- [x] 4. Try live Super DAN eval traces
  - [x] 4-1. Run and score `short-note-create` against a disposable `/private/tmp` workspace
  - [x] 4-2. Run `short-validation-truth` against a disposable pytest workspace after repo-workspace provider escalation was rejected
  - [x] 4-3. Patch the scorer for live artifact fields, per-call token usage, shell-test evidence, and incomplete traces
- [x] 5. Fix live validation shell hang root cause
  - [x] 5-1. Resolve relative `shell_command.working_directory` values against the Super DAN workspace root
  - [x] 5-2. Kill raw and sandboxed shell subprocess sessions on timeout so child test processes do not survive wrapper termination
  - [x] 5-3. Add regressions for workspace-relative shell cwd and child-process timeout cleanup

## Decisions
- Keep this under `tests/eval/` as a deterministic harness plus event-log scorer, not a live-provider test that would make default pytest depend on model credentials.
- Treat short, medium, and long tasks as first-class dimensions so Super DAN quality cannot be claimed from only quick smoke tasks.
- Token and wall-clock budgets are scoring inputs, not mere metadata; a delivered run can still be marked weak if it burns excessive budget.
- Do not run live evals over the repository workspace without explicit approval; use disposable `/private/tmp` fixtures when a live provider only needs a small validation target.

## Notes
- Added `tests/eval/super_dan_capability_benchmark.py`.
- Added `tests/eval/test_super_dan_capability_benchmark.py`.
- Operator-run traces can be scored with `PYTHONPATH=src:. python -m tests.eval.super_dan_capability_benchmark .dan-super/runs/turn-XX/events.jsonl --case-id <case_id>`.
- Live `short-note-create` passed after scorer fixes: `/private/tmp/dan-super-live-evals/short-note-create/.dan-super/runs/turn-02/events.jsonl`, score `0.7387`, 129.38s, 154,729 tokens, validation passed, `note.md` created.
- Live `short-validation-truth` failed/incompleted on the disposable pytest fixture: `/private/tmp/dan-super-live-evals/short-validation-truth/.dan-super/runs/turn-01/events.jsonl`, score `0.1677`, 370.815s, 52,654 tokens, no `tool.completed` for the started `python -m pytest -q` shell command.
- Root cause fixed: the live worker supplied `working_directory: "."`, which previously resolved against the parent process cwd instead of the workspace root and launched repo-level pytest; timeout cleanup killed only the shell wrapper, leaving the child process alive.
