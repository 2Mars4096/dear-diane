# 42-3: Workflow Loader Import-Path Stability

**Parent:** [42-benchmark-execution-trustworthiness](42-benchmark-execution-trustworthiness.md)
**Status:** completed
**Goal:** Make thin workflow wrappers load cleanly under `dan-run` without requiring manual `PYTHONPATH` setup or path-specific workarounds.

## Problem

The current loader path uses `importlib.util.spec_from_file_location("_dan_user_workflow", path)` to load a workflow module from a file path, but it does not guarantee that the repo root is on `sys.path`. Thin wrappers that import package modules can therefore fail under `dan-run` even though they work in a manually configured shell.

Additionally, there is a behavioral split between CLI and server loading: `validate_workflow_path()` in `workflow_loader.py` is only called from `server/gateway/router.py`, not from `dan-run`'s file loading path, so validation behavior diverges between entrypoints.

That is a fragile developer experience and a benchmark trust issue. A workflow should not depend on hidden environment setup just to import its own package modules.

## Tasks

- [ ] 1. Define the supported loader contract
  - [ ] 1-1. Decide whether workflow wrappers are supported as canonical `dan-run` entrypoints.
  - [ ] 1-2. If they are supported, make repo-relative imports work without manual setup.
  - [ ] 1-3. If they are not supported, document that explicitly and point users to the supported path.

- [ ] 2. Make import handling robust
  - [ ] 2-1. Seed `sys.path` appropriately when loading wrapper workflows (e.g. add the workflow file's parent directory), or
  - [ ] 2-2. Package the repo so the import path is stable by construction.
  - [ ] 2-3. Ensure the chosen approach does not create duplicate import behavior between local and server runs.
  - [ ] 2-4. Consider whether `validate_workflow_path()` should also be called from the CLI path for consistency with the gateway.

- [ ] 3. Add loader regressions
  - [ ] 3-1. Test a thin wrapper workflow that imports `workflows.*`.
  - [ ] 3-2. Test direct file loading through `dan-run`.
  - [ ] 3-3. Test that failure messages are explicit when imports are genuinely broken.

## Likely Files

| File | Why |
|------|-----|
| `src/dan/utils/workflow_loader.py` | `load_graph_from_python()` — the `importlib`-based loader with no `sys.path` seeding |
| `src/dan/cli/run.py` | `load_workflow()` → `load_graph()` — `dan-run` integration path (no `validate_workflow_path` call) |
| `src/dan/server/gateway/router.py` | Uses `validate_workflow_path()` — server-side validation that CLI skips |
| `examples/paper_writing.py` | Real thin-wrapper example (note: `paper_review.py` does not exist at repo root) |
| `tests/` loader and CLI tests | Regression coverage |

## Notes

- The best fix is the one that removes the hidden environment dependency without making the loader more magical.
- If the repo is not yet packaged cleanly, a small `sys.path` seeding rule may be the pragmatic bridge.
- The contract should be explicit enough that benchmark setup is reproducible from a clean shell.
- The eval-run-guide (`docs/eval-run-guide.md`) currently uses `PYTHONPATH=src` in every command — this is exactly the fragile pattern this plan addresses.

## Audit Notes (2026-03-25)

- **`paper_review.py` does not exist** at repo root. The closest thin-wrapper example is `examples/paper_writing.py`.
- **`validate_workflow_path()` is gateway-only** — called from `server/gateway/router.py` but not from `dan-run`'s `load_workflow()`. This creates a validation divergence between CLI and server paths.
- **No `sys.path` seeding** exists in `workflow_loader.py` or `cli/run.py`. Some scripts (`scripts/generate_command_docs.py`, `meta/planner.py`, `server/tools/quant.py`) do their own `sys.path` manipulation — none of those patterns propagate to the workflow loader.
- **`docs/eval-run-guide.md` uses `PYTHONPATH=src` in every command** — plan 42-5 should cross-reference this as the primary doc that needs cleanup once 42-3 is resolved.

## Completion Notes (2026-03-26)

- `[src/dan/utils/workflow_loader.py](/Volumes/data/Dropbox/Projects/deep-agent-network/src/dan/utils/workflow_loader.py)` now seeds temporary import roots for wrapper workflows and uses stable per-path module names.
- `[src/dan/cli/run.py](/Volumes/data/Dropbox/Projects/deep-agent-network/src/dan/cli/run.py)` now validates workflow paths against the configured workspace before loading, matching the gateway safety contract.
- Regression coverage includes thin-wrapper sibling-package imports and workspace-bound path rejection.
