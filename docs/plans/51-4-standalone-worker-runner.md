# 51-4: Standalone Worker Runner

**Parent:** [51-universal-worker-hardening-and-standalone-cell](51-universal-worker-hardening-and-standalone-cell.md)
**Status:** not-started
**Goal:** Wrap the hardened worker core in one small standalone runner so the cell can be used independently of the full DAN server/runtime.

## Tasks
- [ ] 1. Define the runner surface
  - [ ] 1-1. Decide the minimal external surfaces: CLI, Python API, or both
  - [ ] 1-2. Keep the interface intentionally small and honest
- [ ] 2. Add the minimum runtime loop
  - [ ] 2-1. Session state
  - [ ] 2-2. Retry/timeout/budget rules
  - [ ] 2-3. Failure/stop conditions
- [ ] 3. Preserve the cell boundary inside the runner
  - [ ] 3-1. Avoid baking organism-level orchestration into the standalone runner
  - [ ] 3-2. Reuse the same core contracts that larger DAN systems will use later
- [ ] 4. Add operator-facing observability
  - [ ] 4-1. Expose compact events, selected evidence refs, capability choices, and final contracted outputs
- [ ] 5. Prove one end-to-end standalone use case
  - [ ] 5-1. Run the same hardened cell outside the main DAN request/runtime path and confirm parity on a small task basket

## Decisions
- The standalone runner exists to prove cell independence, not to become a giant second framework.
- Runner ergonomics matter, but the core contract stays more important than shell polish.

## Notes
- This is the plan that answers “can the worker live on its own?”
