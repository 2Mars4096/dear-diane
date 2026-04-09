# 51-4: Standalone Worker Runner

**Parent:** [51-universal-worker-hardening-and-standalone-cell](51-universal-worker-hardening-and-standalone-cell.md)
**Status:** completed
**Goal:** Wrap the hardened worker core in one small standalone runner so the cell can be used independently of the full DAN server/runtime.

## Tasks
- [x] 1. Define the runner surface
  - [x] 1-1. Decide the minimal external surfaces: CLI, Python API, or both
  - [x] 1-2. Keep the interface intentionally small and honest
- [x] 2. Add the minimum runtime loop
  - [x] 2-1. Session state
  - [x] 2-2. Retry/timeout/budget rules
  - [x] 2-3. Failure/stop conditions
- [x] 3. Preserve the cell boundary inside the runner
  - [x] 3-1. Avoid baking organism-level orchestration into the standalone runner
  - [x] 3-2. Reuse the same core contracts that larger DAN systems will use later
- [x] 4. Add operator-facing observability
  - [x] 4-1. Expose compact events, selected evidence refs, capability choices, and final contracted outputs
- [x] 5. Prove one end-to-end standalone use case
  - [x] 5-1. Run the same hardened cell outside the main DAN request/runtime path and confirm parity on a small task basket

## Decisions
- The standalone runner exists to prove cell independence, not to become a giant second framework.
- Runner ergonomics matter, but the core contract stays more important than shell polish.
- The minimal public surface is an API-first Python runner (`StandaloneWorkerRunner`) plus a reusable session object, not a broader standalone shell. A CLI can wrap this later if operator demand justifies it.
- Retry/timeout rules stay runner-local while acquisition, memory, capability hydration, and output-contract behavior remain owned by the worker core.

## Notes
- This is the plan that answers “can the worker live on its own?”
- 2026-04-08: Landed `src/dan/worker/runner.py` as the deliberately small standalone runner surface. It adds `StandaloneRunPolicy`, `StandaloneSessionState`, compact per-attempt/per-session events, explicit stop reasons, and per-run observability summaries without pulling in DAN server/runtime orchestration.
- 2026-04-08: Re-exported the runner surface from `dan.worker` and validated one end-to-end use case in `tests/test_worker/test_core_executor.py`: a standalone reviewer cell inspects a local file inventory, lazily hydrates the default tool catalog, persists memory, resumes from session continuation without rescanning the workspace, and surfaces compact operator-visible events/ref choices/output keys.
