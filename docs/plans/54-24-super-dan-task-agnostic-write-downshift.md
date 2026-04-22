# 54-24: Super DAN Task-Agnostic Write Downshift

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Move oversized whole-file write recovery out of task-specific prompt wording and into the shared local runtime so live organisms can downshift repeated existing-file overwrites to incremental edits generically.

## Tasks
- [x] 1. Add shared runtime detection for truncated existing-file whole-file writes
  - [x] 1-1. Detect malformed `file_write` payloads that still reveal the target existing file path
  - [x] 1-2. Use `finish_reason="length"` / cut-off raw arguments as the generic trigger for downshift
- [x] 2. Enforce same-turn recovery behavior
  - [x] 2-1. Remember existing files that must stop accepting monolithic `file_write` retries in the current turn
  - [x] 2-2. Emit an explicit controller note that requires `file_edit` plus, if needed, one targeted reread
- [x] 3. Lock the task-agnostic behavior with focused tests and docs
  - [x] 3-1. Add a local-runtime regression for repeated existing-file overwrite downshift
  - [x] 3-2. Update backlog, changelog, architecture, bugs, and README notes

## Decisions
- The runtime only forces the downshift when the target is an existing workspace file and `file_edit` is available; new-file creation still keeps `file_write` available.
- The controller note is turn-scoped. It blocks repeated whole-file overwrites of the same existing file for the rest of the current completion turn instead of globally disabling `file_write`.
- The trigger stays narrow: malformed/truncated raw `file_write` arguments plus an existing-file target. This avoids turning all normal whole-file writes into forced `file_edit` workflows.

## Notes
- Focused validation: `python -m py_compile src/dan/worker/organisms/local_runtime.py tests/test_worker/test_super_organism.py` and `PYTHONPATH=src:. pytest -q tests/test_worker/test_super_organism.py -k 'runtime_downshift or shared_board or universal_agent'` (`4 passed, 14 deselected`).
