# 51-3: Memory And Evidence Lifecycle

**Parent:** [51-universal-worker-hardening-and-standalone-cell](51-universal-worker-hardening-and-standalone-cell.md)
**Status:** completed
**Goal:** Give the worker a true evidence and memory lifecycle instead of a one-shot “fetch more context” helper surface.

## Tasks
- [x] 1. Define the evidence lifecycle
  - [x] 1-1. Distinguish catalog-level evidence refs from expanded full evidence payloads
  - [x] 1-2. Preserve provenance, trust labels, and freshness across both forms
- [x] 2. Separate memory layers
  - [x] 2-1. Define working memory, episodic task memory, and longer-lived retained memory
  - [x] 2-2. Clarify which layers are cell-local versus organism-level
- [x] 3. Add write and compaction behavior
  - [x] 3-1. Define when a worker can persist new observations or summaries
  - [x] 3-2. Define compaction/rehydration rules so memory remains useful without context bloat
- [x] 4. Align memory with continuation hooks
  - [x] 4-1. Make resumed work prefer refs, summaries, and selectively rehydrated detail over rescanning raw history
- [x] 5. Validate on representative single-cell tasks
  - [x] 5-1. Prove that memory improves recoverability and continuation without uncontrolled context growth

## Decisions
- Memory should be layered and inspectable; not every retained fact belongs in the active prompt.
- Provenance and trust labels must survive compaction, not disappear during summarization.

## Notes
- This slice should stay focused on cell-level memory. Global shared memory and organism-wide state remain out of scope until `52-*`.
- 2026-04-08: landed the first layered memory lifecycle in the worker core. `src/dan/worker/core/contracts.py` now defines `MemoryLayer`, `MemoryRecord`, `MemoryRequest`, `MemorySnapshot`, and compaction/write contracts; `src/dan/worker/core/memory.py` provides the local in-memory lifecycle provider; and `WorkerCoreExecutor` now recalls working memory inline, exposes episodic/retained memory as discoverable refs, and persists post-run episode summaries through the same bounded contract.
- 2026-04-08 follow-up: `MemoryRequest` now also carries explicit `MemoryExtractionMode` and `allowed_write_scopes` governance. The worker can skip rehydration entirely, choose between working-only vs catalog-only recall, and refuse non-local writes unless the current request explicitly allows that shared memory scope.
- 2026-04-08 validation:
  - `pytest -q tests/test_worker/test_memory_evidence_lifecycle.py`
  - `pytest -q tests/test_worker/test_core_executor.py tests/test_worker/test_executor.py tests/test_worker/test_workflow.py`
