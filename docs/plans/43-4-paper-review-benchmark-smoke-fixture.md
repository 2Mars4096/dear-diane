# 43-4: Paper-Writing Benchmark Smoke Fixture

**Parent:** [43-paper-review-benchmark-hardening](43-paper-review-benchmark-hardening.md)
**Status:** completed
**Goal:** Define one reproducible acceptance path for the paper-writing workflow that can be run from a clean shell and used as a benchmark smoke check.

## Problem

The workflow needs a single canonical run path that proves the system is operating correctly:

- the expected provider env vars are present (`DAN_LLM_API_KEY`, `OPENROUTER_API_KEY` for web search)
- the workflow produces either a valid artifact or a structured degraded artifact (per 43-3)
- the run is reproducible from a clean checkout

Without one fixed acceptance path, benchmark results become harder to reproduce and easier to misread.

### Current run paths

The workflow can be run in two ways today:
1. **Direct Python:** `python examples/paper_writing.py "topic"` — uses `create_engine()` with custom `ToolRegistry` wiring
2. **`dan-run`:** would require the workflow to export `graph` or `build()` per the loader contract (`src/dan/utils/workflow_loader.py`), but `examples/paper_writing.py` exports `build_paper_workflow()` (not `build()`), so `dan-run` would fail on it without a thin wrapper

## Tasks

- [ ] 1. Choose one canonical smoke input (e.g., `"supply chain resilience"`) and one canonical output expectation (e.g., `output/*.tex` exists or structured degraded artifact present)
- [ ] 2. Document the canonical run command (direct Python or via `dan-run` with wrapper)
- [ ] 3. Document the required provider environment variables (`DAN_LLM_API_KEY`, `DAN_LLM_BASE_URL`, optionally `OPENROUTER_API_KEY`, `DAN_LLM_MODEL`)
- [ ] 4. Make the smoke fixture verify both success and degraded failure behavior (per 43-3 contract)
- [ ] 5. Add a minimal regression test or scripted acceptance check for the smoke path
- [ ] 6. Consider adding a `build()` function to `examples/paper_writing.py` so `dan-run` can load it without a wrapper

## Likely Files

- `examples/paper_writing.py`
- `src/dan/utils/workflow_loader.py` (`load_graph_from_python` expects `graph` or `build()`)
- `src/dan/cli/run.py` (how `dan-run` invokes workflows)
- `tests/test_loader/test_paper_writing_parity.py`

## Notes

- The smoke fixture is meant to be boring and repeatable. If it needs special setup, the docs should say so plainly.
- The `--no-human` flag already exists on `paper_writing.py` for non-interactive runs — the smoke fixture should use it.
- The `create_engine()` function in `paper_writing.py` already wires a custom `ToolRegistry` with 7 tool functions — this wiring is required for the workflow to run and is not available through `dan-run` out of the box.

## Completion Notes (2026-03-26)

- The canonical smoke path is now the direct Python CLI documented in `[README.md](/Volumes/data/Dropbox/Projects/deep-agent-network/README.md)` and the module docstring in `[examples/paper_writing.py](/Volumes/data/Dropbox/Projects/deep-agent-network/examples/paper_writing.py)`.
- `examples/paper_writing.py` now exports `build()` so `dan-run examples/paper_writing.py --server ...` is also supported.
- Regression coverage exercises both the happy path and degraded prerequisite-failure path.
