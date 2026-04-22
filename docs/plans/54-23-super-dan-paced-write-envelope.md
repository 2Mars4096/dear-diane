# 54-23: Super DAN Paced Write Envelope

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Teach Super DAN’s native `--live` execution lane to work at a paced, incremental cadence by explicitly stating a safe `file_write` envelope, preferring incremental edits over giant rewrites, and rejecting scratch-file drift in website mode.

## Tasks
- [x] 1. Tighten Super DAN live worker/task prompts
  - [x] 1-1. Add an explicit safe `file_write` envelope in words/lines
  - [x] 1-2. State “no hurry, no giant chunks” and incremental-edit expectations
  - [x] 1-3. Forbid scratch files outside the required website artifact set
- [x] 2. Tighten runtime recovery guidance
  - [x] 2-1. Extend invalid `file_write` correction nudges with the same safe envelope
  - [x] 2-2. Tell the worker to downshift immediately after one failed large write
- [x] 3. Lock the slice with focused tests and docs
  - [x] 3-1. Add CLI prompt/task contract regressions
  - [x] 3-2. Add runtime nudge regression coverage
  - [x] 3-3. Update backlog, changelog, README, architecture, and bugs notes

## Decisions
- The “write limit” is documented as a conservative safe envelope, not a hard runtime guarantee, because the real truncation boundary depends on remaining response budget.
- Website mode now states scratch-file avoidance explicitly because observed runs drifted into files like `website/test.txt`.
- The patch remains prompt/runtime-contract level; it does not introduce a new scheduler or execution engine.

## Notes
- Current safe envelope: roughly `1200` words or `200` lines per `file_write` payload before the worker should split the work or switch to `file_edit`.
- Focused validation: `python -m py_compile src/dan/cli/super_organism.py src/dan/worker/organisms/local_runtime.py tests/test_cli/test_super_organism.py tests/test_worker/test_local_organism_runtime.py` and `PYTHONPATH=src:. pytest -q tests/test_cli/test_super_organism.py tests/test_worker/test_local_organism_runtime.py -k 'super_organism or invalid_file_write_overwrite or paced or write_stage_keeps_direct_write_tool_enabled_after_repeated_invalid_arguments'` (`23 passed, 72 deselected`).
