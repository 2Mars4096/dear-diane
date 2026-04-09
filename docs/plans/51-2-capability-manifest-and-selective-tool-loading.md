# 51-2: Capability Manifest And Selective Tool Loading

**Parent:** [51-universal-worker-hardening-and-standalone-cell](51-universal-worker-hardening-and-standalone-cell.md)
**Status:** completed
**Goal:** Replace thin tool identifiers with richer capability manifests so a worker can discover, understand, and selectively load only the tools it actually needs.

## Tasks
- [x] 1. Define the capability manifest shape
  - [x] 1-1. Include identity, purpose, input/output contract, side effects, permissions, cost hints, and retry/idempotency expectations
  - [x] 1-2. Separate compact discovery metadata from full detail so the worker can inspect progressively
- [x] 2. Align manifests with the worker core
  - [x] 2-1. Extend the reusable core interfaces so the cell sees typed capability metadata instead of bare tool ids
  - [x] 2-2. Keep the core DAN-independent where possible
- [x] 3. Add selective loading behavior
  - [x] 3-1. Support listing available capabilities before reading their full specs
  - [x] 3-2. Support loading detailed capability information only for the shortlisted set
- [x] 4. Add governance boundaries
  - [x] 4-1. Express capability allowlists, disallowed side effects, and tier/budget gating through the same manifest surface
- [x] 5. Validate on a minimal initial tool family
  - [x] 5-1. Pick one or two high-value capability families and prove that the worker can choose intelligently without eager full-manifest injection

## Decisions
- Tool selection should be capability-driven and manifest-first, not prompt-lore-first.
- Discovery metadata should be cheap enough to expose broadly; detailed specs should be loaded lazily.

## Notes
- This slice turns tools into real organelles instead of opaque names.
- 2026-04-08: landed the first manifest-first capability surface in `src/dan/worker/core/capabilities.py` and the reusable acquisition path. `CompletionRequest` now carries typed `CapabilityManifest` objects plus derived tool schemas, `LocalAcquisitionProvider` discovers compact manifest summaries and expands them lazily, and focused coverage now exercises manifest discovery/hydration through `tests/test_worker/test_capability_manifest.py` and the updated `tests/test_worker/test_core_executor.py`.
- 2026-04-08 follow-up: the request membrane now owns tool exposure explicitly through `ToolUseContract`. `WorkerCoreExecutor` narrows visible tool catalogs/manifests to the request allowlist, keeps manifest selection authoritative once the cell has shortlisted a tool, and forwards the same contract into direct-tool calls so tool governance stays independent from worker defaults.
- 2026-04-08 validation:
  - `pytest -q tests/test_worker/test_core_executor.py tests/test_worker/test_capability_manifest.py`
  - `pytest -q tests/test_worker/test_model.py tests/test_executor_defaults.py`
